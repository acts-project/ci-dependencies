#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "typer",
#   "rich",
# ]
# ///
"""Ensure the Geant4 data sets a given Geant4 requests are available in GHCR.

The set of data sets is read from a ``geant4-config`` script (its ``dataset_list``
is baked from Geant4's upstream ``G4DatasetDefinitions.cmake`` and is therefore
canonical for a given Geant4 version). For each requested data set we:

  * compute its OCI reference ``<repo-prefix>/<name-lower>:<version>``
    (e.g. ``.../geant4-data/g4ndl:4.7.1``),
  * skip it if that tag already exists in the registry (immutable version key),
  * otherwise pack the versioned directory from the source tree (e.g. CVMFS
    ``/cvmfs/geant4.cern.ch/share/data/G4NDL4.7.1``) into a single deterministic
    tarball and push it as an ORAS artifact.

GHCR stores blobs content-addressed by digest, so a data set version is uploaded
exactly once even if several Geant4 releases share it, and re-runs upload nothing
for versions already present. ORAS verifies digests on pull, so the consumer side
needs no MD5 verification.

Only the exact data sets ``geant4-config`` lists are published -- nothing else in
the source tree is mirrored.

Requires ``oras`` on PATH, already authenticated against the target registry.
"""

import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer()
console = Console()

DEFAULT_DATA_DIR = Path("/cvmfs/geant4.cern.ch/share/data")
DEFAULT_CVMFS_ROOT = Path("/cvmfs/geant4.cern.ch")
DEFAULT_REPO_PREFIX = "ghcr.io/acts-project/geant4-data"

ARTIFACT_TYPE = "application/vnd.acts.geant4-data.dataset.v1"
LAYER_MEDIA_TYPE = "application/vnd.acts.geant4-dataset.tar+gzip"

# ``G4NDL4.7.1`` -> name=``G4NDL`` version=``4.7.1``. The version is the trailing
# run of dot-separated integers; the name is everything before it (lazy match).
_NAME_VERSION_RE = re.compile(r"^(?P<name>.+?)(?P<version>\d+(?:\.\d+)*)$")


def split_name_version(dirname: str) -> tuple[str, str] | None:
    """Split a versioned dataset directory name into (name, version)."""
    match = _NAME_VERSION_RE.match(dirname)
    if not match:
        return None
    return match.group("name"), match.group("version")


def dataset_ref(repo_prefix: str, name: str, version: str) -> str:
    """Build the OCI reference for a dataset (name is lowercased for the repo)."""
    return f"{repo_prefix}/{name.lower()}:{version}"


def parse_dataset_dirs(config_path: Path) -> list[str]:
    """Return the versioned dataset directory names a geant4-config requests.

    Parses the ``dataset_list`` line of a ``geant4-config`` script, whose entries
    are ``NAME|ENVVAR|PATH|FILENAME|MD5`` separated by ``;``. The versioned
    directory name is the basename of ``PATH`` (e.g. ``.../data/G4NDL4.7.1``).
    """
    content = config_path.read_text()

    for line in content.splitlines():
        if line.strip().startswith("dataset_list="):
            break
    else:
        console.print("[red]Error: could not find dataset_list in geant4-config[/red]")
        raise typer.Exit(1)

    start = line.find('"') + 1
    end = line.find('", array,')
    if end == -1:
        end = line.rfind('"')
    dataset_string = line[start:end]

    dirs: list[str] = []
    for entry in dataset_string.split(";"):
        if not entry.strip():
            continue
        parts = entry.split("|")
        if len(parts) >= 5:
            dirs.append(Path(parts[2]).name)
    return dirs


def find_geant4_config(cvmfs_root: Path, version: str) -> Path:
    """Locate a geant4-config for ``version`` under a CVMFS-style tree."""
    patterns = [
        f"geant4/{version}/*/bin/geant4-config",
        f"geant4/{version}/bin/geant4-config",
        f"geant4/{version}/*/*/bin/geant4-config",
    ]
    for pattern in patterns:
        matches = sorted(cvmfs_root.glob(pattern))
        if matches:
            return matches[0]

    console.print(
        f"[red]Error: no geant4-config found for version {version} "
        f"under {cvmfs_root}[/red]"
    )
    available = sorted(p.name for p in (cvmfs_root / "geant4").glob("*")) if (
        cvmfs_root / "geant4"
    ).is_dir() else []
    if available:
        console.print(f"[yellow]Available geant4 versions: {available}[/yellow]")
    raise typer.Exit(1)


