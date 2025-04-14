#!/usr/bin/env python3

# /// script
# dependencies = [
#   "rich",
#   "typer",
#   "jinja2"
# ]
# ///


from pathlib import Path
import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.panel import Panel
import json
from jinja2 import Template
from typing import Annotated

DOCKERFILE_TEMPLATE = r"""

# Build the final image using base image
FROM {{ base_image }}

{% for name, full_name, url in layers -%}
COPY --from={{ url }} /spack /spack
{% endfor %}

RUN <<EOT bash
set -eux
{{ preparation_script }}
EOT

RUN curl -LsSf https://astral.sh/uv/0.6.14/install.sh | sh
ENV PATH=/root/.local/bin:$PATH

ENV CCACHE_DIR=/ccache

RUN <<EOT bash
set -eux
set -o pipefail

base_dir=$(dirname $(find /spack -type d -name "root-*"))

# locating package install paths
{%- for name, full_name, url in layers %}
{%- if loop.first %}
echo "export CMAKE_PREFIX_PATH=\$base_dir/{{ full_name }}" >> \$HOME/.bashrc
{%- else %}
echo "export CMAKE_PREFIX_PATH=\$base_dir/{{ full_name }}:"'\$CMAKE_PREFIX_PATH' >> \$HOME/.bashrc
{%- endif -%}
{%- endfor %}

{% set vdt = specs["vdt"] -%}
echo "export CMAKE_PREFIX_PATH=\$base_dir/{{ vdt.name }}-{{ vdt.version }}-{{ vdt.hash }}:"'\$CMAKE_PREFIX_PATH' >> \$HOME/.bashrc

# CLHEP has a special location
{% set clhep = specs["clhep"] -%}
echo "export CMAKE_PREFIX_PATH=\$base_dir/{{ clhep.name }}-{{ clhep.version }}-{{ clhep.hash }}/lib/CLHEP-{{ clhep.version }}:"'\$CMAKE_PREFIX_PATH' >> \$HOME/.bashrc

echo "export PATH=
{%- for _, spec in specs.items() -%}
\$base_dir/{{ spec.name }}-{{ spec.version }}-{{ spec.hash }}/bin:
{%- endfor -%}
:\$PATH" >> \$HOME/.bashrc

{% set python = specs["python"] -%}
{% set python_exe = "\\$base_dir/"+python.name+"-"+python.version+"-"+python.hash+"/bin/python3" -%}
uv pip install --python={{ python_exe }} --system pyyaml jinja2

EOT




ENTRYPOINT ["/bin/bash"]

""".strip()

app = typer.Typer()
console = Console()


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
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Output Dockerfile to this path"),
    ] = None,
    base_image: Annotated[
        str | None,
        typer.Option(
            help="Base image to use. If not provided, will use the last layer as base image"
        ),
    ] = None,
    oci_url: Annotated[
        str,
        typer.Option(
            help="OCI URL to use. If not provided, will use the default OCI URL"
        ),
    ] = "ghcr.io/acts-project/spack-buildcache",
):
    if not lockfile_path.exists():
        print(f"Lockfile {lockfile_path} does not exist")
        typer.Exit(1)

    with lockfile_path.open("r") as f:
        lockfile = json.load(f)

    layers = []

    for root in lockfile["roots"]:
        root_hash = root["hash"]
        spec = lockfile["concrete_specs"][root_hash]
        name = spec["name"]
        version = spec["version"]
        console.print(
            f"{root_hash[:7]} [bold]{spec['name']}[cyan]@{spec['version']}[/cyan][/bold]"
        )
        console.print(
            f"{' '*4} ~> [italic]{oci_url}:[/italic][bold]{name}[/bold]-[cyan]{version}[/cyan]-[bold]{root_hash}[/bold][italic].spack[/italic]",
            highlight=False,
        )

        full_name = f"{name}-{version}-{root_hash}"

        full_url = f"{oci_url}:{full_name}.spack"
        layers.append((name, full_name, full_url))

    console.print()

    if base_image is None:
        base_image = layers[-1][-1]

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

    # preparation_script = "\n".join(lines)

    preparation_script = preparation_script.replace("\\", "\\\\").replace("$", r"\$")

    by_package = {v["name"]: v for _, v in lockfile["concrete_specs"].items()}

    dockerfile = template.render(
        layers=layers,
        base_image=base_image,
        preparation_script=preparation_script,
        specs=by_package,
    )

    console.print(Panel(Syntax(dockerfile, "dockerfile"), title="Dockerfile"))

    if output is not None:
        output.write_text(dockerfile)


if __name__ == "__main__":
    app()
