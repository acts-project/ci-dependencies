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

# Writable home for the container when it runs as the host user (see
# _user_docker_args). The host home isn't mounted, so spack/git get a persisted
# dir here for ~/.spack, bootstrap store and caches instead of a throwaway root.
CI_HOME_DIR = REPO_ROOT / ".local_build" / "home"

# Containers launched by this tool are tagged so leftovers from an interrupted
# run can be cleaned up before the next build — a still-running container holds
# an flock on the bind-mounted spack root and makes spack hang at
# "Waiting for other Spack install process...".
CONTAINER_LABEL = "io.acts-project.local-build"

# Credentials spack reads to authenticate against the buildcache OCI mirror
# (see access_pair in spack.yaml). Forwarded into the push container by name
# only — never with a value — so the token never appears in printed commands.
PUSH_CRED_VARS = ("GH_OCI_USER", "GH_OCI_TOKEN")

# Host builds run as a plain subprocess of this script and so, unlike Docker
# (which starts from a clean container environment), inherit its entire shell
# environment by default. A LD_LIBRARY_PATH/ROOTSYS/PYTHONPATH left over from
# e.g. a CVMFS/LCG `setupATLAS`-style environment sourced earlier in that shell
# points at a *different* ROOT/Python install; ROOT runs itself mid-build (to
# generate tutorials/hsimple.root) and picks it up, loading two copies of its
# own libraries into one process — duplicate class registration, then heap
# corruption ("malloc_consolidate(): unaligned fastbin chunk detected"). Strip
# these before the build ever starts so it only ever sees the store it built.
HOST_BUILD_LEAK_PRONE_VARS = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "DYLD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "ROOTSYS",
    "PYTHONHOME",
    "PYTHONPATH",
    "CMAKE_PREFIX_PATH",
    "PKG_CONFIG_PATH",
    "CPATH",
    "C_INCLUDE_PATH",
    "CPLUS_INCLUDE_PATH",
    "LIBRARY_PATH",
    "ACLOCAL_PATH",
    "SPACK_ENV",
)


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


def load_macos_matrix() -> list[dict]:
    """Parse the macOS build matrix from build.yml.

    These entries have no `image` — build_one.yml runs them on a native macOS
    runner instead of in Docker — so `host` is the only local_build.py command
    that can build them at all.
    """
    with open(WORKFLOW_FILE) as f:
        workflow = yaml.safe_load(f)

    entries = (
        workflow.get("jobs", {})
        .get("build_macos", {})
        .get("strategy", {})
        .get("matrix", {})
        .get("include", [])
    )
    out = []
    for e in entries:
        e = dict(e)
        e.setdefault("label", e.get("os", "macos"))
        # build_macos's `with:` block hardcodes cxxstd: "23" for every entry;
        # matrix.include itself never sets it.
        e.setdefault("cxxstd", "23")
        out.append(e)
    return out


def load_host_matrix() -> list[dict]:
    """Every entry `host` can build directly: the container matrix (built without
    Docker, using the host's own toolchain) plus the macOS matrix."""
    return load_matrix() + load_macos_matrix()


# ---------------------------------------------------------------------------
# Rich rendering
# ---------------------------------------------------------------------------


def entry_flavor(e: dict) -> str:
    """Accelerator flavor of a matrix entry, defaulted like build.yml does."""
    return e.get("flavor", "host")


def describe_entry(entry: dict, index: int | None = None) -> str:
    """One line naming a build unambiguously (Rich markup).

    Every field that can differ between matrix entries is here on purpose: two
    entries can share an image, a compiler and a cxxstd and differ only in
    flavor, so a message that omits one of them cannot identify the build it is
    talking about.
    """
    parts = []
    if index is not None:
        parts.append(f"[bold cyan]#{index}[/bold cyan]")
    if entry.get("label"):
        parts.append(f"[bold green]{entry['label']}[/bold green]")
    parts.append(f"[yellow]{entry['compiler']}[/yellow]")
    parts.append(f"C++{entry.get('cxxstd', '23')}")
    parts.append(f"flavor [red]{entry_flavor(entry)}[/red]")
    platform = entry.get("image") or f"macOS {entry.get('os', '')}, xcode {entry.get('xcode', '')}"
    parts.append(f"[dim]{platform}[/dim]")
    return " · ".join(parts)


