#!/bin/bash

set -u
set -e
set -o pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}"   )" &> /dev/null && pwd   )

export SPACK_COLOR=always


function start_section() {
    local section_name="$1"
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
        echo "::group::${section_name}"
    else
        echo "+ ${section_name}"
    fi
}

function end_section() {
  echo "" > /dev/null
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
        echo "::endgroup::"
    fi
}


if [ -z "${SPACK_ROOT:-}" ]; then
    echo "SPACK_ROOT is not set"
    exit 1
fi

if [ -z "${COMPILER:-}" ]; then
    echo "COMPILER is not set"
    exit 1
fi

if [ -z "${CXXSTD:-}" ]; then
    echo "CXXSTD is not set"
    exit 1
fi

# Accelerator flavor: `host` (default) builds the plain CPU stack and is a no-op
# below. A non-host value (e.g. `cuda90`, `rocm-gfx90a`) overlays the matching
# fragments under flavors/ onto the base environment and is appended to
# TARGET_TRIPLET so all downstream artifacts (lockfile, Dockerfile, image tag,
# buildcache) are namespaced automatically.
FLAVOR="${FLAVOR:-host}"


# --- TEMPORARY: provide crypt.h for ROOT's net/auth on newer Ubuntu images ---
# glibc no longer ships <crypt.h>; it now comes from libxcrypt (libcrypt-dev).
# The ubuntu2604 base image lacks it, so root@6.38 fails to build TAuthenticate.
# Install it here only where it's actually missing. Once verified, move this
# into the base image and delete this block.
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "${ID:-}" = "ubuntu" ] && [ ! -e /usr/include/crypt.h ]; then
        start_section "Install libcrypt-dev (temporary crypt.h shim)"
        apt-get update
        apt-get install -y libcrypt-dev
        end_section
    fi
fi


start_section "Setting up spack from $SPACK_ROOT"
source "$SPACK_ROOT"/share/spack/setup-env.sh
end_section


echo "Spack version: $(spack --version)"

ln -sfn "$SCRIPT_DIR/spack_repo" .

start_section "Create environment"
if [ ! -d .spack-env ]; then
  rm -f spack.yaml
  spack env create -d . "$SCRIPT_DIR"/spack.yaml
fi
# `spack env create` has no overwrite flag, and the `spack config add` calls
# below persist into the env manifest. Reset ./spack.yaml from the source each
# run so stale entries (e.g. an old packages:all:require) don't accumulate
# across reused build directories — matching CI, which always starts fresh.
cp "$SCRIPT_DIR"/spack.yaml ./spack.yaml
# spack -e . mirror set --autopush acts-spack-buildcache
end_section

start_section "Apply accelerator flavor: $FLAVOR"
if [ "$FLAVOR" = "host" ]; then
  echo "host flavor: no overlay applied"
else
  flavor_cfg="$SCRIPT_DIR/flavors/${FLAVOR}.yaml"
  flavor_specs="$SCRIPT_DIR/flavors/${FLAVOR}.specs"
  if [ ! -f "$flavor_cfg" ] && [ ! -f "$flavor_specs" ]; then
    echo "ERROR: unknown flavor '$FLAVOR' (no flavors/${FLAVOR}.yaml or .specs)" >&2
    exit 1
  fi
  # Merge the config delta (packages:/concretizer:/... sections) into the env.
  if [ -f "$flavor_cfg" ]; then
    echo "Merging config overlay $flavor_cfg"
    spack -e . config add -f "$flavor_cfg"
  fi
  # Add the extra specs, one per line; `#` comments and blank lines are ignored.
  if [ -f "$flavor_specs" ]; then
    while IFS= read -r line; do
      line="${line%%#*}"
      line="$(echo "$line" | xargs)"
      [ -n "$line" ] || continue
      echo "Adding spec: $line"
      spack -e . add "$line"
    done < "$flavor_specs"
  fi
fi
end_section

