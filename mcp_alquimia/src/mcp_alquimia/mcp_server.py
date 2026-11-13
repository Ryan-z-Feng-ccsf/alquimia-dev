"""Serve the supported Alquimia benchmarks as stateful MCP tools over stdio."""

import argparse
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from mcp.server.mcpserver import MCPServer

from .manual_search import ManualIndex
from .manual_tools import register_manual_tools
from .session_manager import SessionManager
from .simulation_tools import register_simulation_tools
from .tool_protocol import prepare_tool_schemas

# Loaded by clients on every turn: when to use the server and the call order only;
# each tool's own description carries its arguments and return fields.
SERVER_INSTRUCTIONS = (
    "Geochemical batch-reaction simulator (PFLOTRAN and CrunchFlow through Alquimia). "
    "Use it instead of estimating from memory when asked how a water-mineral-gas system "
    "evolves over time: concentrations, pH, mineral amounts, or the effect of temperature, "
    "porosity, saturation, chemistry or time step. Cases cover calcite "
    "dissolution/precipitation (incl. CO2), CH4-O2 oxidation, aqueous kinetics, ion "
    "exchange, sorption isotherms, surface complexation and microbial reactions. "
    "Call order: list_cases, get_chemistry (before changing chemistry), setup, initialize, "
    "react (repeat to continue), get_results, close. A session runs one engine; to compare "
    "engines, run one session per engine with the same request and time_config. "
    "Look up PFLOTRAN or CrunchFlow manual details with search_docs and read_doc instead of "
    "recalling them, and the Alquimia known issues (collection alquimia) when a setting "
    "seems to have no effect or a result looks wrong."
)


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
        manual_index or manager.repository / "build/mcp_alquimia/manual_index.sqlite"
    )

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
        instructions=SERVER_INSTRUCTIONS
    )
    register_simulation_tools(server, manager)
    register_manual_tools(server, manuals)
    prepare_tool_schemas(server)
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
