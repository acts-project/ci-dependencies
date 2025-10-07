#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "typer",
#   "rich",
#   "httpx",
# ]
# ///

import os
import tarfile
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)

app = typer.Typer()
console = Console()

LHAPDF_BASE_URL = "http://lhapdfsets.web.cern.ch/lhapdfsets/current"


def download_and_extract(pdf_set: str, output_dir: Path) -> tuple[str, bool, str]:
    """Download and extract a single PDF set."""
    filename = f"{pdf_set}.tar.gz"
    url = f"{LHAPDF_BASE_URL}/{filename}"

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".tar.gz"
        ) as tmp:
            tmp_path = tmp.name

            # Stream download
            with httpx.stream(
                "GET", url, follow_redirects=True, timeout=300.0
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=8192):
                    tmp.write(chunk)

        # Extract tarball
        with tarfile.open(tmp_path, "r:gz") as tar:
            tar.extractall(path=output_dir)

        # Clean up temp file
        os.unlink(tmp_path)

        return pdf_set, True, ""
    except Exception as e:
        # Clean up temp file on error
        if "tmp_path" in locals() and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return pdf_set, False, str(e)


def parse_index_file(index_path: Path) -> list[str]:
    """Parse the index file and extract PDF set names."""
    pdf_sets = []
    with open(index_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                pdf_sets.append(parts[1])
    return pdf_sets


def download_sets(pdf_sets: list[str], output_dir: Path, workers: int):
    """Common function to download and extract PDF sets."""
    failed = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Downloading PDF sets...", total=len(pdf_sets))

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(download_and_extract, pdf_set, output_dir): pdf_set
                for pdf_set in pdf_sets
            }

            for future in as_completed(futures):
                pdf_set, success, error = future.result()
                if success:
                    progress.console.print(f"[green]✓[/green] {pdf_set}")
                else:
                    progress.console.print(f"[red]✗[/red] {pdf_set}: {error}")
                    failed.append((pdf_set, error))
                progress.advance(task)

    # Summary
    console.print()
    if failed:
        console.print(f"[yellow]Completed with {len(failed)} failures:[/yellow]")
        for pdf_set, error in failed:
            console.print(f"  [red]✗[/red] {pdf_set}: {error}")
        raise typer.Exit(1)
    else:
        console.print(
            f"[green]✓ Successfully downloaded and extracted all {len(pdf_sets)} PDF sets[/green]"
        )


@app.command()
def main(
    output_dir: Annotated[
        Path, typer.Option(help="Output directory for extracted PDF sets")
    ] = Path.cwd(),
    index_file: Annotated[
        Path | None,
        typer.Option("--index", "-i", help="Index file containing PDF set names"),
    ] = None,
    pdf_sets: Annotated[
        str | None, typer.Argument(help="PDF set names to download")
    ] = None,
    workers: Annotated[
        int, typer.Option("--job", "-j", help="Number of parallel workers")
    ] = os.cpu_count()
    or 4,
):
    """Download and extract LHAPDF PDF sets in parallel."""

    # Validate mutually exclusive options
    if index_file is None and pdf_sets is None:
        console.print("[red]Error: Must specify either --index or explicit sets[/red]")
        raise typer.Exit(1)

    if index_file is not None and pdf_sets is not None:
        console.print("[red]Error: Cannot specify both --index and explicit sets[/red]")
        raise typer.Exit(1)

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    sets = []

    # Get PDF sets from either source
    if index_file is not None:
        if not index_file.exists():
            console.print(f"[red]Error: Index file not found: {index_file}[/red]")
            raise typer.Exit(1)
        sets = parse_index_file(index_file)
        console.print(f"[green]Found {len(sets)} PDF sets to download[/green]")
    elif pdf_sets is not None:
        sets = [s.strip() for s in pdf_sets.split(",")]
        console.print(f"[green]Downloading {len(sets)} PDF sets[/green]")
    else:
        raise ValueError("Unreachable code")

    download_sets(sets, output_dir, workers)


if __name__ == "__main__":
    app()
