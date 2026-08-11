# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import shutil

from spack_repo.acts.packages.hip.rpm import extract_rpm
from spack_repo.builtin.build_systems.generic import Package

from spack.package import *

# ROCm components, as published by AMD in rpm form. One dict per ROCm release:
# {component: (rpm file name, sha256)}. The first entry is used as the package's
# "version" download, the rest are fetched as resources; all of them are
# unpacked into the same prefix, which reproduces the /opt/rocm-x.y.z layout
# that AMD ships and that Spack already knows how to drive as an external ROCm.
#
# rhel8 rpms on purpose:
#   * their payload is xz, which `rpm.py` unpacks with the stdlib alone
#     (rhel9+ switched to zstd, which needs python 3.14 or an extra module), and
#   * they are built against glibc 2.28, so they run on every image in the CI
#     matrix (alma9/10, ubuntu 24.04/26.04) regardless of its glibc.
#
# This set is exactly what is needed to *compile* HIP code and link against the
# HIP runtime; the ML libraries (rocBLAS, MIOpen, ...) and the profiling and
# debugging tools are deliberately left out. `lib/cmake` ends up with hip,
# hip-lang, AMDDeviceLibs, amd_comgr, hsa-runtime64, hsakmt, hiprtc, rocm-core
# and rocprofiler-register, which covers every find_dependency() that
# hip-config.cmake and hip-lang-config.cmake issue.
_rpms = {
    "7.2.3": {
        "rocm-core": (
            "rocm-core-7.2.3.70203-90.el8.x86_64.rpm",
            "ce7297f96b610b56c203e1e77a11a6d5a66070bea2fb489ceb1b632cdcb894b3",
        ),
        "rocm-llvm": (
            "rocm-llvm-22.0.0.26084.70203-90.el8.x86_64.rpm",
            "88d896ccc5abc96aa253280d4a6a14744672bc4f940a7187f535f6da63b588ad",
        ),
        "rocm-device-libs": (
            "rocm-device-libs-1.0.0.70203-90.el8.x86_64.rpm",
            "5552e039c5b2ba1d453bfd63b563d0b53a2539bc8ce356292c29d69bc8681e5f",
        ),
        "hsa-rocr": (
            "hsa-rocr-1.18.0.70203-90.el8.x86_64.rpm",
            "4332bcc5d7e327a274cf7abce4b7836860a90dfa78a6476aa917e2275d8fa240",
        ),
        "hsa-rocr-devel": (
            "hsa-rocr-devel-1.18.0.70203-90.el8.x86_64.rpm",
            "eb520e8a12712c86b854183950b3fc56da4e480a8b505a72bc8649b59b2f20ca",
        ),
        "comgr": (
            "comgr-3.0.0.70203-90.el8.x86_64.rpm",
            "5631e6ec1b02825421422067a0d89a6aba2a8893fe8f851fea73dfa2d08e0136",
        ),
        "hip-runtime-amd": (
            "hip-runtime-amd-7.2.53211.70203-90.el8.x86_64.rpm",
            "51dde52277676e6536e68a718f3d27f37bc7edecbaddfc1da5f135ba3e99e487",
        ),
        "hip-devel": (
            "hip-devel-7.2.53211.70203-90.el8.x86_64.rpm",
            "be362c0667e052d5979a3ade7a4e7d883ee3648918a93375a77d06f9f23dd588",
        ),
        "hipcc": (
            "hipcc-1.1.1.70203-90.el8.x86_64.rpm",
            "39a213ae99e9dfcf88f48b7ba38f26214b5e195d50ac93bde69ce37a24319112",
        ),
        "rocprofiler-register": (
            "rocprofiler-register-0.6.0.70203-90.el8.x86_64.rpm",
            "2aa885c67e42ae7eafa14e462415c3fb031b2f6434356fd8223031cfafa28e21",
        ),
        "rocminfo": (
            "rocminfo-1.0.0.70203-90.el8.x86_64.rpm",
            "8e2aaf1940e4589b14398d164bb79907bfaaf075467498a1e630e0fa4a0ad48f",
        ),
    }
}


def _url(rocm_version, rpm):
    return "https://repo.radeon.com/rocm/rhel8/{0}/main/{1}".format(rocm_version, rpm)