@lru_cache(maxsize=1)
def _tar_is_gnu() -> bool:
    """Return True if the ``tar`` on PATH is GNU tar (needed for reproducibility)."""
    try:
        out = subprocess.run(
            ["tar", "--version"], capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return "GNU tar" in out.stdout


def make_tarball(parent: Path, dirname: str, out_path: Path) -> None:
    """Pack ``parent/dirname`` into ``out_path`` as a .tar.gz.

    On GNU tar the archive is reproducible (sorted entries, zeroed mtime/owner,
    gzip without header timestamp) so re-packing an unchanged dataset yields the
    same digest. Elsewhere (e.g. macOS bsdtar during local testing) we fall back
    to a plain, non-reproducible archive that is still valid.
    """
    if _tar_is_gnu():
        cmd = [
            "tar",
            "--sort=name",
            "--mtime=@0",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "--use-compress-program=gzip -n",
            "-C",
            str(parent),
            "-cf",
            str(out_path),
            dirname,
        ]
    else:
        cmd = ["tar", "-C", str(parent), "-czf", str(out_path), dirname]
    subprocess.run(cmd, check=True)


def tag_exists(ref: str) -> bool:
    """Return True if the manifest for ``ref`` already exists in the registry."""
    result = subprocess.run(
        ["oras", "manifest", "fetch", "--descriptor", ref],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def push_dataset(
    parent: Path,
    dirname: str,
    ref: str,
    source_url: str | None,
) -> None:
    """Pack ``dirname`` from ``parent`` and push it to ``ref`` (raises on failure)."""
    with tempfile.TemporaryDirectory(prefix="g4data-") as tmp:
        # The tarball basename becomes the OCI title annotation, so the consumer
        # can predict the file it will receive: ``<dirname>.tar.gz``.
        tar_name = f"{dirname}.tar.gz"
        tar_path = Path(tmp) / tar_name
        make_tarball(parent, dirname, tar_path)

        cmd = [
            "oras",
            "push",
            "--artifact-type",
            ARTIFACT_TYPE,
            "--annotation",
            f"org.opencontainers.image.title={dirname}",
        ]
        if source_url:
            cmd += ["--annotation", f"org.opencontainers.image.source={source_url}"]
        cmd += [ref, f"{tar_name}:{LAYER_MEDIA_TYPE}"]
        # Run in the temp dir so oras records the bare filename as the layer title.
        subprocess.run(cmd, check=True, cwd=tmp)


@app.command()
def main(
    config: Annotated[
        Path | None,
        typer.Option(help="Path to a geant4-config script (overrides --geant4-version)"),
    ] = None,
    geant4_version: Annotated[
        str | None,
        typer.Option(help="Geant4 version whose geant4-config to locate on CVMFS"),
    ] = None,
    cvmfs_root: Annotated[
        Path,
        typer.Option(help="CVMFS root used to locate geant4-config"),
    ] = DEFAULT_CVMFS_ROOT,
    data_dir: Annotated[
        Path,
        typer.Option(help="Source directory containing versioned dataset dirs"),
    ] = DEFAULT_DATA_DIR,
    repo_prefix: Annotated[
        str,
        typer.Option(help="Target OCI repository prefix"),
    ] = DEFAULT_REPO_PREFIX,
    source_url: Annotated[
        str | None,
        typer.Option(help="Value for the image.source annotation on each artifact"),
    ] = "https://github.com/acts-project/ci-dependencies",
    jobs: Annotated[
        int, typer.Option("--jobs", "-j", help="Parallel pack/push workers")
    ] = 4,
    force: Annotated[
        bool, typer.Option("--force", help="Re-push even if the tag already exists")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be pushed without pushing"),
    ] = False,
) -> None:
    """Ensure the Geant4 data sets requested by a geant4-config are in GHCR."""
    if shutil.which("oras") is None:
        console.print("[red]Error: oras not found in PATH[/red]")
        raise typer.Exit(1)

    if config is not None:
        config_path = config
        if not config_path.exists():
            console.print(f"[red]Error: geant4-config not found: {config_path}[/red]")
            raise typer.Exit(1)
    elif geant4_version is not None:
        config_path = find_geant4_config(cvmfs_root, geant4_version)
    else:
        console.print("[red]Error: pass --config or --geant4-version[/red]")
        raise typer.Exit(1)
    console.print(f"[cyan]Using geant4-config: {config_path}[/cyan]")

    if not data_dir.is_dir():
        console.print(f"[red]Error: data dir not found: {data_dir}[/red]")
        raise typer.Exit(1)

    dataset_dirs = parse_dataset_dirs(config_path)
    console.print(f"[cyan]geant4-config requests {len(dataset_dirs)} dataset(s)[/cyan]")

    to_push: list[tuple[str, str]] = []  # (dirname, ref)
    missing_source: list[str] = []
    for dirname in dataset_dirs:
        parsed = split_name_version(dirname)
        if parsed is None:
            console.print(f"[red]✗ cannot parse name/version from {dirname}[/red]")
            missing_source.append(dirname)
            continue
        name, version = parsed
        ref = dataset_ref(repo_prefix, name, version)

        if not force and tag_exists(ref):
            console.print(f"[green]✓ present[/green] {ref}")
            continue

        if not (data_dir / dirname).is_dir():
            console.print(
                f"[red]✗ missing source[/red] {dirname} (not in {data_dir})"
            )
            missing_source.append(dirname)
            continue

        to_push.append((dirname, ref))

    if to_push and not dry_run:
        console.print(f"[cyan]Pushing {len(to_push)} dataset(s)...[/cyan]")
    elif dry_run:
        console.print(f"[yellow]DRY RUN: would push {len(to_push)} dataset(s):[/yellow]")
        for dirname, ref in to_push:
            console.print(f"  [cyan]•[/cyan] {dirname} -> {ref}")

    failed: list[str] = []
    if to_push and not dry_run:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            future_to_ref = {
                executor.submit(push_dataset, data_dir, dirname, ref, source_url): ref
                for dirname, ref in to_push
            }
            for future in as_completed(future_to_ref):
                ref = future_to_ref[future]
                try:
                    future.result()
                    console.print(f"[green]✓ pushed[/green] {ref}")
                except subprocess.CalledProcessError as exc:
                    console.print(f"[red]✗ failed[/red] {ref}: exit {exc.returncode}")
                    failed.append(ref)
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]✗ failed[/red] {ref}: {exc}")
                    failed.append(ref)

    console.print()
    if missing_source:
        console.print(
            f"[red]{len(missing_source)} requested dataset(s) not found in "
            f"{data_dir}:[/red] {missing_source}"
        )
    if failed:
        console.print(f"[red]{len(failed)} push(es) failed[/red]")
    if missing_source or failed:
        raise typer.Exit(1)

    if dry_run:
        return
    console.print(
        f"[green]✓ All {len(dataset_dirs)} requested dataset(s) available "
        f"({len(to_push)} newly pushed)[/green]"
    )


if __name__ == "__main__":
    app()
