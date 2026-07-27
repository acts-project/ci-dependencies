# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import shutil

from spack_repo.acts.packages.tensorrt.deb import extract_deb
from spack_repo.builtin.build_systems.generic import Package

from spack.package import *

# The pieces of TensorRT to unpack, as published by NVIDIA in its CUDA apt
# repository: {version: {runtime: [(deb file name, sha256), ...]}}. The debs and
# their checksums come straight from that repository's signed Packages index.
#
# Two sets, because the size difference is a factor of 25:
#
#   full  libnvinfer + plugins + ONNX parser. 1879 MB downloaded, 2628 MB
#         installed, essentially all of it libnvinfer itself, which carries the
#         builder and its kernels.
#   lean  the runtime-only library. 15 MB downloaded, 106 MB installed. It can
#         deserialize and execute engines but not build them, and only engines
#         that were built version compatible.
#
# `lean` is not merely `full` minus files: libnvinfer_plugin.so has a DT_NEEDED
# on libnvinfer.so, so anything linking the plugin library pulls the 2.5 GB back
# in. The lean set therefore ships no plugin library at all.
#
# Deliberately absent from both: the samples (208 MB), the Windows cross-build
# resources (1.9 GB), and the dispatch/safe runtimes.
#
# NVIDIA's apt repo rather than the rhel one: it carries a single consistent
# 11.1.0.106+cuda13.3 set that matches the toolkit this flavor pins, whereas the
# rhel repo mixes 10.13.2.6 headers with an 11.0.0.114 runtime, and its -devel
# package bundles static libraries (3.9 GB installed).
_debs = {
    "11.1.0.106": {
        "lean": [
            (
                "libnvinfer-headers-dev_11.1.0.106-1+cuda13.3_amd64.deb",
                "af7870ee878dc9afb5ed4236654bcb2663e133dca78432cfcafad74b36a1e88a",
            ),
            (
                "libnvinfer-lean11_11.1.0.106-1+cuda13.3_amd64.deb",
                "d05b87a67832cbc74ad255c5c7369f788ebe3ac08faeda1546c4296e6daec8a0",
            ),
            (
                "libnvinfer-lean-dev_11.1.0.106-1+cuda13.3_amd64.deb",
                "dd71ad47987778c2994de53a3aa7f7a3db90b8fa4854afa499943a6279da0fdf",
            ),
        ],
        "full": [
            (
                "libnvinfer11_11.1.0.106-1+cuda13.3_amd64.deb",
                "5df5c04749849f112bb8ba365dbd1db53f50e65f4ea7168c4237590abb1af135",
            ),
            (
                "libnvinfer-dev_11.1.0.106-1+cuda13.3_amd64.deb",
                "7cebe7266c185d9133544465480c798e0f39cca0b9c12e2c6947b6377a39e93b",
            ),
            (
                "libnvinfer-headers-dev_11.1.0.106-1+cuda13.3_amd64.deb",
                "af7870ee878dc9afb5ed4236654bcb2663e133dca78432cfcafad74b36a1e88a",
            ),
            (
                "libnvinfer-plugin11_11.1.0.106-1+cuda13.3_amd64.deb",
                "139f95af009e10a9d9d9c3e6da1d607625c82a2c3a727ff99e17a1cd09f52a9b",
            ),
            (
                "libnvinfer-plugin-dev_11.1.0.106-1+cuda13.3_amd64.deb",
                "cc4a630060bd3557453b72c119dcb170d1b453b312c8701518d3821b0a49dda3",
            ),
            (
                "libnvinfer-headers-plugin-dev_11.1.0.106-1+cuda13.3_amd64.deb",
                "75e1d44653ab0a313a801e1dff88821b1c8d814c9fa8f63b26b06fdd36472637",
            ),
            (
                "libnvonnxparsers11_11.1.0.106-1+cuda13.3_amd64.deb",
                "4f0a66d0e2d6e3f8baf928bb1c98c2e0c00a92737b6684ae601594a0417733ac",
            ),
            (
                "libnvonnxparsers-dev_11.1.0.106-1+cuda13.3_amd64.deb",
                "308bfb70a38f7262b57df9fe56d6ffc11746512c15c499dcbf9359dbc637377e",
            ),
        ],
    }
}
_REPO = "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64"


