#!/usr/bin/env python3

# /// script
# requires-python = ">= 3.10"
# dependencies = [
#   "rich",
#   "typer",
#   "jinja2",
#   "pydantic"
# ]
# ///


from pathlib import Path
import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
import json
from jinja2 import Template
from typing import Annotated, Any
import subprocess
from pydantic import BaseModel

DOCKERFILE_TEMPLATE = r"""

# Build the final image using base image

{% if flatten %}
# Grouped downloads of dependencies by root spec
{% for block in spec_blocks %}
{% set root = block[-1] -%}
# for {{ root.name }}-{{ root.hash }}
FROM {{ base_image }} AS stage-{{ root.name }}-{{ root.hash }}
{%- for spec in block %}
COPY --from={{ spec.full_url(oci_url) }} /spack /spack
{%- endfor %}
{% endfor %}

# Assemble stages into final image
FROM {{ base_image }}
{% for block in spec_blocks %}
{% set root = block[-1] -%}
COPY --from=stage-{{ root.name }}-{{ root.hash }} /spack /spack
{%- endfor %}

{% else %}

FROM {{ base_image }}
{% for block in spec_blocks %}
{% set root = block[-1] -%}
COPY --from={{ root.full_url(oci_url) }} /spack /spack
{%- endfor %}

{% endif %}



RUN <<EOT bash
set -eux
{{ preparation_script }}
EOT

RUN curl -LsSf https://astral.sh/uv/0.6.14/install.sh | sh
ENV PATH=/root/.local/bin:$PATH

ENV CCACHE_DIR=/ccache
RUN mkdir /ccache

RUN <<EOT bash
set -eux
set -o pipefail

base_dir=$(dirname $(find /spack -type d -name "root-*"))

echo "BASE_DIR=\$base_dir" >> ~/.bashrc

{% set python = specs["python"] -%}
{% set python_exe = "\\$base_dir/"+python.name+"-"+python.version+"-"+python.hash+"/bin/python3" -%}
uv pip install --python={{ python_exe }} --system pyyaml jinja2

{% set geant4 = specs["geant4"] -%}
{% set geant4_dir = "\\$base_dir/"+geant4.name+"-"+geant4.version+"-"+geant4.hash+"/share/Geant4/data" -%}
mkdir /g4data
ln -sf /g4data {{ geant4_dir }}

EOT

RUN cat <<EOF >> ~/.bashrc

declare -a prefixes=(
{%- for _, spec in specs.items()|reverse -%}
{%- if (loop.index-1) % 3 == 0 %}
 {% endif %} {{ spec.name }}-{{ spec.version }}-{{ spec.hash }}
{%- endfor %}
)

# Configure \$CMAKE_PREFIX_PATH
for p in "\${prefixes[@]}"; do
    CMAKE_PREFIX_PATH="\$BASE_DIR/\$p\${CMAKE_PREFIX_PATH:+:\${CMAKE_PREFIX_PATH}}"
done
export CMAKE_PREFIX_PATH

# CLHEP has a special location
{% set clhep = specs["clhep"] -%}
export CMAKE_PREFIX_PATH=\$BASE_DIR/{{ clhep.name }}-{{ clhep.version }}-{{ clhep.hash }}/lib/CLHEP-{{ clhep.version }}:\$CMAKE_PREFIX_PATH

# Configure \$PATH Variable
for p in "\${prefixes[@]}"; do
    PATH="\$BASE_DIR/\$p/bin\${PATH:+:\${PATH}}"
done
export PATH

cat /etc/motd

EOF



RUN cat <<EOF >> /etc/motd
=============== ACTS development image with dependencies ===============
- Clone repository:
    git clone https://github.com/acts-project/acts.git --recursive
- Configure:
    cmake -S acts -B build -GNinja --preset dev \\
      -DACTS_BUILD_UNITTESTS=OFF -DACTS_BUILD_INTEGRATIONTESTS=OFF
- Build:
    cmake --build build
- Run:
    source build/this_acts_withdeps.sh
    acts/Examples/Scripts/Python/full_chain_odd.py -n1
========================================================================
EOF

ENTRYPOINT ["/bin/bash"]

""".strip()

