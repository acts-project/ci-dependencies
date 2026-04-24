#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "typer",
#   "httpx",
#   "rich",
# ]
# ///

import asyncio
import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

console = Console()
app = typer.Typer()


def find_geant4_config() -> Path:
    """Find geant4-config in PATH."""
    result = shutil.which("geant4-config")
    if not result:
        console.print("[red]Error: geant4-config not found in PATH[/red]")
        raise typer.Exit(1)
    return Path(result)


def parse_datasets(config_path: Path) -> list[dict]:
    """Parse dataset information from geant4-config script."""
    with open(config_path) as f:
        content = f.read()

    # Find the dataset_list line
    for line in content.splitlines():
        if line.strip().startswith("dataset_list="):
            break
    else:
        console.print("[red]Error: Could not find dataset_list in geant4-config[/red]")
        raise typer.Exit(1)

    # Extract the awk script dataset string
    # Format: NAME|ENVVAR|PATH|FILENAME|MD5;...
    # The string is within quotes before the ", array," part
    start = line.find('"') + 1
    # Find the end quote before ", array,"
    end = line.find('", array,')
    if end == -1:
        end = line.rfind('"')
    dataset_string = line[start:end]

    datasets = []
    for entry in dataset_string.split(";"):
        if not entry.strip():
            continue
        parts = entry.split("|")
        if len(parts) >= 5:
            datasets.append(
                {
                    "name": parts[0],
                    "envvar": parts[1],
                    "path": parts[2],
                    "filename": parts[3],
                    "md5": parts[4],
                }
            )

    return datasets


def get_dataset_url(config_path: Path) -> str:
    """Get the base URL for datasets from geant4-config."""
    with open(config_path) as f:
        content = f.read()

    # Find the dataset_url line
    for line in content.splitlines():
        if line.strip().startswith("dataset_url="):
            # Extract URL from line like: dataset_url="https://cern.ch/geant4-data/datasets"
            start = line.find('"') + 1
            end = line.rfind('"')
            return line[start:end]

    # Fallback to default URL
    return "https://cern.ch/geant4-data/datasets"


def verify_md5(filepath: Path, expected_md5: str) -> bool:
    """Verify MD5 checksum of a file."""
    md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest() == expected_md5


def hardlink_tree(src: Path, dst: Path) -> None:
    """Recursively hard-link all files from src into dst.

    Falls back to copying if hard-linking fails (e.g. cross-filesystem).
    """
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        target = dst / entry.name
        if entry.is_dir():
            hardlink_tree(entry, target)
        else:
            try:
                os.link(entry, target)
            except OSError:
                shutil.copy2(entry, target)


def extract_to_cache(
    tarball_path: Path, cache_dir: Path, dataset_dir_name: str, md5: str
) -> tuple[bool, str]:
    """Extract tarball into the cache directory (runs in process pool)."""
    try:
        cache_dataset_dir = cache_dir / dataset_dir_name
        md5_marker = cache_dir / f"{dataset_dir_name}.md5"

        # Check if cache already has this exact version
        if cache_dataset_dir.exists() and md5_marker.exists():
            if md5_marker.read_text().strip() == md5:
                tarball_path.unlink(missing_ok=True)
                return True, f"Cache hit for {dataset_dir_name}"

        # Extract into a temporary directory next to cache, then atomically rename
        tmp_extract = cache_dir / f"{dataset_dir_name}.extracting"
        if tmp_extract.exists():
            shutil.rmtree(tmp_extract)
        tmp_extract.mkdir(parents=True, exist_ok=True)

        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(tmp_extract, filter="data")

        src_dir = tmp_extract / dataset_dir_name

        if cache_dataset_dir.exists():
            shutil.rmtree(cache_dataset_dir)
        shutil.move(str(src_dir), str(cache_dataset_dir))
        shutil.rmtree(tmp_extract, ignore_errors=True)

        # Write MD5 marker so we know this cache entry is valid
        md5_marker.write_text(md5)

        tarball_path.unlink(missing_ok=True)

        return True, f"Extracted {dataset_dir_name} to cache"
    except Exception as e:
        return False, f"Failed to extract to cache: {e}"


