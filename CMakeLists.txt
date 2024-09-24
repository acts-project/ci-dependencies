cmake_minimum_required(VERSION 3.20)
project(ActsDependencies)

include(ExternalProject)
include(GNUInstallDirs)
list(APPEND CMAKE_MODULE_PATH ${CMAKE_CURRENT_SOURCE_DIR}/cmake)

set(PYTHON_VERSION 3.12.2)
set(TBB_VERSION 2021.11.0)
set(GEANT4_VERSION 11.1.3)
set(HEPMC3_VERSION 3.2.5)
set(PYTHIA8_VERSION 311)
set(JSON_VERSION 3.11.2)
set(ROOT_VERSION 6.30.06)
set(ROOT_HASH SHA1=dd95462386e27c205fe8e3396daa9c169627fbf1)
set(PODIO_VERSION 00-17-02)
set(EDM4HEP_VERSION 00-10-01)
set(DD4HEP_VERSION 01-29)
set(BOOST_VERSION 1.84.0)
set(EIGEN_VERSION 3.4.0)
set(GEOMODEL_VERSION 6.3.0)
set(FASTJET_VERSION 3.4.3)

if(POLICY CMP0135)
  cmake_policy(SET CMP0135 NEW)
endif()

if(NOT DEFINED CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

set(CMAKE_BUILD_TYPE RelWithDebInfo)

if(NOT CMAKE_BUILD_PARALLEL_LEVEL)
  set(CMAKE_BUILD_PARALLEL_LEVEL 1)
endif()


if(APPLE)
  set(OS_NAME "macos")
else()
  execute_process(
    COMMAND bash "-c" "cat /etc/os-release | grep -e \"^PRETTY_NAME=\" | sed 's/PRETTY_NAME=\"\\(.*\\)\"/\\1/g'"
    OUTPUT_VARIABLE OS_NAME_PRETTY
    OUTPUT_STRIP_TRAILING_WHITESPACE
  )

  message(STATUS "OS: ${OS_NAME_PRETTY}")


  if(${OS_NAME_PRETTY} MATCHES "AlmaLinux")
    set(OS_NAME "almalinux")
  elseif(${OS_NAME_PRETTY} MATCHES "Ubuntu")
    set(OS_NAME "ubuntu")
  else()
    message(FATAL_ERROR "Unsupported OS: ${OS_NAME_PRETTY}")
  endif()
endif()

macro(install_hint)
  set(options "")
  set(oneValueArgs MACOS UBUNTU ALMA)
  set(multiValueArgs "")
  cmake_parse_arguments(IHINT "${options}" "${oneValueArgs}"
                        "${multiValueArgs}" ${ARGN})
  if(APPLE)
    message(FATAL_ERROR "Install like:\n> brew install ${IHINT_MACOS}")
  elseif(${OS_NAME} STREQUAL "ubuntu")
    message(FATAL_ERROR "Install like:\n> sudo apt-get install -y ${IHINT_UBUNTU}")
  elseif(${OS_NAME} STREQUAL "almalinux")
    message(FATAL_ERROR "Install like:\n> sudo dnf install -y ${IHINT_ALMA}")
  endif()
endmacro()

macro(check_os_package)
  set(options PROGRAM)
  set(oneValueArgs NAME MACOS UBUNTU ALMA)
  set(multiValueArgs "")
  cmake_parse_arguments(OSPKG "${options}" "${oneValueArgs}"
                        "${multiValueArgs}" ${ARGN})

  message(CHECK_START "Checking for ${OSPKG_NAME}")
  if(${OSPKG_PROGRAM})
    find_program(${OSPKG_NAME}_exe ${OSPKG_NAME} NO_CACHE QUIET)
  else()
    find_package(${OSPKG_NAME} QUIET)
  endif()

  if("${${OSPKG_NAME}_FOUND}" OR (${OSPKG_PROGRAM} AND (NOT ${OSPKG_NAME}_exe STREQUAL "${OSPKG_NAME}_exe-NOTFOUND")))
    message(CHECK_PASS "found")
  else()
    message(CHECK_FAIL "not found")
    install_hint(MACOS ${OSPKG_MACOS} UBUNTU ${OSPKG_UBUNTU} ALMA ${OSPKG_ALMA})
  endif()
endmacro()

check_os_package(NAME OpenSSL
                 MACOS openssl@3
                 UBUNTU libssl-dev
                 ALMA openssl-devel)

check_os_package(NAME ZLIB
                 MACOS zlib
                 UBUNTU zlib1g-dev
                 ALMA zlib-devel)

check_os_package(NAME ZSTD
                 MACOS zstd
                 UBUNTU libzstd-dev
                 ALMA libzstd-devel)


check_os_package(NAME Curses
                 MACOS ncurses
                 UBUNTU libncurses5-dev
                 ALMA ncurses-devel)

check_os_package(NAME EXPAT
                 MACOS expat
                 UBUNTU libexpat-dev
                 ALMA expat-devel)

check_os_package(NAME XercesC
                 MACOS xerces-c
                 UBUNTU libxerces-c-dev
                 ALMA "epel-release xerces-c-devel")

check_os_package(PROGRAM
                 NAME rsync
                 MACOS rsync
                 UBUNTU rsync
                 ALMA rsync)

check_os_package(NAME Freetype
                 MACOS freetype
                 UBUNTU libfreetype-dev
                 ALMA freetype-devel)

check_os_package(NAME LibLZMA
                 MACOS xz # not sure about the name
                 UBUNTU liblzma-dev
                 ALMA xz-devel)

check_os_package(NAME LZ4
                 MACOS lz4
                 UBUNTU liblz4-dev
                 ALMA lz4-devel)

check_os_package(NAME X11
                 MACOS libx11
                 UBUNTU libx11-dev
                 ALMA libX11-devel)

if(NOT X11_Xpm_FOUND)
  install_hint(MACOS libxpm UBUNTU libxpm-dev ALMA libXpm-devel)
endif()

if(NOT X11_Xft_FOUND)
  install_hint(MACOS libxft UBUNTU libxft-dev ALMA libXft-devel)
endif()

if(NOT X11_Xext_FOUND)
  install_hint(MACOS libxext UBUNTU libxext-dev ALMA libXext-devel)
endif()

check_os_package(NAME OpenGL
                 MACOS this-should-already-exist
                 UBUNTU libglu1-mesa-dev
                 ALMA "mesa-libGL-devel mesa-libGLU-devel")

if(NOT OPENGL_GLU_FOUND)
  install_hint(MACOS glu
               UBUNTU libglu1-mesa-dev
               ALMA mesa-libGLU-devel)
endif()

check_os_package(NAME LibXml2
                 MACOS libxml2
                 UBUNTU libxml2-dev
                 ALMA libxml2-devel)

check_os_package(PROGRAM NAME git
                 MACOS git
                 UBUNTU git
                 ALMA git)

set(openssl_option "")
if(APPLE)
  execute_process(COMMAND brew --prefix openssl
                  OUTPUT_VARIABLE OPENSSL_DIR)
  set(openssl_option "--with-openssl=${OPENSSL_DIR}")
  message(STATUS "Sysroot: ${CMAKE_OSX_SYSROOT}")
endif()


# Determine current platform as minimum from SYSROOT
if(APPLE)
  find_program(jq_exe jq REQUIRED)
  execute_process(COMMAND ${jq_exe} -r .MinimalDisplayName ${CMAKE_OSX_SYSROOT}/SDKSettings.json
                  COMMAND_ERROR_IS_FATAL ANY
                  OUTPUT_VARIABLE CMAKE_OSX_DEPLOYMENT_TARGET
                  OUTPUT_STRIP_TRAILING_WHITESPACE)
  message(STATUS "Minimal macOS Deployment Target determined to: ${CMAKE_OSX_DEPLOYMENT_TARGET}")
endif()

set(python_sdkroot "")
set(python_deployment_target "")
set(python_cflags "-fPIC")
if(APPLE)
  set(python_sdkroot "SDKROOT=${CMAKE_OSX_SYSROOT}")
  set(python_deployment_target "MACOSX_DEPLOYMENT_TARGET=${CMAKE_OSX_DEPLOYMENT_TARGET}")
endif()


ExternalProject_Add(python
  PREFIX python
  URL https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz
  URL_HASH SHA1=459959e7dbeb89931db03457ea1f012353e34bce
  BUILD_IN_SOURCE ON
  CONFIGURE_COMMAND ${CMAKE_COMMAND} -E env "CFLAGS=${python_cflags}" ${python_sdkroot} ${python_deployment_target} ./configure --prefix=${CMAKE_INSTALL_PREFIX} --enable-optimizations --with-lto ${openssl_option}
  BUILD_COMMAND make -j${CMAKE_BUILD_PARALLEL_LEVEL}
)

string(REGEX MATCH "^([0-9]+\\.[0-9]+)\\.[0-9]+$" _temp ${PYTHON_VERSION})
set(PYTHON_VERSION_MAJOR_MINOR ${CMAKE_MATCH_1})

# We're guessing / expecting this file to be there when it's needed
set(Python_EXECUTABLE "${CMAKE_INSTALL_PREFIX}/bin/python${PYTHON_VERSION_MAJOR_MINOR}")
set(_python_stamp_file "${CMAKE_CURRENT_BINARY_DIR}/python_exe_exists.stamp")

message(STATUS "Python executable: ${Python_EXECUTABLE}")

add_custom_command(OUTPUT ${_python_stamp_file}
  COMMAND ${CMAKE_COMMAND} -E env sh -c "( test -e ${Python_EXECUTABLE} && touch ${_python_stamp_file} ) || ( echo 'Python executable not found at ${Python_EXECUTABLE}!' && exit 1 )"
  VERBATIM
  COMMENT "Checking for Python executable"
  DEPENDS python)
add_custom_target(PythonExeExists DEPENDS ${_python_stamp_file})

ExternalProject_Add(boost
  PREFIX boost
  URL https://github.com/boostorg/boost/releases/download/boost-${BOOST_VERSION}/boost-${BOOST_VERSION}.tar.gz
  URL_HASH SHA1=75366b414f6d0f34533af9ea43acd58bb886e953
  CMAKE_ARGS
  -DBUILD_SHARED_LIBS=ON
  -DBoost_VERBOSE=ON
  # Revisit in boost 1.86.0 ish with BOOST_NUMERIC_ODEINT_NO_ADAPTORS
  -DBOOST_EXCLUDE_LIBRARIES=numeric/odeint
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}
  BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)