app = typer.Typer()
console = Console()


def manifest_exists(url: str):
    proc = subprocess.run(["docker", "manifest", "inspect", url], capture_output=True)
    return proc.returncode == 0


class Spec(BaseModel):
    name: str
    version: str
    hash: str

    class Dependency(BaseModel):
        name: str
        hash: str

        class Parameters(BaseModel):
            deptypes: list[str]

        parameters: Parameters

        @property
        def is_build_only(self) -> bool:
            return self.parameters.deptypes == ["build"]

    dependencies: list[Dependency] | None = None

    class External(BaseModel):
        path: str

    external: External | None = None

    @property
    def is_external(self) -> bool:
        return self.external is not None

    @property
    def full_name(self) -> str:
        return f"{self.name}-{self.version}-{self.hash}"

    def full_url(self, oci_url: str) -> str:
        return f"{oci_url}:{self.full_name}.spack"

    @property
    def markup(self) -> str:
        return f"{self.hash[:7]} [bold]{self.name}[cyan]@{self.version}[/cyan][/bold]"

    @property
    def unformatted(self) -> str:
        return f"{self.hash[:7]} {self.name}@{self.version}"

    def oci_url_markup(self, oci_url: str):
        return (
            f"{' '*4} ~> [italic]{oci_url}:[/italic][bold]{self.name}[/bold]-[cyan]{self.version}[/cyan]-[bold]{self.hash}[/bold][italic].spack[/italic]",
        )

    # @staticmethod
    # def from_dict(info: dict[str, Any]) -> "Spec":
    #     return Spec(name=info["name"], version=info["version"], hash=info["hash"])


def runtime_closure(root_hash: str, concrete_specs: dict[str, Spec]) -> set[str]:
    """Specs whose files are present in the buildcache image of `root_hash`.

    Spack publishes one layer per non-external spec in a root's runtime
    closure, so this is exactly what `COPY --from=<root image> /spack /spack`
    pulls in -- the root's dependencies come along whether we ask for them or
    not.
    """
    seen: set[str] = set()
    stack = [root_hash]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for dep in concrete_specs[current].dependencies or []:
            if dep.is_build_only or concrete_specs[dep.hash].is_external:
                continue
            stack.append(dep.hash)
    return {h for h in seen if not concrete_specs[h].is_external}


def select_covering_roots(
    roots: list[Spec], closures: dict[str, set[str]]
) -> list[Spec]:
    """Drop roots whose closures are already covered by the roots we keep.

    Since every COPY drags in a whole closure, copying from all of them
    rewrites shared dependencies many times over -- the CUDA stack moves the
    4 GB toolkit seven times, which is what exhausts the runner's disk. A
    covering subset produces a byte-identical /spack: install prefixes are
    hash-suffixed, so the closures partition the tree and no file is ever
    written twice, leaving COPY order irrelevant.
    """
    remaining = set().union(*closures.values())
    keep: set[str] = set()
    while remaining:
        # Tie-break on full_name so the generated Dockerfile is reproducible.
        best = min(
            roots,
            key=lambda spec: (-len(closures[spec.hash] & remaining), spec.full_name),
        )
        gain = closures[best.hash] & remaining
        if not gain:
            break
        keep.add(best.hash)
        remaining -= gain

    selected = [spec for spec in roots if spec.hash in keep]
    covered = set().union(*(closures[spec.hash] for spec in selected))
    assert covered == set().union(*closures.values()), (
        "covering subset does not reproduce the full spec set"
    )
    return selected


