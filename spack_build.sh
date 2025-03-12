#!/bin/bash

set -u
set -e
set -o pipefail

echo "SPACK BUILD"

pwd
whoami
ls -al
uname -a


which spack
spack --version