ExternalProject_Add(eigen
  PREFIX eigen
  URL https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VERSION}/eigen-${EIGEN_VERSION}.tar.gz
  URL_HASH SHA1=d222db69a9e87d9006608e029d1039039f360b52
  CMAKE_ARGS
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}
)

ExternalProject_Add(tbb
  PREFIX tbb
  URL https://github.com/oneapi-src/oneTBB/archive/refs/tags/v${TBB_VERSION}.tar.gz
  URL_HASH SHA1=740e86b703f42446ddde392b73a9db3dc0f5f4cd
  CMAKE_ARGS
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}
  -DTBB_TEST=OFF
  BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)

ExternalProject_Add(geant4
  PREFIX geant4
  URL https://gitlab.cern.ch/geant4/geant4/-/archive/v${GEANT4_VERSION}/geant4-v${GEANT4_VERSION}.tar.gz
  URL_HASH SHA1=69119e8a29d3bd79b70886dc7ae78e6d008e1753
  CMAKE_ARGS
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}
  -DGEANT4_BUILD_TLS_MODEL=global-dynamic
  -DGEANT4_INSTALL_DATA=OFF
  -DGEANT4_USE_GDML=ON
  -DGEANT4_USE_SYSTEM_EXPAT=ON
  -DGEANT4_USE_SYSTEM_ZLIB=ON
  BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)


