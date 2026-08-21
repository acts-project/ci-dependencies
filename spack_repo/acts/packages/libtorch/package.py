# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import os
import re
import shutil
import zipfile

from spack_repo.builtin.build_systems.generic import Package

from spack.package import *

# The prebuilt C++ distributions PyTorch publishes, per release:
# {version: {key: (path under https://download.pytorch.org/, sha256)}}, plus the
# sha256 of that release's LICENSE (see the `version` directive below for why
# that, of all things, is the version's own fetch). Keys are mapped onto specs
# by `_WHEN`. What upstream actually ships:
#
#   linux x86_64   the official libtorch zip, as a CPU build and one build per
#                  CUDA minor the release targets (2.13.0: cu126, cu129, cu130,
#                  cu132). `+cuda` takes the newest CUDA 13 one, because that is
#                  the toolkit the cuda13 flavor supplies; CUDA minor version
#                  compatibility covers the 13.2 -> 13.3 gap.
#   darwin arm64   a CPU-only zip. The macOS x86_64 zips stopped at 2.2.2, hence
#                  the conflict below.
#   linux aarch64  *no* libtorch zip at all, only wheels. A wheel's `torch/`
#                  subdirectory is the same C++ distribution (include/, lib/,
#                  share/cmake/Torch) with the Python package sitting next to
#                  it, and install() lifts out only that subtree, so it serves.
#                  The cp314 tag is arbitrary: the C++ payload is identical
#                  across them and only libtorch_python.so, which nothing here
#                  loads, is built per interpreter.
#
# Sizes, which are why only flavors/cuda13.specs asks for this and the base
# spack.yaml does not: CUDA 481 MB downloaded / 961 MB installed, CPU 121 / 454
# on linux x86_64, 88 / 361 on macOS, 147 / 551 for the aarch64 wheel (it
# bundles OpenBLAS and Arm Compute Library). The three CPU builds are carried
# here so the package is whole, not because anything asks for them yet.
#
# Deliberately absent: the ROCm build. It exists, and rocm7.2 would even match
# the rocm7 flavor, but it is a 6.1 GB download whose unpacked tree does
# not fit a CI runner, and ACTS has no HIP torch path to spend it on.
_DISTS = {
    "2.13.0": {
        "license": "bd018feef8825e88181c84eb7e3aa4eafb8f08a20d9fd6ef948569610c4a3e43",
        "linux-x86_64": (
            "libtorch/cpu/libtorch-shared-with-deps-2.13.0%2Bcpu.zip",
            "edbf4cbed78433d803e90a65f1752e57783d164bce66c95c0872b2ab8f5c159e",
        ),
        "linux-x86_64-cuda": (
            "libtorch/cu132/libtorch-shared-with-deps-2.13.0%2Bcu132.zip",
            "79fce57974149b92db6d662e382205c4e16fc73d21fd6fffcd3246288cfa74aa",
        ),
        "linux-aarch64": (
            "whl/cpu/torch-2.13.0%2Bcpu-cp314-cp314-manylinux_2_28_aarch64.whl",
            "ca021f9eb2f8345c83fa03e3a04587308afb8df71bd472670b3ece00df58621c",
        ),
        "darwin-aarch64": (
            "libtorch/cpu/libtorch-macos-arm64-2.13.0.zip",
            "1e10c6c4dc2764150c9fb2ad28e1889191302734e7f939108e5d2f22f21a06f8",
        ),
    }
}

# Which spec each artifact belongs to. Exactly one must match any concrete spec;
# a platform with no entry gets no resource and fails the archive check in
# install() rather than installing an empty prefix.
_WHEN = {
    "linux-x86_64": "~cuda platform=linux target=x86_64:",
    "linux-x86_64-cuda": "+cuda platform=linux target=x86_64:",
    "linux-aarch64": "platform=linux target=aarch64:",
    "darwin-aarch64": "platform=darwin target=aarch64:",
}


