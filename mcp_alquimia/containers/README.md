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

The launcher verifies that the daemon is rootless, uses an existing image without pulling, disables container networking, and mounts only the results directory at `/.alquimia/runs`, mirroring the host path `~/.alquimia/runs`. It uses container UID 0, which maps to your ordinary host UID under rootless Docker; this keeps bind-mounted artifacts accessible to you. The image itself defaults to an unprivileged container user for other launch methods. Do not add `-t`: MCP requires unmodified stdin/stdout.

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

The container user depends on the daemon. A rootful daemon needs your UID:GID, because container UIDs are host UIDs. A rootless daemon needs container UID 0, because only UID 0 maps to your host user; any other UID becomes a subordinate UID that cannot write your results directory. The rootless variant also stores the daemon socket in the command, since MCP clients do not reliably pass `DOCKER_HOST` to servers.

```bash
mkdir -p ~/.alquimia/runs   # A bind mount requires an existing source directory.

if docker info --format '{{json .SecurityOptions}}' | grep -q rootless; then
  # Rootless daemon: record its socket, defaulting to the standard location.
  alquimia_docker=(docker --host "${DOCKER_HOST:-unix://$XDG_RUNTIME_DIR/docker.sock}")
  alquimia_user=(--user=0:0)
else
  # Rootful system daemon (docker group).
  alquimia_docker=(docker)
  alquimia_user=(--user "$(id -u):$(id -g)" --env=HOME=/tmp)
fi

alquimia_mcp=("${alquimia_docker[@]}" run --rm -i --init --pull=never --network=none
  "${alquimia_user[@]}" --cap-drop=ALL --security-opt=no-new-privileges
  --mount "type=bind,src=$HOME/.alquimia/runs,dst=/.alquimia/runs"
  ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0)

# Claude Code: user scope makes the server available in every directory.
claude mcp add -s user alquimia -- "${alquimia_mcp[@]}"
claude mcp list

# Codex CLI: writes [mcp_servers.alquimia] to ~/.codex/config.toml.
codex mcp add alquimia -- "${alquimia_mcp[@]}"
codex mcp list
```

The snippet picks the variant from the daemon that `docker` currently reaches; the image must have been pulled into that same daemon. A rootless daemon started by `start_rootless_docker.sh` must stay running while clients use the server.

Codex stops waiting for an MCP tool after 60 seconds by default. For long `react` calls, add `tool_timeout_sec = 600` under `[mcp_servers.alquimia]` in `~/.codex/config.toml`. In either client, start with a request such as "list the alquimia cases, then run calcite-short-pc-pflotran".

The server marks `list_cases` and `get_results` read-only and the session tools as state-changing, so clients ask before `setup`, `initialize`, `react`, and `close`, and Claude Code's auto mode may refuse them where no project context explains the server. To approve this server's tools once for every directory:

- Claude Code: add `"mcp__alquimia__*"` to `permissions.allow` in `~/.claude/settings.json` (merge it into existing settings), then restart Claude Code. A `.claude/settings.local.json` with the same rule limits approval to one directory.

  ```json
  { "permissions": { "allow": ["mcp__alquimia__*"] } }
  ```

- Codex CLI: add `default_tools_approval_mode = "approve"` under `[mcp_servers.alquimia]` in `~/.codex/config.toml`; `[mcp_servers.alquimia.tools.<tool>]` with `approval_mode` sets one tool.

The rule covers only this server, which has no network access and writes only to its results directory.

### macOS and Windows (Docker Desktop)

Docker Desktop runs containers in its own Linux VM and gives bind-mounted host folders to any container user, so these commands keep the image's default unprivileged user and need no UID mapping. Install [Docker Desktop](https://docs.docker.com/desktop/), keep it running while the client uses the server, and run `docker login ghcr.io -u <github-username>` as above. The image is Linux x86-64 only: Intel Macs and x86-64 Windows run it natively, while Apple Silicon runs it under Rosetta (enable "Use Rosetta for x86_64/amd64 emulation on Apple Silicon" in Docker Desktop's General settings), which is slower.

macOS (Terminal, zsh or bash):

```bash
docker pull --platform=linux/amd64 ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0
mkdir -p ~/.alquimia/runs
alquimia_mcp=(docker run --rm -i --init --pull=never --network=none
  --platform=linux/amd64 --cap-drop=ALL --security-opt=no-new-privileges
  --mount "type=bind,src=$HOME/.alquimia/runs,dst=/.alquimia/runs"
  ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0)
claude mcp add -s user alquimia -- "${alquimia_mcp[@]}"
codex mcp add alquimia -- "${alquimia_mcp[@]}"
```

Windows (Command Prompt, `cmd.exe`, which passes `--` and quotes through unchanged; PowerShell may not):

```bat
docker pull ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0
mkdir "%USERPROFILE%\.alquimia\runs"
claude mcp add -s user alquimia -- docker run --rm -i --init --pull=never --network=none --cap-drop=ALL --security-opt=no-new-privileges --mount "type=bind,src=%USERPROFILE%\.alquimia\runs,dst=/.alquimia/runs" ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0
codex mcp add alquimia -- docker run --rm -i --init --pull=never --network=none --cap-drop=ALL --security-opt=no-new-privileges --mount "type=bind,src=%USERPROFILE%\.alquimia\runs,dst=/.alquimia/runs" ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0
```
Or 

```bat
explorer $env:USERPROFILE\.codex
```

add MCP to the config.toml
```toml
[mcp_servers.alquimia]
command = "docker"
args = ["run", "--rm", "-i", "--init", "--pull=never", "--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--mount", "type=bind,src=C:/Users/mechrev/.alquimia/runs,dst=/.alquimia/runs", "ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0"]
```

Results appear under `~/.alquimia/runs` (`%USERPROFILE%\.alquimia\runs` on Windows). The Codex timeout and the tool approvals above apply unchanged; on Windows the files are `%USERPROFILE%\.claude\settings.json` and `%USERPROFILE%\.codex\config.toml`. If a session fails with a permission error under `/.alquimia/runs`, add `--user=0:0` before the image name; it is root only inside Docker Desktop's VM.

To transfer a built image without publishing it:

```bash
docker save -o build/mcp_alquimia/alquimia-mcp.tar alquimia-mcp:local
# On another Linux x86-64 machine with a configured rootless daemon:
docker load -i alquimia-mcp.tar
```
