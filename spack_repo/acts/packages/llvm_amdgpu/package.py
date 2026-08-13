# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os

from spack_repo.acts.packages.hip.view import symlink_tree
from spack_repo.builtin.build_systems.compiler import CompilerPackage
from spack_repo.builtin.build_systems.generic import Package

from spack.package import *


class LlvmAmdgpu(Package, CompilerPackage):
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

    version("7.2.3")

    depends_on("hip@7.2.3", when="@7.2.3")

    # Accepted for compatibility with packages that spell out the builtin
    # package's variants (the device libraries are always installed here).
    variant("rocm-device-libs", default=True, description="Build ROCm device libraries")
    conflicts("~rocm-device-libs", msg="the ROCm binary distribution always ships the device libs")

    # The builtin package carries the `compiler` tag, and Spack collects its
    # compilers by tag across *all* repos while resolving the name to the
    # highest-precedence one -- this package. So once it is installed, the
    # solver interrogates it as a compiler (CompilerPropertyDetector runs `cc`
    # to detect libc) for any concretization that considers a compiler other
    # than the pinned one, and without the interface below that is an
    # AttributeError rather than a solve. The prefix really is an AMD clang
    # install, so answering these truthfully is also the honest thing to do.
    # Fortran is left out deliberately. AMD does ship flang from 7.0 on (this
    # prefix has bin/flang and bin/amdflang, and builtin declares fortran for
    # @7.0:), but nothing in this stack needs a HIP-side Fortran, and declaring
    # it would let the solver satisfy the *environment's* unconstrained fortran
    # virtual with amdflang instead of gcc.
    provides("c", "cxx")
    compiler_languages = ["c", "cxx"]
    c_names = ["amdclang"]
    cxx_names = ["amdclang++"]
    compiler_wrapper_link_paths = {"c": "rocmcc/amdclang", "cxx": "rocmcc/amdclang++"}
    stdcxx_libs = ("-lstdc++",)

    def _cc_path(self):
        return os.path.join(self.spec.prefix.bin, "amdclang")

    def _cxx_path(self):
        return os.path.join(self.spec.prefix.bin, "amdclang++")

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
