# Alquimia MCP server

The local stdio MCP server controls persistent native processes linked to an existing Alquimia installation. Each simulation session keeps its own engine state across requests.

The server discovers its cases from the `benchmarks/batch_chem/*.cfg` files, covering PFLOTRAN, CrunchFlow and ONNX benchmarks; each case's database or model files are read from its input file. `setup` accepts an optional `request` object that overrides the benchmark's physical settings. The `*-ac-*` benchmarks name a default condition that their input does not define, so `setup` for them requires `request.initial_condition` (for example `initial`). Each supports configurable step count and timestep, structured results, and pytest coverage against the original batch driver. It reuses precompiled engines; compilation is never part of simulation requests.

The `mcp-alquimia` command starts the MCP server. Use `list_cases`, then `setup(case)` to obtain a session ID, `initialize(session_id)`, `react(session_id)`, which runs `max_steps` steps of `dt` seconds in one engine request (an optional `time_config` overrides both, starting from the cfg values), `get_results(session_id)`, and `close(session_id)`. The server closes remaining sessions on disconnect.

`search_docs(query, engine?, limit?)` and `read_doc(doc_id)` search the PFLOTRAN and CrunchFlow manuals and the Alquimia known issues (`engine` is `pflotran`, `crunchflow` or `alquimia`). They read a prebuilt SQLite FTS5 index (default `build/mcp_alquimia/manual_index.sqlite`, override with `--manual-index`), which the container image builds from `docs/`. See [docs/README.md](docs/README.md) for the sources and how to rebuild the index.

Inputs, logs, operation records, results, and failure diagnostics are retained under `~/.alquimia/runs/<session_id>/` by default (override with `--run-root`). Native failures end the affected session; start a new session to retry. Invalid arguments and lifecycle-order errors leave the session usable.

The ONNX adapter ignores timestep during inference; `dt` controls recorded session time. ONNX returns `null` for pH because its adapter provides no auxiliary pH output.

Any case can also be run without MCP through `python -m mcp_alquimia.run_batch_chem_case --case <name> --run-dir <new dir>` (optionally `--request '<json>'` for setup overrides, and `--max-steps`/`--dt`). [Container packaging](containers/README.md) builds a self-contained Linux x86-64 image and runs without host sudo when rootless Docker prerequisites are available; a second launcher runs the published image on a rootful system daemon.
