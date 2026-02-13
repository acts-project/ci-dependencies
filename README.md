# ACTS dependencies

- If you're on **macOS** you need a fortran compiler: so `brew install gcc`
- Install [spack](https://github.com/spack/spack/#installation) and source the setup script (`~/spack/share/spack/setup-env.sh`) so you have shell support
- Clone the [acts-project/ci-dependencies](https://github.com/acts-project/ci-dependencies) repository

> [!IMPORTANT]
> If you're on **macOS** you need a fortran compiler: so `brew install gcc` before proceeding.

- Go to the cloned dependencies repository
    - Run `spack compiler find`. 
      This populates spack's compiler packages with the externally found compilers.
    - Run `spack env activate .`
      This loads the repository as an [environment](https://spack.readthedocs.io/en/latest/environments.html) and allows you to perform local actions.
    - Run `spack concretize -Uf`
      This makes spack resolve all of the dependency packages and their various versions. This step can take a moment.
    - Run `spack install`
      This will actually perform the installaion!

The `spack.yaml` in this repository configures a binary cache that lives on GitHub. 
Spack will attempt to find binary caches for the packages that it installs, 
and this can significantly speed up the install process.

> [!WARNING]
> 🚨 **ROOT install issue on macOS**
> - When you see ROOT building, abort! Then run `spack uninstall root` and then `spack install --no-cache root`. If you see a warning about some `._view` folder already existing, just delete that folder and try again!
> - Once the ROOT build is completed, run `spack install`

- To compile ACTS, you do `spack env activate $CI_DEPS` directory (wherever that `spack.yaml` file is / where you cloned this repository) and then run CMake as usual on an ACTS clone