class Tensorrt(Package):
    """NVIDIA TensorRT, a high performance deep learning inference library.

    Upstream Spack has no `tensorrt` package, and NVIDIA's own tarball needs a
    developer login, so this unpacks the debs from the CUDA apt repository —
    which is public, versioned and checksummed — into a plain prefix that a
    `find_path(NvInfer.h)` / `find_library(nvinfer...)` module resolves.

    `runtime=lean` cuts this from 2.6 GB to 106 MB and is enough to deserialize
    and run prebuilt engines, which is all ACTS does with TensorRT. It is not a
    drop-in for `full`, though: it drops the plugin library (whose DT_NEEDED on
    libnvinfer would pull the 2.5 GB straight back in), so a consumer must not
    call initLibNvInferPlugins or link trt::nvinfer_plugin, must link
    nvinfer_lean instead of nvinfer, and must feed it engines that were built
    version compatible.
    """

    homepage = "https://developer.nvidia.com/tensorrt"
    url = "{0}/{1}".format(_REPO, _debs["11.1.0.106"]["full"][0][0])
    maintainers("paulgessinger")

    license("LicenseRef-NVIDIA-TensorRT-EULA")

    # Prebuilt NVIDIA binaries: they RPATH nothing useful and expect the CUDA
    # runtime from the loader path, which the cuda dependency below provides.
    unresolved_libraries = ["*"]
    conflicts("platform=darwin", msg="TensorRT is linux only")
    conflicts("target=aarch64:", msg="this package packs the x86_64 debs only")

    variant(
        "runtime",
        values=("full", "lean"),
        multi=False,
        default="full",
        description="full: builder + plugins + ONNX parser (2.6 GB); "
        "lean: run prebuilt version-compatible engines only (106 MB)",
    )

    # The debs are built against a specific CUDA major version (+cuda13.3 here),
    # and libnvinfer links libcudart.so.13 directly.
    depends_on("cuda@13", type=("build", "link", "run"))

    for _version, _runtimes in _debs.items():
        # The version's own fetch has to be a deb both sets need, and a small
        # one: it is downloaded before `runtime` is consulted, so pinning it to
        # libnvinfer itself would cost `lean` the 1.9 GB it exists to avoid.
        # The headers package is 114 KB and common to both.
        _main = next(d for d in _runtimes["lean"] if d[0].startswith("libnvinfer-headers-dev"))
        version(
            _version,
            sha256=_main[1],
            url="{0}/{1}".format(_REPO, _main[0]),
            expand=False,  # a .deb is not an archive spack can unpack itself
        )
        for _runtime, _components in _runtimes.items():
            for _deb, _sha256 in _components:
                if _deb == _main[0]:
                    continue
                resource(
                    name="{0}-{1}".format(_runtime, _deb.split("_")[0]),
                    url="{0}/{1}".format(_REPO, _deb),
                    sha256=_sha256,
                    expand=False,
                    destination="debs",
                    placement="{0}-{1}".format(_runtime, _deb.split("_")[0]),
                    when="@{0} runtime={1}".format(_version, _runtime),
                )

    def install(self, spec, prefix):
        # Unpack inside the prefix so promoting the tree is a rename rather than
        # a 2.6 GB copy.
        staging = os.path.join(prefix, ".deb-staging")
        debs = [
            os.path.join(root, f)
            for root, _, files in os.walk(self.stage.source_path)
            for f in sorted(files)
            if f.endswith(".deb")
        ]
        runtime = spec.variants["runtime"].value
        expected = len(_debs[str(spec.version)][runtime])
        if len(debs) != expected:
            raise InstallError(
                "expected {0} debs for runtime={1}, found {2}: {3}".format(
                    expected, runtime, len(debs), debs
                )
            )
        for deb in sorted(debs):
            tty.info("unpacking {0}".format(os.path.basename(deb)))
            extract_deb(deb, staging)

        # Debian multiarch puts everything under a triplet subdirectory; flatten
        # it into the usual <prefix>/{include,lib} that find_path/find_library
        # look at without needing hints.
        for src, dst in (
            (os.path.join(staging, "usr", "include", "x86_64-linux-gnu"), prefix.include),
            (os.path.join(staging, "usr", "lib", "x86_64-linux-gnu"), prefix.lib),
        ):
            if not os.path.isdir(src):
                raise InstallError("the debs did not unpack to {0}".format(src))
            os.makedirs(dst, exist_ok=True)
            for entry in os.listdir(src):
                shutil.move(os.path.join(src, entry), os.path.join(dst, entry))
        shutil.rmtree(staging)

        # The unversioned .so of each component a consumer links, plus the
        # headers. Fail here rather than in a downstream cmake configure if
        # NVIDIA reshuffles the packaging.
        expected_files = [
            prefix.include.join("NvInfer.h"),
            prefix.include.join("NvInferRuntime.h"),
        ]
        if runtime == "lean":
            expected_files.append(prefix.lib.join("libnvinfer_lean.so"))
        else:
            expected_files += [
                prefix.include.join("NvInferPlugin.h"),
                prefix.include.join("NvOnnxParser.h"),
                prefix.lib.join("libnvinfer.so"),
                prefix.lib.join("libnvinfer_plugin.so"),
                prefix.lib.join("libnvonnxparser.so"),
            ]
        for path in expected_files:
            if not os.path.exists(path):
                raise InstallError("missing from the installed TensorRT tree: {0}".format(path))

    def setup_run_environment(self, env: EnvironmentModifications) -> None:
        env.set("TENSORRT_ROOT", self.prefix)
        env.prepend_path("LD_LIBRARY_PATH", self.prefix.lib)

    def setup_dependent_build_environment(
        self, env: EnvironmentModifications, dependent_spec: Spec
    ) -> None:
        # ACTS' FindTensorRT.cmake searches TensorRT_ROOT / TENSORRT_ROOT before
        # the default paths.
        env.set("TENSORRT_ROOT", self.prefix)