def short_entry(entry: dict, index: int | None = None) -> str:
    """Compact form for section rules, which truncate long titles."""
    name = entry.get("label") or entry.get("image", "").rsplit("/", 1)[-1] or "?"
    prefix = f"#{index} " if index is not None else ""
    return f"{prefix}{name} · {entry_flavor(entry)}"


def announce_plan(action: str, entries: list[dict], indices: list[int]) -> None:
    """List, in order, what a sequential run is about to do."""
    console.print(f"\n[bold]{action} plan[/bold] ({len(indices)} in sequence):")
    for n, i in enumerate(indices):
        console.print(f"  {n + 1}. {describe_entry(entries[i], i)}")


def report_sequence_failure(
    action: str, entries: list[dict], indices: list[int], failed_at: int
) -> None:
    """Say which step of a sequential run failed, and what is left unrun."""
    i = indices[failed_at]
    console.print(
        f"\n[bold red]{action} sequence aborted[/bold red] at step "
        f"{failed_at + 1} / {len(indices)}: {describe_entry(entries[i], i)}"
    )
    if failed_at:
        done = ", ".join(short_entry(entries[j], j) for j in indices[:failed_at])
        console.print(f"  [green]succeeded:[/green] {done}")
    remaining = indices[failed_at + 1 :]
    if remaining:
        skipped = ", ".join(short_entry(entries[j], j) for j in remaining)
        console.print(f"  [yellow]not run:[/yellow] {skipped}")


def build_table(entries: list[dict], indices: list[int] | None = None) -> Table:
    """Render entries as a Rich table. `indices` are the global indices to display."""
    if indices is None:
        indices = list(range(len(entries)))

    table = Table(title="Container Build Matrix", show_lines=True, highlight=True)
    table.add_column("#", style="bold cyan", justify="right", no_wrap=True)
    table.add_column("Label", style="bold green")
    table.add_column("Image", style="green")
    table.add_column("Compiler", style="yellow")
    table.add_column("C++ Std", style="magenta", justify="center")
    # Several entries share an image and differ only by flavor, so it has to be
    # visible to tell them apart here and in the selector.
    table.add_column("Flavor", style="red", justify="center")
    table.add_column("Default", style="blue", justify="center")

    for idx in indices:
        e = entries[idx]
        table.add_row(
            str(idx),
            e.get("label", ""),
            e.get("image") or f"(macOS {e.get('os', '')}, xcode {e.get('xcode', '')})",
            e["compiler"],
            str(e.get("cxxstd", "23")),
            entry_flavor(e),
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


def check_spack_root_writable(spack_root: str) -> None:
    """Fail early with an actionable message instead of Spack's cryptic 'cannot
    create lock ... not writable' when the store is owned by another user — e.g.
    a stale root-owned opt/spack/ left behind by a Docker build that ran as root
    (--no-user, or predating the --user default) against the same --ci-spack
    clone that `host` now writes to directly as the plain host user.
    """
    opt = Path(spack_root) / "opt"
    if not opt.exists() or os.access(opt, os.W_OK | os.X_OK):
        return
    try:
        owner = opt.owner()
    except (KeyError, OSError):
        owner = "another user"
    console.print(
        f"[red]Error:[/red] {opt} is owned by [bold]{owner}[/bold] and isn't "
        "writable by you — likely left behind by a Docker build that ran as "
        "root. Fix once with:\n"
        f"  [bold]sudo chown -R $(id -u):$(id -g) {spack_root}[/bold]"
    )
    raise typer.Exit(1)


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


def _user_docker_args(run_as_user: bool) -> list[str]:
    """`docker run` args to run the container as the host user.

    Without this the container runs as root and every file it writes into the
    bind-mounted spack root and build dir is created root-owned on the host,
    which later breaks host-side git ops on the cached spack clone and leaves
    caches you can't clean up without sudo.

    /etc/passwd and /etc/group are mounted read-only so the uid/gid resolve to a
    name inside the container (spack and git call getpwuid and error on an
    unknown uid); HOME is redirected to a persisted, writable dir since the host
    home isn't mounted. Steps that genuinely need root — the apt/dnf installs in
    opengl.sh and the crypt.h shim in spack_build.sh — fall back to sudo.
    """
    if not run_as_user or not hasattr(os, "getuid"):
        return []
    CI_HOME_DIR.mkdir(parents=True, exist_ok=True)
    return [
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-v/etc/passwd:/etc/passwd:ro",
        "-v/etc/group:/etc/group:ro",
        f"-v{CI_HOME_DIR.resolve()}:/home/build",
        "-e",
        "HOME=/home/build",
    ]


def _docker_run_base(
    entry: dict,
    spack_root: str,
    build_dir: Path,
    github_env_file: Path,
    run_as_user: bool = True,
) -> list[str]:
    """Common `docker run` prefix (mounts + env + workdir) shared by build and push."""
    return [
        "docker",
        "run",
        "--rm",
        "--label",
        CONTAINER_LABEL,
        *_user_docker_args(run_as_user),
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
        f"CXXSTD={str(entry.get('cxxstd', '23'))}",
        # Without this every entry would build the plain CPU stack, silently:
        # spack_build.sh defaults FLAVOR to `host`, and the flavor is the only
        # thing that distinguishes e.g. ubuntu26.04-rocm from ubuntu26.04.
        "-e",
        f"FLAVOR={entry_flavor(entry)}",
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
    run_as_user: bool = True,
) -> list[str]:
    cmd = _docker_run_base(entry, spack_root, build_dir, github_env_file, run_as_user)
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
    run_as_user: bool = True,
) -> list[str]:
    """Command to push the just-built env to the buildcache mirror (spack_push.sh).

    BASE_IMAGE defaults to the build image; the OCI credentials are forwarded
    from the host environment by name only (no value), so they never appear in
    the printed command.
    """
    cmd = _docker_run_base(entry, spack_root, build_dir, github_env_file, run_as_user)
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
    run_as_user: bool = True,
    index: int | None = None,
) -> None:
    """Push an already-built environment in `build_dir` to the buildcache mirror."""
    github_env_file = build_dir / "github_env"
    if not dry_run:
        github_env_file.touch(exist_ok=True)

    console.print(f"\n[bold]Pushing:[/bold] {describe_entry(entry, index)}")
    console.print(f"[bold]From:[/bold] [dim]{build_dir}[/dim]")

    push_cmd = build_push_cmd(
        entry, spack_root, build_dir, github_env_file, run_as_user
    )
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
            f"\n[bold red]Push failed[/bold red] "
            f"(exit code [bold]{push_result.returncode}[/bold])"
        )
        console.print(f"  build:     {describe_entry(entry, index)}")
        console.print(f"  build dir: [dim]{build_dir}[/dim]")
        raise typer.Exit(push_result.returncode)


