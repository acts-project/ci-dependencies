#!/bin/bash

set -u
set -e
set -o pipefail

spack_root=$1

if [ -z "$spack_root" ]; then
    echo "spack_root is not set"
    exit 1
fi

source "$spack_root"/share/spack/setup-env.sh


which spack
spack --version
