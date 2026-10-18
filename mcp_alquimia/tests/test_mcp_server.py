"""Exercise MCP discovery, session ownership, and failures without native engines.

Run the npx @modelcontextprotocol/inspector uv run run_container.py for more info
"""

import json
from pathlib import Path
import sys
import threading
import time

import anyio
from mcp import Client
import pytest

from mcp_alquimia.mcp_server import create_server
from mcp_alquimia.session_manager import SessionManager
from mcp_alquimia.subprocess_client import EngineProcessError


@pytest.fixture
def anyio_backend():
    """Use the asyncio backend supported by the project's installed MCP SDK."""
    return "asyncio"


class FakeEngineProcess:
    """Small stateful stand-in used to probe ownership and concurrent requests."""

    instances = []
    fail_operation = None

    def __init__(self, executable, run_dir, timeout):
        self.run_dir = run_dir
        self.closed = False
        self.time = 0
        self.steps = 0
        self.setup_arguments = None
        self.instances.append(self)

    def request(self, operation, **parameters):
        """Return a snapshot or a deliberate engine failure."""
        if operation == "setup":
            self.setup_arguments = parameters
        if self.fail_operation == operation:
            raise EngineProcessError(f"{operation} failed; log: {self.run_dir}/engine_output.log")
        if operation == "setup":
            return {"primary_species": ["H"], "minerals": []}
        if operation == "react":
            previous = self.time
            time.sleep(0.01)
            self.time = previous + parameters["timestep"]
            self.steps += 1
        return {"time": self.time, "max_steps": self.steps, "total_mobile": [1.0]}

    def close(self):
        """Record that this process was reaped."""
        self.closed = True


@pytest.fixture
def manager(monkeypatch, repository_root, tmp_path):
    """Provide an isolated session owner using fake native processes."""
    monkeypatch.setattr("mcp_alquimia.session_manager.AlquimiaEngineProcess", FakeEngineProcess)
    monkeypatch.setattr(FakeEngineProcess, "instances", [])
    monkeypatch.setattr(FakeEngineProcess, "fail_operation", None)
    return SessionManager(
        repository_root,
        Path(sys.executable),
        tmp_path / "runs",
        max_sessions=2
        )


async def call(client, name, **arguments):
    """Call a tool and require a structured successful result."""
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result
    assert isinstance(result.structured_content, dict)
    return result.structured_content


@pytest.mark.anyio
async def test_discovery_and_missing_runtime(repository_root, tmp_path):
    """Discovery needs no compiled runtime; setup returns an actionable error."""
    manager = SessionManager(repository_root, tmp_path / "missing", tmp_path / "runs")
    async with Client(create_server(manager)) as client:
        listing = await client.list_tools()
        tools = {tool.name: tool for tool in listing.tools}
        assert set(tools) == {"list_cases", "setup", "initialize", "react", "get_results", "close"}
        assert tools["setup"].input_schema["properties"]["case"]["type"] == "string"
        assert "request" in tools["setup"].input_schema["properties"]
        # dt has a default value
        assert "dt" not in tools["react"].input_schema.get("required", [])
        names = {item["case"] for item in (await call(client, "list_cases"))["cases"]}
        # Check subset
        assert {"calcite-short-pc-pflotran", "calcite-short-cc-crunch", "ex8-nn-batch1"} <= names
        error = await client.call_tool("setup", {"case": "calcite-short-pc-pflotran"})
        assert error.is_error
        assert "Build it explicitly" in error.content[0].text
        assert not (tmp_path / "runs").exists()


@pytest.mark.anyio
async def test_sessions_lifecycle_isolation_and_disconnect(manager):
    """Separate sessions retain separate states; disconnect reaps open processes."""
    async with Client(create_server(manager)) as client:
        first = await call(client, "setup", case="calcite-short-pc-pflotran")
        second = await call(client, "setup", case="ex8-nn-batch1")
        a, b = first["session_id"], second["session_id"]
        assert a != b
        limited = await client.call_tool("setup", {"case": "calcite-short-cc-crunch"})
        assert limited.is_error
        early = await client.call_tool("react", {"session_id": a, "dt": 5})
        assert early.is_error
        assert "Call initialize" in early.content[0].text
        await call(client, "initialize", session_id=a)
        await call(client, "initialize", session_id=b)
        repeated = await client.call_tool("initialize", {"session_id": a})
        assert repeated.is_error
        # Simultaneous MCP calls must not interleave writes to the same process.
        async with anyio.create_task_group() as group:
            async def react(dt):
                await call(client, "react", session_id=a, dt=dt)
            group.start_soon(react, 2.0)
            group.start_soon(react, 3.0)
        state = (await call(client, "get_results", session_id=a))["state"]
        assert (state["time"], state["max_steps"]) == (5, 2)
        assert (await call(client, "get_results", session_id=b))["state"]["time"] == 0
        assert (await call(client, "close", session_id=a))["closed"] is True
        assert (await call(client, "close", session_id=a))["closed"] is False
        unknown = await client.call_tool("get_results", {"session_id": a})
        assert unknown.is_error
        # Closing one session releases its slot.
        await call(client, "setup", case="calcite-short-cc-crunch")
    assert all(process.closed for process in FakeEngineProcess.instances)
    assert manager._sessions == {}
    saved = json.loads((Path(first["run_dir"]) / "simulation_results.json").read_text())
    assert saved["status"] == "closed"
    assert [row["time"] for row in saved["history"]][-1] == 5
    assert len(saved["history"]) == 3


