"""Run real MCP stdio clients against all supported precompiled chemistry engines."""

import json
import os
from pathlib import Path
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest


pytestmark = [pytest.mark.native, pytest.mark.anyio]


@pytest.fixture
def anyio_backend():
    """Run SDK clients on asyncio without requiring trio."""
    return "asyncio"


@pytest.mark.parametrize("case", ["calcite-short-pc-pflotran", "calcite-short-cc-crunch", "ex8-nn-batch1"])
async def test_stdio_native_lifecycle(repository_root, engine_process_executable, tmp_path, case):
    """Discover tools, react twice, and verify cleanup after the client disconnects."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repository_root / "mcp_alquimia/src")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m", "mcp_alquimia.mcp_server",
            "--repository", str(repository_root),
            "--executable", str(engine_process_executable),
            "--run-root", str(tmp_path / "runs")
        ],
        env=env,
        cwd=tmp_path,
    )
    async with Client(parameters, read_timeout_seconds=30) as client:
        assert len((await client.list_tools()).tools) == 6
        result = await client.call_tool("setup", {"case": case})
        assert not result.is_error, result
        # Parsed JSON
        setup = result.structured_content
        session_id = setup["session_id"]
        initialized = await client.call_tool("initialize", {"session_id": session_id})
        assert not initialized.is_error, initialized
        assert initialized.structured_content["state"]["time"] == 0
        for dt in (2.0, 3.0):
            result = await client.call_tool("react", {"session_id": session_id, "dt": dt})
            assert not result.is_error, result
        final = result.structured_content["state"]
        assert (final["time"], final["max_steps"]) == (5, 2)
        polled = await client.call_tool("get_results", {"session_id": session_id})
        assert polled.structured_content["state"] == final
        assert (final["pH"] is None) == (case == "ex8-nn-batch1")
        # Explicit close is exercised through the real transport too. Leave a
        # second session open to exercise EOF/lifespan cleanup below.
        closed = await client.call_tool("close", {"session_id": session_id})
        assert not closed.is_error, closed
        leftover = await client.call_tool("setup", {"case": case})
        assert not leftover.is_error, leftover
    run_dir = Path(setup["run_dir"])
    saved = json.loads((run_dir / "simulation_results.json").read_text())
    assert saved["status"] == "closed"
    assert saved["final"] == final
    assert len(saved["history"]) == 3
    operations = [json.loads(row) for row in (run_dir / "operations.jsonl").read_text().splitlines()]
    assert operations[-1] == {"operation": "react", "timestep": 3.0}
    leftover_dir = Path(leftover.structured_content["run_dir"])
    assert json.loads((leftover_dir / "simulation_results.json").read_text())["status"] == "closed"
    assert (run_dir / "engine_output.log").is_file()
