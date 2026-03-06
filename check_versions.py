#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pyyaml",
#   "rich",
#   "typer",
# ]
# ///
"""Check spack package versions against latest available."""

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich import box
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Check spack package versions for updates.")
console = Console()

BRANCH_SPECS = {"main", "master", "develop", "HEAD"}


def find_spack() -> str:
    """Return path to the real spack binary (not the shell function wrapper)."""
    spack_root = os.environ.get("SPACK_ROOT")
    if spack_root:
        candidate = Path(spack_root) / "bin" / "spack"
        if candidate.is_file():
            return str(candidate)
    for candidate in [Path.home() / "spack" / "bin" / "spack", Path("/opt/spack/bin/spack")]:
        if candidate.is_file():
            return str(candidate)
    return "spack"  # last resort: hope it's on PATH


SPACK = find_spack()


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


def get_latest_safe_version(package: str) -> str | None:
    """Run `spack info` and return the highest numeric safe version."""
    try:
        result = subprocess.run(
            [SPACK, "info", "--no-variants", "--no-dependencies", package],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] spack binary not found (tried: {SPACK})")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return None

    in_safe = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Safe versions:"):
            in_safe = True
            continue
        if in_safe:
            if not stripped:
                continue
            # Any line not starting a new section is a version entry
            if stripped[0].isalpha() and stripped.endswith(":"):
                break  # next section header
            token = stripped.split()[0]
            if re.match(r"^\d", token):
                return token  # spack lists newest first
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


def check_package(name: str, current: str | None) -> dict:
    latest = get_latest_safe_version(name)
    status, style = status_style(current, latest)
    return {
        "name": name,
        "current": current or "—",
        "latest": latest or "—",
        "status": status,
        "style": style,
    }


@app.command()
def main(
    spack_yaml: Annotated[
        Path,
        typer.Option("--spack-yaml", "-f", help="Path to spack.yaml", exists=True),
    ] = Path("spack.yaml"),
    jobs: Annotated[
        int,
        typer.Option("--jobs", "-j", help="Parallel spack queries.", min=1, max=32),
    ] = 8,
) -> None:
    packages = parse_spack_yaml(spack_yaml)
    console.print(
        f"\nChecking [bold]{len(packages)}[/bold] packages from [cyan]{spack_yaml}[/cyan]"
        f" using [dim]{SPACK}[/dim] with up to [bold]{jobs}[/bold] parallel queries...\n"
    )

    results: list[dict] = [{}] * len(packages)
    items = list(packages.items())

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(check_package, name, ver): idx
            for idx, (name, ver) in enumerate(items)
        }
        completed = 0
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()
            completed += 1
            console.print(f"  [{completed}/{len(packages)}] {results[idx]['name']}", end="\r")

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
        console.print(f"\n[yellow]{len(outdated)} package(s) have newer versions available.[/yellow]")
    else:
        console.print("\n[green]All versioned packages are up-to-date.[/green]")


if __name__ == "__main__":
    app()
