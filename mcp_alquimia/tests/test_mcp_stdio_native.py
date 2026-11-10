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


def server_parameters(repository_root, engine_process_executable, tmp_path):
    """Launch the MCP server module over stdio against the prebuilt engine process."""
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
    return parameters


@pytest.mark.parametrize("engine", ["PFloTran", "CrunchFlow"])
async def test_stdio_native_lifecycle(repository_root, engine_process_executable, tmp_path, engine):
    """Discover tools, react twice, and verify cleanup after the client disconnects."""
    parameters = server_parameters(repository_root, engine_process_executable, tmp_path)
    arguments = {"case": "calcite-short", "engine": engine}
    async with Client(parameters, read_timeout_seconds=30) as client:
        assert len((await client.list_tools()).tools) == 9
        result = await client.call_tool("setup", arguments)
        assert not result.is_error, result
        # Parsed JSON
        setup = result.structured_content
        session_id = setup["session_id"]
        initialized = await client.call_tool("initialize", {"session_id": session_id})
        assert not initialized.is_error, initialized
        assert initialized.structured_content["state"]["time"] == 0
        # One looped request, then a single step
        for time_config in ({"dt": 1.0, "max_steps": 2}, {"dt": 3.0, "max_steps": 1}):
            result = await client.call_tool(
                "react", {"session_id": session_id, "time_config": time_config})
            assert not result.is_error, result
            assert result.structured_content["steps_executed"] == time_config["max_steps"]
        final = result.structured_content["state"]
        assert (final["time"], final["completed_steps"]) == (5, 3)
        assert result.structured_content["current_time"] == 5
        polled = await client.call_tool("get_results", {"session_id": session_id})
        assert polled.structured_content["state"] == final
        assert final["pH"] is not None
        # Explicit close is exercised through the real transport too. Leave a
        # second session open to exercise EOF/lifespan cleanup below.
        closed = await client.call_tool("close", {"session_id": session_id})
        assert not closed.is_error, closed
        leftover = await client.call_tool("setup", arguments)
        assert not leftover.is_error, leftover
    run_dir = Path(setup["run_dir"])
    saved = json.loads((run_dir / "simulation_results.json").read_text())
    assert saved["status"] == "closed"
    assert saved["final"] == final
    assert len(saved["history"]) == 3
    operations = [json.loads(row) for row in (run_dir / "operations.jsonl").read_text().splitlines()]
    assert operations[-1] == {"operation": "react", "dt": 3.0, "max_steps": 1}
    leftover_dir = Path(leftover.structured_content["run_dir"])
    assert json.loads((leftover_dir / "simulation_results.json").read_text())["status"] == "closed"
    assert (run_dir / "engine_output.log").is_file()


async def test_stdio_native_chemistry_compared_across_sessions(
    repository_root,engine_process_executable, tmp_path
):
    """One session per engine, same request: both engines agree on the changed initial state."""
    parameters = server_parameters(repository_root, engine_process_executable, tmp_path)
    initial = {}
    async with Client(parameters, read_timeout_seconds=30) as client:
        for engine in ("PFloTran", "CrunchFlow"):
            for request in ({}, {"chemistry": {"aqueous": {"Ca++": 1e-3}}}):
                setup = await client.call_tool(
                    "setup", {"case": "calcite-short", "engine": engine, "request": request}
                )
                assert not setup.is_error, setup
                session_id = setup.structured_content["session_id"]
                species = setup.structured_content["metadata"]["primary_species"]
                initialized = await client.call_tool("initialize", {"session_id": session_id})
                assert not initialized.is_error, initialized
                state = initialized.structured_content["state"]
                initial[engine, bool(request)] = state["total_mobile"][species.index("Ca++")]
                reacted = await client.call_tool(
                    "react", {"session_id": session_id, "time_config": {"max_steps": 2}}
                )
                assert not reacted.is_error, reacted
                closed = await client.call_tool("close", {"session_id": session_id})
                assert not closed.is_error, closed
    for engine in ("PFloTran", "CrunchFlow"):
        # Total Ca++ is reported in mol/L: 1e-3 mol/kg times about 0.997 kg/L
        assert initial[engine, False] == pytest.approx(1e-5, rel=1e-2)
        assert initial[engine, True] == pytest.approx(1e-3 * 0.99716, rel=1e-3), engine
