# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from typing import Optional

from spack_repo.builtin.build_systems.generic import Package

from spack.package import *
from spack.package import version as version_directive
import spack.spec
from spack.util.prefix import Prefix


def _gpu_asset(version: str, cuda_major: str) -> str:
    """Name of the linux x86_64 GPU tarball for a release.

    Upstream ships one GPU build per CUDA major version, and has renamed the
    assets twice: `gpu_cuda13` appears from 1.24.4, and from 1.27.0 the CUDA 12
    build is `gpu_cuda12` rather than the historical bare `gpu`. Deriving the
    name here means a version bump fails on a checksum rather than on a 404
    from a stale hardcoded asset name.
    """
    if cuda_major == "13":
        return f"onnxruntime-linux-x64-gpu_cuda13-{version}.tgz"
    if Version(version) >= Version("1.27.0"):
        return f"onnxruntime-linux-x64-gpu_cuda12-{version}.tgz"
    return f"onnxruntime-linux-x64-gpu-{version}.tgz"


def _add_version(
    version: str,
    sha256: str,
    sha256_linux_x86_64: Optional[str] = None,
    sha256_linux_x86_64_gpu: Optional[str] = None,
    sha256_linux_x86_64_gpu_cuda13: Optional[str] = None,
    sha256_linux_aarch64: Optional[str] = None,
    sha256_darwin_aarch64: Optional[str] = None,
    sha256_darwin_x86_64: Optional[str] = None,
):

    version_directive(version, sha256=sha256)

    if sha256_linux_x86_64_gpu is not None:
        resource(
            name=f"linux-x86_64-gpu-v{version}",
            url=f"https://github.com/microsoft/onnxruntime/releases/download/v{version}/{_gpu_asset(version, '12')}",
            sha256=sha256_linux_x86_64_gpu,
            when=f"@{version} +gpu cuda_major=12 platform=linux target=x86_64:",
            destination="./",
            placement="binaries",
        )

    if sha256_linux_x86_64_gpu_cuda13 is not None:
        resource(
            name=f"linux-x86_64-gpu-cuda13-v{version}",
            url=f"https://github.com/microsoft/onnxruntime/releases/download/v{version}/{_gpu_asset(version, '13')}",
            sha256=sha256_linux_x86_64_gpu_cuda13,
            when=f"@{version} +gpu cuda_major=13 platform=linux target=x86_64:",
            destination="./",
            placement="binaries",
        )
    else:
        # No CUDA 13 tarball upstream (releases before 1.24.4); asking for one
        # should fail at concretization, not download something for CUDA 12.
        conflicts(
            "cuda_major=13",
            when=f"@{version}",
            msg=f"onnxruntime {version} has no CUDA 13 build upstream (1.24.4 is the first)",
        )

    if sha256_linux_x86_64 is not None:
        resource(
            name=f"linux-x86_64-v{version}",
            url=f"https://github.com/microsoft/onnxruntime/releases/download/v{version}/onnxruntime-linux-x64-{version}.tgz",
            sha256=sha256_linux_x86_64,
            when=f"@{version} ~gpu platform=linux target=x86_64:",
            destination="./",
            placement="binaries",
        )

    if sha256_linux_aarch64 is not None:
        resource(
            name=f"linux-aarch64-v{version}",
            url=f"https://github.com/microsoft/onnxruntime/releases/download/v{version}/onnxruntime-linux-aarch64-{version}.tgz",
            sha256=sha256_linux_aarch64,
            when=f"@{version} ~gpu platform=linux target=aarch64:",
            destination="./",
            placement="binaries",
        )

    if sha256_darwin_aarch64 is not None:
        resource(
            name=f"osx-arm64-{version}",
            url=f"https://github.com/microsoft/onnxruntime/releases/download/v{version}/onnxruntime-osx-arm64-{version}.tgz",
            sha256=sha256_darwin_aarch64,
            when=f"@{version} ~gpu platform=darwin target=aarch64:",
            destination="./",
            placement="binaries",
        )

    if sha256_darwin_x86_64 is not None:
        resource(
            name=f"osx-x86_64-{version}",
            url=f"https://github.com/microsoft/onnxruntime/releases/download/v{version}/onnxruntime-osx-x86_64-{version}.tgz",
            sha256=sha256_darwin_x86_64,
            when=f"@{version} ~gpu platform=darwin target=x86_64:",
            destination="./",
            placement="binaries",
        )


