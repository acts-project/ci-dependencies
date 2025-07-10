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


def inspect(manifest: str):
    subprocess.run(["docker", "manifest", "inspect", manifest], check=True)


def create_manifest(target: str, inputs: list[str]):
    cmd = ["docker", "manifest", "create", target]
    for manifest in inputs:
        cmd += ["--amend", manifest]
    subprocess.run(cmd, check=True)


def push(manifest: str):
    subprocess.run(["docker", "manifest", "push", manifest], check=True)


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
    matching = [a for a in dockerfiles if ex.match(a)]
    matching = [m.replace("@", "-").replace("Dockerfile.", "") for m in matching]

    manifests = [f"{registry}:{version}_{m}" for m in matching]

    if len(manifests) == 0:
        raise ValueError("No manifests matched the pattern given")

    output_manifest = manifests[0].replace("-aarch64", "").replace("-x86_64", "")
    console.print(
        f"Will combine the following [bold green]{len(manifests)} manifests [/bold green]",
        highlight=False,
    )
    for manifest in manifests:
        console.print(f" - [b]{manifest}[/b]", highlight=False)

    console.print(f"~> into [b green]{output_manifest}[/b green]", highlight=False)

    create_manifest(output_manifest, manifests)

    if do_push:
        console.print("[b]Pushing manifest[/b]")
        push(output_manifest)

    console.print("[bold green]DONE![/bold green]")


if "__main__" == __name__:
    typer.run(main)
