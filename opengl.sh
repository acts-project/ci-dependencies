#!/bin/bash

set -u
set -e

source detect_os.sh

mkdir -p ~/.spack/
packages_file=~/.spack/packages.yaml

if [ "$os" == "ubuntu" ]; then
  sudo apt-get update
  sudo apt-get install -y mesa-utils libgl1-mesa-dev
cat <<EOF > $packages_file
    packages:
      opengl:
        buildable: false
        externals:
        - prefix: /usr/
          spec: opengl@4.5
EOF
elif [ "$os" == "almalinux" ]; then
  dnf install mesa-libGLU glx-utils
cat <<EOF > $packages_file
    packages:
      opengl:
        buildable: false
        externals:
        - prefix: /usr/
          spec: opengl@4.6
EOF
fi
