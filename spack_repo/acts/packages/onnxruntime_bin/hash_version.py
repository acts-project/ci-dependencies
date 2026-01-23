# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "typer>=0.15",
#     "rich>=13",
#     "httpx>=0.28",
# ]
# ///
"""
Calculate SHA256 hashes for onnxruntime binary releases.

Usage:
    uv run hash_version.py 1.21.0
"""

import asyncio
import hashlib
import tempfile
from concurrent.futures import ProcessPoolExecutor
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
    DownloadColumn,
    TaskID,
)
from rich.table import Table

app = typer.Typer(help="Calculate SHA256 hashes for onnxruntime releases")
console = Console()


def get_urls(version: str) -> dict[str, str]:
    """Generate download URLs for all platforms."""
    base = f"https://github.com/microsoft/onnxruntime/releases/download/v{version}"
    return {
        "source": f"https://github.com/microsoft/onnxruntime/archive/refs/tags/v{version}.tar.gz",
        "linux_x86_64": f"{base}/onnxruntime-linux-x64-{version}.tgz",
        "linux_x86_64_gpu": f"{base}/onnxruntime-linux-x64-gpu-{version}.tgz",
        "linux_aarch64": f"{base}/onnxruntime-linux-aarch64-{version}.tgz",
        "darwin_aarch64": f"{base}/onnxruntime-osx-arm64-{version}.tgz",
        "darwin_x86_64": f"{base}/onnxruntime-osx-x86_64-{version}.tgz",
    }


def compute_sha256(file_path: str) -> str:
    """Compute SHA256 hash from file. Runs in subprocess to avoid GIL."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


async def download_and_hash(
    name: str,
    url: str,
    client: httpx.AsyncClient,
    executor: ProcessPoolExecutor,
    progress: Progress,
    task_id: TaskID,
    temp_dir: Path,
) -> tuple[str, str | None]:
    """Download a file to temp storage and compute hash in process pool."""
    temp_file = temp_dir / f"{name}.tmp"
    try:
        async with client.stream("GET", url, follow_redirects=True) as response:
            if response.status_code == 404:
                progress.update(task_id, description=f"[red]{name} (not found)[/red]")
                return name, None
            response.raise_for_status()

            content_length = response.headers.get("content-length")
            total = int(content_length) if content_length else None
            progress.update(
                task_id,
                description=f"[cyan]{name}[/cyan] [dim]downloading[/dim]",
                total=total,
                completed=0,
            )

            # Stream to temporary file
            with open(temp_file, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    progress.update(task_id, advance=len(chunk))

            # Compute hash in process pool
            progress.update(
                task_id,
                description=f"[yellow]{name}[/yellow] [dim]hashing[/dim]",
            )
            loop = asyncio.get_running_loop()
            hash_value = await loop.run_in_executor(
                executor, compute_sha256, str(temp_file)
            )

            progress.update(task_id, description=f"[green]{name}[/green] [dim]done[/dim]")
            return name, hash_value

    except httpx.HTTPStatusError:
        progress.update(task_id, description=f"[red]{name} (error)[/red]")
        return name, None
    finally:
        # Clean up temp file
        if temp_file.exists():
            temp_file.unlink()


async def download_all(version: str, progress: Progress) -> dict[str, str | None]:
    """Download all files concurrently and compute hashes."""
    urls = get_urls(version)
    hashes: dict[str, str | None] = {}

    # Create tasks upfront
    tasks: dict[str, TaskID] = {}
    for name in urls:
        tasks[name] = progress.add_task(f"[cyan]{name}[/cyan]", total=None)

    with tempfile.TemporaryDirectory(prefix="onnxruntime_hash_") as temp_dir:
        temp_path = Path(temp_dir)
        async with httpx.AsyncClient(timeout=300) as client:
            with ProcessPoolExecutor() as executor:
                coros = [
                    download_and_hash(
                        name, url, client, executor, progress, tasks[name], temp_path
                    )
                    for name, url in urls.items()
                ]
                results = await asyncio.gather(*coros)

    for name, hash_value in results:
        hashes[name] = hash_value

    return hashes


@app.command()
def main(
    version: Annotated[str, typer.Argument(help="Version to download (e.g., 1.21.0)")],
    skip_missing: Annotated[
        bool, typer.Option("--skip-missing", "-s", help="Use '0' for missing platforms")
    ] = True,
) -> None:
    """Download onnxruntime releases and calculate SHA256 hashes."""
    console.print(f"\n[bold]Calculating hashes for onnxruntime v{version}[/bold]\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description:<30}"),
        BarColumn(),
        DownloadColumn(),
        console=console,
        refresh_per_second=10,
    ) as progress:
        hashes = asyncio.run(download_all(version, progress))

    # Display results table
    table = Table(title=f"SHA256 Hashes for v{version}")
    table.add_column("Platform", style="cyan")
    table.add_column("SHA256", style="green")
    table.add_column("Status")

    for name, hash_value in hashes.items():
        if hash_value:
            table.add_row(name, hash_value, "[green]OK[/green]")
        else:
            table.add_row(name, "N/A", "[red]Not found[/red]")

    console.print(table)

    # Generate code fragment
    console.print("\n[bold]Code fragment for package.py:[/bold]\n")

    def get_hash(key: str) -> str:
        h = hashes.get(key)
        if h:
            return h
        return "0" if skip_missing else "None  # NOT AVAILABLE"

    fragment = f'''_add_version(
    "{version}",
    sha256="{get_hash("source")}",
    sha256_darwin_aarch64="{get_hash("darwin_aarch64")}",
    sha256_darwin_x86_64="{get_hash("darwin_x86_64")}",
    sha256_linux_aarch64="{get_hash("linux_aarch64")}",
    sha256_linux_x86_64="{get_hash("linux_x86_64")}",
    sha256_linux_x86_64_gpu="{get_hash("linux_x86_64_gpu")}",
)'''

    console.print(fragment)
    console.print()


if __name__ == "__main__":
    app()