@pytest.mark.anyio
async def test_invalid_arguments_preserve_session(manager):
    """Schema errors and invalid dt never reach or invalidate the engine."""
    async with Client(create_server(manager)) as client:
        bad_case = await client.call_tool("setup", {"case": "arbitrary-file"})
        assert bad_case.is_error
        assert not manager.run_root.exists()
        setup = await call(client, "setup", case="calcite-short-pc-pflotran")
        session_id = setup["session_id"]
        await call(client, "initialize", session_id=session_id)
        for dt in (-1, 0, True, "5"):
            # Call from the client
            result = await client.call_tool("react", {"session_id": session_id, "dt": dt})
            assert result.is_error
        for dt in (float("nan"), float("inf")):
            with pytest.raises(ValueError, match="finite"):
                # Directly call the react function
                manager.react(session_id, dt)
        assert (await call(client, "get_results", session_id=session_id))["state"]["time"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["setup", "initialize", "react"])
async def test_native_failure_releases_session_and_keeps_diagnosis(manager, monkeypatch, operation):
    """Failed operations release their slot and preserve engine diagnostics."""
    async with Client(create_server(manager)) as client:
        if operation == "setup":
            arguments = {"case": "calcite-short-pc-pflotran"}
        else:
            setup = await call(client, "setup", case="calcite-short-pc-pflotran")
            arguments = {"session_id": setup["session_id"]}
            if operation == "react":
                await call(client, "initialize", **arguments)
                arguments["dt"] = 5
        monkeypatch.setattr(FakeEngineProcess, "fail_operation", operation)
        result = await client.call_tool(operation, arguments)
        assert result.is_error
        assert f"{operation} failed" in result.content[0].text
        assert "engine_output.log" in result.content[0].text
        assert all(process.closed for process in FakeEngineProcess.instances)
        assert not manager._sessions
        failures = list(manager.run_root.glob("*/simulation_failure.json"))
        assert len(failures) == 1
        assert json.loads(failures[0].read_text())["operation"] == operation


@pytest.mark.anyio
async def test_cleanup_continues_after_one_close_failure(manager, monkeypatch):
    """A shutdown error must not leave other sessions running."""
    async with Client(create_server(manager)) as client:
        await call(client, "setup", case="calcite-short-pc-pflotran")
        await call(client, "setup", case="calcite-short-cc-crunch")
        process = FakeEngineProcess.instances[0]
        def fail_close():
            process.closed = True
            raise EngineProcessError("shutdown failed")
        monkeypatch.setattr(process, "close", fail_close)
    assert all(process.closed for process in FakeEngineProcess.instances)
    assert not manager._sessions


@pytest.mark.anyio
async def test_cancelled_setup_does_not_orphan_process(manager, monkeypatch):
    """An in-flight setup remains owned when its client cancels and disconnects."""
    
    # Flags to pause and resume the background thread
    started = threading.Event()
    release = threading.Event()
    request = FakeEngineProcess.request # Backup the original normal execution

    # Intercept the 'setup' operation to freeze it mid-flight
    def delayed_request(self, operation, **parameters):
        if operation == "setup":
            started.set()   # 1. Notify main thread that setup has begun
            assert release.wait(timeout=5)  # 2. Freeze here until main thread releases it
        return request(self, operation, **parameters)

    monkeypatch.setattr(FakeEngineProcess, "request", delayed_request)
    async with Client(create_server(manager)) as client:
        async with anyio.create_task_group() as group:
            async def setup():
                await client.call_tool("setup", {"case": "calcite-short-pc-pflotran"})
            
            # Start the setup request in the background (another thread)
            # main thread continues
            group.start_soon(setup)
            
            # Wait until the background thread gets stuck at our freeze point
            with anyio.fail_after(5):
                while not started.is_set():
                    await anyio.sleep(0.001)
                    
            # Simulate a client disconnect by cancelling the task mid-flight
            group.cancel_scope.cancel()
            
            # Unfreeze the thread so the system can handle the cancellation and clean up
            release.set()
    # Verify the system cleaned up properly
    assert len(FakeEngineProcess.instances) == 1
    assert FakeEngineProcess.instances[0].closed
    assert not manager._sessions