def execute_build(
    entry: dict,
    spack_root: str,
    build_dir: Path,
    dry_run: bool,
    shell: bool,
    push: bool = False,
    jobs: int | None = None,
    run_as_user: bool = True,
    index: int | None = None,
) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    github_env_file = build_dir / "github_env"
    github_env_file.touch(exist_ok=True)

    # Pushing only makes sense after a real, non-interactive build.
    push = push and not shell

    cmd = build_docker_cmd(
        entry, spack_root, build_dir, github_env_file, shell, jobs, run_as_user
    )

    # Announced before the command and before any work, so an interrupted or
    # scrolled-off run still says which configuration was being built.
    console.print(f"\n[bold]Build:[/bold] {describe_entry(entry, index)}")
    console.print(f"[bold]Build dir:[/bold] [dim]{build_dir}[/dim]")

    console.print("\n[bold]Docker command:[/bold]")
    console.print("  " + " \\\n    ".join(cmd), style="dim")

    if dry_run:
        if push:
            execute_push(
                entry,
                spack_root,
                build_dir,
                dry_run=True,
                run_as_user=run_as_user,
                index=index,
            )
        else:
            console.print("\n[yellow]Dry run — not executing.[/yellow]")
        return

    console.print(
        f"\n[bold green]Starting:[/bold green] {describe_entry(entry, index)}\n"
    )
    result = subprocess.run(cmd)
    if result.returncode != 0:
        console.print(
            f"\n[bold red]Build failed[/bold red] (exit code [bold]{result.returncode}[/bold])"
        )
        console.print(f"  build:     {describe_entry(entry, index)}")
        console.print(f"  build dir: [dim]{build_dir}[/dim]")
        console.print(f"  spack env: [dim]{build_dir / '.spack-env'}[/dim]")
        raise typer.Exit(result.returncode)

    console.print(
        f"\n[bold green]Build succeeded:[/bold green] {describe_entry(entry, index)}"
    )

    if push:
        execute_push(
            entry,
            spack_root,
            build_dir,
            dry_run=False,
            run_as_user=run_as_user,
            index=index,
        )