start_section "List visible compilers"
# Debug aid for compiler discovery (e.g. alma10 finding no compiler).
echo "COMPILER=$COMPILER"
echo "COMPILER_PATH=${COMPILER_PATH:-<unset>}"
echo "CXX=${CXX:-<unset>}"
echo "CC=${CC:-<unset>}"
echo "which c++:  $(command -v c++ || echo '<not found>')"
echo "which g++:  $(command -v g++ || echo '<not found>')"
echo "which clang++: $(command -v clang++ || echo '<not found>')"
if [ -n "${CXX:-}" ] && command -v "$CXX" >/dev/null 2>&1; then
  echo "\$CXX version: $("$CXX" --version | head -1)"
fi
if [ -n "${COMPILER_PATH:-}" ]; then
# `spack compiler find <prefix>` only searches <prefix> and <prefix>/bin, but
# gcc-toolset keeps its binaries in <prefix>/usr/bin. On some images (e.g.
# alma10) the <prefix>/bin -> usr/bin symlink is absent, so search usr/bin
# explicitly. Non-existent hints are ignored by spack, so this is safe anywhere.
spack -e . compiler find --scope "env:$PWD" "$COMPILER_PATH" "$COMPILER_PATH/usr/bin"
else
spack -e . compiler find --scope "env:$PWD"
fi
spack -e . compiler list
end_section

start_section "Locate OpenGL"
"$SCRIPT_DIR"/opengl.sh
end_section

start_section "Select compiler and cxxstd"
spack -e . compiler list
echo "Looking for compiler: $COMPILER"
spack -e . compiler list | grep "$COMPILER"
# Require the compiler as the provider of the C/C++ language virtuals rather than
# as a blanket `%compiler` dependency on `packages:all` (which spack warns is
# really a provider requirement). Fortran is left unconstrained on purpose: the
# llvm/apple-clang matrix entries don't provide fortran, so it resolves freely
# (typically to gcc), matching the previous behavior.
spack -e . config add "packages:all:require:[\"cxxstd=$CXXSTD\"]"
spack -e . config add "packages:c:require:[\"$COMPILER\"]"
spack -e . config add "packages:cxx:require:[\"$COMPILER\"]"
end_section

start_section "Concretize"
spack -e . concretize -Uf
spack -e . find -c
end_section

echo "+ Spack build"
args="--no-check-signature --show-log-on-error --concurrent-packages 8"
if [ -n "${FAIL_FAST:-}" ]; then
  args="$args --fail-fast"
fi
if [ -n "${BUILD_JOBS:-}" ]; then
  args="$args -j $BUILD_JOBS"
fi
spack -e . install $args

start_section "Verify ROOT C++ standard"
root_config="$(spack -e . location -i root)/bin/root-config"
root_cflags=$("$root_config" --cflags)
echo "root-config --cflags: $root_cflags"
if [[ "$root_cflags" =~ c[+][+]([0-9a-z]+) ]]; then
  root_cxxstd="${BASH_REMATCH[1]}"
else
  root_cxxstd=""
fi
echo "ROOT C++ standard: '${root_cxxstd}' (expected '$CXXSTD')"
if [ "$root_cxxstd" != "$CXXSTD" ]; then
  echo "ERROR: ROOT reports C++ standard '$root_cxxstd' but '$CXXSTD' was requested"
  exit 1
fi
end_section

function set_env {
  key="$1"
  value="$2"

  echo "=> ${key}=${value}"

  export "${key}=${value}"
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "${key}=${value}" >> "$GITHUB_ENV"
  fi
}

set_env TARGET_ARCH "$(spack arch --family)"
# `host` keeps the historical 3-token triplet byte-for-byte; any other flavor
# adds a fourth token so its artifacts never collide with the CPU stack.
if [ "$FLAVOR" = "host" ]; then
  set_env TARGET_TRIPLET "${TARGET_ARCH}_${COMPILER}_cxx${CXXSTD}"
else
  set_env TARGET_TRIPLET "${TARGET_ARCH}_${COMPILER}_cxx${CXXSTD}_${FLAVOR}"
fi
