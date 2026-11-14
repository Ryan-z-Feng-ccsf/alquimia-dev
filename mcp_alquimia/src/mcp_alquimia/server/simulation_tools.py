"""MCP tools that list cases, inspect chemistry and run engine sessions."""

from mcp.server.mcpserver import MCPServer

from ..cases.catalog import get_chemistry as describe_chemistry
from ..cases.registry import get_case
from ..requests.setup import Engine, SetupRequest
from ..requests.time_config import TimeConfigRequest
from ..response.contract import shape_react, shape_setup, shape_snapshot
from ..simulation.sessions import SessionManager, list_cases as describe_cases
from .protocol import IDEMPOTENT, MUTATING, READ_ONLY, invoke


def register_simulation_tools(server: MCPServer, manager: SessionManager) -> None:
    """Register the case, chemistry and session lifecycle tools.

    Args:
        server: Server receiving the tools.
        manager: Owner of the sessions and engine processes the tools act on.
    """

    @server.tool(structured_output=False, annotations=READ_ONLY)
    async def list_cases() -> str:
        """List each case's engines and description, and the default dt (s) and max_steps."""
        return await invoke(describe_cases, manager.repository)

    @server.tool(structured_output=False, annotations=READ_ONLY)
    async def get_chemistry(case: str, engine: Engine) -> str:
        """Show a case's initial chemistry on one engine: aqueous constraints, minerals, rates.
        These names, values and units are what setup's request.chemistry accepts; a
        null value cannot be changed (e.g. a charge balance or mineral equilibrium).

        Units are engine-independent and converted to each engine's input units.

        Args:
            case: Case name from list_cases.
            engine: PFloTran or CrunchFlow, one list_cases shows for the case.
        """
        # Use lambda to run get_case() in a worker thread
        # preventing it from blocking the main event loop
        return await invoke(
            lambda: describe_chemistry(manager.repository, get_case(case), engine))

    @server.tool(structured_output=False, annotations=MUTATING)
    async def setup(
        case: str,
        engine: Engine,
        request: SetupRequest | None = None
    ) -> str:
        """Create a session running one case on one engine.
        Returns session_id, the settings in effect, time (dt, max_steps) and the units of
        all responses. To compare engines, create one session per engine with the same
        request.

        The input is staged into the session's run directory, so chemistry changes
        never touch the benchmark files.

        Args:
            case: Case name from list_cases.
            engine: PFloTran or CrunchFlow, one list_cases shows for the case.
            request: Optional overrides of the physical settings and, through
                chemistry, of the initial condition. Omitted fields keep their
                defaults; invalid or unknown fields are rejected before an engine starts.
        """
        return await invoke(manager.setup, case, engine, request, shape=shape_setup)

    @server.tool(structured_output=False, annotations=MUTATING)
    async def initialize(session_id: str) -> str:
        """Apply the initial condition, once per session; returns time, total_steps and state.

        Args:
            session_id: ID returned by setup.
        """
        return await invoke(manager.initialize, session_id, shape=shape_snapshot)

    @server.tool(structured_output=False, annotations=MUTATING)
    async def react(
        session_id: str,
        time_config: TimeConfigRequest | None = None
    ) -> str:
        """Run max_steps reaction steps of dt seconds, continuing from the previous state.
        Returns time (cumulative), steps (this call), total_steps, dt and state. A failed
        call ends the session; create a new one.

        The engine runs all steps in one request.

        Args:
            session_id: ID returned by setup, after initialize.
            time_config: Optional dt and max_steps overrides; they persist for later
                react calls.
        """
        return await invoke(manager.react, session_id, time_config, shape=shape_react)

    @server.tool(structured_output=False, annotations=READ_ONLY)
    async def get_results(session_id: str) -> str:
        """Return the latest time, total_steps and state without advancing the simulation.

        Args:
            session_id: ID returned by setup, after initialize.
        """
        return await invoke(manager.get_results, session_id, shape=shape_snapshot)

    @server.tool(structured_output=False, annotations=IDEMPOTENT)
    async def close(session_id: str) -> str:
        """Close a session and stop its engine; closing again is harmless.

        The session's artifacts are kept on disk.

        Args:
            session_id: ID returned by setup.
        """
        return await invoke(manager.close, session_id)
