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
from abc import ABC, abstractmethod
import contextlib
import logging

import typer
import slugify
from rich import print
from rich.rule import Rule
from rich.live import Live
from rich.logging import RichHandler

app = typer.Typer()


FORMAT = "%(message)s"
logging.basicConfig(
    level="NOTSET", format=FORMAT, datefmt="[%X]", handlers=[RichHandler()]
)

log = logging.getLogger("rich")


class Environment(ABC):
    base_dir: Path
    script_dir: Path
    spack_root: Path
    build_dir: Path
    compiler: str
    image: str

    def __init__(
        self,
        base_dir: Path,
        script_dir: Path,
        spack_root: Path,
        build_dir: Path,
        compiler: str,
        image: str,
    ):
        self.base_dir = base_dir
        self.script_dir = script_dir
        self.spack_root = spack_root
        self.build_dir = build_dir
        self.compiler = compiler
        self.image = image

    @abstractmethod
    def run_script(self, cmd: str): ...

    @property
    def oci_user(self) -> str | None:
        return os.environ.get("GH_OCI_USER")

    @property
    def oci_token(self) -> str | None:
        return os.environ.get("GH_OCI_TOKEN")

    @property
    def oci_configured(self) -> bool:
        return self.oci_token is not None and self.oci_user is not None

    @abstractmethod
    def rmbuild(self): ...

    @abstractmethod
    def rmspack(self): ...


class HostEnvironment(Environment):
    def run_script(self, cmd: str):
        env = {
            **os.environ,
            "SPACK_ROOT": self.spack_root,
            "COMPILER": self.compiler,
            "BASE_IMAGE": self.image,
        }

        if self.oci_configured:
            env.update(
                {
                    "GH_OCI_USER": self.oci_user,
                    "GH_OCI_TOKEN": self.oci_token,
                }
            )
        subprocess.run(
            [self.script_dir / cmd],
            env=env,
            cwd=self.build_dir,
            check=True,
        )

    def rmbuild(self):
        shutil.rmtree(self.build_dir)

    def rmspack(self):
        shutil.rmtree(self.spack_root)


class ContainerEnvironment(Environment):

    def run_script(self, cmd: str):
        args = [
            "docker",
            "run",
            f"-v{self.script_dir}:/src",
            f"-v{self.spack_root}:/spack",
            f"-v{self.build_dir}:/build",
            "-eSPACK_ROOT=/spack",
            f"-eCOMPILER={self.compiler}",
        ]
        if self.oci_configured:
            args += [
                f"-eGH_OCI_USER={self.oci_user}",
                f"-eGH_OCI_TOKEN={self.oci_token}",
            ]
        args += [
            f"-eBASE_IMAGE={self.image}",
            "-w/build",
            self.image,
            f"/src/{cmd}",
        ]

        subprocess.run(args, check=True)

    def rmbuild(self):
        rel_build = self.build_dir.relative_to(self.base_dir)
        container_build = Path("/base") / rel_build
        args = [
            "docker",
            "run",
            f"-v{self.base_dir}:/base",
            self.image,
        ] + ["rm", "-rf", str(container_build)]
        subprocess.run(args, check=True)

    def rmspack(self):
        rel_spack = self.spack_root.relative_to(self.base_dir)
        container_spack = Path("/base") / rel_spack
        args = [
            "docker",
            "run",
            f"-v{self.base_dir}:/base",
            self.image,
        ] + ["rm", "-rf", str(container_spack)]
        subprocess.run(args, check=True)


class Mode(enum.StrEnum):
    host = "host"
    container = "container"


@contextlib.contextmanager
def checkpoint(label: str):
    print(Rule(f":hourglass_not_done: {label}", align="left"))
    yield
    print(Rule(f":white_check_mark: {label}", align="left"))


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
    spack_version: str = "develop",
    reinstall_spack: bool = False,
):

    script_dir = Path(__file__).resolve().parent

    base_dir = base_dir / slugify.slugify(f"{image}-{compiler}")
    base_dir = base_dir.resolve()

    # Always created on host!
    if not base_dir.exists():
        base_dir.mkdir(parents=True)

    spack_root = base_dir / "spack"
    build_dir = base_dir / "build"

    if mode == Mode.container:
        env_type = ContainerEnvironment
    else:
        env_type = HostEnvironment

    env = env_type(
        base_dir=base_dir,
        script_dir=script_dir,
        spack_root=spack_root,
        build_dir=build_dir,
        compiler=compiler,
        image=image,
    )

    if build_dir.exists():
        if force:
            env.rmbuild()
        elif ignore:
            pass
        else:
            raise RuntimeError(f"Build directory {build_dir} already exists")

    build_dir.mkdir(parents=True, exist_ok=True)

    if reinstall_spack:
        with checkpoint("Removing spack"):
            env.rmspack()
    if not spack_root.exists():
        with checkpoint("Installing spack"):
            subprocess.run(
                [
                    "git",
                    "clone",
                    "https://github.com/spack/spack.git",
                    spack_root,
                ],
                check=True,
            )

            subprocess.run(
                [
                    "git",
                    "checkout",
                    spack_version,
                ],
                cwd=spack_root,
                check=True,
            )
        print(f"Spack is at: [bold]{spack_version}[/bold]")

    if build:
        with checkpoint("Running spack build"):
            env.run_script("spack_build.sh")
    if push:
        if not env.oci_configured:
            log.warning("OCI environment variables not configured, skipping push")
        else:
            with checkpoint("Pushing build caches"):
                env.run_script("spack_push.sh")


app()
