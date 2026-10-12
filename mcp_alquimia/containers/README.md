# Rootless Alquimia MCP container

This image builds Alquimia, PETSc, PFLOTRAN, CrunchFlow, ONNX Runtime, and the native engine process inside Ubuntu 22.04. It does not copy the host's MPI/compiler installation or `build/install`. It currently targets Linux x86-64, matching the ONNX Runtime archive used by the project's superbuild.

The host user needs no sudo. Docker's rootless prerequisites must already be available: user namespaces, `newuidmap`/`newgidmap`, subordinate UID/GID ranges, and the rootless Docker tools. Check with `dockerd-rootless-setuptool.sh check`. An administrator is needed if those host prerequisites are missing; the project scripts do not change them. See [Docker's rootless documentation](https://docs.docker.com/engine/security/rootless/).

From the repository root, start a dedicated daemon in a terminal:

```bash
bash mcp_alquimia/containers/start_rootless_docker.sh
```

It runs in the foreground; Ctrl-C stops it. Images and build cache remain in `build/mcp_alquimia/rootless-docker/`. It does not install a system service or change the default Docker context.

In a second terminal:

```bash
export DOCKER_HOST="unix://${XDG_RUNTIME_DIR}/alquimia-docker/docker.sock"
docker build -f mcp_alquimia/containers/Dockerfile -t alquimia-mcp:local .
```

The first build downloads and compiles the native dependencies and can take tens of minutes. Python-only edits reuse the native build layer. `--build-arg BUILD_JOBS=4` controls top-level build parallelism. Dependency revisions follow `cmake/Superbuild.cmake`, including its current CrunchFlow branch; this is not a fully pinned source build.

Configure an MCP client with [mcp_config.example.json](mcp_config.example.json), replacing the checkout path. The launcher needs only host Python 3 and Docker; Python MCP dependencies are inside the image. For a manual stdio launch:

```bash
python3 mcp_alquimia/containers/run_container.py
```

The launcher verifies that the daemon is rootless, uses an existing image without pulling, disables container networking, and mounts only the results directory at `/runs`. It uses container UID 0, which maps to your ordinary host UID under rootless Docker; this keeps bind-mounted artifacts accessible to you. The image itself defaults to an unprivileged container user for other launch methods. Do not add `-t`: MCP requires unmodified stdin/stdout.

Results persist under `build/mcp_alquimia/runs/<session_id>/`. Override the location with `--run-root`; use `--docker-host` or `DOCKER_HOST` for another rootless endpoint. Each MCP connection starts one container, and each simulation session starts an engine process inside it. Normal client disconnect closes all sessions and removes the container while preserving results.

Validate both the full test suite and the production image:

```bash
docker build -f mcp_alquimia/containers/Dockerfile --target test -t alquimia-mcp:test .
mcp_alquimia/.venv/bin/python mcp_alquimia/tests/smoke_container.py
```

The second command requires the host's MCP test environment. It exercises all three engines through actual container stdio, verifies host-visible results and disconnect cleanup, and saves a report under `build/mcp_alquimia/test_reports/`.

To transfer a built image without publishing it:

```bash
docker save -o build/mcp_alquimia/alquimia-mcp.tar alquimia-mcp:local
# On another Linux x86-64 machine with a configured rootless daemon:
docker load -i alquimia-mcp.tar
```
