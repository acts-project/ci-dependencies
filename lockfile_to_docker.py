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
# Load root specs as builders
{% for name, full_name, url in layers -%}
FROM {{ url }} AS {{ name }}
{% endfor %}

# Build the final image using base image
FROM {{ base_image }}

{% for name, full_name, url in layers -%}
COPY --from={{ name }} /spack /spack
{% endfor %}

RUN <<EOT bash
set -eux
{{ preparation_script }}
EOT

RUN curl -LsSf https://astral.sh/uv/0.6.14/install.sh | sh
ENV PATH=/root/.local/bin:$PATH

RUN <<EOT bash
set -eux
set -o pipefail

# locating package install pathsx
{%- for name, full_name, url in layers %}
dir=`find /spack -type d -name "{{ full_name }}*"`
{%- if loop.first %}
echo "export CMAKE_PREFIX_PATH=\$dir" >> \$HOME/.bashrc
{%- else %}
echo "export CMAKE_PREFIX_PATH=\$dir:"'\$CMAKE_PREFIX_PATH' >> \$HOME/.bashrc
{%- endif -%}
{%- endfor %}

# CLHEP has a special location
clhep_dir=$(dirname $(find /spack -type f -name "CLHEPConfig.cmake"))
echo "export CMAKE_PREFIX_PATH=\$clhep_dir:"'\$CMAKE_PREFIX_PATH' >> \$HOME/.bashrc

cmake_bin_dir=$(dirname $(find /spack -type f -name cmake -executable))
echo "export PATH=\$cmake_bin_dir:"'\$PATH' >> \$HOME/.bashrc

python_bin_dir=$(find /spack -type d -name "python-3*")/bin
echo "export PATH=\$python_bin_dir:"'\$PATH' >> \$HOME/.bashrc

echo "source /.venv/bin/activate" >> \$HOME/.bashrc

uv venv --python=\$python_bin_dir/python3
uv pip install pyyaml jinja2

EOT




ENTRYPOINT ["/bin/bash"]

""".strip()

app = typer.Typer()
console = Console()

# RUN cmake_exe=`find /spack -type f -name cmake -executable` \\
#     && cmake_bin_dir=`dirname $cmake_exe` \\
#     && echo "export PATH=$cmake_bin_dir:\$PATH" >> $HOME/.bashrc


# RUN {% for name, full_name, url in layers -%}
#     {% if loop.first %}dir=`find /spack -type d -name "{{ full_name }}*"` \\
#     && echo "export CMAKE_PREFIX_PATH=$dir" >> $HOME/.bashrc \\
#     {% else %}&& dir=`find /spack -type d -name "{{ full_name }}*"` \\
#     && echo "export CMAKE_PREFIX_PATH=\$CMAKE_PREFIX_PATH:$dir" >> $HOME/.bashrc{% if not loop.last %} \\{% endif %}
#     {% endif %}
# {%- endfor %}


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

    preparation_script = preparation_script.replace("$", r"\$")

    dockerfile = template.render(
        layers=layers, base_image=base_image, preparation_script=preparation_script
    )

    console.print(Panel(Syntax(dockerfile, "dockerfile"), title="Dockerfile"))

    if output is not None:
        output.write_text(dockerfile)


if __name__ == "__main__":
    app()
