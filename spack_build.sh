#!/bin/bash

set -u
set -e
set -o pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}"   )" &> /dev/null && pwd   )

export SPACK_COLOR=always

function set_env {
  key="$1"
  value="$2"

  echo "=> ${key}=${value}"

  export "${key}=${value}"
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "${key}=${value}" >> "$GITHUB_ENV"
  fi
}

function start_section() {
    local section_name="$1"
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
        echo "::group::${section_name}"
    else
        echo "+ ${section_name}"
    fi
}

function end_section() {
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

if [ -z "${IS_DEFAULT:-}" ]; then
    echo "IS_DEFAULT is not set"
    exit 1
fi

start_section "Setting up spack from $SPACK_ROOT"
source "$SPACK_ROOT"/share/spack/setup-env.sh
end_section


echo "Spack version: $(spack --version)"


start_section "List visible compilers"
spack compiler find
spack compilers
end_section

start_section "Locate OpenGL"
"$SCRIPT_DIR"/opengl.sh
end_section

start_section "Select compiler"
spack compilers | grep "$COMPILER"
spack env create -d . "$SCRIPT_DIR"/spack.yaml # !!!! TURN BACK ON
spack -e . config add "packages:all:require: [\"%$COMPILER\"]"
end_section

start_section "Concretize"
spack -e . concretize -Uf
spack -e . find -c
end_section

start_section "Lockfile bookkeeping"
arch=$(spack arch --family)
set_env TARGET_TRIPLET "${arch}_${COMPILER}"
cp spack.lock "spack_${TARGET_TRIPLET}.lock"
if [[ "${IS_DEFAULT}" == "true" ]]; then
  # this will be become the default combination for this architecture
  cp spack.lock "spack_${arch}.lock"
fi
end_section

start_section "Spack build"
spack -e . install --no-check-signature --show-log-on-error
end_section
