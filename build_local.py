#!/usr/bin/env python3

# /// script
# dependencies = [
#   "rich",
#   "typer",
#   "python-slugify",
# ]
# ///

import subprocess
from pathlib import Path
from typing import Annotated
import os
import shutil
import enum

import typer
import slugify

app = typer.Typer()


def run_container(
    cmd: str,
    script_dir: Path,
    spack_root: Path,
    build_dir: Path,
    image: str,
    compiler: str,
):

    oci_user = os.environ["GH_OCI_USER"]
    oci_token = os.environ["GH_OCI_TOKEN"]

    args = [
        "docker",
        "run",
        f"-v{script_dir}:/src",
        f"-v{spack_root}:/spack",
        f"-v{build_dir}:/build",
        "-eSPACK_ROOT=/spack",
        f"-eCOMPILER={compiler}",
        f"-eGH_OCI_USER={oci_user}",
        f"-eGH_OCI_TOKEN={oci_token}",
        f"-eBASE_IMAGE={image}",
        "-w/build",
        image,
        f"/src/{cmd}",
    ]

    subprocess.run(args, check=True)


def run_host(
    cmd: str,
    script_dir: Path,
    spack_root: Path,
    build_dir: Path,
    image: str,
    compiler: str,
):
    oci_user = os.environ["GH_OCI_USER"]
    oci_token = os.environ["GH_OCI_TOKEN"]

    subprocess.run(
        [script_dir / cmd],
        env={
            **os.environ,
            "SPACK_ROOT": spack_root,
            "COMPILER": compiler,
            "GH_OCI_USER": oci_user,
            "GH_OCI_TOKEN": oci_token,
            "BASE_IMAGE": image,
        },
        cwd=build_dir,
        check=True,
    )


class Mode(enum.StrEnum):
    host = "host"
    container = "container"


@app.command()
def main(
    base_dir: Annotated[Path, typer.Option(file_okay=False)],
    compiler: Annotated[str, typer.Option()],
    image: Annotated[str, typer.Option()],
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
    ignore: Annotated[bool, typer.Option("-i", "--ignore")] = False,
    build: bool = True,
    push: bool = True,
    mode: Annotated[Mode, typer.Option()] = Mode.container,
):

    base_dir = base_dir / slugify.slugify(f"{image}-{compiler}")
    base_dir = base_dir.resolve()

    if not base_dir.exists():
        base_dir.mkdir(parents=True)

    spack_root = base_dir / "spack"
    build_dir = base_dir / "build"

    if build_dir.exists():
        if force:
            shutil.rmtree(build_dir)
        elif ignore:
            pass
        else:
            raise typer.Exit(f"Build directory {build_dir} already exists")
    build_dir.mkdir(parents=True, exist_ok=True)

    if not spack_root.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=2",
                "https://github.com/spack/spack.git",
                spack_root,
            ],
            check=True,
        )

    script_dir = Path(__file__).resolve().parent

    if mode == Mode.container:
        run_func = run_container
    else:
        run_func = run_host

    if build:
        run_func(
            "spack_build.sh",
            script_dir=script_dir,
            spack_root=spack_root,
            build_dir=build_dir,
            image=image,
            compiler=compiler,
        )
    if push:
        run_func(
            "spack_push.sh",
            script_dir=script_dir,
            spack_root=spack_root,
            build_dir=build_dir,
            image=image,
            compiler=compiler,
        )


app()
