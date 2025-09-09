#!/usr/bin/env python3

# /// script
# dependencies = [
#   "typer",
#   "packaging",
# ]
# ///

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Optional, List
import plistlib

import typer
from packaging import version


class XcodeVersion(NamedTuple):
    version: str
    build: str
    path: Path
    is_beta: bool


def find_installed_xcode_apps() -> List[XcodeVersion]:
    """Find all Xcode installations in /Applications."""
    apps_dir = Path("/Applications")
    if not apps_dir.exists():
        return []

    xcode_apps = []
    seen_targets = set()  # Track resolved paths to avoid duplicates
    xcode_pattern = re.compile(r"^Xcode.*\.app$", re.IGNORECASE)

    for app_path in apps_dir.iterdir():
        if not app_path.is_dir() or not xcode_pattern.match(app_path.name):
            continue

        # Resolve symlinks to get the actual target
        resolved_path = app_path.resolve()
        
        # Skip if we've already seen this target
        if resolved_path in seen_targets:
            continue
        seen_targets.add(resolved_path)

        info_plist = resolved_path / "Contents" / "Info.plist"
        if not info_plist.exists():
            continue

        try:
            with open(info_plist, "rb") as f:
                plist_data = plistlib.load(f)

            bundle_version = plist_data.get("CFBundleShortVersionString", "")
            build_version = plist_data.get("DTXcodeBuild", "")

            if not bundle_version:
                continue

            # Check if it's a beta version (check both original and resolved paths)
            is_beta = (
                "beta" in bundle_version.lower() 
                or "beta" in app_path.name.lower()
                or "beta" in resolved_path.name.lower()
            )

            # Use the resolved path as the canonical path
            xcode_apps.append(
                XcodeVersion(
                    version=bundle_version,
                    build=build_version,
                    path=resolved_path,
                    is_beta=is_beta,
                )
            )

        except Exception as e:
            typer.echo(f"Warning: Could not parse {app_path}: {e}", err=True)
            continue

    # Sort by version (newest first)
    try:
        xcode_apps.sort(key=lambda x: version.parse(x.version.split()[0]), reverse=True)
    except Exception:
        # Fallback to string sorting if version parsing fails
        xcode_apps.sort(key=lambda x: x.version, reverse=True)

    return xcode_apps


def normalize_version(ver: str) -> str:
    """Normalize version string for comparison (e.g., '16.4' -> '16.4.0')."""
    # Split on space to handle "16.4 Beta" format
    version_part = ver.split()[0]
    
    # Split version into parts
    parts = version_part.split('.')
    
    # Ensure we have at least major.minor.patch
    while len(parts) < 3:
        parts.append('0')
    
    return '.'.join(parts[:3])  # Take only major.minor.patch


def versions_match(target: str, candidate: str) -> bool:
    """Check if two versions match, handling different formats."""
    try:
        target_norm = normalize_version(target)
        candidate_norm = normalize_version(candidate)
        return version.parse(target_norm) == version.parse(candidate_norm)
    except Exception:
        # Fallback to string comparison
        return target.lower() == candidate.lower()


def find_xcode_version(
    target_version: str, xcode_apps: List[XcodeVersion]
) -> Optional[XcodeVersion]:
    """Find a specific Xcode version."""
    if target_version == "latest":
        return xcode_apps[0] if xcode_apps else None

    if target_version == "latest-stable":
        for app in xcode_apps:
            if not app.is_beta:
                return app
        return None

    # Try exact match first
    for app in xcode_apps:
        if app.version == target_version:
            return app

    # Try normalized version matching (handles 16.4 vs 16.4.0)
    for app in xcode_apps:
        if versions_match(target_version, app.version):
            return app

    # Try version prefix matching
    for app in xcode_apps:
        if app.version.startswith(target_version):
            return app

    return None


def is_github_actions() -> bool:
    """Check if running in GitHub Actions."""
    return os.getenv("GITHUB_ACTIONS") == "true"


app = typer.Typer()


@app.command()
def list_versions():
    """List all installed Xcode versions."""
    xcode_apps = find_installed_xcode_apps()

    if not xcode_apps:
        typer.echo("No Xcode installations found.")
        return

    typer.echo("Found Xcode installations:")
    for xcode_app in xcode_apps:
        beta_marker = " (Beta)" if xcode_app.is_beta else ""
        typer.echo(
            f"  {xcode_app.version} ({xcode_app.build}){beta_marker} - {xcode_app.path}"
        )


