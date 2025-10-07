#!/bin/bash
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}"   )" &> /dev/null && pwd   )

target_dir="$(spack location -i lhapdfsets)"

uv run "$SCRIPT_DIR"/download_lhapdf.py "MMHT2014lo68cl,MMHT2014nlo68cl,CT14lo,CT14nlo,NNPDF23_nlo_as_0119_qed" "$target_dir"

