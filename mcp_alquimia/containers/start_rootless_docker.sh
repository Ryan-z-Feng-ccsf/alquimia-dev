#!/usr/bin/env bash
# Run a dedicated, foreground Docker daemon as the current unprivileged user.
set -euo pipefail

# Get the repo absolute path
alquimia_repository=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# For container image
alquimia_data="$alquimia_repository/build/mcp_alquimia/rootless-docker"
alquimia_runtime="${XDG_RUNTIME_DIR:?Set XDG_RUNTIME_DIR to a private writable runtime directory}/alquimia-docker"

# Can't be sudo
if [[ $(id -u) == 0 ]]; then
  echo "Run this script as your normal user, without sudo." >&2
  exit 1
fi
# Check all the dependencies
dockerd-rootless-setuptool.sh check
mkdir -p "$alquimia_data" "$alquimia_runtime"
# Grant permission for executor (read, write, execute)
chmod 700 "$alquimia_runtime"
echo "Docker endpoint: unix://$alquimia_runtime/docker.sock" >&2
echo "Set DOCKER_HOST to that endpoint in your build/run terminal." >&2
# The rootlesskit will write tmp files
export DOCKERD_ROOTLESS_ROOTLESSKIT_STATE_DIR="$alquimia_runtime/rootlesskit"
exec dockerd-rootless.sh \
  --host "unix://$alquimia_runtime/docker.sock" \
  --data-root "$alquimia_data/data" \
  --exec-root "$alquimia_runtime/exec" \
  --pidfile "$alquimia_runtime/docker.pid" "$@"
