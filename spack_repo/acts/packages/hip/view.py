"""Helpers for exposing part of one prefix through another.

`llvm-amdgpu` and `hsa-rocr-dev` are components of the single ROCm root that
the `hip` package installs, so their prefixes are symlink farms over it.

The farms link *files*, under real directories, rather than symlinking whole
directories: Spack's environment view merges prefixes, and a symlinked
directory where another prefix has a real one is a fatal merge conflict. Two
symlinks that resolve to the same file are not a conflict at all, so a
file-level farm merges cleanly.
"""

import glob as _glob
import os


def _link_dir(src, dst):
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        src_entry = os.path.join(src, name)
        dst_entry = os.path.join(dst, name)
        if os.path.isdir(src_entry) and not os.path.islink(src_entry):
            _link_dir(src_entry, dst_entry)
        else:
            # files and symlinks alike are linked back to the source prefix
            os.symlink(src_entry, dst_entry)


def symlink_tree(src_root, dst_root, entries):
    """Mirror `entries` of `src_root` into `dst_root`.

    Each entry is a path relative to `src_root` and may be a glob. Directories
    are recreated as real directories whose contents are symlinked; everything
    else is symlinked directly. An entry that matches nothing raises, so a
    change in AMD's layout fails the build instead of quietly installing an
    incomplete prefix.
    """
    for entry in entries:
        matches = _glob.glob(os.path.join(src_root, entry))
        if not matches:
            raise RuntimeError("nothing matches {0} under {1}".format(entry, src_root))
        for src in matches:
            dst = os.path.join(dst_root, os.path.relpath(src, src_root))
            if os.path.isdir(src) and not os.path.islink(src):
                _link_dir(src, dst)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.symlink(src, dst)
