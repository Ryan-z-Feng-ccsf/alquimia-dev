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
            self.time = previous + parameters["dt"] * parameters["max_steps"]
            self.steps += parameters["max_steps"]
        return {"time": self.time, "total_steps": self.steps, "total_mobile": [1.0]}

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
    """Call a tool and require one compact JSON text result, returned parsed."""
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result
    assert result.structured_content is None
    text, = (block.text for block in result.content)
    payload = json.loads(text)
    assert text == json.dumps(payload, separators=(",", ":")), "not compact JSON"
    return payload


@pytest.mark.anyio
async def test_discovery_and_missing_runtime(repository_root, tmp_path):
    """Discovery needs no compiled runtime; setup returns an actionable error."""
    manager = SessionManager(repository_root, tmp_path / "missing", tmp_path / "runs")
    async with Client(create_server(manager)) as client:
        listing = await client.list_tools()
        tools = {tool.name: tool for tool in listing.tools}
        assert set(tools) == {
            "list_cases",
            "get_chemistry",
            "setup",
            "initialize",
            "react",
            "get_results",
            "close",
            "search_docs",
            "read_doc"
        }
        assert tools["setup"].input_schema["properties"]["case"]["type"] == "string"
        assert "request" in tools["setup"].input_schema["properties"]
        # time_config has a default value
        assert "time_config" not in tools["react"].input_schema.get("required", [])
        names = set((await call(client, "list_cases"))["cases"])
        # Check subset; cfg stems and ONNX fixtures are not served
        assert {"calcite-short", "microbial", "calcite-co2"} <= names
        assert not {"calcite-short-pc-pflotran", "ex8-nn-batch1"} & names
        error = await client.call_tool("setup", {"case": "calcite-short", "engine": "PFloTran"})
        assert error.is_error
        assert "Build it explicitly" in error.content[0].text
        assert not (tmp_path / "runs").exists()


@pytest.mark.anyio
async def test_sessions_lifecycle_isolation_and_disconnect(manager):
    """Separate sessions retain separate states; disconnect reaps open processes."""
    async with Client(create_server(manager)) as client:
        first = await call(client, "setup", case="calcite-short", engine="PFloTran")
        second = await call(client, "setup", case="microbial", engine="PFloTran")
        a, b = first["session_id"], second["session_id"]
        assert a != b
        limited = await client.call_tool(
            "setup", {"case": "calcite-short", "engine": "CrunchFlow"}
        )
        assert limited.is_error and "Session limit" in limited.content[0].text
        early = await client.call_tool("react", {"session_id": a, "time_config": {"dt": 5}})
        assert early.is_error
        assert "Call initialize" in early.content[0].text
        await call(client, "initialize", session_id=a)
        await call(client, "initialize", session_id=b)
        repeated = await client.call_tool("initialize", {"session_id": a})
        assert repeated.is_error
        # Simultaneous MCP calls must not interleave writes to the same process.
        async with anyio.create_task_group() as group:
            async def react(dt):
                await call(client, "react", session_id=a, time_config={"dt": dt, "max_steps": 1})
            group.start_soon(react, 2.0)
            group.start_soon(react, 3.0)
        results = await call(client, "get_results", session_id=a)
        assert (results["time"], results["total_steps"]) == (5, 2)
        assert results["state"] == {"aqueous_total": {"H": 1.0}}
        assert (await call(client, "get_results", session_id=b))["time"] == 0
        assert (await call(client, "close", session_id=a))["closed"] is True
        assert (await call(client, "close", session_id=a))["closed"] is False
        unknown = await client.call_tool("get_results", {"session_id": a})
        assert unknown.is_error
        # Closing one session releases its slot.
        await call(client, "setup", case="calcite-short", engine="CrunchFlow")
    assert all(process.closed for process in FakeEngineProcess.instances)
    assert manager._sessions == {}
    saved = json.loads((manager.run_root / a / "simulation_results.json").read_text())
    assert saved["status"] == "closed"
    assert saved["engine"] == "PFloTran"
    assert [row["time"] for row in saved["history"]][-1] == 5
    assert len(saved["history"]) == 3