class Hip(Package):
    """HIP is a C++ Runtime API and Kernel Language that allows developers to
    create portable applications for AMD and NVIDIA GPUs from single source
    code.

    This is a *binary* replacement for the builtin `hip` package. The builtin
    one builds the whole ROCm stack (llvm-amdgpu, comgr, hsa-rocr-dev, the
    runtime, ...) from source, which takes hours and more disk than a CI runner
    has. Downstream packages such as vecmem and covfie `depends_on("hip")`
    literally -- HIP is not a virtual -- so a lightweight package could not be
    swapped in under a different name; it has to be called `hip`.

    Unlike the builtin package, everything lives in one prefix: the prefix *is*
    the ROCm root (bin/, include/, lib/, llvm -> lib/llvm, amdgcn -> ...), the
    layout AMD builds, tests and RPATHs its binaries against. `llvm-amdgpu` and
    `hsa-rocr-dev` in this repo are thin views into it.
    """

    homepage = "https://github.com/ROCm/HIP"
    url = _url("7.2.3", _rpms["7.2.3"]["rocm-core"][0])
    maintainers("paulgessinger")

    license("MIT")

    # Prebuilt AMD binaries: x86_64 linux only. Their RPATHs only cover the ROCm
    # tree itself, so the post-install check would flag libnuma/libdrm/libelf,
    # which they expect the loader to find; the deps below provide them.
    unresolved_libraries = ["*"]
    conflicts("platform=darwin", msg="the ROCm binary distribution is linux only")
    conflicts("target=aarch64:", msg="the ROCm binary distribution is x86_64 only")

    # Kept for compatibility with the builtin package's variants: ROCmPackage
    # requires `hip +rocm`, and packages that ask for +cuda should get a clear
    # error rather than a silently AMD-only HIP.
    variant("rocm", default=True, description="Enable ROCm support")
    variant("cuda", default=False, description="Build with CUDA")
    conflicts("+cuda", msg="this binary hip package only provides the AMD (ROCm) platform")
    conflicts("~rocm", msg="this binary hip package only provides the AMD (ROCm) platform")

    # Shared libraries the ROCm binaries need but do not ship. They are found
    # via the dependent's RPATH, which Spack's compiler wrapper fills in from
    # these link dependencies.
    depends_on("elf", type="link")
    depends_on("numactl", type="link")
    depends_on("libdrm", type="link")
    depends_on("zlib-api", type="link")
    depends_on("zstd", type="link")

    for _version, _components in _rpms.items():
        _main = list(_components)[0]
        version(
            _version,
            sha256=_components[_main][1],
            url=_url(_version, _components[_main][0]),
            expand=False,
        )
        for _component, (_rpm, _sha256) in _components.items():
            if _component == _main:
                continue
            resource(
                name=_component,
                url=_url(_version, _rpm),
                sha256=_sha256,
                expand=False,
                destination="rpms",
                placement=_component,
                when="@{0}".format(_version),
            )

    @property
    def llvm_prefix(self):
        return self.prefix.llvm

    @property
    def bitcode_prefix(self):
        return self.prefix.amdgcn.bitcode

    def install(self, spec, prefix):
        # The rpms unpack to ./opt/rocm-<version>/... (plus /usr/lib/.build-id
        # aliases). Unpack inside the prefix so that promoting the ROCm root to
        # the prefix is a rename rather than a 2 GB copy.
        staging = os.path.join(prefix, ".rpm-staging")
        rpms = [
            os.path.join(root, f)
            for root, _, files in os.walk(self.stage.source_path)
            for f in sorted(files)
            if f.endswith(".rpm")
        ]
        expected = len(_rpms[str(spec.version)])
        if len(rpms) != expected:
            raise InstallError(
                "expected {0} rpms in the stage, found {1}: {2}".format(expected, len(rpms), rpms)
            )
        for rpm in sorted(rpms):
            tty.info("unpacking {0}".format(os.path.basename(rpm)))
            extract_rpm(rpm, staging)

        rocm_root = os.path.join(staging, "opt", "rocm-{0}".format(spec.version))
        if not os.path.isdir(rocm_root):
            raise InstallError("the rpms did not unpack to {0}".format(rocm_root))
        for entry in os.listdir(rocm_root):
            shutil.move(os.path.join(rocm_root, entry), os.path.join(prefix, entry))
        shutil.rmtree(staging)

        # Sanity check the pieces the environment below points at, so a bad rpm
        # set fails here rather than halfway through a downstream HIP build.
        for path in (
            self.prefix.bin.hipcc,
            self.prefix.lib.join("libamdhip64.so"),
            self.llvm_prefix.bin.join("clang++"),
            self.bitcode_prefix.join("ocml.bc"),
            self.prefix.lib.cmake.join("hip").join("hip-config.cmake"),
            self.prefix.lib.cmake.join("hip-lang").join("hip-lang-config.cmake"),
        ):
            if not os.path.exists(path):
                raise InstallError("missing from the installed ROCm tree: {0}".format(path))

    def set_variables(self, env: EnvironmentModifications) -> None:
        """The environment hipcc, clang and CMake's HIP language look at.

        Mirrors the builtin package, except that every path is inside this one
        prefix (this is the "external ROCm" case as far as the tools are
        concerned).
        """
        env.set("ROCM_PATH", self.prefix)
        env.set("HIP_PLATFORM", "amd")
        env.set("HIP_COMPILER", "clang")
        # bin directory where clang++ resides; also where CMake looks for the
        # HIP compiler when CMAKE_HIP_COMPILER is not set explicitly.
        env.set("HIP_CLANG_PATH", self.llvm_prefix.bin)
        env.set("HSA_PATH", self.prefix)
        env.set("ROCMINFO_PATH", self.prefix)
        # used by hipcc to run `clang --hip-device-lib-path=...`
        env.set("DEVICE_LIB_PATH", self.bitcode_prefix)
        # and by clang when --hip-device-lib-path is not passed
        env.set("HIP_DEVICE_LIB_PATH", self.bitcode_prefix)
        # used by comgr, and needed by the JIT compiler (hiprtcCreateProgram)
        env.set("LLVM_PATH", self.llvm_prefix)
        env.set("COMGR_PATH", self.prefix)
        env.prepend_path("LD_LIBRARY_PATH", self.prefix.lib)
        # Dependents pick these up through the RPATH Spack builds from the link
        # dependencies, but the tools *in this prefix* (rocminfo, and anything
        # dlopen-ing the runtime) only have the loader path -- and e.g. the
        # ubuntu 26.04 CI image ships no libnuma at all.
        for dep in ("elf", "numactl", "libdrm", "zlib-api", "zstd"):
            env.prepend_path("LD_LIBRARY_PATH", self.spec[dep].prefix.lib)

    def setup_build_environment(self, env: EnvironmentModifications) -> None:
        self.set_variables(env)

    def setup_run_environment(self, env: EnvironmentModifications) -> None:
        self.set_variables(env)

    def setup_dependent_build_environment(
        self, env: EnvironmentModifications, dependent_spec: Spec
    ) -> None:
        self.set_variables(env)
        # CMake's `enable_language(HIP)` otherwise picks the first clang++ on
        # PATH, which on an image that ships its own clang (or a ccache shim)
        # is not AMD's; HIPCXX is the env var it consults first.
        env.set("HIPCXX", self.llvm_prefix.bin.join("clang++"))
        # --rocm-path keeps clang from going looking in /opt/rocm, and
        # -isystem <prefix>/include gets it the rocm-core headers.
        env.set("HIPCC_COMPILE_FLAGS_APPEND", "")
        env.append_path(
            "HIPCC_COMPILE_FLAGS_APPEND", "--rocm-path={0}".format(self.prefix), separator=" "
        )
        env.append_path(
            "HIPCC_COMPILE_FLAGS_APPEND",
            "-isystem {0}".format(self.prefix.include),
            separator=" ",
        )
        env.append_path(
            "HIPCC_LINK_FLAGS_APPEND", "--rocm-path={0}".format(self.prefix), separator=" "
        )

        if "amdgpu_target" in dependent_spec.variants:
            arch = dependent_spec.variants["amdgpu_target"].value
            # some packages define their own amdgpu_target variant that is not multi
            if isinstance(arch, str):
                arch = [arch]
            if "none" not in arch and "auto" not in arch:
                env.set("HCC_AMDGPU_TARGET", ",".join(arch))

    def setup_dependent_package(self, module, dependent_spec):
        self.spec.hipcc = join_path(self.prefix.bin, "hipcc")
