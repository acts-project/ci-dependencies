# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack.package import *
from spack_repo.builtin.packages.openblas.package import Openblas as OpenblasBase

from spack.package import *


# Temporary until https://github.com/spack/spack-packages/pull/4656 lands
class Openblas(OpenblasBase):
    # https://github.com/OpenMathLib/OpenBLAS/pull/5796
    patch(
        "https://github.com/OpenMathLib/OpenBLAS/commit/88705a932831c0de1ed136b461c6c239802828b2.diff?full_index=1",
        when="@0.3.32",
        sha256="723ddc1553b6d27ff89d96985f7732695935c0d4d8df766987702689bdb750ac",
    )
