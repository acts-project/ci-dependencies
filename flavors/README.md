# Accelerator flavors

Each *flavor* is an overlay that `spack_build.sh` applies on top of the base
`spack.yaml` to produce an accelerator-enabled variant of the dependency stack
(CUDA, ROCm, …). The base `spack.yaml` remains the single source of truth for
package versions; a flavor only expresses the **delta**.

## Naming

The flavor name becomes the fourth token of `TARGET_TRIPLET`
(`<arch>_<compiler>_cxx<std>_<flavor>`), which in turn namespaces the lockfile,
Dockerfile, buildcache entries, and container image tag automatically. The
special flavor `host` is the plain CPU stack and produces the historical
three-token triplet with no overlay.

Name a flavor after what a consumer cannot work around — the hard compatibility
boundaries — and leave the rest to the lockfile. Which axes those are differs by
vendor, which is why `cuda13` and `rocm-gfx90a` are spelled differently:

- **CUDA**: the toolkit major version is hard (a consumer linking
  `libcudart.so.12` cannot use a `cuda13` stack, and it decides whether prebuilt
  binaries such as ONNX Runtime's CUDA execution provider load), while the GPU
  target is soft — `-arch=sm_75` embeds PTX that the driver JIT-compiles onto
  newer GPUs. So the toolkit is in the name and the arch is not; `cuda_arch` is
  still visible in the concretized lockfile.
- **ROCm**: there is no PTX equivalent. HIP embeds a code object per gfx target,
  so gfx90a code will not run on gfx1100 and the target *is* a hard boundary —
  hence `rocm-gfx90a`.

Add an arch to a CUDA flavor name only once you ship two of them side by side
(say an sm_90-tuned build alongside the portable one), and spell it `sm<arch>`
if you do: `cuda75` and `cuda90` read as CUDA 7.5 and CUDA 9.0, both of which
were real toolkit versions.

## File format

For a flavor `<name>`, `spack_build.sh` looks for two optional files:

- `flavors/<name>.yaml` — a Spack **config** fragment merged via
  `spack config add -f`. Contains config sections only (`packages:`,
  `concretizer:`, `config:`), **not** wrapped in a top-level `spack:` key and
  **not** containing `specs:`.
- `flavors/<name>.specs` — extra specs, one per line. `#` comments and blank
  lines are ignored. Each non-empty line is passed to `spack change` first,
  which merges it onto the existing root spec of the same package name if the
  base `spack.yaml` already has one — overriding only the attributes that
  actually conflict (e.g. flipping a variant) and leaving the rest (version,
  other variants) as the base declared them. If no root spec with that name
  exists yet, it falls back to `spack add`, adding it as a new one.

At least one of the two must exist, or the build fails with "unknown flavor".

## Adding a flavor

1. Create `flavors/<name>.yaml` and/or `flavors/<name>.specs`.
2. Validate locally:
   ```bash
   FLAVOR=<name> COMPILER=gcc@13.3.0 CXXSTD=20 SPACK_ROOT=$(spack location -r) \
     ./spack_build.sh          # or just run `spack concretize -Uf` in the env
   ```
3. Add a matrix entry in `.github/workflows/build.yml` with `flavor: <name>` and
   `default: false` (a GPU flavor must never be the arch-canonical lockfile).

> The `.specs` in this directory are **starting points**. Some packages
> (`traccc`, `detray`, `covfie`, `vecmem`, `algebra-plugins`) may not exist in
> upstream Spack and may need a `package.py` under
> `spack_repo/acts/packages/`. Always concretize before enabling in CI.

## ROCm is a binary toolchain here

Spack's `cuda` is a binary installer, but its `hip` builds the entire ROCm stack
from source — AMD's clang fork included — which is hours of CPU and more disk
than a CI runner has. `spack_repo/acts/packages/` therefore overrides three
packages with binary ones that unpack AMD's own rpms from `repo.radeon.com`:

| package | prefix |
| --- | --- |
| `hip` | the whole ROCm root (`bin/`, `include/`, `lib/`, `llvm`, `amdgcn`) |
| `llvm-amdgpu` | symlink farm over `<hip>/lib/llvm` (+ `amdgcn`) |
| `hsa-rocr-dev` | symlink farm over the ROCr parts of `<hip>` |

They keep the upstream names because `depends_on("hip")` and `ROCmPackage`'s
`depends_on("llvm-amdgpu"/"hsa-rocr-dev")` are literal — HIP is not a virtual,
so a differently named package could not be substituted for it.

To move to another ROCm release, add an entry to `_rpms` in
`spack_repo/acts/packages/hip/package.py` (rpm file names and sha256 sums from
`https://repo.radeon.com/rocm/rhel8/<version>/main/`) and bump the `version`
and the `depends_on("hip@...")` in the other two packages. Stay on the **rhel8**
rpms: their payload is xz (the unpacker is stdlib-only, no `rpm2cpio` needed)
and they are built against glibc 2.28, so they run on every image in the
matrix.