@pytest.mark.anyio
async def test_invalid_arguments_preserve_session(manager):
    """Schema errors and invalid dt never reach or invalidate the engine."""
    async with Client(create_server(manager)) as client:
        bad_case = await client.call_tool("setup", {"case": "arbitrary-file"})
        assert bad_case.is_error
        bad_engine = await client.call_tool("setup", {"case": "microbial", "engine": "CrunchFlow"})
        assert bad_engine.is_error and "no CrunchFlow input" in bad_engine.content[0].text
        assert not manager.run_root.exists()
        setup = await call(client, "setup", case="calcite-short", engine="PFloTran")
        session_id = setup["session_id"]
        await call(client, "initialize", session_id=session_id)
        for time_config in ({"dt": -1}, {"dt": 0}, {"dt": True}, {"dt": "5 s"}, {"dt": "inf"},
                            {"max_steps": 0}, {"max_steps": 1.5}, {"max_steps": "1.5"},
                            {"max_steps": True}, {"max_steps": 10001}, {"timestep": 5}):
            result = await client.call_tool(
                "react", {"session_id": session_id, "time_config": time_config})
            assert result.is_error
            assert "time_config" in result.content[0].text
        assert (await call(client, "get_results", session_id=session_id))["time"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["setup", "initialize", "react"])
async def test_native_failure_releases_session_and_keeps_diagnosis(manager, monkeypatch, operation):
    """Failed operations release their slot and preserve engine diagnostics."""
    async with Client(create_server(manager)) as client:
        if operation == "setup":
            arguments = {"case": "calcite-short", "engine": "PFloTran"}
        else:
            setup = await call(client, "setup", case="calcite-short", engine="PFloTran")
            arguments = {"session_id": setup["session_id"]}
            if operation == "react":
                await call(client, "initialize", **arguments)
                arguments["time_config"] = {"dt": 5}
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
        await call(client, "setup", case="calcite-short", engine="PFloTran")
        await call(client, "setup", case="calcite-short", engine="CrunchFlow")
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
                await client.call_tool("setup", {"case": "calcite-short", "engine": "PFloTran"})
            
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
        session = await call(client, "setup", case="calcite-short", engine="PFloTran",
                             request={"temperature": 60.0, "porosity": 0.3})
        setup_arguments = FakeEngineProcess.instances[0].setup_arguments
        assert setup_arguments["temperature"] == 60.0 and setup_arguments["porosity"] == 0.3
        assert setup_arguments["density"] == 997.16 and setup_arguments["engine"] == "PFloTran"
        assert session["settings"] == {
            key: value for key, value in setup_arguments.items()
            if key not in ("engine", "input_file", "initial_condition")}
        assert (session["case"], session["engine"]) == ("calcite-short", "PFloTran")
        assert session["units"]["aqueous_total"] == "mol/L water"
        assert set(session) == {"session_id", "case", "engine", "settings", "time", "units"}
        run_dir = manager.run_root / session["session_id"]
        saved = json.loads((run_dir / "simulation_request.json").read_text())
        assert saved["request"] == {"temperature": 60.0, "porosity": 0.3}