ExternalProject_Add(hepmc3
  PREFIX hepmc3
  URL https://gitlab.cern.ch/hepmc/HepMC3/-/archive/${HEPMC3_VERSION}/HepMC3-${HEPMC3_VERSION}.tar.gz
  URL_HASH SHA1=d4c12f5d50a507cd60cf2377286cbd23f59f1f84
  CMAKE_ARGS
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}
  -DHEPMC3_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DHEPMC3_BUILD_STATIC_LIBS=OFF
  -DHEPMC3_ENABLE_PYTHON=OFF
  -DHEPMC3_ENABLE_ROOTIO=OFF
  -DHEPMC3_ENABLE_SEARCH=OFF
  BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)

# Hard-coding the compile flags is not ideal, but seems needed
set(pythia_configure  "./configure")
list(APPEND pythia_configure "--prefix=${CMAKE_INSTALL_PREFIX}")
set(pythia_cxx_common "-O2 -std=c++${CMAKE_CXX_STANDARD} -pedantic -W -Wall -Wshadow -fPIC -pthread")
if(APPLE)
  list(APPEND pythia_configure "--cxx-common='-isysroot ${CMAKE_OSX_SYSROOT} ${pythia_cxx_common}'")
else()
  list(APPEND pythia_configure "--cxx-common='${pythia_cxx_common}'")
endif()

