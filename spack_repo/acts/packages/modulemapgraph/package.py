# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

from spack_repo.builtin.build_systems.cmake import CMakePackage
from spack_repo.builtin.build_systems.cuda import CudaPackage

from spack.package import *


class Modulemapgraph(CMakePackage, CudaPackage):
    """Standalone C++ and CUDA code to compute a module map and to build graphs
    from it.

    The Module Map Graph (MMG) library performs the graph construction stage of
    the GNN4ITk track reconstruction pipeline: it derives a module map from
    simulated events and uses it to turn hits into a graph of candidate edges,
    on CPU or on GPU.
    """

    homepage = "https://gitlab.cern.ch/gnn4itkteam/ModuleMapGraph"
    git = "https://gitlab.cern.ch/gnn4itkteam/ModuleMapGraph.git"

    license("MPL-2.0")

    version("1.4.2", tag="1.4.2", commit="4b377b589c1cd9213d32336da5f2afd9380938b4")
    version("1.4.1", tag="1.4.1", commit="57771b24c471e96ecf39772f7e970df282260805")
    version("1.4.0", tag="1.4.0", commit="c54645d8ffc2d0de6c013adc1e86ff377a90479b")

    variant("mpi", default=False, description="Build the MPI module map creator")
    variant("executables", default=True, description="Build the command line executables")
    # Upstream defaults the numpy export to ON, but it drags in boost +python
    # +numpy, so keep it opt-in here.
    variant("numpy", default=False, description="Enable graph export to numpy .npz files")
    variant("pytorch", default=False, description="Enable graph export to pytorch .pyg files")
    variant("timing", default=False, description="Enable code timing measurements")
    variant("debug_code", default=False, description="Enable code debugging functions")
    variant(
        "launch_bounds",
        default=False,
        description="Use CUDA launch bounds to optimize performance",
        when="+cuda",
    )

    depends_on("cxx", type="build")
    depends_on("cmake @3.25:", type="build")

    depends_on("boost @1.75: +program_options +test +graph +regex")
    depends_on("boost +python +numpy", when="+numpy")
    depends_on("root @6.28:")

    depends_on("mpi", when="+mpi")

    # The C++20 sources are compiled as CUDA too, which needs nvcc >= 12
    depends_on("cuda @12:", when="+cuda")

    depends_on("python", when="+numpy")
    depends_on("py-numpy", when="+numpy", type=("build", "link", "run"))
    depends_on("py-torch", when="+pytorch")

    # The GPU sources are only built as part of the CUDA build, and the build
    # system falls back to `native` detection without an explicit architecture.
    conflicts(
        "cuda_arch=none", when="+cuda", msg="A CUDA architecture is required for the GPU build"
    )

    # The project pins itself to C++20
    conflicts("%gcc @:9", msg="ModuleMapGraph requires a C++20 capable compiler")

    def cmake_args(self):
        spec = self.spec

        args = [
            self.define_from_variant("MMG_USE_CUDA", "cuda"),
            self.define_from_variant("MMG_USE_MPI", "mpi"),
            self.define_from_variant("MMG_BUILD_EXECUTABLES", "executables"),
            self.define_from_variant("MMG_WITH_NUMPY_EXPORT", "numpy"),
            self.define_from_variant("MMG_WITH_PYTORCH_EXPORT", "pytorch"),
            self.define_from_variant("MMG_ENABLE_TIMING", "timing"),
            self.define_from_variant("MMG_ENABLE_DEBUG", "debug_code"),
            # Not guarded by a `when=` in the build system, so always pass it
            self.define("MMG_USE_CUDA_LAUNCH_BOUNDS", spec.satisfies("+launch_bounds")),
        ]

        if spec.satisfies("+cuda"):
            # The CUDA sources include the C++20 CPU headers, and the build
            # system does not set the CUDA standard itself
            args.append(self.define("CMAKE_CUDA_STANDARD", "20"))
            args.append(self.define("CMAKE_CUDA_ARCHITECTURES", spec.variants["cuda_arch"].value))

        if spec.satisfies("+pytorch"):
            args.append(
                self.define(
                    "Torch_DIR",
                    join_path(
                        spec["py-torch"].prefix,
                        "lib",
                        "python{0}".format(spec["python"].version.up_to(2)),
                        "site-packages",
                        "torch",
                        "share",
                        "cmake",
                        "Torch",
                    ),
                )
            )

        return args