# ---------------------------------------------------------------------------
# Host (no-Docker) builds
# ---------------------------------------------------------------------------

# Persistent envs live here (not under build/, which is the Docker bind-mount
# dir) so a host build can never collide with a container build of the same
# entry, and so they survive independently of --build-dir usage.
HOST_ENV_ROOT = REPO_ROOT / ".local_build" / "host-envs"


def entry_slug(e: dict) -> str:
    """Filesystem-safe id for an entry, used to name its persistent host env dir.

    Mirrors TARGET_TRIPLET (compiler_cxxstd_flavor) but keyed by label/image
    instead of arch, since arch is whatever this host actually is.
    """
    base = e.get("label") or (e.get("image", "").rsplit("/", 1)[-1] if e.get("image") else "host")
    compiler = e["compiler"].replace("@", "-").replace("/", "-").replace(" ", "")
    slug = f"{base}_{compiler}_cxx{e.get('cxxstd', '23')}"
    flavor = entry_flavor(e)
    if flavor != "host":
        slug += f"_{flavor}"
    return slug


def host_env_dir_for(entry: dict, override: Path | None) -> Path:
    return override if override is not None else HOST_ENV_ROOT / entry_slug(entry)


def platform_mismatch_warning(entry: dict) -> str | None:
    """A nudge, not a block: flag an entry whose assumptions likely don't match
    this host (a gcc-toolset compiler_path, an apple-clang compiler, ...)."""
    is_macos_entry = "image" not in entry
    host_is_macos = sys.platform == "darwin"
    if is_macos_entry and not host_is_macos:
        return "This is a macOS matrix entry; the current host is not macOS."
    if not is_macos_entry and host_is_macos:
        return (
            "This is a Linux container matrix entry (its compiler/compiler_path "
            "target a Linux base image); running it natively on macOS is untested."
        )
    return None


