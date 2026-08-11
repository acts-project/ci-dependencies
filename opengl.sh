#!/bin/bash

set -u
set -e

packages_file=$(spack location -r)/etc/spack/packages.yaml
echo "Packages file: $packages_file"

os=$(spack arch --family)

echo "OS: $os"

if [[ "$os" == *darwin* ]]; then
  echo "Nothing to do on Darwin"
  exit 0
fi

# Every base image in this matrix already ships OpenGL dev headers/libs, and
# installing them here needs root — passwordless via sudo in CI containers,
# but a blocking password prompt on a bare host build (local_build.py host).
# So just check they're present instead of installing, and fail with
# instructions if they're genuinely missing.
check_opengl_present() {
  local header="/usr/include/GL/gl.h"
  local has_lib=false
  if command -v ldconfig &> /dev/null && ldconfig -p | grep -q 'libGL\.so'; then
    has_lib=true
  fi
  if [ ! -f "$header" ] || [ "$has_lib" != true ]; then
    echo "ERROR: OpenGL dev headers/libraries not found ($header, libGL.so)." >&2
    echo "Install them for your OS and re-run, e.g.:" >&2
    echo "  Ubuntu/Debian:  sudo apt-get install libgl1-mesa-dev" >&2
    echo "  RHEL/AlmaLinux: sudo dnf install mesa-libGL-devel mesa-libGLU-devel" >&2
    exit 1
  fi
}

if [[ "$os" == *ubuntu* ]]; then
  check_opengl_present

if [[ "$os" == *ubuntu26* ]] || [[ "$os" == *ubuntu24* ]]; then
  version="4.6"
elif [[ "$os" == *ubuntu20* ]]; then
  version="4.5"
else
  echo "Unknown OS version, default OpenGL version"
  version="4.5"
fi

cat <<EOF > "$packages_file"
packages:
  opengl:
    buildable: false
    externals:
    - prefix: /usr/
      spec: opengl@${version}
EOF
cat "$packages_file"
elif [[ "$os" == *almalinux* ]]; then
  check_opengl_present
cat <<EOF > "$packages_file"
packages:
  opengl:
    buildable: false
    externals:
    - prefix: /usr/
      spec: opengl@4.6
EOF
cat "$packages_file"
else [[ "$os" == *darwin* ]]
  echo "Nothing to do on Darwin"
fi
