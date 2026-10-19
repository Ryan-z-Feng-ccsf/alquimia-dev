"""Exercise the production image with a real MCP client and inspect saved results.
Ensure the rootless Docker daemon is running first: ./containers/start_rootless_docker.sh
"""

import argparse
import json
from pathlib import Path
import sys
import tempfile

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def smoke(image: str, docker_host: str | None, output: Path) -> dict:
    """Run each supported engine through container stdio and verify its artifacts.

    Args:
        image: Existing local image tag; the launcher does not pull images.
        docker_host: Optional explicit rootless Docker socket.
        output: New directory containing bind-mounted runs and the test report.

    Returns:
        Summary of the image, exercised cases, and persisted results.

    Raises:
        AssertionError: An MCP operation or persisted-state check fails.
    """
    project = Path(__file__).resolve().parents[1]
    args = [
        str(project / "containers/run_container.py"),
        "--image", image,
        "--run-root", str(output / "runs")
    ]
    if docker_host:
        args.extend(["--docker-host", docker_host])
    summary = {"image": image, "cases": []}
    # arg: project_root/containers/run_container.py --image ... --run-root ... --docker-host ...
    async with Client(StdioServerParameters(command=sys.executable, args=args),
                      read_timeout_seconds=60) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 6
        # 3 different engines
        cases = await client.call_tool("list_cases")
        assert not cases.is_error, cases
        for case in cases.structured_content["cases"]:
            # An *-ac-* case names a driver_ic condition its input does not define,
            # so the client must pick one that exists; max_steps covers the two reacts.
            request = {"max_steps": 2}
            if case["requires_initial_condition"]:
                request["initial_condition"] = "initial"
            setup = await client.call_tool("setup", {"case": case["case"], "request": request})
            assert not setup.is_error, setup
            session_id = setup.structured_content["session_id"]
            initialized = await client.call_tool("initialize", {"session_id": session_id})
            assert not initialized.is_error, initialized
            for dt in (2.0, 3.0):
                result = await client.call_tool("react", {"session_id": session_id, "dt": dt})
                assert not result.is_error, result
            final = result.structured_content["state"]
            assert (final["time"], final["max_steps"]) == (5, 2)
            polled = await client.call_tool("get_results", {"session_id": session_id})
            assert not polled.is_error, polled
            assert polled.structured_content["state"] == final
            closed = await client.call_tool("close", {"session_id": session_id})
            assert not closed.is_error, closed
            saved = json.loads((output / "runs" / session_id / "simulation_results.json").read_text())
            assert saved["status"] == "closed" and saved["final"] == final
            summary["cases"].append(
                {
                    "case": case["case"],
                    "session_id": session_id,
                    "final": final
                }
            )

        leftover = await client.call_tool("setup", {"case": "calcite-short-pc-pflotran"})
        assert not leftover.is_error, leftover
        leftover_id = leftover.structured_content["session_id"]
    # Test the forcily exit (close all the sessions)
    saved = json.loads((output / "runs" / leftover_id / "simulation_results.json").read_text())
    assert saved["status"] == "closed"
    return summary


def main() -> None:
    """Save a production-container smoke report under build/mcp_alquimia."""
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    # Default parameters in ./containers/run_container.py
    parser.add_argument("--image", default="alquimia-mcp:local")
    # Default parameters in ./containers/run_container.py
    parser.add_argument("--docker-host")
    args = parser.parse_args()
    reports = repository / "build/mcp_alquimia/test_reports"
    reports.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="smoke-", dir=reports))
    summary = anyio.run(smoke, args.image, args.docker_host, output)
    (output / "smoke_report.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"All three engines passed container MCP smoke tests. Report: {output}")


if __name__ == "__main__":
    main()