@app.command()
def main(
    lockfile_path: Annotated[
        Path,
        typer.Argument(
            help="Path to the lockfile",
            exists=True,
            dir_okay=False,
            file_okay=True,
        ),
    ],
    base_image: Annotated[
        str,
        typer.Option(
            help="Base image to use. If not provided, will use the last layer as base image"
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Output Dockerfile to this path"),
    ] = None,
    oci_url: Annotated[
        str,
        typer.Option(
            help="OCI URL to use. If not provided, will use the default OCI URL"
        ),
    ] = "ghcr.io/acts-project/spack-buildcache",
    verbose: bool = False,
    flatten: bool = False,
    minimize_copies: Annotated[
        bool,
        typer.Option(
            help="Emit COPYs only for a covering subset of the root specs. "
            "The resulting /spack is identical, but shared dependencies are "
            "moved once instead of once per dependent root."
        ),
    ] = True,
):
    if not lockfile_path.exists():
        print(f"Lockfile {lockfile_path} does not exist")
        typer.Exit(1)

    with lockfile_path.open("r") as f:
        lockfile = json.load(f)

    layers: list[Spec] = []

    def process_spec(spec_info: dict[str, Any]) -> Spec:
        spec = Spec(
            name=spec_info["name"], version=spec_info["version"], hash=spec_info["hash"]
        )
        return spec

    concrete_specs = {
        h: Spec.model_validate(v) for h, v in lockfile["concrete_specs"].items()
    }

    assigned_specs: set[str] = set()

    spec_blocks: list[list[Spec]] = []

    root_specs = [concrete_specs[root["hash"]] for root in lockfile["roots"]]

    if minimize_copies:
        closures = {
            spec.hash: runtime_closure(spec.hash, concrete_specs)
            for spec in root_specs
        }
        selected_roots = select_covering_roots(root_specs, closures)
        dropped = len(root_specs) - len(selected_roots)
        console.print(
            f"Covering subset: [bold]{len(selected_roots)}[/bold] of "
            f"{len(root_specs)} roots ({dropped} already covered)\n"
        )
    else:
        selected_roots = root_specs

    for spec in selected_roots:
        console.print(spec.markup)

        block: list[Spec] = []

        if flatten:
            for dep in spec.dependencies or []:
                full_dep = concrete_specs[dep.hash]
                if dep.is_build_only or full_dep.is_external:
                    continue

                already_assigned = dep.hash in assigned_specs

                if already_assigned:
                    console.print(
                        f"~> [bright_black italic]{full_dep.unformatted}[/bright_black italic]",
                        highlight=False,
                    )
                else:
                    console.print(f"~> {full_dep.markup}", highlight=False)
                    assigned_specs.add(dep.hash)
                    block.append(full_dep)

        block.append(spec)

        spec_blocks.append(block)

        console.print()

    template = Template(DOCKERFILE_TEMPLATE)

    preparation_script = (
        Path(__file__).parent / "docker" / "install_packages.sh"
    ).read_text()
    lines = []
    for i, line in enumerate(preparation_script.strip().split("\n")):
        if i == 0:
            lines.append(f"RUN {line} \\\\")
        elif i == len(preparation_script) - 1:
            lines.append(f"    {line}")
        else:
            lines.append(f"    {line} \\\\")

    preparation_script = preparation_script.replace("\\", "\\\\").replace("$", r"\$")

    by_package = {v["name"]: v for _, v in lockfile["concrete_specs"].items()}
    by_package.pop("git")
    if "git-lfs" in by_package:
        by_package.pop("git-lfs")

    dockerfile = template.render(
        base_image=base_image,
        preparation_script=preparation_script,
        specs=by_package,
        oci_url=oci_url,
        spec_blocks=spec_blocks,
        flatten=flatten,
    )

    if dockerfile is None:
        raise RuntimeError("Failed to render dockerfile template")

    if verbose:
        console.print(Panel(Syntax(dockerfile, "dockerfile"), title="Dockerfile"))

    if output is not None:
        output.write_text(dockerfile)
        console.print(
            f"Generated Dockerfile written to [bold]{output.resolve()}[/bold]"
        )


if __name__ == "__main__":
    app()
