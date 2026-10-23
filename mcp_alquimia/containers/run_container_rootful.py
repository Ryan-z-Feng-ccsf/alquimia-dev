"""Launch the published MCP image over stdio using the system (rootful) Docker daemon."""

import argparse
import json
import os
from pathlib import Path
import subprocess


def main() -> None:
    """Validate rootful Docker, bind artifact storage, and replace this process.

    A rootful daemon does not remap UIDs, so the container runs as the invoking
    host UID:GID; results written to the bind mount stay owned by that user
    instead of root. Stdout is reserved for MCP; there is no TTY, port
    publication, or image pull. Pull the image once beforehand with
    `docker pull`.

    Raises:
        SystemExit: Docker is unavailable, is rootless, lacks the image, or
            arguments are invalid.
        OSError: Creating the output directory or launching Docker fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image",
                        default="ghcr.io/ryan-z-feng-ccsf/alquimia-mcp:0.1.0")
    parser.add_argument("--docker-host", default=os.environ.get(
        "DOCKER_HOST", "unix:///var/run/docker.sock"))
    parser.add_argument("--run-root", type=Path,
                        default=Path.home() / ".alquimia/runs")
    args = parser.parse_args()
    if os.getuid() == 0:
        parser.exit(1, "Run this launcher as your normal user, not with sudo; "
                    "join the docker group for daemon access.\n")
    docker = ["docker", "--host", args.docker_host]
    try:
        info = subprocess.check_output(
            [
                *docker,
                "info",
                "--format",
                "{{json .SecurityOptions}}"
            ],
            text=True,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    # For docker group
    except (OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Docker unavailable at {args.docker_host}: {error}\n"
                    "Check that your account is in the docker group.\n")
    if "name=rootless" in json.loads(info):
        parser.exit(1, "This daemon is rootless; use run_container.py instead.\n")
    # Check if the image exists
    try:
        subprocess.run([*docker, "image", "inspect", args.image],
                       check=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        parser.exit(1, f"Image {args.image} not found locally. Run:\n"
                    f"  docker login ghcr.io\n  docker pull {args.image}\n")
    run_root = args.run_root.resolve()
    if "," in str(run_root):
        parser.error("--run-root cannot contain a comma (Docker mount syntax)")
    run_root.mkdir(parents=True, exist_ok=True)
    # Unlike the rootless launcher, container privileges here are real host
    # privileges, so drop all capabilities and block privilege escalation.
    command = [
        *docker,
        "run",
        "--rm",
        "--interactive",
        "--init",
        "--pull=never",
        "--network=none",
        f"--user={os.getuid()}:{os.getgid()}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--env=HOME=/tmp",
        "--mount",
        f"type=bind,src={run_root},dst=/runs",
        args.image,
    ]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
