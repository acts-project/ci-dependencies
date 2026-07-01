# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack.package import *
from spack_repo.builtin.packages.xxhash.package import Xxhash as XxhashBase


# Temporary until https://github.com/spack/spack-packages/pull/5410 lands.
# xxhash's static libxxhash.a is built without PIC, so ROOT 6.40 fails to link
# it into libCore.so ("relocation R_X86_64_32S ... recompile with -fPIC").
class Xxhash(XxhashBase):
    variant("pic", default=True, description="Enable position-independent code (PIC)")

    def flag_handler(self, name, flags):
        if name == "cflags" and self.spec.satisfies("+pic"):
            flags.append(self.compiler.cc_pic_flag)
        return (flags, None, None)