ExternalProject_Add(pythia8
  PREFIX pythia8
  DEPENDS PythonExeExists
  URL https://pythia.org/download/pythia83/pythia8${PYTHIA8_VERSION}.tgz
  URL_HASH SHA1=3186d9c289914896838f1050610121870854eb36
  BUILD_IN_SOURCE ON
  PATCH_COMMAND patch -p1 -i ${CMAKE_CURRENT_SOURCE_DIR}/pythia8-forward-decl.patch
  CONFIGURE_COMMAND ${CMAKE_COMMAND} -E env CXX=${CMAKE_CXX_COMPILER} CC=${CMAKE_C_COMPILER} ${pythia_configure}
  BUILD_COMMAND make -j${CMAKE_BUILD_PARALLEL_LEVEL}
)

ExternalProject_Add(nlohmann_json
  PREFIX nlohmann_json
  URL https://github.com/nlohmann/json/archive/refs/tags/v${JSON_VERSION}.tar.gz
  URL_HASH SHA1=1b0701dc7fdc068aad8ce68fc3e019a038232437
  CMAKE_ARGS
  -DJSON_BuildTests=OFF
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)

ExternalProject_Add(geomodel
  PREFIX geomodel
  DEPENDS nlohmann_json geant4 eigen
  URL https://gitlab.cern.ch/GeoModelDev/GeoModel/-/archive/${GEOMODEL_VERSION}/GeoModel-${GEOMODEL_VERSION}.tar.gz
  URL_HASH SHA1=6e0fa7eaf6502b34f82d008cddeb7b05ec0ebc63
  CMAKE_ARGS
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}
  -DGEOMODEL_BUILD_GEOMODELG4=ON
  BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)


# the builtin vdt build requires a python to be on path: we happen to have one ready
set(root_vdt_path "${CMAKE_INSTALL_PREFIX}/bin:$ENV{PATH}")

ExternalProject_Add(root
  PREFIX root
  DEPENDS nlohmann_json PythonExeExists tbb
  URL https://root.cern/download/root_v${ROOT_VERSION}.source.tar.gz
  URL_HASH ${ROOT_HASH}
  LIST_SEPARATOR |
  CMAKE_ARGS
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_PREFIX_PATH=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DPython_EXECUTABLE=${Python_EXECUTABLE}
  -DPython3_EXECUTABLE=${Python_EXECUTABLE}
  -Dfail-on-missing=ON
  -Dgdml=ON
  -Dx11=ON
  -Dpyroot=ON
  -Ddataframe=ON
  -Dmysql=OFF
  -Doracle=OFF
  -Dpgsql=OFF
  -Dsqlite=OFF
  -Dpythia6=OFF
  -Dpythia8=OFF
  -Dfftw3=OFF
  -Dbuiltin_tbb=OFF
  -Dbuiltin_cfitsio=ON
  -Dbuiltin_xxhash=ON
  -Dbuiltin_afterimage=ON
  -Dbuiltin_openssl=OFF
  -Dbuiltin_ftgl=ON
  -Dbuiltin_gsl=ON
  -Dbuiltin_gl2ps=ON
  -Dbuiltin_glew=ON
  -Dbuiltin_pcre=ON
  -Dgfal=OFF
  -Ddavix=OFF
  -Dbuiltin_vdt=ON
  -Dxrootd=OFF
  -Dtmva=OFF
  BUILD_COMMAND ${CMAKE_COMMAND} -E env PATH=${root_vdt_path} CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)


ExternalProject_Add(podio
  PREFIX podio
  DEPENDS root PythonExeExists
  URL https://github.com/AIDASoft/podio/archive/refs/tags/v${PODIO_VERSION}.tar.gz
  URL_HASH SHA1=600b99000f0c7a6c25c04a8b32bebf70413c9f57
  LIST_SEPARATOR |
  CMAKE_ARGS
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DPython_EXECUTABLE=${Python_EXECUTABLE}
  -DCMAKE_PREFIX_PATH=${CMAKE_INSTALL_PREFIX}
  -DBUILD_TESTING=OFF
  -DUSE_EXTERNAL_CATCH2=OFF
  BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)

set(venv_dir ${CMAKE_CURRENT_BINARY_DIR}/venv)
set(venv_python_exe ${venv_dir}/bin/python)

add_custom_command(OUTPUT ${venv_python_exe}
                   COMMAND ${Python_EXECUTABLE} -m venv ${venv_dir} && ${venv_python_exe} -m pip install --upgrade pip jinja2 pyyaml
                   COMMENT "Creating virtualenv for edm4hep build"
                   DEPENDS PythonExeExists)

