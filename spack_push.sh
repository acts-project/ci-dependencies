#!/bin/bash

set -u
set -e
set -o pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}"   )" &> /dev/null && pwd   )

export SPACK_COLOR=always

if [ -z "${SPACK_ROOT:-}" ]; then
    echo "SPACK_ROOT is not set"
    exit 1
fi

if [ -z "${BASE_IMAGE:-}" ]; then
    echo "BASE_IMAGE is not set"
    exit 1
fi

source "$SPACK_ROOT"/share/spack/setup-env.sh

echo "+ Pushing to buildcache"
"$SCRIPT_DIR/retry.sh" spack -e . \
  buildcache push \
  --base-image "${BASE_IMAGE}" \
  --unsigned \
  acts-spack-buildcache

  # --update-index \
