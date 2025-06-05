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

start_section "Setting up spack from $SPACK_ROOT"
source "$SPACK_ROOT"/share/spack/setup-env.sh
end_section


echo "Spack version: $(spack --version)"

start_section "Create environment"
spack env create -d . "$SCRIPT_DIR"/spack.yaml 
end_section

start_section "List visible compilers"
spack -e . compiler find --scope "env:$PWD"
spack -e . compiler list
end_section

start_section "Locate OpenGL"
"$SCRIPT_DIR"/opengl.sh
end_section

start_section "Select compiler"
spack -e . compiler list
echo "Looking for compiler: $COMPILER"
spack -e . compiler list | grep "$COMPILER"
spack -e . config add "packages:all:require: [\"%$COMPILER\"]"
end_section

start_section "Concretize"
spack -e . concretize -Uf
spack -e . find -c
end_section

echo "+ Spack build"
args="--no-check-signature --show-log-on-error"
if [ -n "${FAIL_FAST:-}" ]; then
  args="$args --fail-fast"
fi
spack -e . install $args

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
set_env TARGET_TRIPLET "${TARGET_ARCH}_${COMPILER}"
