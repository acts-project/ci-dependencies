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
boundaries — and leave the rest to the lockfile. In practice that is the
**toolkit major version** for both vendors, which is why `cuda13` and `rocm7`
are spelled the same way:

- **CUDA**: a consumer linking `libcudart.so.12` cannot use a `cuda13` stack,
  and the major decides whether prebuilt binaries such as ONNX Runtime's CUDA
  execution provider load.
- **ROCm**: same shape — `libamdhip64.so.7` is the soname AMD ships for all of
  7.x, so a binary built against ROCm 6 cannot load against a ROCm 7 stack.

The GPU target stays out of the name in both, but for different reasons, and
the difference matters to whoever consumes the image:

- For CUDA it is genuinely soft. `-arch=sm_75` embeds PTX that the driver
  JIT-compiles onto anything newer, so `cuda13` runs on GPUs that did not exist
  when it was built.
- For ROCm it is hard *per GPU* — HIP embeds a code object per gfx target and
  has no PTX equivalent — but a flavor ships a **set** of targets, and a set is
  a capability list rather than a boundary, so it cannot go in the name without
  churning every time the list grows. The consequence is that `rocm7`
  over-promises where `cuda13` does not: it runs on exactly the targets
  enumerated in `rocm7.yaml` and fails at kernel launch ("no kernel image is
  available") anywhere else. Keep that list documented there, and treat adding
  a GPU as a rebuild of the flavor rather than a new one.

`cuda_arch` / `amdgpu_target` stay visible in the concretized lockfile either
way, so nothing is lost by leaving them out of the name.

Two spelling rules:

- The version token is the **major** only. Add a dotted minor (`rocm7.2`) only
  once two minors have to coexist — which is likelier on the ROCm side, where
  `rocthrust`/`rocrand` pin `hip@` to an exact version and so nail a flavor to
  one patch release. This is also what disambiguates a future `rocm10` (ROCm 10,
  not ROCm 1.0).
- Add an arch to a name only once you ship two of them side by side (say an
  sm_90-tuned CUDA build alongside the portable one), and spell it `sm<arch>`
  if you do: `cuda75` and `cuda90` read as CUDA 7.5 and CUDA 9.0, both of which
  were real toolkit versions.

Renaming a flavor is cheap — two files here, one matrix entry in
`build.yml` — but it moves `TARGET_TRIPLET`, so the lockfile name, image tag
and buildcache namespace all move with it and the old artifacts go stale rather
than updating. That is the point: the tag change is the signal to a consumer
that the ABI moved under them.

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
> (`traccc`, `detray`, `algebra-plugins`) may not exist in upstream Spack and
> may need a `package.py` under `spack_repo/acts/packages/`. Always concretize
> before enabling in CI.
>
> `vecmem`, `covfie` and `alpaka` are upstream *and* are root specs in the base
> `spack.yaml` (the CPU builds need them for detray/traccc). A flavor therefore
> only flips their accelerator variant and must not repeat their version — and,
> for a multi-valued variant like alpaka's `backend`, must restate every value
> it wants kept, since `spack change` substitutes the variant wholesale rather
> than merging values into it.

## ROCm is a binary toolchain here

Spack's `cuda` is a binary installer, but its `hip` builds the entire ROCm stack
from source — AMD's clang fork included — which is hours of CPU and more disk
than a CI runner has. `spack_repo/acts/packages/` therefore overrides four
packages with binary ones that unpack AMD's own rpms from `repo.radeon.com`:

| package | prefix |
| --- | --- |
| `hip` | the whole ROCm root (`bin/`, `include/`, `lib/`, `llvm`, `amdgcn`) |
| `llvm-amdgpu` | symlink farm over `<hip>/lib/llvm` (+ `amdgcn`) |
| `hsa-rocr-dev` | symlink farm over the ROCr parts of `<hip>` |
| `comgr` | symlink farm over the `amd_comgr` parts of `<hip>` |

They keep the upstream names because `depends_on("hip")` and `ROCmPackage`'s
`depends_on("llvm-amdgpu"/"hsa-rocr-dev")` are literal — HIP is not a virtual,
so a differently named package could not be substituted for it.

To move to another ROCm release, replace the entry in `_rpms` in
`spack_repo/acts/packages/hip/package.py` (rpm file names and sha256 sums from
`https://repo.radeon.com/rocm/rhel8/<version>/main/` — the sums in that
directory's `repodata/*-primary.xml.gz` are the rpm file sums, so there is no
need to download 450 MB to compute them) and bump the `version` and the
`depends_on("hip@...")` in the other three packages. The reachable versions are
capped by upstream Spack's `rocthrust`, which pins `hip@` to its own exact
version, so pick a release both AMD and `rocthrust` ship. A ROCm **major** bump also means renaming
this flavor (`rocm7` -> `rocm8`) — see [Naming](#naming); a minor or patch bump
does not.

Check the `_contents` lists in the view packages against the new layout — AMD
moves paths between majors (7.0 renamed the ROCr doc directory from
`hsa-runtime64` to `hsa-rocr`), and `symlink_tree` raises on an entry that
matches nothing.

Stay on the **rhel8** rpms: their payload is xz (the unpacker is stdlib-only, no
`rpm2cpio` needed) and they are built against glibc 2.28, so they run on every
image in the matrix.
