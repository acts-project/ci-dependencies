#!/usr/bin/env python3

# /// script
# dependencies = [
#   "typer",
#   "rich",
# ]
# ///

import typer
from typing import Annotated
import subprocess
import json
import re
from rich.console import Console


def get_release_assets(version: str):
    res = subprocess.run(
        ["gh", "release", "view", f"v{version}", "--json", "assets"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    data = json.loads(res.stdout)
    return data["assets"]


# The per-build tags carry the architecture as part of the triplet; dropping it
# yields the arch-independent tag the per-arch images are merged into.
ARCH_RE = re.compile(r"-(?:x86_64|aarch64)")


def inspect(manifest: str):
    subprocess.run(["docker", "buildx", "imagetools", "inspect", manifest], check=True)


def create_manifest(target: str, inputs: list[str], do_push: bool):
    # `docker manifest create` refuses inputs that are themselves manifest lists,
    # which is what buildx pushes since the builds gained provenance attestations
    # (an OCI index holding the image plus its attestation manifest). `buildx
    # imagetools create` merges those, keeping the attestations intact. It writes
    # straight to the registry, so --dry-run stands in for "don't push".
    cmd = ["docker", "buildx", "imagetools", "create", "--tag", target]
    if not do_push:
        cmd.append("--dry-run")
    cmd += inputs
    subprocess.run(cmd, check=True)


def main(
    version: str,
    pattern: str,
    registry: str = "ghcr.io/acts-project/spack-container",
    do_push: Annotated[bool, typer.Option("--push/--no-push")] = False,
):
    console = Console()

    if version.startswith("v"):
        version = version[1:]

    assets = get_release_assets(version)
    dockerfiles = [a["name"] for a in assets if a["name"].startswith("Dockerfile.")]

    ex = re.compile(pattern)
    # fullmatch, so a pattern ending in `cxx23` does not also pull in the
    # flavored `cxx23_cuda13` / `cxx23_rocm-gfx90a` builds. Those are published
    # under their own per-arch tags only; merging them here would put several
    # x86_64 images into one manifest list.
    matching = [a for a in dockerfiles if ex.fullmatch(a)]
    matching = sorted(m.replace("@", "-").replace("Dockerfile.", "") for m in matching)

    if len(matching) == 0:
        raise ValueError("No manifests matched the pattern given")

    # Everything that is merged must be the same build for different
    # architectures. Anything else is a too-broad pattern, and silently produces
    # a manifest list where one architecture shadows another.
    output_triplets = {ARCH_RE.sub("", m) for m in matching}
    if len(output_triplets) > 1:
        raise ValueError(
            "Pattern matched builds that differ in more than the architecture: "
            + ", ".join(sorted(output_triplets))
        )

    output_manifest = f"{registry}:{version}_{output_triplets.pop()}"
    manifests = [f"{registry}:{version}_{m}" for m in matching]

    console.print(
        f"Will combine the following [bold green]{len(manifests)} manifests [/bold green]",
        highlight=False,
    )
    for manifest in manifests:
        console.print(f" - [b]{manifest}[/b]", highlight=False)

    console.print(f"~> into [b green]{output_manifest}[/b green]", highlight=False)

    create_manifest(output_manifest, manifests, do_push)

    console.print("[bold green]DONE![/bold green]")


if "__main__" == __name__:
    typer.run(main)
