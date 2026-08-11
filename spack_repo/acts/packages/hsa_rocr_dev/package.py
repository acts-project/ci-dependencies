# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.acts.packages.hip.view import symlink_tree
from spack_repo.builtin.build_systems.generic import Package

from spack.package import *


class HsaRocrDev(Package):
    """The HSA runtime for AMD GPUs (ROCr), as shipped in the ROCm binary
    distribution.

    A view into the `hip` package in this repo, for the same reason as
    `llvm-amdgpu`: `ROCmPackage` has a hard `depends_on("hsa-rocr-dev")`, and
    building it from source drags in the rest of the ROCm source stack. The
    runtime, its headers and the hsa-runtime64 CMake package are already in the
    ROCm root that `hip` installs, so this prefix just points at them.
    """

    homepage = "https://github.com/ROCm/ROCR-Runtime"
    maintainers("paulgessinger")

    license("NCSA")

    # All content comes from `hip`; there is nothing to fetch here.
    has_code = False

    version("7.2.3")

    depends_on("hip@7.2.3", when="@7.2.3")

    # Accepted for compatibility with packages that spell out the builtin
    # package's variants; AMD ships libhsa-runtime64 as a shared library.
    variant("shared", default=True, description="Build shared or static library")
    conflicts("~shared", msg="the ROCm binary distribution only ships shared libraries")

    # Exactly what AMD's hsa-rocr and hsa-rocr-devel rpms drop into the ROCm
    # root. These paths also exist in `hip`'s prefix, but a view resolves both
    # sides to the same file and keeps one entry, so the overlap is harmless.
    # `symlink_tree` raises on an entry that matches nothing, so this list has
    # to track AMD's layout exactly: the doc directory was `hsa-runtime64`
    # through 6.4.x and is `hsa-rocr` (the rpm name) from 7.x on.
    _contents = [
        "include/hsa",
        "include/hsakmt",
        "lib/libhsa-runtime64.so*",
        "lib/libhsakmt.a",
        "lib/cmake/hsa-runtime64",
        "lib/cmake/hsakmt",
        "lib/pkgconfig/libhsakmt.pc",
        "share/doc/hsa-rocr",
    ]

    def install(self, spec, prefix):
        symlink_tree(spec["hip"].prefix, prefix, self._contents)
