"""Serve the supported Alquimia benchmarks as stateful MCP tools over stdio."""

import argparse
from contextlib import asynccontextmanager
from pathlib import Path
import subprocess
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, ValidationError

from .session_manager import SessionManager, list_cases as describe_cases
from .setup_request import SetupRequest
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


def create_server(manager: SessionManager) -> MCPServer:
    """Create an MCP server that owns and cleans up the supplied session manager.

    Args:
        manager: Runtime configuration and native process owner for this server.

    Returns:
        A stdio-ready server; no engines start until the setup tool is called.
    """
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
            "Call list_cases, then setup for a case, optionally with a request object overriding its settings. Retain its session_id. "
            "Call initialize once, then react: each call runs max_steps reaction steps of dt "
            "seconds in the engine, defaulting to the case's cfg values; an optional time_config "
            "object overrides dt and max_steps for that and later calls. Call get_results "
            "to inspect state, and close when finished. Each session has an isolated "
            "native process and artifact directory. Failed native calls end that session; "
            "create a new one. Chemistry constraints come from the case input; physical settings default to the benchmark and may be overridden in setup. "
            "ONNX has no pH output and ignores dt during inference. Engines are prebuilt."
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

    async def invoke(function, *args):
        """Run a sequential native operation without blocking the MCP event loop."""
        try:
            # The default cancellation shielding keeps ownership until the native
            # call returns or times out, allowing lifespan cleanup to reap it.
            return await anyio.to_thread.run_sync(function, *args)
        except ValidationError as error:
            raise ToolError(describe_validation_error(error)) from error
        except (EngineProcessError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise ToolError(str(error)) from error

    @server.tool(annotations=read_only)
    async def list_cases() -> dict[str, Any]:
        """List supported cases and benchmark defaults without launching engines."""
        return describe_cases(manager.repository)

    @server.tool(annotations=mutating)
    async def setup(case: str, request: SetupRequest | None = None) -> dict[str, Any]:
        """Stage a benchmark and create a session; returns session_id, applied setup and metadata.

        Args:
            case: Identifier returned by list_cases.
            request: Optional overrides of the benchmark's defaults; the schema lists the
                allowed fields, units and ranges. Omitted fields keep the values shown by
                list_cases. Invalid or unknown fields are rejected before any engine starts.
        """
        return await invoke(manager.setup, case, request)

    @server.tool(annotations=mutating)
    async def initialize(session_id: str) -> dict[str, Any]:
        """Apply the session's initial condition once; returns its initial state."""
        return await invoke(manager.initialize, session_id)

    @server.tool(annotations=mutating)
    async def react(
        session_id: str,
        time_config: TimeConfigRequest | None = None
    ) -> dict[str, Any]:
        """Run max_steps reaction steps of dt seconds; repeated calls continue the state.

        Returns status, time_step (dt), steps_executed, current_time (s) and the final state.

        Args:
            session_id: ID returned by setup, after initialize has succeeded.
            time_config: Optional overrides of the session's dt (seconds per step) and
                max_steps (steps per call), which start at the cfg values shown by
                list_cases. Set values persist for later react calls. ONNX only uses
                dt for recorded time; it does not change model inference.
        """
        return await invoke(manager.react, session_id, time_config)

    @server.tool(annotations=read_only)
    async def get_results(session_id: str) -> dict[str, Any]:
        """Read the latest initialized state without advancing time or chemistry."""
        return await invoke(manager.get_results, session_id)

    @server.tool(annotations=ToolAnnotations(
            readOnlyHint=False, # With manual confirmation
            destructiveHint=False,  # Changes state, but does not destroy data
            idempotentHint=True,
            openWorldHint=False # Confined to the local sandbox.
        )
    )
    async def close(session_id: str) -> dict[str, Any]:
        """Close and reap a session, retaining artifacts; repeated calls are harmless."""
        return await invoke(manager.close, session_id)

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
        help="Session artifacts (default: REPOSITORY/build/mcp_alquimia/runs)"
    )
    parser.add_argument(
        "--timeout",
        type=float, default=60.0,
        help="Maximum seconds per native response"
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=4
    )
    args = parser.parse_args()
    repository = args.repository.resolve()
    try:
        manager = SessionManager(
            repository,
            args.executable or repository / "build/mcp_alquimia/bin/alquimia_engine_process",
            args.run_root or repository / "build/mcp_alquimia/runs",
            args.timeout,
            args.max_sessions,
        )
    except ValueError as error:
        parser.error(str(error))
    create_server(manager).run(transport="stdio")


if __name__ == "__main__":
    main()
