# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os

from spack_repo.acts.packages.hip.view import symlink_tree
from spack_repo.builtin.build_systems.generic import Package

from spack.package import *


class LlvmAmdgpu(Package):
    """AMD's fork of LLVM, as shipped in the ROCm binary distribution.

    A view into the `hip` package in this repo, which unpacks AMD's rpms into a
    single /opt/rocm-style prefix. `ROCmPackage` (covfie +rocm, and any other
    consumer of Spack's ROCm build system) has a hard `depends_on("llvm-amdgpu")`
    on this name, and the builtin package builds AMD's clang fork from source --
    hours of CPU and more disk than a CI runner has. This prefix is a symlink
    farm over the LLVM tree that `hip` already installed, so clang, lld, the
    device libraries and the AMDDeviceLibs CMake package are all where a
    consumer expects them without a second copy.

    Deliberately not a standalone package: the ROCm binaries RPATH each other
    within one ROCm root, so splitting them across prefixes would break them.
    """

    homepage = "https://github.com/ROCm/llvm-project"
    maintainers("paulgessinger")

    license("Apache-2.0")

    # All content comes from `hip`; there is nothing to fetch here.
    has_code = False

    version("6.4.3")

    depends_on("hip@6.4.3", when="@6.4.3")

    # Accepted for compatibility with packages that spell out the builtin
    # package's variants (the device libraries are always installed here).
    variant("rocm-device-libs", default=True, description="Build ROCm device libraries")
    conflicts("~rocm-device-libs", msg="the ROCm binary distribution always ships the device libs")

    def install(self, spec, prefix):
        # AMD keeps its LLVM under <rocm root>/lib/llvm; that subtree shares no
        # relative path with the rest of the ROCm root, so mirroring it here
        # cannot collide with `hip` in an environment view.
        symlink_tree(spec["hip"].prefix.lib.llvm, prefix, ["*"])
        # Consumers (`hip` included) look for the device library bitcode under
        # <llvm-amdgpu prefix>/amdgcn/bitcode, which in AMD's layout hangs off
        # the ROCm root rather than off the LLVM tree. Left as a plain symlink
        # to match `hip`'s own `amdgcn` link: they resolve to the same
        # directory, which a view treats as one entry rather than a conflict.
        os.symlink(spec["hip"].prefix.amdgcn, os.path.join(prefix, "amdgcn"))

    def setup_run_environment(self, env: EnvironmentModifications) -> None:
        env.set("LLVM_PATH", self.prefix)