def install_from_cache(
    cache_dir: Path, dataset_dir_name: str, dest_dir: Path
) -> tuple[bool, str]:
    """Hard-link (or copy) a cached dataset into the final location (runs in process pool)."""
    try:
        cache_dataset_dir = cache_dir / dataset_dir_name

        dest_dir.parent.mkdir(parents=True, exist_ok=True)

        if dest_dir.exists():
            shutil.rmtree(dest_dir)

        hardlink_tree(cache_dataset_dir, dest_dir)

        return True, f"Successfully installed {dataset_dir_name}"
    except Exception as e:
        return False, f"Failed to install from cache: {e}"


async def download_dataset(
    client: httpx.AsyncClient,
    dataset: dict,
    base_url: str,
    cache_dir: Path,
    progress: Progress,
    executor: concurrent.futures.ProcessPoolExecutor,
) -> tuple[bool, str]:
    """Download, cache, and hard-link a single dataset."""
    filename = dataset["filename"]
    url = f"{base_url}/{filename}"
    dest_dir = Path(dataset["path"])
    dataset_dir_name = dest_dir.name
    md5_marker = cache_dir / f"{dataset_dir_name}.md5"

    task_id = progress.add_task(f"[cyan]{filename}", total=None)
    loop = asyncio.get_event_loop()

    try:
        # Check if already cached with correct md5
        cached = (
            (cache_dir / dataset_dir_name).exists()
            and md5_marker.exists()
            and md5_marker.read_text().strip() == dataset["md5"]
        )

        if not cached:
            # Download tarball to cache directory
            tarball_path = cache_dir / filename
            async with client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                progress.update(task_id, total=total)

                with open(tarball_path, "wb") as f:
                    downloaded = 0
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress.update(task_id, completed=downloaded)

            # Verify MD5
            progress.update(task_id, description=f"[yellow]{filename} (verifying)")
            md5_valid = await loop.run_in_executor(
                executor, verify_md5, tarball_path, dataset["md5"]
            )
            if not md5_valid:
                tarball_path.unlink(missing_ok=True)
                progress.update(task_id, description=f"[red]{filename} (MD5 mismatch)")
                return False, f"MD5 mismatch for {filename}"

            # Extract to cache
            progress.update(task_id, description=f"[yellow]{filename} (extracting)")
            success, msg = await loop.run_in_executor(
                executor,
                extract_to_cache,
                tarball_path,
                cache_dir,
                dataset_dir_name,
                dataset["md5"],
            )
            if not success:
                progress.update(task_id, description=f"[red]{filename} (failed)")
                return False, msg
        else:
            progress.update(task_id, total=1, completed=1)

        # Hard-link from cache to destination
        progress.update(task_id, description=f"[yellow]{filename} (linking)")
        success, msg = await loop.run_in_executor(
            executor, install_from_cache, cache_dir, dataset_dir_name, dest_dir
        )

        if success:
            status = "[green]{} (cached + linked)" if cached else "[green]{} (installed)"
            progress.update(task_id, description=status.format(filename))
            return True, f"Successfully installed {dataset['name']}"
        else:
            progress.update(task_id, description=f"[red]{filename} (failed)")
            return False, msg

    except Exception as e:
        progress.update(task_id, description=f"[red]{filename} (failed)")
        return False, f"Failed to download {filename}: {e}"