@pytest.mark.anyio
async def test_invalid_request_is_rejected_before_launch(manager):
    """Bad fields fail without creating a run directory or process."""
    async with Client(create_server(manager)) as client:
        for request in ({"input_file": "/etc/passwd"}, {"porosity": 2}, {"density": "heavy"},
                        {"density": True}, {"density": "true"}, {"porosity": "2"}, {"initial_condition": ""}):
            error = await client.call_tool(
                "setup", {"case": "calcite-short", "engine": "PFloTran", "request": request}
            )
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
            "density",
            "porosity",
            "temperature",
            "pressure",
            "volume",
            "saturation",
            "initial_condition",
            "chemistry"
        }
        assert set(schema["$defs"]["ChemistryRequest"]["properties"]) == {
            "aqueous",
            "minerals",
            "rates"
        }
        assert schema["required"] == ["case", "engine"]
        # Numeric strings are accepted server-side but never advertised to the agent.
        assert request["properties"]["temperature"]["type"] == "number"
        assert schema["$defs"]["ChemistryRequest"]["properties"]["aqueous"][
            "additionalProperties"]["type"] == "number"
        # Optional fields are advertised as their type; omitting them means "no change"
        time_config = tools["react"].input_schema["$defs"]["TimeConfigRequest"]["properties"]
        assert time_config["dt"] == {
            "exclusiveMinimum": 0,
            "type": "number",
            "description": "Seconds per reaction step."
        }
        assert (time_config["max_steps"]["type"], time_config["max_steps"]["maximum"]) == (
            "integer", 10000)
        # Ranges use standard JSON Schema keywords
        assert request["properties"]["porosity"]["exclusiveMinimum"] == 0
        assert request["properties"]["porosity"]["maximum"] == 1
        assert schema["properties"]["engine"]["enum"] == ["PFloTran", "CrunchFlow"]
        assert request["additionalProperties"] is False
        assert "request" not in schema.get("required", [])


@pytest.mark.anyio
async def test_tools_are_described_from_docstrings_only(manager):
    """The model sees each docstring's summary and Args, and no developer notes or titles."""

    def keys(node):
        """Every key anywhere in a schema, skipping the names in properties and $defs."""
        if isinstance(node, list):
            return set().union(*map(keys, node)) if node else set()
        if not isinstance(node, dict):
            return set()
        found = set()
        for key, value in node.items():
            found.add(key)
            values = value.values() if key in ("properties", "$defs") else [value]
            found |= set().union(*map(keys, values)) if values else set()
        return found

    async with Client(create_server(manager)) as client:
        for tool in (await client.list_tools()).tools:
            assert "\n" not in tool.description and "Args:" not in tool.description, tool.name
            for name, parameter in tool.input_schema.get("properties", {}).items():
                assert parameter.get("description"), (tool.name, name)
            assert not keys(tool.input_schema) & {"title", "gt", "ge", "lt", "le", "propertyNames"}
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        setup_request = tools["setup"].input_schema["$defs"]["SetupRequest"]
        # Developer rationale below a model docstring's first paragraph is not sent
        assert setup_request["description"] == "Session settings; omitted fields keep the defaults shown."
        assert "doc_id" in tools["read_doc"].input_schema["properties"]["doc_id"]["description"]


@pytest.mark.anyio
async def test_chemistry_is_listed_and_patched_into_the_session_input(manager):
    """get_chemistry shows one input's values; setup patches only that engine's staged input."""
    async with Client(create_server(manager)) as client:
        chemistry = await call(client, "get_chemistry", case="calcite-short", engine="CrunchFlow")
        assert (chemistry["engine"], chemistry["rates"]) == ("CrunchFlow", {"Calcite": 1e-9})
        assert chemistry["units"]["rates"] == "mol/m^2/s"
        session = await call(client, "setup", case="calcite-short", engine="CrunchFlow",request={
            "chemistry": {"minerals": {"Calcite": {"volume_fraction": 0.1}}}})
        run_dir = manager.run_root / session["session_id"]
        assert sorted(path.name for path in run_dir.glob("*.in")) == ["calcite-short-crunch.in"]
        assert "Calcite          0.1      bulk_surface_area 100.0" in (
            run_dir / "calcite-short-crunch.in").read_text()
        saved = json.loads((run_dir / "simulation_request.json").read_text())
        assert saved["engine"] == "CrunchFlow"
        assert saved["request"]["chemistry"]["minerals"] == {
            "Calcite": {"volume_fraction": 0.1}}
        # Species B is defined only in the PFLOTRAN isotherms input
        assert "B" in (await call(client, "get_chemistry", case="isotherms",
                                  engine="PFloTran"))["aqueous"]
        refused = await client.call_tool("setup", {
            "case": "isotherms", "engine": "CrunchFlow",
            "request": {"chemistry": {"aqueous": {"B": 1e-3}}}})
        assert refused.is_error and "no aqueous species 'B'" in refused.content[0].text
        accepted = await call(client, "setup", case="isotherms", engine="PFloTran",
                              request={"chemistry": {"aqueous": {"B": 1e-3}}})
        assert accepted["engine"] == "PFloTran"
        assert len(FakeEngineProcess.instances) == 2


