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


def payload(result) -> dict:
    """Parse a successful tool result's single compact JSON text block."""
    assert not result.is_error, result
    assert result.structured_content is None
    text, = (block.text for block in result.content)
    return json.loads(text)


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
        assert len(tools.tools) == 9
        # The image ships the knowledge base index for every source
        for collection in ("pflotran", "crunchflow", "alquimia"):
            found = payload(await client.call_tool(
                "search_docs", {"query": "mineral", "collection": collection, "limit": 1}))
            hit, = found["results"]
            section = payload(await client.call_tool("read_doc", {"doc_id": hit["doc_id"]}))
            assert section["text"], section
        listing = payload(await client.call_tool("list_cases"))
        for case, entry in listing["cases"].items():
            for engine in entry["engines"]:
                arguments = {"case": case, "engine": engine}
                payload(await client.call_tool("get_chemistry", arguments))
                session_id = payload(await client.call_tool("setup", arguments))["session_id"]
                initialized = payload(
                    await client.call_tool("initialize", {"session_id": session_id}))
                assert (initialized["time"], initialized["total_steps"]) == (0, 0)
                # A looped request inside the engine, then a single step
                for time_config in ({"dt": 1.0, "max_steps": 2}, {"dt": 3.0, "max_steps": 1}):
                    result = payload(await client.call_tool(
                        "react", {"session_id": session_id, "time_config": time_config}))
                    assert result["steps"] == time_config["max_steps"]
                assert (result["time"], result["total_steps"]) == (5, 3)
                final = result["state"]
                polled = payload(await client.call_tool("get_results", {"session_id": session_id}))
                assert polled == {"time": 5, "total_steps": 3, "state": final}
                assert payload(await client.call_tool("close", {"session_id": session_id})) == {
                    "closed": True}
                saved = json.loads(
                    (output / "runs" / session_id / "simulation_results.json").read_text())
                # Artifacts keep the raw engine snapshot
                assert saved["status"] == "closed"
                assert (saved["final"]["time"], saved["final"]["total_steps"]) == (5, 3)
                summary["cases"].append(
                    {
                        "case": case,
                        "engine": engine,
                        "session_id": session_id,
                        "final": final
                    }
                )

        leftover_id = payload(await client.call_tool(
            "setup", {"case": "calcite-short", "engine": "PFloTran"}))["session_id"]
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
    print(f"Every case passed container MCP smoke tests. Report: {output}")


if __name__ == "__main__":
    main()
