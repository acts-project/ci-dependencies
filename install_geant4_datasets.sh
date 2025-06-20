#!/bin/bash
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}"   )" &> /dev/null && pwd   )

ln -s "$(spack location -i geant4)" $SCRIPT_DIR/.spack-env/view/share/Geant4/data

geant4-config --install-datasets
