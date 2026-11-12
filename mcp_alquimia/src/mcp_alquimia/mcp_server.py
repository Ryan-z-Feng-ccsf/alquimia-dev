"""Serve the supported Alquimia benchmarks as stateful MCP tools over stdio."""

import argparse
from contextlib import asynccontextmanager
from pathlib import Path
import subprocess

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, ValidationError

from .case_catalog import get_chemistry as describe_chemistry
from .case_registry import get_case
from .response_contract import to_json
from .manual_search import ManualIndex, SearchCollection, SearchLimit, SearchQuery
from .session_manager import SessionManager, list_cases as describe_cases
from .setup_request import Engine, SetupRequest
from .subprocess_client import EngineProcessError
from .time_config_request import TimeConfigRequest


def describe_validation_error(error: ValidationError) -> str:
    """Summarize each failing field as `location: message` for the client."""
    details = "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}"
        for item in error.errors()
    )
    return f"Invalid {error.title}: {details}"


def forbid_unknown_arguments(server: MCPServer) -> None:
    """Make every registered tool reject arguments outside its signature.

    The SDK's argument models silently drop unknown keys, so a misspelled or
    retired argument (e.g. a top-level dt) would run with defaults. A **kwargs
    parameter cannot catch them either: the SDK turns it into a required field.
    """
    for tool in server._tool_manager.list_tools():
        arguments = tool.fn_metadata.arg_model
        
        # Create a strict subclass of the original arguments model.
        # Copy the existing config but force `extra="forbid"` to reject unknown parameters.
        strict_arguments = type(arguments.__name__, (arguments,), {
            "model_config": ConfigDict(**arguments.model_config, extra="forbid")})
        
        # Update the internal validation model.
        # This ensures the Python server throws an error if extra arguments are received.
        tool.fn_metadata.arg_model = strict_arguments
        
        # Update the external JSON schema exposed to the LLM.
        # This sets `additionalProperties: false` so the LLM knows it cannot hallucinate arguments.
        tool.parameters = strict_arguments.model_json_schema(by_alias=True)


