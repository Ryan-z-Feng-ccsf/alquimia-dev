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

Results persist under `~/.alquimia/runs/<session_id>/`. Override the location with `--run-root`; use `--docker-host` or `DOCKER_HOST` for another rootless endpoint. Each MCP connection starts one container, and each simulation session starts an engine process inside it. Normal client disconnect closes all sessions and removes the container while preserving results.

Validate both the full test suite and the production image:

```bash
docker build -f mcp_alquimia/containers/Dockerfile --target test -t alquimia-mcp:test .
mcp_alquimia/.venv/bin/python mcp_alquimia/tests/smoke_container.py
```

The second command requires the host's MCP test environment. It exercises all three engines through actual container stdio, verifies host-visible results and disconnect cleanup, and saves a report under `build/mcp_alquimia/test_reports/`.

## Rootful Docker (system daemon)

Users in the host's `docker` group can run the published image on the system daemon at `/var/run/docker.sock` with [run_container_rootful.py](run_container_rootful.py). It is standalone (host Python 3 only), so copy it anywhere. The image is private: the publisher must grant your GitHub account read access to the `alquimia-mcp` package, and you log in once with a personal access token (classic) that has the `read:packages` scope:

```bash
docker login ghcr.io -u <github-username>
docker pull ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0
# Use the absolute path to your copy of the launcher; a missing file makes
# the client report the connection as closed.
claude mcp add alquimia -- python3 "$PWD/run_container_rootful.py"
```

A rootful daemon does not remap UIDs, so the launcher runs the container as your own UID:GID (not container root), drops all capabilities, and forbids privilege escalation; results stay owned by you under `~/.alquimia/runs/<session_id>/` (override with `--run-root`). Do not launch it with `sudo`. Like the rootless launcher, it never pulls: run `docker pull` again to update, and pass `--image` for another tag.

### Setup without the repository

The image contains the engines, server, and benchmark inputs; the launcher only assembles a `docker run` command. Without a checkout, register that command directly, after the `docker login` and `docker pull` above. The shell expands `$(id -u)`, `$(id -g)`, and `$HOME` once, so the client config stores literal values; the flags match the rootful launcher, minus its diagnostics (a missing image reports Docker's "No such image").

```bash
mkdir -p ~/.alquimia/runs   # A bind mount requires an existing source directory.
alquimia_mcp=(docker run --rm -i --init --pull=never --network=none
  --user "$(id -u):$(id -g)" --cap-drop=ALL --security-opt=no-new-privileges
  --env=HOME=/tmp --mount "type=bind,src=$HOME/.alquimia/runs,dst=/runs"
  ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0)

# Claude Code: user scope makes the server available in every directory.
claude mcp add -s user alquimia -- "${alquimia_mcp[@]}"
claude mcp list

# Codex CLI: writes [mcp_servers.alquimia] to ~/.codex/config.toml.
codex mcp add alquimia -- "${alquimia_mcp[@]}"
codex mcp list
```

Codex stops waiting for an MCP tool after 60 seconds by default. For long `react` calls, add `tool_timeout_sec = 600` under `[mcp_servers.alquimia]` in `~/.codex/config.toml`. In either client, start with a request such as "list the alquimia cases, then run calcite-short-pc-pflotran".

To transfer a built image without publishing it:

```bash
docker save -o build/mcp_alquimia/alquimia-mcp.tar alquimia-mcp:local
# On another Linux x86-64 machine with a configured rootless daemon:
docker load -i alquimia-mcp.tar
```