async def download_all_datasets(
    datasets: list[dict],
    base_url: str,
    cache_dir: Path,
    max_concurrent: int,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    """Download all datasets with limited concurrency."""
    # Filter out already installed datasets
    if force:
        datasets_to_install = datasets
    else:
        datasets_to_install = [ds for ds in datasets if not Path(ds["path"]).exists()]

    if not datasets_to_install:
        console.print("[green]All datasets already installed[/green]")
        return

    if dry_run:
        console.print(
            f"[yellow]DRY RUN: Would download {len(datasets_to_install)} datasets:[/yellow]"
        )
        for ds in datasets_to_install:
            cached = (
                (cache_dir / Path(ds["path"]).name).exists()
                and (cache_dir / f"{Path(ds['path']).name}.md5").exists()
                and (cache_dir / f"{Path(ds['path']).name}.md5")
                .read_text()
                .strip()
                == ds["md5"]
            )
            status = "[green](cached)[/green]" if cached else "[yellow](download)[/yellow]"
            console.print(
                f"  [cyan]•[/cyan] {ds['name']} ({ds['filename']}) {status}"
            )
            console.print(f"    URL: {base_url}/{ds['filename']}")
            console.print(f"    Destination: {ds['path']}")
            console.print(f"    MD5: {ds['md5']}")
        return

    console.print(f"[cyan]Downloading {len(datasets_to_install)} datasets...[/cyan]")
    console.print(f"[cyan]Cache directory: {cache_dir}[/cyan]")

    cache_dir.mkdir(parents=True, exist_ok=True)

    progress = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    # Use process pool for extraction
    with concurrent.futures.ProcessPoolExecutor() as executor:
        async with httpx.AsyncClient(timeout=1800.0) as client:
            with progress:
                # Use semaphore to limit concurrent downloads
                semaphore = asyncio.Semaphore(max_concurrent)

                async def bounded_download(dataset):
                    async with semaphore:
                        return await download_dataset(
                            client, dataset, base_url, cache_dir, progress, executor
                        )

                results = await asyncio.gather(
                    *[bounded_download(ds) for ds in datasets_to_install],
                    return_exceptions=True,
                )

    # Print summary
    console.print()
    successes = sum(1 for r in results if not isinstance(r, Exception) and r[0])
    failures = len(results) - successes

    if failures == 0:
        console.print(f"[green]✓ Successfully installed {successes} datasets[/green]")
    else:
        console.print(
            f"[yellow]⚠ Installed {successes} datasets, {failures} failed[/yellow]"
        )
        for result in results:
            if isinstance(result, Exception) or not result[0]:
                msg = str(result) if isinstance(result, Exception) else result[1]
                console.print(f"[red]  • {msg}[/red]")


@app.command()
def main(
    max_concurrent: Annotated[
        int, typer.Option("--jobs", "-j", help="Maximum concurrent downloads")
    ] = 4,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would be downloaded without actually downloading",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Redownload and reinstall datasets even if they already exist",
        ),
    ] = False,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to geant4-config script")
    ] = None,
    cache_dir: Annotated[
        Path,
        typer.Option(
            "--cache-dir",
            help="Directory to cache downloaded datasets (avoids re-downloading)",
        ),
    ] = Path.home() / ".cache" / "geant4-datasets",
) -> None:
    """Download Geant4 datasets in parallel.

    Datasets are cached in --cache-dir and hard-linked into the target
    locations. This means changing install prefixes does not require
    re-downloading, and identical datasets share storage on disk.
    """
    # Find geant4-config
    if config:
        config_path = config
        if not config_path.exists():
            console.print(f"[red]Error: {config_path} does not exist[/red]")
            raise typer.Exit(1)
    else:
        config_path = find_geant4_config()
    console.print(f"[cyan]Found geant4-config at: {config_path}[/cyan]")

    # Parse datasets
    datasets = parse_datasets(config_path)
    console.print(f"[cyan]Found {len(datasets)} datasets[/cyan]")

    # Get base URL
    base_url = get_dataset_url(config_path)
    console.print(f"[cyan]Base URL: {base_url}[/cyan]")
    console.print()

    # Download datasets using cache
    asyncio.run(
        download_all_datasets(
            datasets, base_url, cache_dir, max_concurrent, dry_run, force
        )
    )


if __name__ == "__main__":
    app()