class OnnxruntimeBin(Package):
    homepage = "https://github.com/microsoft/onnxruntime"
    # git = "https://github.com/microsoft/onnxruntime.git"
    url = "https://github.com/microsoft/onnxruntime/archive/refs/tags/v1.21.0.tar.gz"

    license("MIT")

    # version("1.22.2", tag="v1.22.2", commit="5630b081cd25e4eccc7516a652ff956e51676794")
    # Source versions are not used

    variant(
        "gpu",
        default=True,
        when="platform=linux target=x86_64:",
        description="Install with GPU support",
    )

    variant(
        "gpu",
        default=False,
        when="platform=darwin",
        description="Install with GPU support",
    )

    variant(
        "gpu",
        default=False,
        when="platform=linux target=aarch64:",
        description="Install with GPU support",
    )

    # variant(
    #     "gpu",
    #     default=True,
    #     when="platform=linux",
    #     description="Install with GPU support",
    # )

    # Which CUDA the prebuilt CUDA execution provider links against. It is not a
    # `depends_on`: `+gpu` is the default on linux x86_64, and making the plain
    # CPU stack drag in a CUDA toolkit for a provider it never loads would be a
    # poor trade. A flavor that wants the EP to actually load supplies cuda and
    # cudnn itself (see flavors/cuda75.*).
    variant(
        "cuda_major",
        values=("12", "13"),
        multi=False,
        default="12",
        when="+gpu",
        description="CUDA major version the GPU (CUDA EP) build targets",
    )

    conflicts(
        "+gpu", when="platform=darwin", msg="GPU variant is not supported on macOS"
    )
    conflicts(
        "+gpu",
        when="platform=linux target=aarch64:",
        msg="GPU variant is not supported on macOS",
    )

    _add_version(
        "1.21.0",
        sha256="b395c72e0e6c6cb28525f4617bc8477c99cf65659c20439fe955c511c2da2cd9",
        sha256_darwin_aarch64="5c3f2064ee97eb7774e87f396735c8eada7287734f1bb7847467ad30d4036115",
        sha256_darwin_x86_64="8305afd2d75ee5702844a23b099d41885af30ad3d1b4cf3d8d795e3d8c1f9396",
        sha256_linux_aarch64="4508084bde1232ee1ab4b6fad2155be0ea2ccab1c1aae9910ddb3fb68a60805e",
        sha256_linux_x86_64="7485c7e7aac6501b27e353dcbe068e45c61ab51fbaf598d13970dfae669d20bf",
        sha256_linux_x86_64_gpu="ef37a33ba75e457aebfd0d7b342ab20424aa6126bc5f565d247f1201b66996cf",
    )

    _add_version(
        "1.22.0",
        sha256="08b078eb7afbf376064b2b0f1781e3d78151cac0592988a0c0ec78bf72fde810",
        sha256_darwin_aarch64="cab6dcbd77e7ec775390e7b73a8939d45fec3379b017c7cb74f5b204c1a1cc07",
        sha256_darwin_x86_64="e4ec94a7696de74fb1b12846569aa94e499958af6ffa186022cfde16c9d617f0",
        sha256_linux_aarch64="bb76395092d150b52c7092dc6b8f2fe4d80f0f3bf0416d2f269193e347e24702",
        sha256_linux_x86_64="8344d55f93d5bc5021ce342db50f62079daf39aaafb5d311a451846228be49b3",
        sha256_linux_x86_64_gpu="2a19dbfa403672ec27378c3d40a68f793ac7a6327712cd0e8240a86be2b10c55",
    )

    _add_version(
        "1.23.0",
        sha256="907c99c9c66f31bc3c6f7dada064c84c3b97b9c47128ca6d76288edb99390110",
        sha256_darwin_aarch64="8182db0ebb5caa21036a3c78178f17fabb98a7916bdab454467c8f4cf34bcfdf",
        sha256_darwin_x86_64="a8e43edcaa349cbfc51578a7fc61ea2b88793ccf077b4bc65aca58999d20cf0f",
        sha256_linux_aarch64="0b9f47d140411d938e47915824d8daaa424df95a88b5f1fc843172a75168f7a0",
        sha256_linux_x86_64="b6deea7f2e22c10c043019f294a0ea4d2a6c0ae52a009c34847640db75ec5580",
        sha256_linux_x86_64_gpu="fc5d0e2dbdb893de11758da83523169761a70562dc0f6991f2cdb614f6a62c3d",
    )

    _add_version(
        "1.24.4",
        sha256="0cf4d2ee4392fbb8aedaabc6b2ba11b4a680d1071fa4f75546c2289ca5b404cf",
        sha256_darwin_aarch64="93787795f47e1eee369182e43ed51b9e5da0878ab0346aecf4258979b8bba989",
        # sha256_darwin_x86_64 not available for this version
        sha256_linux_aarch64="866109a9248d057671a039b9d725be4bd86888e3754140e6701ec621be9d4d7e",
        sha256_linux_x86_64="3a211fbea252c1e66290658f1b735b772056149f28321e71c308942cdb54b747",
        sha256_linux_x86_64_gpu="c5f804ff5d239b436fa59e9f2fb288a39f7eb9552f6a636c8b71e792e91a8808",
        sha256_linux_x86_64_gpu_cuda13="fdc6eb18317b4eaeda8b3b86595e5da7e853f72bac67ccac9b04ffc20c9f7fe0",
    )

    def install(self, spec: spack.spec.Spec, prefix: Prefix) -> None:
        install_tree("./binaries", prefix)
