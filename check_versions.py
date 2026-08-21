#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "diskcache",
#   "httpx",
#   "pyyaml",
#   "rich",
#   "typer",
# ]
# ///
"""Check spack package versions against latest available.

Checks the base `spack.yaml` and, by default, every `flavors/*.specs`
overlay (see `flavors/README.md`) — those pin their own package versions
(e.g. cuda, cudnn, tensorrt, rocthrust) that never appear in `spack.yaml`.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import diskcache
import httpx
import typer
import yaml
from rich import box
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Check spack package versions for updates.")
console = Console()

BRANCH_SPECS = {"main", "master", "develop", "HEAD"}
PACKAGES_URL = "https://packages.spack.io/data/packages"
SPEC_NAME_VERSION_RE = re.compile(r"^([a-zA-Z0-9_-]+)(?:\s*@\s*([^\s+~^%]+))?")

cache = diskcache.Cache(Path.home() / ".cache" / "spack-check-versions")


NO_AUTO_UPDATE_MARKER = "no-auto-update"


@dataclass
class Source:
    """One file that pins package versions: spack.yaml or a flavor overlay."""

    path: Path
    packages: dict[str, str | None]
    kind: str  # "yaml" or "specs"
    no_auto_update: set[str]


def extract_name_version(spec: str) -> tuple[str, str | None] | None:
    """Parse a spack spec string like 'name@1.2.3 +variant' into (name, version)."""
    match = SPEC_NAME_VERSION_RE.match(spec.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def parse_spack_yaml(yaml_path: Path) -> tuple[dict[str, str | None], set[str]]:
    """Return ({package_name: current_version_or_None}, no_auto_update_names)."""
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    packages: dict[str, str | None] = {}
    no_auto_update: set[str] = set()
    for spec in data["spack"]["specs"]:
        parsed = extract_name_version(spec)
        if parsed:
            packages[parsed[0]] = parsed[1]
            if NO_AUTO_UPDATE_MARKER in spec:
                no_auto_update.add(parsed[0])
    return packages, no_auto_update


def parse_flavor_specs(specs_path: Path) -> tuple[dict[str, str | None], set[str]]:
    """Return ({package_name: current_version_or_None}, no_auto_update_names)
    from a flavors/*.specs file.

    Mirrors spack_build.sh's own parsing of these files: everything from the
    first `#` onward is stripped, then the line is trimmed and skipped if
    empty. Lines with no `@version` (e.g. `vecmem +cuda`, which only flips a
    variant on the root spec already versioned in spack.yaml) are still
    recorded, with version None, so they show up as "unversioned" rather than
    being silently dropped.

    A spec whose line carries a trailing `# ... no-auto-update ...` comment
    (e.g. `rocthrust@7.2.3  # no-auto-update: exact hip@ pin`) is still
    checked and reported, but never rewritten by `--update` — for packages
    whose reachable version is capped by something check_versions.py can't
    see, like the hand-maintained rpm table in
    spack_repo/acts/packages/hip/package.py (see flavors/README.md).
    """
    packages: dict[str, str | None] = {}
    no_auto_update: set[str] = set()
    for raw_line in specs_path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parsed = extract_name_version(line)
        if parsed:
            packages[parsed[0]] = parsed[1]
            if NO_AUTO_UPDATE_MARKER in raw_line:
                no_auto_update.add(parsed[0])
    return packages, no_auto_update


def discover_sources(
    spack_yaml: Path, flavors_dir: Path, include_flavors: bool
) -> list[Source]:
    yaml_packages, yaml_no_update = parse_spack_yaml(spack_yaml)
    sources = [Source(spack_yaml, yaml_packages, "yaml", yaml_no_update)]
    if include_flavors and flavors_dir.is_dir():
        for specs_path in sorted(flavors_dir.glob("*.specs")):
            packages, no_update = parse_flavor_specs(specs_path)
            sources.append(Source(specs_path, packages, "specs", no_update))
    return sources


async def get_latest_safe_version(
    package: str, client: httpx.AsyncClient, sem: asyncio.Semaphore
) -> str | None:
    """Query packages.spack.io and return the latest numeric safe version."""
    if package in cache:
        data = cache[package]
    else:
        url = f"{PACKAGES_URL}/{package}.json"
        try:
            async with sem:
                resp = await client.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, httpx.TimeoutException):
            return None
        cache.set(package, data, expire=60 * 60)

    for v in data.get("versions", []):
        name = v if isinstance(v, str) else v.get("name", "")
        if re.match(r"^\d", name):
            return normalize_version(name)  # site lists newest first
    return None


def version_satisfied(constraint: str, latest: str) -> bool:
    """Return True if `latest` satisfies the spack version constraint.

    Spack treats a short version like '4.2' as a prefix: it matches '4.2',
    '4.2.0', '4.2.1', etc.  An exact multi-component pin like '4.2.0' only
    matches '4.2.0' itself (or the identical string).
    """
    if constraint == latest:
        return True
    # prefix match: constraint is a strict prefix of latest when followed by '.'
    return latest.startswith(constraint + ".")


def normalize_version(v: str) -> str:
    """Normalize a version string to dot-separated, stripping leading zeros.

    Converts dash-separated versions (e.g. '05-01-00') to dot-separated
    ('5.1.0') so they can be compared with standard spack constraints.
    Pure dot-separated versions are returned as-is.
    """
    if re.match(r"^\d+(-\d+)*$", v):
        return ".".join(str(int(part)) for part in v.split("-"))
    return v


def status_style(
    constraint: str | None, latest: str | None, pinned: bool = False
) -> tuple[str, str]:
    """Return (status_text, rich_style)."""
    if constraint is None:
        return "unversioned", "dim"
    if constraint in BRANCH_SPECS:
        return f"branch ({constraint})", "cyan"
    if latest is None:
        return "unknown", "dim"
    if version_satisfied(constraint, latest):
        if constraint == latest:
            return "up-to-date", "green"
        return f"up-to-date  (resolves to {latest})", "green"
    if pinned:
        return f"outdated  →  {latest}  (pinned, no auto-update)", "yellow"
    return f"outdated  →  {latest}", "yellow"


def update_spack_yaml(yaml_path: Path, updates: dict[str, str]) -> int:
    """Rewrite yaml_path replacing versions for packages in updates.

    Returns the count of lines changed.
    """
    lines = yaml_path.read_text().splitlines(keepends=True)
    changed = 0
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            spec = stripped[2:].strip()
            match = re.match(r"^([a-zA-Z0-9_-]+)\s*@\s*([^\s+~^%]+)", spec)
            if match:
                name, old_ver = match.group(1), match.group(2)
                if name in updates:
                    new_line = re.sub(
                        r"(" + re.escape(name) + r"\s*@\s*)" + re.escape(old_ver),
                        r"\g<1>" + updates[name],
                        line,
                        count=1,
                    )
                    if new_line != line:
                        changed += 1
                    line = new_line
        new_lines.append(line)
    yaml_path.write_text("".join(new_lines))
    return changed


def update_flavor_specs(specs_path: Path, updates: dict[str, str]) -> int:
    """Rewrite specs_path replacing versions for packages in updates.

    Only touches lines that already carry an `@version` for a package in
    `updates` — lines like `vecmem +cuda` (no version) are never touched,
    since flavors/README.md documents that those versions live in
    spack.yaml only. Preserves comments and formatting.

    Returns the count of lines changed.
    """
    lines = specs_path.read_text().splitlines(keepends=True)
    changed = 0
    new_lines = []
    for line in lines:
        code = line.split("#", 1)[0].strip()
        parsed = extract_name_version(code) if code else None
        if parsed and parsed[1] is not None:
            name, old_ver = parsed
            if name in updates:
                new_line = re.sub(
                    r"(" + re.escape(name) + r"\s*@\s*)" + re.escape(old_ver),
                    r"\g<1>" + updates[name],
                    line,
                    count=1,
                )
                if new_line != line:
                    changed += 1
                line = new_line
        new_lines.append(line)
    specs_path.write_text("".join(new_lines))
    return changed


def build_table(source: Source, latest_by_name: dict[str, str | None]) -> tuple[Table, list[dict]]:
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        title=str(source.path),
        title_style="bold",
    )
    table.add_column("Package", style="bold")
    table.add_column("Current", justify="right")
    table.add_column("Latest safe", justify="right")
    table.add_column("Status")

    rows = []
    for name in sorted(source.packages):
        current = source.packages[name]
        latest = latest_by_name.get(name)
        pinned = name in source.no_auto_update
        status, style = status_style(current, latest, pinned)
        rows.append(
            {
                "name": name,
                "current": current,
                "latest": latest,
                "status": status,
                "style": style,
                "pinned": pinned,
            }
        )
        table.add_row(name, current or "—", latest or "—", f"[{style}]{status}[/{style}]")
    return table, rows


@app.command()
def main(
    spack_yaml: Annotated[
        Path,
        typer.Option("--spack-yaml", "-f", help="Path to spack.yaml", exists=True),
    ] = Path("spack.yaml"),
    flavors_dir: Annotated[
        Path,
        typer.Option(
            "--flavors-dir", help="Directory of flavors/*.specs overlays to also check."
        ),
    ] = Path("flavors"),
    include_flavors: Annotated[
        bool,
        typer.Option(
            "--flavors/--no-flavors",
            help="Also check flavors/*.specs overlay files (see flavors/README.md).",
        ),
    ] = True,
    jobs: Annotated[
        int,
        typer.Option("--jobs", "-j", help="Max concurrent requests.", min=1, max=32),
    ] = 8,
    update: Annotated[
        bool,
        typer.Option(
            "--update", "-u", help="Write latest versions back to their source files."
        ),
    ] = False,
) -> None:
    sources = discover_sources(spack_yaml, flavors_dir, include_flavors)
    if include_flavors and len(sources) == 1:
        console.print(
            f"[dim]No flavor overlays found under {flavors_dir}, checking {spack_yaml} only.[/dim]"
        )

    all_names = sorted({name for source in sources for name in source.packages})
    console.print(
        f"\nChecking [bold]{len(all_names)}[/bold] unique packages across "
        f"[bold]{len(sources)}[/bold] file(s) via [dim]{PACKAGES_URL}[/dim] with up to "
        f"[bold]{jobs}[/bold] concurrent requests...\n"
    )

    async def run() -> dict[str, str | None]:
        sem = asyncio.Semaphore(jobs)
        async with httpx.AsyncClient() as client:
            tasks = {
                asyncio.ensure_future(get_latest_safe_version(name, client, sem)): name
                for name in all_names
            }
            results: dict[str, str | None] = {}
            completed = 0
            pending = set(tasks.keys())
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for fut in done:
                    name = tasks[fut]
                    results[name] = fut.result()
                    completed += 1
                    console.print(f"  [{completed}/{len(tasks)}] {name}", end="\r")
        return results

    latest_by_name = asyncio.run(run())
    console.print(" " * 60, end="\r")  # clear progress line

    total_outdated = 0
    for source in sources:
        table, rows = build_table(source, latest_by_name)
        console.print(table)

        outdated_rows = [r for r in rows if r["style"] == "yellow"]
        total_outdated += len(outdated_rows)
        if outdated_rows:
            console.print(
                f"[yellow]{len(outdated_rows)} package(s) in {source.path} "
                f"have newer versions available.[/yellow]"
            )
        else:
            console.print(f"[green]{source.path} is up-to-date.[/green]")

        updatable_rows = [r for r in outdated_rows if not r["pinned"]]
        pinned_rows = [r for r in outdated_rows if r["pinned"]]
        if pinned_rows:
            console.print(
                f"[dim]{len(pinned_rows)} of those are marked no-auto-update and were "
                f"left alone: {', '.join(r['name'] for r in pinned_rows)}[/dim]"
            )

        if update and updatable_rows:
            updates = {r["name"]: r["latest"] for r in updatable_rows}
            if source.kind == "yaml":
                n = update_spack_yaml(source.path, updates)
            else:
                n = update_flavor_specs(source.path, updates)
            console.print(
                f"[green]Updated {n} version(s) in [cyan]{source.path}[/cyan].[/green]"
            )
            for r in updatable_rows:
                console.print(f"  [bold]{r['name']}[/bold]: {r['current']} → {r['latest']}")
        console.print()

    if total_outdated == 0:
        console.print("[green]All versioned packages across all files are up-to-date.[/green]")
    else:
        console.print(
            f"[yellow]{total_outdated} package(s) total have newer versions available.[/yellow]"
        )


if __name__ == "__main__":
    app()