@pytest.mark.anyio
async def test_setup_request_overrides_and_is_recorded(manager):
    """A client request overrides benchmark defaults and is saved with the session."""
    async with Client(create_server(manager)) as client:
        session = await call(client, "setup", case="calcite-short-pc-pflotran",
                             request={"temperature": 60.0, "porosity": 0.3})
        setup_arguments = FakeEngineProcess.instances[0].setup_arguments
        assert setup_arguments["temperature"] == 60.0 and setup_arguments["porosity"] == 0.3
        assert setup_arguments["density"] == 997.16 and setup_arguments["engine"] == "PFloTran"
        assert session["setup"] == setup_arguments and session["case"] == "calcite-short-pc-pflotran"
        saved = json.loads((Path(session["run_dir"]) / "simulation_request.json").read_text())
        assert saved["request"] == {"temperature": 60.0, "porosity": 0.3}


@pytest.mark.anyio
async def test_invalid_request_is_rejected_before_launch(manager):
    """Bad fields fail without creating a run directory or process."""
    async with Client(create_server(manager)) as client:
        for request in ({"input_file": "/etc/passwd"}, {"porosity": 2}, {"density": "heavy"},
                        {"density": True}, {"initial_condition": ""}):
            error = await client.call_tool("setup", {"case": "calcite-short-pc-pflotran", "request": request})
            assert error.is_error
        assert FakeEngineProcess.instances == []
        assert not manager.run_root.exists() or not any(manager.run_root.iterdir())


@pytest.mark.anyio
async def test_setup_schema_exposes_the_typed_request(manager):
    """The agent can read the allowed request fields, and no others, from the tool schema."""
    async with Client(create_server(manager)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        schema = tools["setup"].input_schema
        request = schema["$defs"]["SetupRequest"]
        assert set(request["properties"]) == {
            "density", "porosity", "temperature", "pressure", "volume", "saturation",
            "initial_condition", "dt", "max_steps"}
        assert request["additionalProperties"] is False
        assert "request" not in schema.get("required", [])


@pytest.mark.anyio
@pytest.mark.parametrize("case, engine", [
    ("calcite-short-pc-pflotran", "PFloTran"),
    ("calcite-short-cc-crunch", "CrunchFlow"),
    ("ex8-nn-batch1", "ONNX"),
])
async def test_request_overrides_apply_to_every_engine(manager, case, engine):
    """Overrides are not calcite-specific; each engine receives the merged setup."""
    async with Client(create_server(manager)) as client:
        session = await call(client, "setup", case=case, request={"temperature": 40.0})
        setup_arguments = FakeEngineProcess.instances[0].setup_arguments
        assert setup_arguments["engine"] == engine and setup_arguments["temperature"] == 40.0
        assert session["setup"] == setup_arguments


@pytest.mark.anyio
async def test_undefined_default_condition_requires_a_request(manager):
    """An *-ac-* case refuses to start until the agent names a condition that exists."""
    async with Client(create_server(manager)) as client:
        refused = await client.call_tool("setup", {"case": "calcite-short-ac-pflotran"})
        assert refused.is_error and "request.initial_condition" in refused.content[0].text
        assert FakeEngineProcess.instances == []
        created = await call(client, "setup", case="calcite-short-ac-pflotran",
                             request={"initial_condition": "initial"})
        assert created["setup"]["initial_condition"] == "initial"


@pytest.mark.anyio
async def test_request_controls_dt_and_max_steps(manager):
    """dt and max_steps are session controls: not sent to the engine setup, used by react."""
    async with Client(create_server(manager)) as client:
        session = await call(client, "setup", case="calcite-short-pc-pflotran",
                             request={"dt": 7.5, "max_steps": 2})
        assert session["simulation"] == {"dt": 7.5, "max_steps": 2}
        setup_arguments = FakeEngineProcess.instances[0].setup_arguments
        assert "dt" not in setup_arguments and "max_steps" not in setup_arguments
        session_id = session["session_id"]
        await call(client, "initialize", session_id=session_id)
        await call(client, "react", session_id=session_id)
        await call(client, "react", session_id=session_id, dt=3)
        # max_steps is 2
        refused = await client.call_tool("react", {"session_id": session_id})
        assert refused.is_error and "max_steps" in refused.content[0].text


@pytest.mark.anyio
@pytest.mark.parametrize("request_body", [
    {"dt": 0}, {"dt": -1.0}, {"dt": "5"}, {"max_steps": -1}, {"max_steps": 1.5}, {"max_steps": True}])
async def test_invalid_controls_are_rejected_before_launch(manager, request_body):
    """Bad dt or max_steps never starts an engine."""
    async with Client(create_server(manager)) as client:
        result = await client.call_tool(
            "setup", {"case": "calcite-short-pc-pflotran", "request": request_body})
        assert result.is_error
        assert FakeEngineProcess.instances == []
