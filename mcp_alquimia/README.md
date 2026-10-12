# Alquimia MCP server

The local stdio MCP server controls persistent native processes linked to an existing Alquimia installation. Each simulation session keeps its own engine state across requests.

The current implementation supports the existing PFLOTRAN and CrunchFlow calcite dissolution cases and the ONNX EX8 neural-network fixed-batch case. Each supports configurable step count and timestep, structured results, and pytest coverage against the original batch driver. It reuses precompiled engines; compilation is never part of simulation requests.

The `mcp-alquimia` command starts the MCP server. Use `list_cases`, then `setup(case)` to obtain a session ID, `initialize(session_id)`, `react(session_id, dt)` for each step in seconds, `get_results(session_id)`, and `close(session_id)`. The server closes remaining sessions on disconnect.

Inputs, logs, operation records, results, and failure diagnostics are retained under `build/mcp_alquimia/runs/<session_id>/` by default. Native failures end the affected session; start a new session to retry. Invalid arguments and lifecycle-order errors leave the session usable.

The ONNX adapter ignores timestep during inference; `dt` controls recorded session time. ONNX returns `null` for pH because its adapter provides no auxiliary pH output.

The standalone case entry points `run_pflotran_calcite`, `run_crunchflow_calcite`, and `run_onnx_ex8` remain available as Python modules. [Rootless container packaging](containers/README.md) builds a self-contained Linux x86-64 image and runs without host sudo when rootless Docker prerequisites are available.