@pytest.mark.anyio
async def test_numeric_strings_and_quoted_names_from_small_models_are_accepted(manager):
    """Quoted numbers and backticked names, as local models send them, run like typed ones."""
    async with Client(create_server(manager)) as client:
        session = await call(client, "setup", case="calcite-short", engine="PFloTran", request={
            "temperature": " 60 ", "chemistry": {"aqueous": {"`Ca++`": "1e-10"}}})
        run_dir = manager.run_root / session["session_id"]
        saved = json.loads((run_dir / "simulation_request.json").read_text())
        assert saved["request"]["temperature"] == 60.0
        assert saved["request"]["chemistry"]["aqueous"] == {"Ca++": 1e-10}
        session_id = session["session_id"]
        await call(client, "initialize", session_id=session_id)
        await call(
            client,
            "react",
            session_id=session_id,
            time_config={"dt": "1.d1", "max_steps": "1e2"}
        )
        assert FakeEngineProcess.instances[0].steps == 100
        assert FakeEngineProcess.instances[0].time == 1000.0
        duplicate = await client.call_tool("setup", {
            "case": "calcite-short", "engine": "PFloTran",
            "request": {"chemistry": {"aqueous": {"Ca++": 1e-3, "`Ca++`": "1e-10"}}}})
        assert duplicate.is_error and "name the same entry" in duplicate.content[0].text
        with_unit = await client.call_tool("setup", {
            "case": "calcite-short", "engine": "PFloTran",
            "request": {"chemistry": {"aqueous": {"Ca++": "1e-10 mol/L"}}}})
        assert with_unit.is_error and "bare number" in with_unit.content[0].text
        assert len(FakeEngineProcess.instances) == 1


@pytest.mark.anyio
async def test_unknown_initial_condition_is_refused(manager):
    """Only conditions the input defines are accepted, before any engine starts."""
    async with Client(create_server(manager)) as client:
        refused = await client.call_tool("setup", {
            "case": "calcite-short", "engine": "PFloTran",
            "request": {"initial_condition": "driver_ic"}
            }
        )
        assert refused.is_error and "valid: initial" in refused.content[0].text
        assert FakeEngineProcess.instances == []


