#!/bin/bash

# python=/opt/homebrew/Cellar/python@3.12/3.12.0/bin/python3.12
# python=$HOME/.asdf/installs/python/3.12.0/bin/python3.12

suffix=15p4_3

cmake -S . -B build_all_${suffix} \
  -DCMAKE_CXX_STANDARD=20 \
  -DCMAKE_INSTALL_PREFIX=$PWD/install_all_${suffix} \
  -DCMAKE_BUILD_PARALLEL_LEVEL=7 $@
