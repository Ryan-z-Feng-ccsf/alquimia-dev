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


def payload(result):
    """Parse a successful tool result's single compact JSON text block."""
    assert not result.is_error, result
    assert result.structured_content is None
    text, = (block.text for block in result.content)
    return json.loads(text)


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
        setup = payload(await client.call_tool("setup", arguments))
        session_id = setup["session_id"]
        initialized = payload(await client.call_tool("initialize", {"session_id": session_id}))
        assert (initialized["time"], initialized["total_steps"]) == (0, 0)
        # One looped request, then a single step
        for time_config in ({"dt": 1.0, "max_steps": 2}, {"dt": 3.0, "max_steps": 1}):
            reacted = payload(await client.call_tool(
                "react", {"session_id": session_id, "time_config": time_config}
                )
            )
            assert reacted["steps"] == time_config["max_steps"]
        assert (reacted["time"], reacted["total_steps"], reacted["dt"]) == (5, 3, 3.0)
        final = reacted["state"]
        polled = payload(await client.call_tool("get_results", {"session_id": session_id}))
        assert polled == {"time": 5, "total_steps": 3, "state": final}
        # Name-keyed, rounded state: pH to 3 decimals, values to 6 significant digits
        assert final["pH"] == round(final["pH"], 3)
        assert set(final["aqueous_total"]) == {"H+", "HCO3-", "Ca++"}
        assert set(final["mineral_volume_fraction"]) == {"Calcite"}
        assert all(value == float(f"{value:.6g}") for value in final["aqueous_total"].values())
        # Explicit close is exercised through the real transport too. Leave a
        # second session open to exercise EOF/lifespan cleanup below.
        closed = await client.call_tool("close", {"session_id": session_id})
        assert not closed.is_error, closed
        leftover = payload(await client.call_tool("setup", arguments))
    runs = tmp_path / "runs"
    run_dir = runs / session_id
    saved = json.loads((run_dir / "simulation_results.json").read_text())
    assert saved["status"] == "closed"
    # Artifacts keep the raw, full-precision engine snapshot
    assert (saved["final"]["time"], saved["final"]["total_steps"]) == (5, 3)
    assert len(saved["history"]) == 3
    operations = [json.loads(row) for row in (run_dir / "operations.jsonl").read_text().splitlines()]
    assert operations[-1] == {"operation": "react", "dt": 3.0, "max_steps": 1}
    leftover_dir = runs / leftover["session_id"]
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
                setup = payload(await client.call_tool(
                    "setup", {"case": "calcite-short", "engine": engine, "request": request}
                ))
                session_id = setup["session_id"]
                initialized = payload(
                    await client.call_tool("initialize", {"session_id": session_id}))
                initial[engine, bool(request)] = initialized["state"]["aqueous_total"]["Ca++"]
                reacted = await client.call_tool(
                    "react", {"session_id": session_id, "time_config": {"max_steps": 2}}
                )
                assert not reacted.is_error, reacted
                closed = await client.call_tool("close", {"session_id": session_id})
                assert not closed.is_error, closed
    for engine in ("PFloTran", "CrunchFlow"):
        # Requests and results share mol/L water, so the requested value comes back.
        # CrunchFlow converts its mol/kg input back to mol/L with its own 25 degC
        # water density (~997.07 kg/m^3) rather than setup's density, a 1e-4 offset.
        # TODO: 
        # Known issue when initialized in CrunchFlow (Just like the temperature issue)
        assert initial[engine, False] == pytest.approx(1e-5, rel=1e-2)
        assert initial[engine, True] == pytest.approx(1e-3, rel=2e-4), engine
