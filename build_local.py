#!/usr/bin/env python3

# /// script
# dependencies = [
#   "rich",
#   "typer",
#   "python-slugify",
#   "docker",
#   "httpx",
#   "PyYAML",
# ]
# ///

import subprocess
from pathlib import Path
from typing import Annotated, cast
import os
import shutil
import enum
from abc import ABC, abstractmethod
import tempfile
import contextlib
import logging
import yaml

import typer
import slugify
from rich.console import Console
from rich.rule import Rule
from rich.live import Live
from rich.logging import RichHandler
import httpx

app = typer.Typer()


FORMAT = "%(message)s"
logging.basicConfig(
    level="INFO",
    format=FORMAT,
    datefmt="[%X]",
)

log = logging.getLogger(__name__)
log.handlers.append(RichHandler(markup=True))
log.propagate = False


class Environment(ABC):
    base_dir: Path
    script_dir: Path
    spack_root: Path
    build_dir: Path
    compiler: str
    image: str
    fail_fast: bool

    def __init__(
        self,
        base_dir: Path,
        script_dir: Path,
        spack_root: Path,
        build_dir: Path,
        compiler: str,
        image: str,
        fail_fast: bool,
    ):
        self.base_dir = base_dir
        self.script_dir = script_dir
        self.spack_root = spack_root
        self.build_dir = build_dir
        self.compiler = compiler
        self.image = image
        self.fail_fast = fail_fast

    @abstractmethod
    def run_script(self, cmd: str, console: Console): ...

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
    def run_script(self, cmd: str, console: Console):
        env = {
            **os.environ,
            "SPACK_ROOT": self.spack_root,
            "COMPILER": self.compiler,
            "BASE_IMAGE": self.image,
        }

        if self.fail_fast:
            env["FAIL_FAST"] = "1"

        if self.oci_configured:
            env.update(
                {
                    "GH_OCI_USER": self.oci_user,
                    "GH_OCI_TOKEN": self.oci_token,
                }
            )
        # subprocess.run(
        #     [self.script_dir / cmd],
        #     env=env,
        #     cwd=self.build_dir,
        #     check=True,
        # )

        proc = subprocess.Popen(
            [self.script_dir / cmd],
            env=env,
            cwd=self.build_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            assert proc.stdout is not None, "no stdout"
            while line := proc.stdout.readline() or proc.poll() is None:
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                    console.print(
                        line, end="\n", highlight=False, markup=False, emoji=False
                    )
        except KeyboardInterrupt:
            proc.kill()
            proc.terminate()
            log.debug("Waiting to terminate")
            proc.wait()
            raise

    def rmbuild(self):
        shutil.rmtree(self.build_dir)

    def rmspack(self):
        shutil.rmtree(self.spack_root)


class ContainerEnvironment(Environment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import docker

        self.client = docker.from_env()

    def run_script(self, cmd: str, console: Console):
        env = {
            "SPACK_ROOT": "/spack",
            "COMPILER": self.compiler,
            "BASE_IMAGE": self.image,
        }

        if self.fail_fast:
            env["FAIL_FAST"] = "1"

        if self.oci_configured:
            assert self.oci_user is not None
            assert self.oci_token is not None
            env.update(
                {
                    "GH_OCI_USER": self.oci_user,
                    "GH_OCI_TOKEN": self.oci_token,
                }
            )

        container = self.client.containers.run(
            self.image,
            f"/src/{cmd}",
            environment=env,
            volumes=[
                f"{self.script_dir}:/src",
                f"{self.spack_root}:/spack",
                f"{self.build_dir}:/build",
            ],
            working_dir="/build",
            detach=True,
        )

        try:
            for s in container.logs(stream=True):
                s = s.decode("utf-8")
                console.print(s, end="", highlight=False, markup=False, emoji=False)

            res = container.wait()
            ec = res["StatusCode"]
            if ec != 0:
                log.error("Container execution failed")
                raise RuntimeError()
        except KeyboardInterrupt:
            container.kill()
            raise

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
    log.info(":hourglass_not_done: Starting: %s", label)
    try:
        yield
        log.info(":white_check_mark: Finished: %s", label)
    except KeyboardInterrupt:
        raise
    except:
        log.error(":red_square: Failed: %s", label)
        raise


@app.command()
def build(
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
    fail_fast: bool = False,
    spack_patches: list[str] = [],
    external_spack: Path | None = None,
):

    console = Console()

    script_dir = Path(__file__).resolve().parent

    base_dir = base_dir / slugify.slugify(f"{image}-{compiler}")
    base_dir = base_dir.resolve()

    # Always created on host!
    if not base_dir.exists():
        base_dir.mkdir(parents=True)

    if external_spack is not None:
        if not external_spack.exists():
            log.error(
                "--external-spack was given, but %s does not exist", external_spack
            )
            raise typer.Exit(1)

        spack_root = external_spack
    else:
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
        fail_fast=fail_fast,
    )

    if build_dir.exists():
        if force:
            env.rmbuild()
        elif ignore:
            pass
        else:
            log.error("Build directory %s already exists", build_dir)
            raise typer.Exit(1)

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

            for patch in spack_patches:
                if os.path.exists(patch):
                    with open(patch, "rb") as fh:
                        content = fh.read()
                else:
                    res = httpx.get(patch)
                    res.raise_for_status()
                    content = res.content
                subprocess.run(
                    [
                        "git",
                        "am",
                    ],
                    input=content,
                    cwd=spack_root,
                    check=True,
                )

        log.info(f"Spack is at: [bold]{spack_version}[/bold]")

    try:
        if build:
            with checkpoint("Running spack build"):
                env.run_script("spack_build.sh", console)
        if push:
            if not env.oci_configured:
                log.warning("OCI environment variables not configured, skipping push")
            else:
                with checkpoint("Pushing build caches"):
                    env.run_script("spack_push.sh", console)
    except Exception:
        log.error("Terminating build due to error")
        return typer.Exit(1)


@app.command()
def matrix(
    base_dir: Annotated[Path | None, typer.Option(file_okay=False)] = None,
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
    ignore: Annotated[bool, typer.Option("-i", "--ignore")] = False,
    build_flag: Annotated[bool, typer.Option("--build")] = True,
    push: bool = True,
    spack_version: str = "develop",
    reinstall_spack: bool = False,
    fail_fast: bool = False,
    spack_patches: list[str] = [],
    external_spack: Path | None = None,
):

    log.info("Finding matrix configuration from CI config")
    with (Path(__file__).parent / ".github/workflows/build_spack.yml").open() as fh:
        config = yaml.safe_load(fh)
    matrix = config["jobs"]["build_container"]["strategy"]["matrix"]["include"]

    log.info("Will run the following combinations:")
    for entry in matrix:
        image = entry["image"]
        compiler = entry["compiler"]
        os = entry["os"]
        log.info(f"{image=}, {compiler=}, {os=}")

    with contextlib.ExitStack() as ex:
        if base_dir is None:
            base_dir = Path(ex.enter_context(tempfile.TemporaryDirectory()))
            log.info(f"Using temporary directory {base_dir}")

        for entry in matrix:
            image = entry["image"]
            compiler = entry["compiler"]
            os = entry["os"]

            build(
                base_dir=base_dir,
                compiler=compiler,
                image=image,
                force=force,
                ignore=ignore,
                build=build_flag,
                push=push,
                mode=Mode.container,
                spack_version=spack_version,
                spack_patches=spack_patches,
                reinstall_spack=reinstall_spack,
                fail_fast=fail_fast,
                external_spack=external_spack,
            )


app()