class Libtorch(Package):
    """PyTorch's prebuilt C++ distribution, which is what `find_package(Torch)`
    resolves against.

    Upstream Spack has no `libtorch`: it has `py-torch`, which builds all of
    PyTorch from source. That is hours of compute and more disk than a CI runner
    has, for a tree whose C++ half PyTorch already publishes as a signed-off
    binary release. This unpacks that release instead, the same trade as
    `onnxruntime-bin` and the ROCm packages in this repo.

    The install is the distribution's include/, lib/ and share/ verbatim, so
    `<prefix>/share/cmake/Torch/TorchConfig.cmake` sits where CMAKE_PREFIX_PATH
    will find it and TorchConfig's own `../../../` walk back up to the prefix
    lands on the right include/ and lib/.

    `find_package(Torch)` warns `library kineto not found` against any of these.
    That is upstream's: the shared distributions ship kineto's headers and link
    its objects into libtorch_cpu, but no libkineto of its own for
    TorchConfig.cmake to find. Nothing here drops it.
    """

    homepage = "https://pytorch.org/"
    maintainers("paulgessinger")

    license("BSD-3-Clause")

    # No CudaPackage inheritance, deliberately: it would bring a `cuda_arch`
    # variant that this package cannot honour. libtorch_cuda.so arrives with
    # whatever fatbin set PyTorch chose for the release, so exposing cuda_arch
    # would let flavors/cuda13.yaml's blanket `variants: [cuda_arch=75]` change
    # the spec hash without changing a byte of what gets installed.
    variant(
        "cuda",
        default=False,
        when="platform=linux target=x86_64:",
        description="use the CUDA build of libtorch rather than the CPU one",
    )

    conflicts(
        "platform=darwin target=x86_64:",
        msg="PyTorch published no macOS x86_64 libtorch after 2.2.2",
    )

    # Prebuilt binaries: nothing here was linked against a spack prefix, and the
    # CUDA build's RPATH points into pip's layout ($ORIGIN/../../nvidia/...),
    # which does not exist in an install tree. Same reasoning as `tensorrt`.
    unresolved_libraries = ["*"]

    with when("+cuda"):
        # What the CUDA build needs from outside its own prefix. The zip is named
        # "shared-with-deps" but the deps it bundles are torch's own; everything
        # NVIDIA is external:
        #
        #   cudart, cublas, cublasLt, cusparse, cufft, cufile, curand, nvrtc,
        #   cupti                                             -> the toolkit
        #   cudnn 9, cusparseLt 0, nccl 2, nvshmem_host 3     -> the four below
        #
        # The zip does contain libtorch_nvshmem.so, but that is torch's own thin
        # wrapper, not NVIDIA's runtime — it carries its own DT_NEEDED on
        # libnvshmem_host.so.3, and libtorch_cuda.so pulls the wrapper in, so the
        # runtime has to be here whether or not anything calls it.
        #
        # The floors are the versions PyTorch links against, read off the
        # matching wheel's Requires-Dist (nvidia-cudnn-cu13 9.20.0.48,
        # nvidia-cusparselt-cu13 0.8.1, nvidia-nccl-cu13 2.29.7,
        # nvidia-nvshmem-cu13 3.4.5); the sonames (.so.9, .so.0, .so.2, .so.3)
        # make anything newer in the same major fine. cudnn and cusparselt encode
        # the CUDA major as a `-13` version suffix that ties them back to `cuda`
        # on its own, so no constraint here has to repeat it.
        depends_on("cuda@13.2:13", type=("build", "link", "run"))
        depends_on("cudnn@9.20:9", type=("build", "link", "run"))
        depends_on("cusparselt@0.8.1:", type=("build", "link", "run"))

        # `fabrics=auto` against nccl's own `verbs` default: nccl is here only
        # because libtorch_cuda.so has a DT_NEEDED on libnccl.so.2, nothing in
        # this stack runs a collective, and `verbs` adds rdma-core (plus the
        # python/cmake tail it pulls at run time) for a transport nccl would
        # dlopen on demand anyway. libtorch itself is indifferent to the
        # transport, so this is a size choice, not a correctness one.
        #
        # nccl is a CudaPackage and conflicts with `cuda_arch=none`, so anything
        # depending on this needs a cuda_arch from somewhere; in CI that is
        # flavors/cuda13.yaml's blanket `variants: [cuda_arch=75]`. The same goes
        # for nvshmem below.
        depends_on("nccl@2.29: fabrics=auto", type=("build", "link", "run"))

        # nvshmem, trimmed for the same reason as nccl: all this stack wants is
        # libnvshmem_host.so.3 for the DT_NEEDED chain, and its transports are
        # dead weight in a build that never runs a collective. `~mpi` is the one
        # that matters — the default would put an entire MPI implementation in
        # the image. `+gdrcopy` is not a choice: upstream conflicts `~gdrcopy`
        # with `~ucx`, so one of the two has to stay, and gdrcopy is much the
        # smaller. Unlike nccl this is not in every CUDA stack, so the floor is
        # pinned exactly to what PyTorch links against.
        depends_on("nvshmem@3.4.5: ~mpi ~ucx ~nccl ~shmem +gdrcopy", type=("build", "link", "run"))

    for _version, _dist in _DISTS.items():
        # A version fetches one file under one checksum, and every artifact
        # above is specific to a platform (and, on linux x86_64, to `cuda`), so
        # none of them can be it. The release's LICENSE can: it is pinned to the
        # exact tag, is the same 3.4 KB for all four builds, and costs one
        # request. The payload comes from the resources below.
        version(
            _version,
            sha256=_dist["license"],
            url="https://raw.githubusercontent.com/pytorch/pytorch/v{0}/LICENSE".format(_version),
            expand=False,
        )
        for _key, _artifact in _dist.items():
            if _key == "license":
                continue
            _path, _sha256 = _artifact
            resource(
                name=_key,
                url="https://download.pytorch.org/{0}".format(_path),
                sha256=_sha256,
                # Spack can expand a .zip but not a .whl, and install() has to
                # pick one subtree out of either archive anyway, so neither is
                # expanded here and both go through the same code path.
                expand=False,
                destination="dist",
                placement=_key,
                when="@{0} {1}".format(_version, _WHEN[_key]),
            )

    # Everything a consumer needs, and nothing else: this is what keeps the
    # wheel's ~100 MB of Python sources, and both archives' metadata, out of the
    # prefix. Neither ships a bin/ that is any use to find_package(Torch).
    _TREES = ("include", "lib", "share")

    # PyTorch's vendored copy of FindCUDA's architecture table. `find_package
    # (Torch)` reaches it through Caffe2Config -> public/cuda.cmake, which calls
    # torch_cuda_get_nvcc_gencode_flag() and pushes the result into
    # CUDA_NVCC_FLAGS for the *consumer's* CUDA sources.
    _ARCH_TABLE = os.path.join(
        "share",
        "cmake",
        "Caffe2",
        "Modules_CUDA_fix",
        "upstream",
        "FindCUDA",
        "select_compute_arch.cmake",
    )

    # One architecture literal on a line that builds one of the two arch lists:
    #   set(CUDA_COMMON_GPU_ARCHITECTURES "5.0")
    #   list(APPEND CUDA_ALL_GPU_ARCHITECTURES "9.0a")
    # The `a` and `+PTX` suffixes ride on the same compute capability, and
    # `list(REMOVE_ITEM ...)` lines are matched but never dropped -- removing an
    # item that is no longer in the list is a no-op, removing the removal is not.
    _ARCH_ENTRY = re.compile(
        r"^(?P<indent>\s*)(?P<stmt>set|list\s*\(\s*(?P<op>APPEND|REMOVE_ITEM))\s*\(?\s*"
        r"(?P<var>CUDA_(?:COMMON|ALL)_GPU_ARCHITECTURES)\s+"
        r'"(?P<major>\d+)\.(?P<minor>\d+)(?P<suffix>[a-z]*(?:\+PTX)?)"'
    )

    # Libraries the CUDA build has a DT_NEEDED on and expects to find beside
    # itself, because pip ships them in sibling nvidia/* wheels — which is what
    # torch's `$ORIGIN/../../nvidia/*/lib` RPATH entries point at. Neither is
    # reachable from a spack install: CUPTI is in the toolkit but buried under
    # extras/, which is on no library path, and NVIDIA's nvshmem runtime is not
    # in the zip at all.
    #
    # {soname: the spec that provides it}. Symlinked into our own lib/, which is
    # both what `$ORIGIN` resolves to at run time and what a consumer reaches
    # through -rpath-link (a view merges it into <view>/lib) at link time. Left
    # out, anything linking libtorch into an *executable* fails on `undefined
    # reference to cuptiSubscribe@libcupti.so.13` / `nvshmem_malloc@NVSHMEM`: ld
    # allows unresolved DT_NEEDED when producing a shared library but not an
    # executable, so the plugin builds and only the test binaries break.
    _SIBLING_LIBS = {"libcupti.so.13": "cuda", "libnvshmem_host.so.3": "nvshmem"}

    def _link_sibling_libraries(self, spec, prefix):
        """Symlink the pip-layout siblings into lib/ (see _SIBLING_LIBS)."""
        for soname, provider in sorted(self._SIBLING_LIBS.items()):
            root = spec[provider].prefix
            found = [
                os.path.join(parent, soname)
                for parent, _, files in os.walk(root)
                if soname in files
            ]
            if not found:
                raise InstallError(
                    "{0} provides no {1}; libtorch_cuda's DT_NEEDED chain needs it "
                    "and nothing in this prefix supplies it".format(spec[provider], soname)
                )
            if len(found) > 1:
                # An exact soname match, so the toolkit's differently-versioned
                # copies (nsight ships libcupti.so.13.3) do not land here. If two
                # ever do, pick deliberately rather than by walk order.
                raise InstallError(
                    "{0} provides {1} more than once: {2}".format(spec[provider], soname, found)
                )
            os.symlink(found[0], prefix.lib.join(soname))
            tty.info("linked {0} -> {1}".format(soname, found[0]))

    def _prune_dead_cuda_archs(self, spec, prefix):
        """Drop architectures the paired CUDA toolkit cannot target.

        PyTorch seeds both of its architecture lists with Maxwell —

            set(CUDA_COMMON_GPU_ARCHITECTURES "5.0")

        — unconditionally, and never removes it. Its CUDA 13 branch is careful
        enough to swap 10.1a for 11.0a but leaves the seed alone, so with CUDA
        13, whose floor is compute_75, the default list opens with an
        architecture nvcc refuses outright: `nvcc fatal : Unsupported gpu
        architecture 'compute_50'`. Every consumer that compiles a single .cu
        file after find_package(Torch) hits it, because those flags land in
        CUDA_NVCC_FLAGS globally rather than on torch's own targets.

        Setting TORCH_CUDA_ARCH_LIST would also avoid it, but only for consumers
        spack builds itself. ACTS is configured by hand against the view or the
        container image, neither of which carries a package's build environment,
        so the correction has to be in the installed file.

        The supported set comes from the toolkit rather than a hardcoded floor,
        so this keeps working when CUDA 14 raises it again. Unsupported entries
        are dropped; the seed, which has to stay non-empty, becomes the lowest
        architecture the toolkit does support.
        """
        path = os.path.join(prefix, self._ARCH_TABLE)
        if not os.path.exists(path):
            raise InstallError("libtorch no longer ships {0}".format(self._ARCH_TABLE))

        nvcc = Executable(spec["cuda"].prefix.bin.nvcc)
        supported = {
            int(m.group(1))
            for m in re.finditer(r"compute_(\d+)", nvcc("--list-gpu-arch", output=str, error=str))
        }
        if not supported:
            raise InstallError("`nvcc --list-gpu-arch` named no architectures")
        floor = min(supported)

        kept, dropped = [], []
        for line in open(path).readlines():
            match = self._ARCH_ENTRY.match(line)
            if match:
                arch = int(match.group("major")) * 10 + int(match.group("minor"))
                if arch not in supported:
                    label = "{0}.{1}{2}".format(*match.group("major", "minor", "suffix"))
                    if match.group("op") == "APPEND":
                        dropped.append(label)
                        continue
                    if match.group("stmt") == "set":
                        dropped.append(label)
                        line = '{0}set({1} "{2}.{3}")\n'.format(
                            match.group("indent"), match.group("var"), floor // 10, floor % 10
                        )
            kept.append(line)

        if not dropped:
            # Either upstream fixed it or the table moved out from under this
            # regex. Both want a look before the next release goes out.
            raise InstallError(
                "no unsupported architectures found in {0}; CUDA {1} starts at compute_{2}, "
                "so either PyTorch fixed the table or its shape changed".format(
                    self._ARCH_TABLE, spec["cuda"].version, floor
                )
            )
        with open(path, "w") as f:
            f.writelines(kept)
        tty.info(
            "pruned CUDA architectures unsupported by cuda@{0}: {1}".format(
                spec["cuda"].version, ", ".join(dropped)
            )
        )

    def install(self, spec, prefix):
        archives = [
            os.path.join(root, f)
            for root, _, files in os.walk(self.stage.source_path)
            for f in sorted(files)
            if f.endswith((".zip", ".whl"))
        ]
        if len(archives) != 1:
            raise InstallError(
                "expected exactly one libtorch archive in the stage, found {0} -- "
                "no artifact is declared for {1}?".format(archives, spec.architecture)
            )
        archive = archives[0]

        # The distribution root inside the archive: the official zips wrap it in
        # `libtorch/`, a wheel calls it `torch/` and drops the Python package
        # into the same directory.
        root = "torch" if archive.endswith(".whl") else "libtorch"
        wanted = tuple("{0}/{1}/".format(root, tree) for tree in self._TREES)

        # Extracted member by member rather than with install_tree over an
        # unpacked stage: the CUDA tree is 961 MB and this writes it into the
        # prefix once instead of twice. No archive contains symlinks (checked
        # for all four), so a plain write per file is faithful; the execute bit
        # is the only mode worth carrying over, and only the wheel sets any.
        extracted = 0
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                if member.is_dir() or not member.filename.startswith(wanted):
                    continue
                dest = os.path.join(prefix, member.filename[len(root) + 1 :])
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if (member.external_attr >> 16) & 0o111:
                    os.chmod(dest, 0o755)
                extracted += 1
        tty.info("unpacked {0} files from {1}".format(extracted, os.path.basename(archive)))

        # Fail here rather than in a downstream cmake configure if PyTorch
        # reshuffles the distribution. TorchConfig.cmake is what
        # find_package(Torch) loads, and it in turn requires Caffe2's config.
        suffix = "dylib" if spec.satisfies("platform=darwin") else "so"
        expected = [
            prefix.share.cmake.Torch.join("TorchConfig.cmake"),
            prefix.share.cmake.Caffe2.join("Caffe2Config.cmake"),
            prefix.include.join("torch/csrc/api/include/torch/torch.h"),
            prefix.lib.join("libtorch.{0}".format(suffix)),
            prefix.lib.join("libtorch_cpu.{0}".format(suffix)),
            prefix.lib.join("libc10.{0}".format(suffix)),
        ]
        if spec.satisfies("+cuda"):
            expected += [prefix.lib.join("libtorch_cuda.so"), prefix.lib.join("libc10_cuda.so")]
            # Both only for +cuda: the CPU builds ship the same arch table, but
            # their Caffe2Config never includes public/cuda.cmake so nothing
            # reads it, they have no DT_NEEDED on either sibling library, and
            # there is no toolkit in the spec to ask about either one.
            self._prune_dead_cuda_archs(spec, prefix)
            self._link_sibling_libraries(spec, prefix)
        for path in expected:
            if not os.path.exists(path):
                raise InstallError("missing from the installed libtorch tree: {0}".format(path))

    def setup_run_environment(self, env: EnvironmentModifications) -> None:
        # Prebuilt, so nothing rewrote the RPATHs to point at spack prefixes:
        # libtorch_cuda.so reaches libcudart and friends through the loader path
        # only. A consumer that links libtorch through cmake gets the right
        # RPATH on its own binaries; this covers running out of the prefix.
        env.prepend_path("LD_LIBRARY_PATH", self.prefix.lib)