@app.command()
def select(
    version_spec: str = typer.Argument(
        ..., help="Version to select (e.g., '15.0', 'latest', 'latest-stable')"
    ),
    cleanup: bool = typer.Option(
        False,
        "--cleanup",
        help="🚨 DANGER: Remove other Xcode versions and move selected to /Applications/Xcode.app",
    ),
    force_cleanup: bool = typer.Option(
        False,
        "--force-cleanup",
        help="🚨🚨 SUPER DANGER: Force cleanup even outside GitHub Actions",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be done without making any changes"
    ),
):
    """Select an Xcode version and optionally clean up others."""

    # Safety check for cleanup
    if cleanup and not is_github_actions() and not force_cleanup and not dry_run:
        typer.echo(
            "🚨 SAFETY CHECK: --cleanup is only allowed in GitHub Actions!", err=True
        )
        typer.echo(
            "If you really want to do this locally, use --force-cleanup", err=True
        )
        typer.echo(
            "Or use --dry-run to see what would happen without making changes", err=True
        )
        typer.echo("This will DELETE other Xcode installations!", err=True)
        raise typer.Exit(1)

    xcode_apps = find_installed_xcode_apps()

    if not xcode_apps:
        typer.echo("No Xcode installations found.", err=True)
        raise typer.Exit(1)

    selected = find_xcode_version(version_spec, xcode_apps)

    if not selected:
        typer.echo(f"Xcode version '{version_spec}' not found.", err=True)
        typer.echo("Available versions:")
        for app in xcode_apps:
            beta_marker = " (Beta)" if app.is_beta else ""
            typer.echo(f"  {app.version}{beta_marker}")
        raise typer.Exit(1)

    typer.echo(
        f"Selected Xcode {selected.version} ({selected.build}) at {selected.path}"
    )

    # Determine final path after cleanup (if any)
    final_path = selected.path
    if cleanup:
        canonical_path = Path("/Applications/Xcode.app")
        if selected.path != canonical_path:
            final_path = canonical_path

    # Set as active Xcode (skip if cleanup will handle it)
    if not cleanup:
        developer_dir = final_path / "Contents" / "Developer"
        if dry_run:
            typer.echo(f"[DRY RUN] Would run: sudo xcode-select --switch {developer_dir}")
        else:
            try:
                cmd = ["sudo", "xcode-select", "--switch", str(developer_dir)]
                typer.echo(f"Running: {' '.join(cmd)}")
                subprocess.run(cmd, check=True)
                typer.echo(f"Set active Xcode developer directory to {developer_dir}")
            except subprocess.CalledProcessError as e:
                typer.echo(f"Failed to set Xcode developer directory: {e}", err=True)
                raise typer.Exit(1)

    if cleanup:
        typer.echo("🔧 Ensuring canonical path points to selected Xcode...")

        canonical_path = Path("/Applications/Xcode.app")

        # Check if canonical path already points to the selected Xcode
        if canonical_path.exists():
            if canonical_path.is_symlink():
                current_target = canonical_path.resolve()
                if current_target == selected.path:
                    if dry_run:
                        typer.echo("[DRY RUN] Canonical path already points to selected Xcode")
                    else:
                        typer.echo("✅ Canonical path already points to selected Xcode")
                else:
                    # Remove existing symlink and create new one
                    if dry_run:
                        typer.echo(f"[DRY RUN] Would remove existing symlink {canonical_path} -> {current_target}")
                        typer.echo(f"[DRY RUN] Would create symlink {canonical_path} -> {selected.path}")
                    else:
                        typer.echo(f"Removing existing symlink {canonical_path} -> {current_target}")
                        canonical_path.unlink()
                        typer.echo(f"Creating symlink {canonical_path} -> {selected.path}")
                        canonical_path.symlink_to(selected.path)
                        typer.echo("✅ Canonical path updated!")
            else:
                # Canonical path exists but is not a symlink
                if canonical_path.resolve() == selected.path:
                    if dry_run:
                        typer.echo("[DRY RUN] Selected Xcode is already at canonical location")
                    else:
                        typer.echo("✅ Selected Xcode is already at canonical location")
                else:
                    typer.echo(f"ERROR: {canonical_path} exists but is not a symlink and not the selected Xcode!", err=True)
                    typer.echo("Cannot safely replace it. Please remove it manually first.", err=True)
                    raise typer.Exit(1)
        else:
            # Create new symlink
            if dry_run:
                typer.echo(f"[DRY RUN] Would create symlink {canonical_path} -> {selected.path}")
            else:
                typer.echo(f"Creating symlink {canonical_path} -> {selected.path}")
                canonical_path.symlink_to(selected.path)
                typer.echo("✅ Canonical path created!")
        
        # Set as active Xcode after cleanup
        developer_dir = final_path / "Contents" / "Developer"
        if dry_run:
            typer.echo(f"[DRY RUN] Would run: sudo xcode-select --switch {developer_dir}")
        else:
            try:
                cmd = ["sudo", "xcode-select", "--switch", str(developer_dir)]
                typer.echo(f"Running: {' '.join(cmd)}")
                subprocess.run(cmd, check=True)
                typer.echo(f"Set active Xcode developer directory to {developer_dir}")
            except subprocess.CalledProcessError as e:
                typer.echo(f"Failed to set Xcode developer directory: {e}", err=True)
                raise typer.Exit(1)

    # Verify selection
    if dry_run:
        typer.echo("[DRY RUN] Would verify selection with: xcode-select --print-path")
    else:
        try:
            result = subprocess.run(
                ["xcode-select", "--print-path"],
                capture_output=True,
                text=True,
                check=True,
            )
            typer.echo(f"Active Xcode: {result.stdout.strip()}")
        except subprocess.CalledProcessError:
            typer.echo("Warning: Could not verify Xcode selection", err=True)


if __name__ == "__main__":
    app()

