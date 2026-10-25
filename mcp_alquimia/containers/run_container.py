"""Launch the MCP image over stdio using a rootless Docker daemon."""

import argparse
import json
import os
from pathlib import Path
import subprocess


def main() -> None:
    """Validate rootless Docker, bind artifact storage, and replace this process.

    The container's UID 0 maps to the invoking host user under rootless Docker,
    allowing bind-mounted results to remain readable and writable on the host.
    Stdout is reserved for MCP; there is no TTY, port publication, or image pull.

    Raises:
        SystemExit: Docker is unavailable, is not rootless, or arguments are invalid.
        OSError: Creating the output directory or launching Docker fails.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="alquimia-mcp:local")
    parser.add_argument("--docker-host", default=os.environ.get(
        "DOCKER_HOST", f"unix://{runtime}/alquimia-docker/docker.sock"))
    parser.add_argument("--run-root", type=Path,
                        default=Path.home() / ".alquimia/runs")
    args = parser.parse_args()
    docker = ["docker", "--host", args.docker_host]
    try:
        # docker --host "unix:///run/user/$(id -u)/alquimia-docker/docker.sock" info --format '{{json .SecurityOptions}}'
        # Return ["name=seccomp,profile=builtin", "name=rootless", "name=cgroupns"]
        info = subprocess.check_output(
            [*docker,
             "info",    
             "--format",
             "{{json .SecurityOptions}}"],
            text=True,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    # The sudo user is not available
    except (OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Rootless Docker unavailable at {args.docker_host}: {error}\n"
                    "Start containers/start_rootless_docker.sh first.\n")

    # launch the rootless Docker daemon first (./start_rootless_docker.sh)
    if "name=rootless" not in json.loads(info):
        parser.exit(1, "This launcher requires a rootless Docker daemon.\n")
    run_root = args.run_root.resolve()
    if "," in str(run_root):
        parser.error("--run-root cannot contain a comma (Docker mount syntax)")
    run_root.mkdir(parents=True, exist_ok=True)
    # Assemble container execution command:
    # - Enable stdio interaction and signal handling (--interactive, --init)
    # - Enforce strict isolation: disable networking (--network=none) and use local image only (--pull=never)
    # - Bind-mount run_root to /runs and rely on rootless UID mapping (--user=0:0)
    # - Automatically remove container upon exit (--rm)
    command = [
        *docker,
        "run",
        "--rm",
        "--interactive",
        "--init",
        "--pull=never",
        "--network=none",
        "--user=0:0",
        "--mount",
        f"type=bind,src={run_root},dst=/.alquimia/runs",
        args.image
    ]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
