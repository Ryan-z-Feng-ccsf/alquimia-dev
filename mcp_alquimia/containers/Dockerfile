# cd project root
# docker build -t alquimia-mcp:local -f mcp_alquimia/containers/Dockerfile .
# Ubuntu 22.04 supplies /usr/bin/python3.10, required by the PETSc superbuild.
FROM ubuntu:22.04 AS native-build
ARG BUILD_JOBS=4

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
# gcc/gfortran, openmpi, blas/lapack, cmake, python3.10 are requested
# git and make are required for the build process
# libhdf5-openmpi-dev is used in dev.yml and detected by Superbuild
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gfortran cmake git pkg-config ca-certificates \
    python3.10 python3.10-venv python3-pip libopenmpi-dev openmpi-bin \
    && rm -rf /var/lib/apt/lists/*
    
# Set compilers to MPI wrappers to ensure correct linkage
ENV CC=mpicc CXX=mpicxx FC=mpif90

# Set the working directory
WORKDIR /opt/alquimia

# Copy necessary files
COPY CMakeLists.txt LICENSE.txt ./
COPY cmake/ cmake/
COPY alquimia/ alquimia/
COPY drivers/ drivers/

# CMake Required
COPY unit_tests/ unit_tests/
COPY benchmarks/ benchmarks/

# Cmake and make build
RUN test "$(uname -m)" = x86_64 \
    && cmake -S . -B build -DALQUIMIA_SUPERBUILD=ON \
       -DCMAKE_BUILD_TYPE=Release -DXSDK_WITH_PFLOTRAN=ON \
       -DXSDK_WITH_CRUNCHFLOW=ON -DXSDK_WITH_ONNX=ON \
    && cmake --build build --parallel "${BUILD_JOBS}"

# Python/server edits reuse the expensive native-build layer.
FROM native-build AS server-build
ARG UV_VERSION=0.12.3
RUN python3.10 -m pip install --no-cache-dir "uv==${UV_VERSION}"

COPY models/ models/
COPY mcp_alquimia/ mcp_alquimia/
ENV UV_PROJECT_ENVIRONMENT=/opt/alquimia/venv UV_LINK_MODE=copy
# Build the entry point for the C subprocess
RUN uv sync --project mcp_alquimia --locked --no-dev --no-editable \
    && python3.10 mcp_alquimia/build_tools/build_alquimia_engine_process.py
# Cjson license is in build/install/share/licenses/cjson (through cmake Superbuild)
RUN mkdir -p build/install/share/licenses/petsc \
             build/install/share/licenses/pflotran \
             build/install/share/licenses/crunchflow \
             build/install/share/licenses/onnxruntime \
    && cp build/external/petsc/src/petsc/LICENSE build/install/share/licenses/petsc/ \
    && cp build/external/pflotran/src/pflotran/LICENSE build/install/share/licenses/pflotran/ \
    && cp build/external/crunchflow/src/crunchflow/license.txt \
          build/external/crunchflow/src/crunchflow/Legal.txt \
          build/external/crunchflow/src/crunchflow/CopyrightNotice-CrunchFlow.txt \
          build/install/share/licenses/crunchflow/ \
    && cp build/external/onnx/src/onnx/LICENSE \
          build/external/onnx/src/onnx/ThirdPartyNotices.txt \
          build/install/share/licenses/onnxruntime/

FROM ubuntu:22.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive

# chown grants permission
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3.10 libopenmpi3 openmpi-bin libgfortran5 libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 alquimia \
    && mkdir /runs && chown alquimia:alquimia /runs
WORKDIR /opt/alquimia
COPY --from=server-build /opt/alquimia/build/install/lib/ build/install/lib/
COPY --from=server-build /opt/alquimia/build/install/share/licenses/ licenses/
COPY --from=server-build /opt/alquimia/build/mcp_alquimia/bin/ build/mcp_alquimia/bin/
COPY --from=server-build /opt/alquimia/venv/ venv/

# Necessary .cfg / .in / .dbs / .dat / .dbsx
COPY --from=server-build /opt/alquimia/benchmarks/batch_chem/*.cfg \
     /opt/alquimia/benchmarks/batch_chem/*.in \
     /opt/alquimia/benchmarks/batch_chem/*.dbs \
     /opt/alquimia/benchmarks/batch_chem/*.dat \
     /opt/alquimia/benchmarks/batch_chem/*.dbsx benchmarks/batch_chem/
COPY --from=server-build /opt/alquimia/models/ models/
COPY LICENSE.txt ./

# Update the PATH for Python executable
# Update LD_LIBRARY_PATH so the executables can find the shared libraries
# PYTHONUNBUFFERED=1 Print info immediately
# PYTHONDONTWRITEBYTECODE=1 No Python cache file
ENV PATH="/opt/alquimia/venv/bin:${PATH}" \
    LD_LIBRARY_PATH=/opt/alquimia/build/install/lib \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER alquimia
ENTRYPOINT ["mcp-alquimia", "--repository", "/opt/alquimia", "--run-root", "/runs"]

# The validation image includes pytest and baseline drivers; the runtime does not.
FROM server-build AS test
RUN uv sync --project mcp_alquimia --locked --group test --no-editable

# chown -R grants permission recursively
ENV PATH="/opt/alquimia/venv/bin:${PATH}" \
    LD_LIBRARY_PATH=/opt/alquimia/build/install/lib
RUN useradd --create-home --uid 1000 alquimia \
    && chown -R alquimia:alquimia /opt/alquimia/build/mcp_alquimia
USER alquimia
RUN python mcp_alquimia/tests/run_pytest_pipeline.py --skip-build

# Keep the production runtime as the default target.
FROM runtime AS final