@pytest.mark.anyio
async def test_time_config_overrides_dt_and_max_steps(manager):
    """One react runs max_steps steps of dt in one engine request; overrides persist."""
    async with Client(create_server(manager)) as client:
        session = await call(client, "setup", case="calcite-short", engine="PFloTran")
        defaults_time_config = session["time"]
        session_id = session["session_id"]
        await call(client, "initialize", session_id=session_id)
        default_time = defaults_time_config["dt"] * defaults_time_config["max_steps"]
        reacted = await call(client, "react", session_id=session_id)
        assert reacted == {
            "time": default_time,
            "steps": defaults_time_config["max_steps"],
            "total_steps": defaults_time_config["max_steps"],
            "dt": defaults_time_config["dt"],
            "state": {"aqueous_total": {"H": 1.0}},
        }
        reacted = await call(client, "react", session_id=session_id,
                             time_config={"dt": 7.5, "max_steps": 3})
        assert (reacted["dt"], reacted["steps"]) == (7.5, 3)
        assert reacted["total_steps"] == defaults_time_config["max_steps"] + 3
        # Both overrides persist for the next call
        reacted = await call(client, "react", session_id=session_id)
        # 7.5 * 3 * 2 = 45
        assert reacted["time"] == default_time + 45
        reacted = await call(client, "react", session_id=session_id, time_config={"max_steps": 1})
        # 45 + 7.5
        assert (reacted["dt"], reacted["time"]) == (7.5, default_time + 52.5)
        # Each react call is one engine request and one history entry
        process = FakeEngineProcess.instances[0]
        # 3 + 3 + 1
        assert process.steps == defaults_time_config["max_steps"] + 7
        operations = (manager.run_root / session_id / "operations.jsonl").read_text().splitlines()
        assert json.loads(operations[-1]) == {"operation": "react", "dt": 7.5, "max_steps": 1}
        # 1 initialize + 4 react
        assert len(operations) == 5


@pytest.mark.anyio
async def test_unknown_tool_arguments_are_rejected(manager):
    """Retired or misspelled arguments fail instead of running with defaults."""
    async with Client(create_server(manager)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert all(tool.input_schema["additionalProperties"] is False for tool in tools.values())
        setup = await call(client, "setup", case="calcite-short", engine="PFloTran")
        session_id = setup["session_id"]
        await call(client, "initialize", session_id=session_id)
        for extra in ({"dt": 5}, {"timestep": 5}, {"max_steps": 2}):
            result = await client.call_tool("react", {"session_id": session_id, **extra})
            assert result.is_error
            assert next(iter(extra)) in result.content[0].text
        result = await client.call_tool(
            "setup", {"case": "calcite-short", "engine": "PFloTran", "dt": 1})
        assert result.is_error
        assert FakeEngineProcess.instances[0].steps == 0
        # Only 1 engine instance was setup
        assert len(FakeEngineProcess.instances) == 1


@pytest.mark.anyio
async def test_validation_error_becomes_tool_error_with_field_details(manager, monkeypatch):
    """A ValidationError raised behind the tool boundary is reported, not a crash."""
    from mcp_alquimia.time_config_request import TimeConfigRequest

    def react(session_id, time_config):
        TimeConfigRequest.model_validate({"dt": -1, "extra": 1})

    monkeypatch.setattr(manager, "react", react)
    async with Client(create_server(manager)) as client:
        result = await client.call_tool("react", {"session_id": "x"})
        assert result.is_error
        text = result.content[0].text
        assert "Invalid TimeConfigRequest" in text and "dt:" in text and "extra:" in text
        assert (await client.call_tool("list_cases")).is_error is False


@pytest.mark.anyio
@pytest.mark.parametrize("request_body", [{"dt": 5.0}, {"max_steps": 2}])
async def test_setup_rejects_time_config_fields(manager, request_body):
    """dt and max_steps belong to react's time_config, not setup's request."""
    async with Client(create_server(manager)) as client:
        result = await client.call_tool(
            "setup", {"case": "calcite-short", "engine": "PFloTran", "request": request_body})
        assert result.is_error
        assert FakeEngineProcess.instances == []


@pytest.mark.anyio
@pytest.mark.parametrize("engine", ["pflotran", "ONNX", ["PFloTran"], None])
async def test_engine_is_required_and_spelled_exactly(manager, engine):
    """setup and get_chemistry need one exact engine name before any engine starts."""
    async with Client(create_server(manager)) as client:
        for tool in ("setup", "get_chemistry"):
            arguments = {"case": "calcite-short"}
            if engine is not None:
                arguments["engine"] = engine
            result = await client.call_tool(tool, arguments)
            assert result.is_error and "engine" in result.content[0].text, tool
        assert FakeEngineProcess.instances == []
        assert not manager.run_root.exists()
