#!/usr/bin/env python3

# /// script
# requires-python = ">= 3.10"
# dependencies = [
#   "rich",
#   "typer",
#   "pydantic"
# ]
# ///

"""Diff two spack lockfiles and report what changed between them.

A spack lockfile (``spack.lock``) is a JSON document whose ``concrete_specs``
maps a hash to a concretized spec (``name``, ``version``, ``parameters``,
``hash``, ...). This script compares two such lockfiles, grouping specs by
package name, and produces a concise but rich summary:

* **Updated** -- the package version changed (with any variant deltas).
* **Changed** -- same version, but variants / build options shifted.
* **Added** / **Removed** -- packages that appeared or disappeared.

The intended use is to diff the canonical per-architecture lockfile
(``spack_x86_64.lock``) of two releases for the release notes. The compiler
and runtime nodes (gcc, glibc, ...) are treated as ordinary packages, so a
toolchain bump shows up like any other version change.
"""

from pathlib import Path
import json
from typing import Annotated

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.markdown import Markdown

console = Console()

# Parameter keys that are noise for a human-readable diff.
_SKIP_VARIANTS = {"patches", "cflags", "cppflags", "cxxflags", "fflags", "ldflags", "ldlibs"}


class Spec(BaseModel):
    """A single concretized spec, reduced to the bits we diff on."""

    name: str
    version: str
    hash: str
    variants: dict[str, bool | str]

    @classmethod
    def from_concrete(cls, spec: dict) -> "Spec":
        variants: dict[str, bool | str] = {}
        for key, value in spec.get("parameters", {}).items():
            if key in _SKIP_VARIANTS or isinstance(value, list):
                continue
            variants[key] = value
        return cls(
            name=spec["name"],
            version=str(spec["version"]),
            hash=spec["hash"],
            variants=variants,
        )


def variant_delta(old: Spec, new: Spec) -> list[str]:
    """Spack-style list of variant changes between two specs.

    Boolean flips render as ``+feature`` / ``-feature``; scalar changes render
    as ``key=newvalue``.
    """
    changes: list[str] = []
    for key in sorted(old.variants.keys() | new.variants.keys()):
        o = old.variants.get(key)
        n = new.variants.get(key)
        if o == n:
            continue
        if isinstance(o, bool) or isinstance(n, bool):
            changes.append(f"+{key}" if n else f"-{key}")
        else:
            changes.append(f"{key}={n}")
    return changes


def load_specs(path: Path) -> dict[str, list[Spec]]:
    """Load a lockfile and group its concrete specs by package name.

    A unified concretization usually has one spec per name, but multiple are
    possible (e.g. two pythons), so specs are collected into a list.
    """
    data = json.loads(path.read_text())
    by_name: dict[str, list[Spec]] = {}
    for raw in data.get("concrete_specs", {}).values():
        spec = Spec.from_concrete(raw)
        by_name.setdefault(spec.name, []).append(spec)
    return by_name


class Update(BaseModel):
    name: str
    old_version: str
    new_version: str
    variants: list[str]


class Changed(BaseModel):
    name: str
    version: str
    variants: list[str]


class Diff(BaseModel):
    added: list[Spec]
    removed: list[Spec]
    updated: list[Update]
    changed: list[Changed]

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.updated or self.changed)


def _versions(specs: list[Spec]) -> str:
    return ", ".join(sorted({s.version for s in specs}))


def diff_specs(
    old: dict[str, list[Spec]],
    new: dict[str, list[Spec]],
    show_rebuilds: bool,
) -> Diff:
    old_names = set(old)
    new_names = set(new)

    added = [s for n in sorted(new_names - old_names) for s in new[n]]
    removed = [s for n in sorted(old_names - new_names) for s in old[n]]

    updated: list[Update] = []
    changed: list[Changed] = []

    for name in sorted(old_names & new_names):
        old_specs = old[name]
        new_specs = new[name]

        # Rich per-package detail only makes sense for the common 1:1 case.
        if len(old_specs) == 1 and len(new_specs) == 1:
            o, n = old_specs[0], new_specs[0]
            delta = variant_delta(o, n)
            if o.version != n.version:
                updated.append(
                    Update(
                        name=name,
                        old_version=o.version,
                        new_version=n.version,
                        variants=delta,
                    )
                )
            elif delta:
                changed.append(Changed(name=name, version=n.version, variants=delta))
            elif show_rebuilds and o.hash != n.hash:
                changed.append(
                    Changed(name=name, version=n.version, variants=["(rebuilt)"])
                )
            continue

        # Fallback: multiple specs share this name -> compare version sets only.
        old_versions = {s.version for s in old_specs}
        new_versions = {s.version for s in new_specs}
        if old_versions != new_versions:
            updated.append(
                Update(
                    name=name,
                    old_version=_versions(old_specs),
                    new_version=_versions(new_specs),
                    variants=[],
                )
            )

    return Diff(added=added, removed=removed, updated=updated, changed=changed)


def _label(name: str, variants: list[str]) -> str:
    """Spack-style ``name +foo -bar key=val`` label for inside a code span."""
    return f"{name} {' '.join(variants)}".rstrip()


def render_markdown(diff: Diff) -> str:
    if diff.empty:
        return "_No package changes._\n"

    lines: list[str] = []

    if diff.updated:
        lines += ["**Updated**:", ""]
        for u in diff.updated:
            lines.append(
                f"- `{_label(u.name, u.variants)}` -- "
                f"`{u.old_version}` -> **`{u.new_version}`**"
            )
        lines.append("")

    if diff.changed:
        lines += ["**Changed**:", ""]
        for c in diff.changed:
            lines.append(f"- `{_label(c.name, c.variants)}` `{c.version}`")
        lines.append("")

    if diff.added:
        lines += ["**Added**:", ""]
        for spec in diff.added:
            lines.append(f"- `{spec.name}` **`{spec.version}`**")
        lines.append("")

    if diff.removed:
        lines += ["**Removed**:", ""]
        for spec in diff.removed:
            lines.append(f"- `{spec.name}` `{spec.version}`")
        lines.append("")

    return "\n".join(lines) + "\n"


def main(
    old: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, help="Old (baseline) lockfile."
        ),
    ],
    new: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="New lockfile to compare against the baseline.",
        ),
    ],
    markdown: Annotated[
        bool,
        typer.Option("--markdown", "-m", help="Emit Markdown instead of a rich table."),
    ] = False,
    show_rebuilds: Annotated[
        bool,
        typer.Option(
            "--show-rebuilds",
            help="Also report packages whose hash changed but version/variants did not.",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write output to a file instead of stdout."),
    ] = None,
) -> None:
    """Diff two spack lockfiles (OLD vs NEW)."""
    old_specs = load_specs(old)
    new_specs = load_specs(new)
    diff = diff_specs(old_specs, new_specs, show_rebuilds=show_rebuilds)

    text = render_markdown(diff)

    if output is not None:
        output.write_text(text)
    elif markdown:
        # Raw Markdown to stdout (for piping into release notes).
        console.print(text, markup=False, highlight=False)
    else:
        # Pretty-rendered Markdown for the terminal.
        console.print(Markdown(text))


if __name__ == "__main__":
    typer.run(main)
