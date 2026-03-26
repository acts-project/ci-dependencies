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
"""Check spack package versions against latest available."""

import asyncio
import re
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

cache = diskcache.Cache(Path.home() / ".cache" / "spack-check-versions")


def parse_spack_yaml(yaml_path: Path) -> dict[str, str | None]:
    """Return {package_name: current_version_or_None} from spack.yaml specs."""
    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    packages: dict[str, str | None] = {}
    for spec in data["spack"]["specs"]:
        spec = spec.strip()
        match = re.match(r"^([a-zA-Z0-9_-]+)(?:\s*@\s*([^\s+~^%]+))?", spec)
        if match:
            packages[match.group(1)] = match.group(2)
    return packages


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


def status_style(constraint: str | None, latest: str | None) -> tuple[str, str]:
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
    return f"outdated  →  {latest}", "yellow"


async def check_package(
    name: str, current: str | None, client: httpx.AsyncClient, sem: asyncio.Semaphore
) -> dict:
    latest = await get_latest_safe_version(name, client, sem)
    status, style = status_style(current, latest)
    return {
        "name": name,
        "current": current or "—",
        "latest": latest or "—",
        "status": status,
        "style": style,
    }


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


@app.command()
def main(
    spack_yaml: Annotated[
        Path,
        typer.Option("--spack-yaml", "-f", help="Path to spack.yaml", exists=True),
    ] = Path("spack.yaml"),
    jobs: Annotated[
        int,
        typer.Option("--jobs", "-j", help="Max concurrent requests.", min=1, max=32),
    ] = 8,
    update: Annotated[
        bool,
        typer.Option(
            "--update", "-u", help="Write latest versions back to spack.yaml."
        ),
    ] = False,
) -> None:
    packages = parse_spack_yaml(spack_yaml)
    console.print(
        f"\nChecking [bold]{len(packages)}[/bold] packages from [cyan]{spack_yaml}[/cyan]"
        f" via [dim]{PACKAGES_URL}[/dim] with up to [bold]{jobs}[/bold] concurrent requests...\n"
    )

    async def run() -> list[dict]:
        sem = asyncio.Semaphore(jobs)
        async with httpx.AsyncClient() as client:
            tasks = [
                check_package(name, ver, client, sem)
                for name, ver in packages.items()
            ]
            pending = {asyncio.ensure_future(t): i for i, t in enumerate(tasks)}
            results = [{}] * len(tasks)
            completed = 0
            while pending:
                done, _ = await asyncio.wait(
                    pending.keys(), return_when=asyncio.FIRST_COMPLETED
                )
                for fut in done:
                    idx = pending.pop(fut)
                    results[idx] = fut.result()
                    completed += 1
                    console.print(
                        f"  [{completed}/{len(tasks)}] {results[idx]['name']}", end="\r"
                    )
        return results

    results = asyncio.run(run())
    console.print(" " * 60, end="\r")  # clear progress line

    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        title="Spack Package Version Report",
        title_style="bold",
    )
    table.add_column("Package", style="bold")
    table.add_column("In spack.yaml", justify="right")
    table.add_column("Latest safe", justify="right")
    table.add_column("Status")

    for row in results:
        table.add_row(
            row["name"],
            row["current"],
            row["latest"],
            f"[{row['style']}]{row['status']}[/{row['style']}]",
        )

    console.print(table)

    outdated = [r for r in results if r["style"] == "yellow"]
    if outdated:
        console.print(
            f"\n[yellow]{len(outdated)} package(s) have newer versions available.[/yellow]"
        )
    else:
        console.print("\n[green]All versioned packages are up-to-date.[/green]")

    if update and outdated:
        updates = {r["name"]: r["latest"] for r in outdated}
        n = update_spack_yaml(spack_yaml, updates)
        console.print(
            f"\n[green]Updated {n} version(s) in [cyan]{spack_yaml}[/cyan].[/green]"
        )
        for r in outdated:
            console.print(f"  [bold]{r['name']}[/bold]: {r['current']} → {r['latest']}")


if __name__ == "__main__":
    app()