def create_server(manager: SessionManager, manual_index: Path | None = None) -> MCPServer:
    """Create an MCP server that owns and cleans up the supplied session manager.

    Args:
        manager: Runtime configuration and native process owner for this server.
        manual_index: Index read by search_docs and read_doc (default:
            REPOSITORY/build/mcp_alquimia/manual_index.sqlite).

    Returns:
        A stdio-ready server; no engines start until the setup tool is called.
    """
    manuals = ManualIndex(
        manual_index or manager.repository / "build/mcp_alquimia/manual_index.sqlite")

    @asynccontextmanager
    async def lifespan(_server):
        """Reap all native sessions when the MCP connection finishes."""
        try:
            yield
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(manager.close_all)

    server = MCPServer(
        "Alquimia",
        version="0.1.0",
        lifespan=lifespan,
        instructions=(
            "Geochemical batch-reaction simulator running PFLOTRAN and CrunchFlow through "
            "Alquimia. Use it instead of estimating from memory when the user asks how a "
            "water-mineral-gas system evolves over time: species concentrations, pH, mineral "
            "amounts, or the effect of temperature, porosity, saturation or time step. "
            "Scenarios: calcite dissolution/precipitation (incl. CO2), CH4-O2 oxidation, "
            "general aqueous kinetics, ion exchange, sorption isotherms, surface complexation "
            "and microbial reactions. "
            "Call list_cases; it shows each engine's cases. Pass that engine with the case to "
            "get_chemistry and setup, and retain setup's session_id. A session runs one engine; "
            "to compare engines, run one session per engine with the same request and "
            "time_config. setup's optional request overrides the default physical settings "
            "and, through request.chemistry, initial concentrations, pH, gas pressures, "
            "mineral amounts and rate constants; call get_chemistry first for the valid names, "
            "current values and units. "
            "Call initialize once, then react: each call runs max_steps reaction steps of dt "
            "seconds in the engine, starting from the defaults; an optional time_config object "
            "overrides dt and max_steps for that and later calls. Call get_results to inspect "
            "state, and close when finished. Each session has an isolated native process and "
            "artifact directory. Failed native calls end that session; create a new one. "
            "Engines are prebuilt. "
            "Call search_docs, then read_doc, to look up PFLOTRAN or CrunchFlow manual "
            "sections (input keywords, reaction types, units) instead of recalling them, and "
            "Alquimia known issues (collection=alquimia) when a setting seems to have no effect "
            "or a result looks wrong."
        ),
    )
    # Safe and harmless. Does not modify any system state (e.g., fetching lists, reading results).
    read_only = ToolAnnotations(
        readOnlyHint=True,  # Without manual confirmation
        openWorldHint=False # Confined to the local sandbox.
    )
    # Modifies system state (e.g., initializing engines, advancing time steps).
    mutating = ToolAnnotations(
        readOnlyHint=False, # With manual confirmation
        destructiveHint=False,  # Changes state, but does not destroy data
        idempotentHint=False,
        openWorldHint=False # Confined to the local sandbox.
    )

    async def invoke(function, *args) -> str:
        """Run an operation off the MCP event loop; returns its result as compact JSON."""
        try:
            # The default cancellation shielding keeps ownership until the native
            # call returns or times out, allowing lifespan cleanup to reap it.
            return to_json(await anyio.to_thread.run_sync(function, *args))
        except ValidationError as error:
            raise ToolError(describe_validation_error(error)) from error
        except (EngineProcessError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise ToolError(str(error)) from error

    @server.tool(structured_output=False, annotations=read_only)
    async def list_cases() -> str:
        """List supported cases and benchmark defaults without launching engines."""
        return await invoke(describe_cases, manager.repository)

    @server.tool(structured_output=False, annotations=read_only)
    async def get_chemistry(case: str, engine: Engine) -> str:
        """Show an engine input's initial chemistry: species constraints, minerals and rates.

        Values use the listed units, the same on every engine; a null value cannot be
        changed (e.g. charge balance or mineral equilibrium). Names and values are what
        setup's request.chemistry accepts for this case and engine.

        Args:
            case: Identifier returned by list_cases.
            engine: Engine list_cases shows the case under.
        """
        # Use lambda to run get_case() in a worker thread
        # preventing it from blocking the main event loop       
        return await invoke(
            lambda: describe_chemistry(manager.repository, get_case(case), engine))

    @server.tool(structured_output=False, annotations=mutating)
    async def setup(
        case: str,
        engine: Engine,
        request: SetupRequest | None = None
    ) -> str:
        """Stage a case for one engine and create a session; returns session_id, setup and metadata.

        Args:
            case: Identifier returned by list_cases.
            engine: Engine list_cases shows the case under.
            request: Optional overrides; the schema lists the allowed fields, units and
                ranges. Omitted fields keep the defaults shown by list_cases. chemistry
                changes the initial condition (see get_chemistry). Invalid or unknown
                fields are rejected before any engine starts.
        """
        return await invoke(manager.setup, case, engine, request)

    @server.tool(structured_output=False, annotations=mutating)
    async def initialize(session_id: str) -> str:
        """Apply the session's initial condition once; returns its initial state."""
        return await invoke(manager.initialize, session_id)

    @server.tool(structured_output=False, annotations=mutating)
    async def react(
        session_id: str,
        time_config: TimeConfigRequest | None = None
    ) -> str:
        """Run max_steps reaction steps of dt seconds; repeated calls continue the state.

        Returns status, time_step (dt), steps_executed, current_time (s) and the final state.

        Args:
            session_id: ID returned by setup, after initialize has succeeded.
            time_config: Optional overrides of the session's dt (seconds per step) and
                max_steps (steps per call), which start at the defaults shown by
                list_cases. Set values persist for later react calls.
        """
        return await invoke(manager.react, session_id, time_config)

    @server.tool(structured_output=False, annotations=read_only)
    async def get_results(session_id: str) -> str:
        """Read the latest initialized state without advancing time or chemistry."""
        return await invoke(manager.get_results, session_id)

    @server.tool(structured_output=False, annotations=ToolAnnotations(
            readOnlyHint=False, # With manual confirmation
            destructiveHint=False,  # Changes state, but does not destroy data
            idempotentHint=True,
            openWorldHint=False # Confined to the local sandbox.
        )
    )
    async def close(session_id: str) -> str:
        """Close and reap a session, retaining artifacts; repeated calls are harmless."""
        return await invoke(manager.close, session_id)

    @server.tool(structured_output=False, annotations=read_only)
    async def search_docs(
        query: SearchQuery,
        collection: SearchCollection = None,
        limit: SearchLimit = 5,
    ) -> str:
        """Search the engine manuals and Alquimia known issues; returns ranked section snippets.

        Args:
            query: Words or input-file keywords to match.
            collection: Manual or known-issues collection to search; all when omitted.
            limit: Maximum number of sections returned.
        """
        return await invoke(manuals.search, query, collection, limit)

    @server.tool(structured_output=False, annotations=read_only)
    async def read_doc(doc_id: str) -> str:
        """Return the full text of one manual section by a doc_id from search_docs."""
        return await invoke(manuals.read_section, doc_id)

    forbid_unknown_arguments(server)
    return server


def main() -> None:
    """Run the local stdio MCP server using an explicitly precompiled runtime."""
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        type=Path,
        default=root
    )
    parser.add_argument(
        "--executable",
        type=Path,
        help="Precompiled engine process (default: REPOSITORY/build/mcp_alquimia/bin/alquimia_engine_process)"
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        help="Session artifacts (default: ~/.alquimia/runs)"
    )
    parser.add_argument(
        "--manual-index",
        type=Path,
        help="Manual search index (default: REPOSITORY/build/mcp_alquimia/manual_index.sqlite)"
    )
    parser.add_argument(
        "--timeout",
        type=float, default=60.0,
        help="Maximum seconds per native response"
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=4,
        help="Maximum simultaneously open sessions, each with one engine process"
    )
    args = parser.parse_args()
    repository = args.repository.resolve()
    try:
        manager = SessionManager(
            repository,
            args.executable or repository / "build/mcp_alquimia/bin/alquimia_engine_process",
            args.run_root or Path.home() / ".alquimia/runs",
            args.timeout,
            args.max_sessions,
        )
    except ValueError as error:
        parser.error(str(error))
    create_server(manager, args.manual_index).run(transport="stdio")


if __name__ == "__main__":
    main()
