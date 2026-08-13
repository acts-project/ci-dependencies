# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.acts.packages.hip.view import symlink_tree
from spack_repo.builtin.build_systems.generic import Package

from spack.package import *


class Comgr(Package):
    """AMD's Code Object Manager (COMGR), as shipped in the ROCm binary
    distribution.

    A view into the `hip` package in this repo, for the same reason as
    `llvm-amdgpu` and `hsa-rocr-dev`, but reached by a different route: nothing
    in the ROCm build system depends on comgr directly — `rocprim` does, and
    `rocthrust` pulls `rocprim`. Without this override that one edge would drag
    the whole source stack back in.

    The builtin package builds comgr out of the full ROCm llvm-project tarball
    and links LLVM statically, which is expensive on its own. It also cannot
    work against the binary toolchain here: it wants clang's CMake package at
    `<llvm-amdgpu prefix>/lib/cmake/clang`, and AMD's rocm-llvm rpm ships a
    toolchain rather than an LLVM SDK, so that directory does not exist. Source
    builds of comgr are therefore not merely slow in this stack, they fail.

    AMD's own comgr rpm is already unpacked by `hip`, so the library, its header
    and the amd_comgr CMake package are all present; this prefix just points at
    them.
    """

    homepage = "https://github.com/ROCm/llvm-project"
    maintainers("paulgessinger")

    license("Apache-2.0")

    # All content comes from `hip`; there is nothing to fetch here.
    has_code = False

    version("7.2.3")

    depends_on("hip@7.2.3", when="@7.2.3")

    # Accepted for compatibility with packages that spell out the builtin
    # package's variants. AMD ships no sanitizer build in the binary rpms.
    variant("asan", default=False, description="Build with address-sanitizer enabled or disabled")
    conflicts("+asan", msg="the ROCm binary distribution ships no address-sanitizer build")

    # Exactly what AMD's comgr rpm drops into the ROCm root. The versioned
    # sonames matter: consumers link libamd_comgr.so but the runtime resolves
    # the .so.3 that hip's own libraries already carry in their DT_NEEDED.
    _contents = [
        "include/amd_comgr",
        "lib/libamd_comgr.so*",
        "lib/cmake/amd_comgr",
        "share/doc/amd_comgr",
    ]

    def install(self, spec, prefix):
        symlink_tree(spec["hip"].prefix, prefix, self._contents)
