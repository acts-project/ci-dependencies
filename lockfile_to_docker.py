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

DOCKERFILE_TEMPLATE = """
# Load root specs as builders
{% for name, full_name, url in layers -%}
FROM {{ url }} AS {{ name }}
{% endfor %}

# Build the final image using base image
FROM {{ base_image }}

{% for name, full_name, url in layers -%}
COPY --from={{ name }} /spack /spack
{% endfor %}

RUN {% for name, full_name, url in layers -%}
    {% if loop.first %}dir=`find /spack -type d -name "{{ full_name }}*"` \\
    && echo "export CMAKE_PREFIX_PATH=$dir" >> $HOME/.bashrc \\
    {% else %}&& dir=`find /spack -type d -name "{{ full_name }}*"` \\
    && echo "export CMAKE_PREFIX_PATH=\$CMAKE_PREFIX_PATH:$dir" >> $HOME/.bashrc{% if not loop.last %} \\{% endif %}
    {% endif %}
{%- endfor %}

RUN cat $HOME/.bashrc

RUN cmake_exe=`find /spack -type f -name cmake -executable` \\
    && cmake_bin_dir=`dirname $cmake_exe` \\
    && echo "export PATH=$cmake_bin_dir:\$PATH" >> $HOME/.bashrc

RUN <<EOT bash
set -eux
{{ preparation_script }}
EOT

ENTRYPOINT ["/bin/bash"]

""".strip()

app = typer.Typer()
console = Console()


@app.command()
def main(
    lockfile: Annotated[
        Path,
        typer.Argument(
            help="Path to the lockfile", exists=True, dir_okay=False, file_okay=True
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
    if not lockfile.exists():
        print(f"Lockfile {lockfile} does not exist")
        typer.Exit(1)

    with lockfile.open("r") as f:
        lockfile = json.load(f)

    layers = []

    for root in lockfile["roots"]:
        root_hash = root["hash"]
        spec = lockfile["concrete_specs"][root_hash]
        name = spec["name"]
        version = spec["version"]
        console.print(
            f"[black]{root_hash[:7]}[/black] [bold]{spec['name']}[cyan]@{spec['version']}[/cyan][/bold]"
        )
        console.print(
            f"{' '*4} ~> [italic black]{oci_url}:[/italic black][bold]{name}[/bold]-[cyan]{version}[/cyan]-[black bold]{root_hash}[/black bold][italic black].spack[/italic black]",
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

    preparation_script = preparation_script.replace("$", r"\$")

    dockerfile = template.render(
        layers=layers, base_image=base_image, preparation_script=preparation_script
    )

    console.print(Panel(Syntax(dockerfile, "dockerfile"), title="Dockerfile"))

    if output is not None:
        output.write_text(dockerfile)


if __name__ == "__main__":
    app()