add_custom_target(PythonVenv DEPENDS ${venv_python_exe})

ExternalProject_Add(edm4hep
  PREFIX edm4hep
  DEPENDS podio PythonVenv
  URL https://github.com/key4hep/EDM4hep/archive/refs/tags/v${EDM4HEP_VERSION}.tar.gz
  URL_HASH SHA1=7781f6c85bd2e88480b699a3a554182fd8bb75bf
  LIST_SEPARATOR |
  CMAKE_ARGS
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_PREFIX_PATH=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DPython_EXECUTABLE=${venv_python_exe}
  -DBUILD_TESTING=OFF
  -DUSE_EXTERNAL_CATCH2=OFF
  BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
)

ExternalProject_Add(dd4hep
  PREFIX dd4hep
  DEPENDS edm4hep geant4 PythonExeExists boost nlohmann_json
  URL https://github.com/AIDASoft/DD4hep/archive/v${DD4HEP_VERSION}.tar.gz
  URL_HASH SHA256=435d25a7ef093d8bf660f288b5a89b98556b4c1c293c55b93bf641fb4cba77e9
  LIST_SEPARATOR |
  PATCH_COMMAND patch -p1 -i ${CMAKE_CURRENT_SOURCE_DIR}/dd4hep-python.patch
  BUILD_COMMAND ${CMAKE_COMMAND} -E env LD_LIBRARY_PATH=${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR} CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND}  --build <BINARY_DIR>
  CONFIGURE_COMMAND ${CMAKE_COMMAND} -E env LD_LIBRARY_PATH=${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR}  ${CMAKE_COMMAND} -S <SOURCE_DIR> -B <BINARY_DIR>
  -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
  -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
  -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
  -DCMAKE_PREFIX_PATH=${CMAKE_INSTALL_PREFIX}
  -DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}
  -DCMAKE_OSX_DEPLOYMENT_TARGET=${CMAKE_OSX_DEPLOYMENT_TARGET}
  -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
  -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER}
  -DPython_EXECUTABLE=${Python_EXECUTABLE}
  -DBUILD_TESTING=OFF
  -DDD4HEP_BUILD_PACKAGES=DDG4|DDDetectors|DDRec|UtilityApps
  -DDD4HEP_USE_GEANT4=ON
  -DDD4HEP_USE_XERCESC=ON
  -DDD4HEP_USE_EDM4HEP=ON
)

ExternalProject_Add(fastjet
  PREFIX fastjet
  DEPENDS PythonExeExists
  URL https://fastjet.fr/repo/fastjet-${FASTJET_VERSION}.tar.gz
  URL_HASH SHA256=cc175471bfab8656b8c6183a8e5e9ad05d5f7506e46f3212a9a8230905b8f6a3
  CONFIGURE_COMMAND ${CMAKE_COMMAND} -E env CXX=${CMAKE_CXX_COMPILER} <SOURCE_DIR>/configure --prefix=${CMAKE_INSTALL_PREFIX} --enable-allplugins --enable-pyext
  BUILD_COMMAND make -j${CMAKE_BUILD_PARALLEL_LEVEL}
)

option(BUILD_ACTS "Build ACTS" OFF)

if(BUILD_ACTS)
  message(STATUS "Building ACTS")
  ExternalProject_Add(acts
    PREFIX acts
    DEPENDS root podio edm4hep geant4 nlohmann_json boost dd4hep tbb eigen pythia8 hepmc3 PythonVenv
    SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/acts
    LIST_SEPARATOR |
    CMAKE_ARGS
    -DCMAKE_CXX_COMPILER_LAUNCHER=${CMAKE_CXX_COMPILER_LAUNCHER}
    -DCMAKE_CXX_STANDARD=${CMAKE_CXX_STANDARD}
    -DCMAKE_INSTALL_PREFIX=${CMAKE_INSTALL_PREFIX}/acts
    -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
    -DCMAKE_PREFIX_PATH=${CMAKE_INSTALL_PREFIX}
    -DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}
    -DPython_EXECUTABLE=${venv_python_exe}
    # ACTS args
    -DACTS_BUILD_EVERYTHING=ON
    BUILD_COMMAND ${CMAKE_COMMAND} -E env CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL} ${CMAKE_COMMAND} --build <BINARY_DIR>
  )
endif()
