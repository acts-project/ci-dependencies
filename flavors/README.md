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

Encode the GPU target in the name (`cuda80`, `cuda90`, `rocm-gfx90a`) so the
CUDA arch / AMD gfx target is not a separate CI axis.

## File format

For a flavor `<name>`, `spack_build.sh` looks for two optional files:

- `flavors/<name>.yaml` — a Spack **config** fragment merged via
  `spack config add -f`. Contains config sections only (`packages:`,
  `concretizer:`, `config:`), **not** wrapped in a top-level `spack:` key and
  **not** containing `specs:`.
- `flavors/<name>.specs` — extra specs, one per line. `#` comments and blank
  lines are ignored. Each non-empty line is passed to `spack add`.

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