def execute_host_build(
    entry: dict,
    spack_root: str,
    env_dir: Path,
    dry_run: bool,
    shell: bool,
    jobs: int | None = None,
    compiler_major_only: bool = False,
    install: bool = True,
    index: int | None = None,
) -> None:
    """Run spack_build.sh directly on the host, installing into `env_dir`.

    No Docker: SPACK_ROOT/COMPILER/COMPILER_PATH/CXXSTD/FLAVOR are passed as
    real env vars instead of `docker run -e`, and `env_dir` is the actual host
    directory spack_build.sh runs in (it symlinks spack_repo and creates
    .spack-env there), so it persists across runs — the env is reused and
    refreshed, not recreated, if `env_dir` already holds one.
    """
    warning = platform_mismatch_warning(entry)
    if warning:
        console.print(f"[yellow]Warning:[/yellow] {warning}")

    action = "Shell" if shell else ("Host build" if install else "Host env setup")
    console.print(f"\n[bold]{action}:[/bold] {describe_entry(entry, index)}")
    console.print(f"[bold]Env dir:[/bold] [dim]{env_dir}[/dim]")
    if compiler_major_only:
        console.print(
            f"[dim]Matching compiler by major version only ({entry['compiler'].split('@')[0]}"
            f"@{entry['compiler'].split('@', 1)[1].split('.')[0]}.*), not the exact pinned version.[/dim]"
        )

    if shell:
        require_built_env(env_dir, dry_run)

    env = os.environ.copy()
    leaked = [v for v in HOST_BUILD_LEAK_PRONE_VARS if env.pop(v, None) is not None]
    if leaked:
        console.print(
            f"[yellow]Stripped from the build environment:[/yellow] {', '.join(leaked)} "
            "(inherited from your shell — e.g. a CVMFS/LCG setup — and can collide "
            "with the freshly built stack)."
        )
    env["SPACK_ROOT"] = spack_root
    env["COMPILER"] = entry["compiler"]
    env["COMPILER_PATH"] = entry.get("compiler_path", "")
    env["CXXSTD"] = str(entry.get("cxxstd", "23"))
    env["FLAVOR"] = entry_flavor(entry)
    if jobs is not None:
        env["BUILD_JOBS"] = str(jobs)
    if compiler_major_only:
        env["COMPILER_MATCH_MAJOR"] = "1"
    if not install:
        env["SKIP_INSTALL"] = "1"

    if shell:
        cmd = [
            "bash",
            "-c",
            f'source "{spack_root}/share/spack/setup-env.sh" && '
            f'spack env activate -d "{env_dir}" && exec "${{SHELL:-bash}}"',
        ]
    else:
        cmd = ["bash", str(REPO_ROOT / "spack_build.sh")]

    console.print("\n[bold]Command:[/bold]")
    console.print("  " + " ".join(cmd), style="dim")

    if dry_run:
        console.print("\n[yellow]Dry run — not executing.[/yellow]")
        return

    check_spack_root_writable(spack_root)

    env_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"\n[bold green]Starting:[/bold green] {describe_entry(entry, index)}\n")
    result = subprocess.run(cmd, cwd=env_dir, env=env)
    if result.returncode != 0:
        if shell:
            failed = "Shell exited with an error"
        elif install:
            failed = "Build failed"
        else:
            failed = "Env setup failed"
        console.print(
            f"\n[bold red]{failed}[/bold red] (exit code [bold]{result.returncode}[/bold])"
        )
        console.print(f"  build:   {describe_entry(entry, index)}")
        console.print(f"  env dir: [dim]{env_dir}[/dim]")
        raise typer.Exit(result.returncode)

    if shell:
        pass
    elif install:
        console.print(f"\n[bold green]Build succeeded:[/bold green] {describe_entry(entry, index)}")
        console.print(
            f"  Installed into [dim]{env_dir}[/dim] — it persists, so re-running this "
            "build reuses and updates it. Activate it later with:\n"
            f"  [bold]spack env activate -d {env_dir}[/bold]"
        )
    else:
        console.print(
            f"\n[bold green]Env ready:[/bold green] {describe_entry(entry, index)} "
            "(created/concretized, not installed)"
        )
        console.print(
            f"  Persisted at [dim]{env_dir}[/dim]. Install it with:\n"
            f"  [bold]spack -e {env_dir} install[/bold]\n"
            "  or re-run this same selection without --no-install."
        )


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
            str(e.get("cxxstd", "23")),
            # label and flavor are the only handles on the GPU entries: they
            # share image, compiler and cxxstd with the plain CPU build.
            e.get("label", ""),
            entry_flavor(e),
            # macOS entries have no image/label to search by otherwise.
            e.get("os", ""),
            e.get("xcode", ""),
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
def list_builds(
    host: Annotated[
        bool,
        typer.Option(
            "--host",
            help="Include the macOS matrix alongside the container matrix "
            "(the set `host` can build; `run`/`push` only ever see the container one).",
        ),
    ] = False,
):
    """List all container builds from the CI matrix."""
    entries = load_host_matrix() if host else load_matrix()
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
    user: Annotated[
        bool,
        typer.Option(
            "--user/--no-user",
            help="Run the container as the host user so bind-mounted files aren't root-owned (default). --no-user runs as root.",
        ),
    ] = True,
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
        announce_plan("Build", entries, indices)
        for n, i in enumerate(indices):
            console.rule(
                f"[bold]Build {n + 1} / {len(indices)}:[/bold] {short_entry(entries[i], i)}"
            )
            try:
                execute_build(
                    entries[i],
                    sr,
                    build_dir / f"build_{i}",
                    dry_run,
                    shell,
                    push,
                    jobs,
                    user,
                    index=i,
                )
            except typer.Exit:
                report_sequence_failure("Build", entries, indices, n)
                raise
        console.print(
            f"\n[bold green]All {len(indices)} build(s) completed successfully.[/bold green]"
        )
        return

    if selector is None or len(selector) == 0:
        idx = select_interactively(entries, list(range(len(entries))))
    else:
        idx = resolve_entry(entries, selector)

    sr = resolve_spack_root(spack_root, ci_spack, spack_ref, refresh_spack, dry_run)
    execute_build(
        entries[idx], sr, build_dir, dry_run, shell, push, jobs, user, index=idx
    )


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
    user: Annotated[
        bool,
        typer.Option(
            "--user/--no-user",
            help="Run the container as the host user so bind-mounted files aren't root-owned (default). --no-user runs as root.",
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
                console.print(
                    f"[red]No builds matching {selector_label(selector)}[/red]"
                )
                raise typer.Exit(1)
        else:
            indices = list(range(len(entries)))
        sr = resolve_spack_root(spack_root, ci_spack, spack_ref, refresh_spack, dry_run)
        announce_plan("Push", entries, indices)
        for n, i in enumerate(indices):
            console.rule(
                f"[bold]Push {n + 1} / {len(indices)}:[/bold] {short_entry(entries[i], i)}"
            )
            bd = build_dir / f"build_{i}"
            try:
                require_built_env(bd, dry_run)
                execute_push(entries[i], sr, bd, dry_run, user, index=i)
            except typer.Exit:
                report_sequence_failure("Push", entries, indices, n)
                raise
        console.print(
            f"\n[bold green]All {len(indices)} push(es) completed successfully.[/bold green]"
        )
        return

    if selector is None or len(selector) == 0:
        idx = select_interactively(entries, list(range(len(entries))))
    else:
        idx = resolve_entry(entries, selector)

    sr = resolve_spack_root(spack_root, ci_spack, spack_ref, refresh_spack, dry_run)
    require_built_env(build_dir, dry_run)
    execute_push(entries[idx], sr, build_dir, dry_run, user, index=idx)


@app.command("host")
def host_build(
    selector: Annotated[
        Optional[list[str]],
        typer.Argument(
            help="One or more substrings to match against compiler/image/os/cxxstd (AND logic), or a single index."
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
    env_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--env-dir",
            "-e",
            help="Persistent spack env directory. Default: .local_build/host-envs/<slug> "
            "derived from the selected build, reused across runs. Not allowed with --all.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", "-n", help="Print the command without running it."
        ),
    ] = False,
    shell: Annotated[
        bool,
        typer.Option(
            "--shell",
            help="Activate the persisted env and open a shell instead of building; "
            "requires a prior successful build in that env dir.",
        ),
    ] = False,
    install: Annotated[
        bool,
        typer.Option(
            "--install/--no-install",
            help="Also run 'spack install' after concretizing (default). --no-install "
            "just creates/updates and concretizes the persistent env, so you can "
            "inspect it or install it yourself later.",
        ),
    ] = True,
    run_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Build sequentially: every config matching the selector, or the whole matrix if no selector is given.",
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
    compiler_major_only: Annotated[
        bool,
        typer.Option(
            "--compiler-major-only",
            help="Match the matrix compiler by name + major version only (e.g. gcc@15) "
            "instead of the exact pinned patch version. Opt-in because it builds "
            "against whatever patch version this host actually has, which CI doesn't "
            "guarantee is the same one.",
        ),
    ] = False,
):
    """Build a config directly on this host, no Docker — installs into a
    persistent spack env under .local_build/host-envs/ so it sticks around for
    later testing (e.g. building ACTS against a GPU flavor). This is also the
    only way to build the macOS matrix, which has no container image.
    """
    entries = load_host_matrix()

    if run_all:
        if env_dir is not None:
            console.print(
                "[red]--env-dir can't be combined with --all[/red] "
                "(each build needs its own directory)."
            )
            raise typer.Exit(1)
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
        announce_plan("Host build", entries, indices)
        for n, i in enumerate(indices):
            console.rule(
                f"[bold]Build {n + 1} / {len(indices)}:[/bold] {short_entry(entries[i], i)}"
            )
            try:
                execute_host_build(
                    entries[i],
                    sr,
                    host_env_dir_for(entries[i], None),
                    dry_run,
                    shell,
                    jobs,
                    compiler_major_only,
                    install,
                    index=i,
                )
            except typer.Exit:
                report_sequence_failure("Host build", entries, indices, n)
                raise
        console.print(
            f"\n[bold green]All {len(indices)} host build(s) completed successfully.[/bold green]"
        )
        return

    if selector is None or len(selector) == 0:
        idx = select_interactively(entries, list(range(len(entries))))
    else:
        idx = resolve_entry(entries, selector)

    sr = resolve_spack_root(spack_root, ci_spack, spack_ref, refresh_spack, dry_run)
    execute_host_build(
        entries[idx],
        sr,
        host_env_dir_for(entries[idx], env_dir),
        dry_run,
        shell,
        jobs,
        compiler_major_only,
        install,
        index=idx,
    )


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Run CI container builds locally. With no subcommand, opens interactive build selection."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(run_build)


if __name__ == "__main__":
    app()
