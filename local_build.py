#!/usr/bin/env python3

# /// script
# dependencies = [
#   "typer",
#   "rich",
#   "pyyaml",
# ]
# ///

"""Helper script to run CI container builds locally using Docker."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

app = typer.Typer(
    name="local-build",
    help="Run CI container builds locally in Docker.",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()

REPO_ROOT = Path(__file__).parent
WORKFLOW_FILE = REPO_ROOT / ".github/workflows/build.yml"

# CI sets up spack via `spack/setup-spack@v2`, which defaults to the `develop`
# branch, then applies any spack_patches/*.patch (see build_one.yml). These
# mirror that so a local build uses the same spack as CI.
SPACK_GIT_URL = "https://github.com/spack/spack.git"
SPACK_PATCHES_DIR = REPO_ROOT / "spack_patches"
CI_SPACK_DIR = REPO_ROOT / ".local_build" / "spack"

# Containers launched by this tool are tagged so leftovers from an interrupted
# run can be cleaned up before the next build — a still-running container holds
# an flock on the bind-mounted spack root and makes spack hang at
# "Waiting for other Spack install process...".
CONTAINER_LABEL = "io.acts-project.local-build"

# Credentials spack reads to authenticate against the buildcache OCI mirror
# (see access_pair in spack.yaml). Forwarded into the push container by name
# only — never with a value — so the token never appears in printed commands.
PUSH_CRED_VARS = ("GH_OCI_USER", "GH_OCI_TOKEN")


# ---------------------------------------------------------------------------
# Matrix parsing
# ---------------------------------------------------------------------------


def load_matrix() -> list[dict]:
    """Parse the container build matrix from build.yml (image-based entries only)."""
    with open(WORKFLOW_FILE) as f:
        workflow = yaml.safe_load(f)

    entries = (
        workflow.get("jobs", {})
        .get("build_container", {})
        .get("strategy", {})
        .get("matrix", {})
        .get("include", [])
    )
    return [e for e in entries if e.get("image")]


# ---------------------------------------------------------------------------
# Rich rendering
# ---------------------------------------------------------------------------


def build_table(entries: list[dict], indices: list[int] | None = None) -> Table:
    """Render entries as a Rich table. `indices` are the global indices to display."""
    if indices is None:
        indices = list(range(len(entries)))

    table = Table(title="Container Build Matrix", show_lines=True, highlight=True)
    table.add_column("#", style="bold cyan", justify="right", no_wrap=True)
    table.add_column("Image", style="green")
    table.add_column("Compiler", style="yellow")
    table.add_column("C++ Std", style="magenta", justify="center")
    table.add_column("Default", style="blue", justify="center")

    for idx in indices:
        e = entries[idx]
        table.add_row(
            str(idx),
            e["image"],
            e["compiler"],
            str(e.get("cxxstd", "20")),
            "✓" if e.get("default") else "",
        )
    return table


# ---------------------------------------------------------------------------
# Spack root discovery
# ---------------------------------------------------------------------------


def _git(
    args: list[str], cwd: Path | None = None, quiet: bool = False
) -> subprocess.CompletedProcess:
    """Run a git command, exiting with a friendly message on failure."""
    kwargs: dict = {"capture_output": True, "text": True} if quiet else {}
    try:
        result = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, **kwargs)
    except FileNotFoundError:
        console.print("[red]Error:[/red] 'git' not found in PATH.")
        raise typer.Exit(1)
    if result.returncode != 0:
        detail = (
            (result.stderr or "").strip() if quiet else f"git {' '.join(args)} failed"
        )
        console.print(f"[red]git error:[/red] {detail}")
        raise typer.Exit(1)
    return result


def apply_spack_patches(repo: Path) -> None:
    """(Re)apply spack_patches/*.patch onto a clean `ci-patched` branch.

    Mirrors the 'Apply spack patches' step in build_one.yml. The branch is
    recreated from the pristine `ci-base` each run so application is idempotent.
    """
    patches = (
        sorted(SPACK_PATCHES_DIR.glob("*.patch")) if SPACK_PATCHES_DIR.is_dir() else []
    )
    _git(["checkout", "-fB", "ci-patched", "ci-base"], cwd=repo, quiet=True)
    if not patches:
        return
    # `git am` needs an author identity configured in the spack checkout.
    _git(["config", "user.name", "local-build"], cwd=repo, quiet=True)
    _git(["config", "user.email", "local-build@example.com"], cwd=repo, quiet=True)
    console.print(f"[dim]Applying {len(patches)} spack patch(es)…[/dim]")
    for p in patches:
        _git(["am", "-3", str(p.resolve())], cwd=repo, quiet=True)


def setup_ci_spack(ref: str, refresh: bool) -> str:
    """Clone spack `ref` (shallow, cached) and apply spack_patches, like CI does."""
    repo = CI_SPACK_DIR
    fresh = not repo.exists()
    if fresh:
        console.print(f"[dim]Cloning spack '{ref}' into {repo} (shallow)…[/dim]")
        repo.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--depth", "1", "--branch", ref, SPACK_GIT_URL, str(repo)])
    elif refresh:
        console.print(f"[dim]Updating cached spack clone to latest '{ref}'…[/dim]")
        _git(["fetch", "--depth", "1", "origin", ref], cwd=repo, quiet=True)
    else:
        console.print(
            f"[dim]Using cached spack clone at {repo} (pass --refresh-spack to update).[/dim]"
        )

    # Detach so we can force-update the pristine `ci-base` branch even if it is
    # currently checked out, then point it at the desired base commit.
    _git(["checkout", "-f", "--detach"], cwd=repo, quiet=True)
    base = "HEAD" if fresh else ("FETCH_HEAD" if refresh else "ci-base")
    _git(["branch", "-f", "ci-base", base], cwd=repo, quiet=True)

    apply_spack_patches(repo)
    rev = _git(["rev-parse", "--short", "HEAD"], cwd=repo, quiet=True).stdout.strip()
    console.print(f"[dim]spack ready at {repo} → {rev}[/dim]")
    return str(repo)


def resolve_spack_root(
    override: str | None,
    ci_spack: bool,
    spack_ref: str,
    refresh_spack: bool,
    dry_run: bool = False,
) -> str:
    if override:
        return override
    if ci_spack:
        if dry_run:
            console.print(
                f"[yellow]Dry run — skipping spack clone; would use {CI_SPACK_DIR}.[/yellow]"
            )
            return str(CI_SPACK_DIR)
        return setup_ci_spack(spack_ref, refresh_spack)
    try:
        result = subprocess.run(
            ["spack", "location", "-r"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except FileNotFoundError:
        console.print(
            "[red]Error:[/red] 'spack' not found in PATH. Pass --spack-root explicitly."
        )
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        console.print(
            f"[red]Error running 'spack location -r':[/red] {e.stderr.strip()}"
        )
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Docker invocation
# ---------------------------------------------------------------------------


def cleanup_stale_containers() -> None:
    """Remove containers left over from previous (interrupted) local-build runs.

    A still-running container from an earlier run keeps an flock on the
    bind-mounted spack root, which makes a fresh `spack install` hang forever at
    "Waiting for other Spack install process...". They are matched by the label
    we tag every launch with, so only this tool's containers are touched.
    """
    try:
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label={CONTAINER_LABEL}"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        console.print("[red]Error:[/red] 'docker' not found in PATH.")
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        console.print(
            f"[yellow]Warning:[/yellow] could not list containers: {e.stderr.strip()}"
        )
        return

    ids = result.stdout.split()
    if not ids:
        return

    console.print(
        f"[yellow]Cleaning up {len(ids)} leftover local-build container(s)…[/yellow]"
    )
    subprocess.run(["docker", "rm", "-f", *ids], capture_output=True, text=True)


def _docker_run_base(
    entry: dict,
    spack_root: str,
    build_dir: Path,
    github_env_file: Path,
) -> list[str]:
    """Common `docker run` prefix (mounts + env + workdir) shared by build and push."""
    return [
        "docker",
        "run",
        "--rm",
        "--label",
        CONTAINER_LABEL,
        f"-v{REPO_ROOT.resolve()}:/src",
        f"-v{spack_root}:/spack",
        f"-v{build_dir.resolve()}:/build",
        f"-v{github_env_file.resolve()}:/github_env",
        "-e",
        "SPACK_ROOT=/spack",
        "-e",
        f"COMPILER={entry['compiler']}",
        "-e",
        f"COMPILER_PATH={entry.get('compiler_path', '')}",
        "-e",
        f"CXXSTD={str(entry.get('cxxstd', '20'))}",
        "-e",
        "GITHUB_ENV=/github_env",
        "-w",
        "/build",
    ]


def build_docker_cmd(
    entry: dict,
    spack_root: str,
    build_dir: Path,
    github_env_file: Path,
    shell: bool,
    jobs: int | None = None,
) -> list[str]:
    cmd = _docker_run_base(entry, spack_root, build_dir, github_env_file)
    image = entry["image"]

    if jobs is not None:
        # Consumed by spack_build.sh as `spack install -j $BUILD_JOBS`.
        cmd += ["-e", f"BUILD_JOBS={jobs}"]

    if shell:
        cmd += ["-it", "--entrypoint", "/bin/bash", image]
    else:
        # Allocate a pseudo-TTY and keep stdin open for the build so spack inside
        # the container sees an interactive terminal (progress bars, colors) and
        # any prompts work — but only when this script is itself attached to a
        # TTY, otherwise `docker run -t` errors.
        if sys.stdin.isatty() and sys.stdout.isatty():
            cmd.append("-it")
        cmd += [image, "/src/spack_build.sh"]

    return cmd


def build_push_cmd(
    entry: dict,
    spack_root: str,
    build_dir: Path,
    github_env_file: Path,
) -> list[str]:
    """Command to push the just-built env to the buildcache mirror (spack_push.sh).

    BASE_IMAGE defaults to the build image; the OCI credentials are forwarded
    from the host environment by name only (no value), so they never appear in
    the printed command.
    """
    cmd = _docker_run_base(entry, spack_root, build_dir, github_env_file)
    cmd += ["-e", f"BASE_IMAGE={entry['image']}"]
    for var in PUSH_CRED_VARS:
        if os.environ.get(var):
            cmd += ["-e", var]
    cmd += [entry["image"], "/src/spack_push.sh"]
    return cmd


def execute_push(
    entry: dict,
    spack_root: str,
    build_dir: Path,
    dry_run: bool,
) -> None:
    """Push an already-built environment in `build_dir` to the buildcache mirror."""
    github_env_file = build_dir / "github_env"
    if not dry_run:
        github_env_file.touch(exist_ok=True)

    push_cmd = build_push_cmd(entry, spack_root, build_dir, github_env_file)
    console.print("\n[bold]Push command:[/bold]")
    console.print("  " + " \\\n    ".join(push_cmd), style="dim")

    if dry_run:
        console.print("\n[yellow]Dry run — not executing.[/yellow]")
        return

    if not all(os.environ.get(v) for v in PUSH_CRED_VARS):
        console.print(
            f"[yellow]Warning:[/yellow] {' / '.join(PUSH_CRED_VARS)} not set in the environment; "
            "buildcache push may fail (unauthenticated)."
        )
    console.print("\n[bold green]Pushing to buildcache…[/bold green]\n")
    push_result = subprocess.run(push_cmd)
    if push_result.returncode != 0:
        console.print(
            f"\n[bold red]Push failed[/bold red] with exit code [bold]{push_result.returncode}[/bold]"
        )
        raise typer.Exit(push_result.returncode)


def execute_build(
    entry: dict,
    spack_root: str,
    build_dir: Path,
    dry_run: bool,
    shell: bool,
    push: bool = False,
    jobs: int | None = None,
) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    github_env_file = build_dir / "github_env"
    github_env_file.touch(exist_ok=True)

    # Pushing only makes sense after a real, non-interactive build.
    push = push and not shell

    cmd = build_docker_cmd(entry, spack_root, build_dir, github_env_file, shell, jobs)

    console.print("\n[bold]Docker command:[/bold]")
    console.print("  " + " \\\n    ".join(cmd), style="dim")

    if dry_run:
        if push:
            execute_push(entry, spack_root, build_dir, dry_run=True)
        else:
            console.print("\n[yellow]Dry run — not executing.[/yellow]")
        return

    console.print(
        f"\n[bold green]Starting:[/bold green] "
        f"[yellow]{entry['compiler']}[/yellow] · "
        f"[green]{entry['image']}[/green]"
        f" (C++{entry.get('cxxstd', '20')})\n"
    )
    result = subprocess.run(cmd)
    if result.returncode != 0:
        console.print(
            f"\n[bold red]Build failed[/bold red] with exit code [bold]{result.returncode}[/bold]"
        )
        raise typer.Exit(result.returncode)

    if push:
        execute_push(entry, spack_root, build_dir, dry_run=False)


# ---------------------------------------------------------------------------
# Entry selection helpers
# ---------------------------------------------------------------------------


def select_interactively(entries: list[dict], indices: list[int]) -> int:
    """Show a sub-table of `indices` and prompt the user to pick one."""
    console.print(build_table(entries, indices))
    valid = [str(i) for i in indices]
    choice = Prompt.ask(
        f"Select build [bold cyan]({'|'.join(valid)})[/bold cyan]",
        choices=valid,
        show_choices=False,
    )
    return int(choice)


def entry_searchable(e: dict) -> str:
    """Return a single lowercase string of all searchable fields for an entry."""
    return " ".join(
        [
            e.get("compiler", ""),
            e.get("image", ""),
            str(e.get("cxxstd", "20")),
        ]
    ).lower()


def resolve_entries(entries: list[dict], terms: list[str]) -> list[int]:
    """Resolve selector terms to all matching entry indices.

    A single numeric term is treated as an exact index. Otherwise, all terms
    must match (case-insensitive substring AND logic) against compiler, image,
    and cxxstd fields. Returns every match (empty list if none).
    """
    if len(terms) == 1 and terms[0].isdigit():
        idx = int(terms[0])
        if not 0 <= idx < len(entries):
            console.print(f"[red]Index {idx} out of range (0–{len(entries) - 1})[/red]")
            raise typer.Exit(1)
        return [idx]

    # AND: entry must match every term
    return [
        i
        for i, e in enumerate(entries)
        if all(t.lower() in entry_searchable(e) for t in terms)
    ]


def selector_label(terms: list[str]) -> str:
    return " ".join(f"'[bold]{t}[/bold]'" for t in terms)


def resolve_entry(entries: list[dict], terms: list[str]) -> int:
    """Resolve selector terms to a single entry index, prompting if ambiguous."""
    matches = resolve_entries(entries, terms)
    label = selector_label(terms)
    if not matches:
        console.print(f"[red]No builds matching {label}[/red]")
        raise typer.Exit(1)
    if len(matches) == 1:
        return matches[0]

    console.print(f"[yellow]Multiple matches for {label}:[/yellow]")
    return select_interactively(entries, matches)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


@app.command("list")
def list_builds():
    """List all container builds from the CI matrix."""
    entries = load_matrix()
    console.print(build_table(entries))


@app.command("run")
def run_build(
    selector: Annotated[
        Optional[list[str]],
        typer.Argument(
            help="One or more substrings to match against compiler/image/cxxstd (AND logic), or a single index."
        ),
    ] = None,
    spack_root: Annotated[
        Optional[str],
        typer.Option(
            "--spack-root", "-s", help="Explicit spack root path; overrides --ci-spack."
        ),
    ] = None,
    ci_spack: Annotated[
        bool,
        typer.Option(
            "--ci-spack/--no-ci-spack",
            help="Use a cloned & patched spack matching CI (default). --no-ci-spack auto-detects via 'spack location -r'.",
        ),
    ] = True,
    spack_ref: Annotated[
        str,
        typer.Option("--spack-ref", help="Git ref of spack to clone for --ci-spack."),
    ] = "develop",
    refresh_spack: Annotated[
        bool,
        typer.Option(
            "--refresh-spack",
            help="Fetch the latest --spack-ref into the cached clone before building.",
        ),
    ] = False,
    build_dir: Annotated[
        Path,
        typer.Option(
            "--build-dir",
            "-b",
            help="Host directory mounted as /build inside the container.",
        ),
    ] = Path("build"),
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Print the Docker command without running it."
        ),
    ] = False,
    shell: Annotated[
        bool,
        typer.Option(
            "--shell",
            help="Open an interactive shell in the container instead of running the build.",
        ),
    ] = False,
    run_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Run builds sequentially: every config matching the selector, or the whole matrix if no selector is given.",
        ),
    ] = False,
    cleanup: Annotated[
        bool,
        typer.Option(
            "--cleanup/--no-cleanup",
            help="Remove leftover local-build containers before starting (default). They hold spack's install lock.",
        ),
    ] = True,
    push: Annotated[
        bool,
        typer.Option(
            "--push",
            help="After a successful build, push to the buildcache mirror. Forwards GH_OCI_USER/GH_OCI_TOKEN from the environment.",
        ),
    ] = False,
    jobs: Annotated[
        Optional[int],
        typer.Option(
            "--jobs",
            "-j",
            min=1,
            help="Total build parallelism (spack 'config:build_jobs'); default is spack's own min(16, ncpu).",
        ),
    ] = (os.cpu_count() or 1),
):
    """Run a container build locally in Docker."""
    entries = load_matrix()

    if cleanup and not dry_run:
        cleanup_stale_containers()

    if run_all:
        if selector:
            indices = resolve_entries(entries, selector)
            if not indices:
                console.print(
                    f"[red]No builds matching {selector_label(selector)}[/red]"
                )
                raise typer.Exit(1)
        else:
            indices = list(range(len(entries)))
        sr = resolve_spack_root(spack_root, ci_spack, spack_ref, refresh_spack, dry_run)
        for n, i in enumerate(indices):
            console.rule(f"[bold]Build {n + 1} / {len(indices)} (#{i})[/bold]")
            execute_build(
                entries[i], sr, build_dir / f"build_{i}", dry_run, shell, push, jobs
            )
        return

    if selector is None or len(selector) == 0:
        idx = select_interactively(entries, list(range(len(entries))))
    else:
        idx = resolve_entry(entries, selector)

    sr = resolve_spack_root(spack_root, ci_spack, spack_ref, refresh_spack, dry_run)
    execute_build(entries[idx], sr, build_dir, dry_run, shell, push, jobs)


def require_built_env(build_dir: Path, dry_run: bool) -> None:
    """Fail if `build_dir` doesn't contain a spack environment to push."""
    if dry_run:
        return
    if not (build_dir / ".spack-env").exists():
        console.print(
            f"[red]No spack environment in {build_dir}[/red] (missing .spack-env); "
            "run a build there first."
        )
        raise typer.Exit(1)


@app.command("push")
def push_builds(
    selector: Annotated[
        Optional[list[str]],
        typer.Argument(
            help="One or more substrings to match against compiler/image/cxxstd (AND logic), or a single index."
        ),
    ] = None,
    spack_root: Annotated[
        Optional[str],
        typer.Option(
            "--spack-root", "-s", help="Explicit spack root path; overrides --ci-spack."
        ),
    ] = None,
    ci_spack: Annotated[
        bool,
        typer.Option(
            "--ci-spack/--no-ci-spack",
            help="Use the cloned & patched CI spack (default). --no-ci-spack auto-detects via 'spack location -r'.",
        ),
    ] = True,
    spack_ref: Annotated[
        str,
        typer.Option("--spack-ref", help="Git ref of spack to clone for --ci-spack."),
    ] = "develop",
    refresh_spack: Annotated[
        bool,
        typer.Option(
            "--refresh-spack",
            help="Fetch the latest --spack-ref into the cached clone before pushing.",
        ),
    ] = False,
    build_dir: Annotated[
        Path,
        typer.Option(
            "--build-dir",
            "-b",
            help="Host directory mounted as /build (must hold the built environment).",
        ),
    ] = Path("build"),
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Print the push command without running it."
        ),
    ] = False,
    push_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Push sequentially: every config matching the selector (from build/build_<i>), or the whole matrix if no selector is given.",
        ),
    ] = False,
    cleanup: Annotated[
        bool,
        typer.Option(
            "--cleanup/--no-cleanup",
            help="Remove leftover local-build containers before starting (default).",
        ),
    ] = True,
) -> None:
    """Push already-built environment(s) to the buildcache mirror, without rebuilding."""
    entries = load_matrix()

    if cleanup and not dry_run:
        cleanup_stale_containers()

    if push_all:
        if selector:
            indices = resolve_entries(entries, selector)
            if not indices:
                console.print(f"[red]No builds matching {selector_label(selector)}[/red]")
                raise typer.Exit(1)
        else:
            indices = list(range(len(entries)))
        sr = resolve_spack_root(spack_root, ci_spack, spack_ref, refresh_spack, dry_run)
        for n, i in enumerate(indices):
            console.rule(f"[bold]Push {n + 1} / {len(indices)} (#{i})[/bold]")
            bd = build_dir / f"build_{i}"
            require_built_env(bd, dry_run)
            execute_push(entries[i], sr, bd, dry_run)
        return

    if selector is None or len(selector) == 0:
        idx = select_interactively(entries, list(range(len(entries))))
    else:
        idx = resolve_entry(entries, selector)

    sr = resolve_spack_root(spack_root, ci_spack, spack_ref, refresh_spack, dry_run)
    require_built_env(build_dir, dry_run)
    execute_push(entries[idx], sr, build_dir, dry_run)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Run CI container builds locally. With no subcommand, opens interactive build selection."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(run_build)


if __name__ == "__main__":
    app()
