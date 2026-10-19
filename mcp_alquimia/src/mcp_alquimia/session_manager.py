"""Own sequential Alquimia sessions and retain their simulation artifacts."""

from dataclasses import dataclass, field
import json
import logging
import math
import os
from pathlib import Path
import threading
from typing import Any
import uuid

from .case_catalog import (
    describe_case, discover_cases, get_case, default_time_config, stage_case)
from .setup_request import SetupRequest
from .subprocess_client import AlquimiaEngineProcess
from .time_config_request import TimeConfigRequest


LOGGER = logging.getLogger(__name__)


def list_cases(repository: str | Path) -> dict[str, Any]:
    """Describe every benchmark found in the repository without starting an engine.

    Args:
        repository: Alquimia checkout whose batch-chemistry cfg files are scanned.

    Returns:
        A "cases" list with each case's engine, input and database files, default
        settings, and whether all of its files are present.
    """
    return {"cases": [describe_case(spec) for spec in discover_cases(repository).values()]}


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace a JSON artifact, rejecting non-finite numbers."""
    temporary: Path = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


@dataclass
class SimulationSession:
    """Native process, lifecycle, and history belonging to one session ID."""

    case: str
    run_dir: Path
    process: AlquimiaEngineProcess
    metadata: dict[str, Any]
    dt: float
    max_steps: int
    history: list[dict[str, Any]] = field(default_factory=list)


class SessionManager:
    """Serialize native calls and own all engine processes for one MCP connection.

    A lock covers whole operations, including setup and close, because the
    subprocess client is synchronous and not thread-safe. MCP handlers run these
    operations in worker threads so protocol traffic stays responsive.
    """

    def __init__(
        self,
        repository: Path,
        executable: Path,
        run_root: Path,
        timeout: float = 60.0,
        max_sessions: int = 4
    ):
        """Configure a runtime without launching processes or creating files.

        Args:
            repository: Checkout supplying the supported benchmark assets.
            executable: Existing, precompiled native engine process.
            run_root: Parent directory for uniquely named session directories.
            timeout: Positive, finite seconds per native response.
            max_sessions: Maximum number of simultaneously open sessions.

        Raises:
            ValueError: Timeout or session limit is invalid.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("max_sessions must be a positive integer")
        self.repository = Path(repository).resolve()
        self.executable = Path(executable).resolve()
        self.run_root = Path(run_root).resolve()
        self.timeout = timeout
        self.max_sessions = max_sessions
        self._sessions: dict[str, SimulationSession] = {}
        self._lock = threading.RLock()

    def setup(self, case: str, request: SetupRequest | None = None) -> dict[str, Any]:
        """Stage a discovered case and start a persistent engine process.

        Args:
            case: Identifier from list_cases.
            request: Optional validated overrides of the case's setup fields.
                Omitted fields keep the benchmark's values.

        Returns:
            Session ID, artifact directory, and native metadata. Initialization
            is a separate operation; setup does not perform a reaction.

        Raises:
            ValueError: The case is unknown, its default initial condition is undefined
                and the request supplies none, or the session limit is reached.
            OSError: Runtime or assets (cfg parameters) are missing, or artifact I/O fails.
            EngineProcessError: Native startup or setup fails.
        """
        with self._lock:
            spec = get_case(self.repository, case)
            if len(self._sessions) >= self.max_sessions:
                raise ValueError("Session limit reached; close a session before setup")
            if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                raise FileNotFoundError(
                    f"Engine process is missing or not executable: {self.executable}. "
                    "Build it explicitly with build_tools/build_alquimia_engine_process.py.")
            
            session_id = uuid.uuid4().hex
            run_dir = self.run_root / session_id
            process = None
            try:
                setup = stage_case(spec, run_dir, request)
                # Starting dt and max_steps; react's TimeConfigRequest may change them
                time_step_config = default_time_config(spec)
                write_json(
                    run_dir / "simulation_request.json", 
                    {
                        "session_id": session_id,
                        "case": spec.name,
                        "request": request.model_dump(exclude_none=True) if request else {},
                        "setup": setup,
                        "simulation": time_step_config,
                        "engine_process": str(self.executable),
                    }
                )
                process = AlquimiaEngineProcess(self.executable, run_dir, self.timeout)
                metadata = process.request("setup", **setup)
                session = SimulationSession(
                    spec.name, run_dir, process, metadata, time_step_config["dt"], time_step_config["max_steps"])
                self._save(session_id, session, "ready")
                self._sessions[session_id] = session
            except Exception as error:
                self._record_failure(run_dir, "setup", error, process)
                raise
            return {
                "session_id": session_id,
                "case": spec.name,
                "run_dir": str(run_dir),
                "setup": setup,
                "simulation": time_step_config,
                "metadata": metadata
            }

    def initialize(self, session_id: str) -> dict[str, Any]:
        """Apply the benchmark's named initial condition once and return its state."""
        return self._request(session_id, "initialize")

    def react(
        self,
        session_id: str,
        time_config: TimeConfigRequest | None = None
    ) -> dict[str, Any]:
        """Perform one reaction and return the updated state.

        Args:
            session_id: Initialized session.
            time_config: Optional validated overrides of the session's dt (seconds)
                and max_steps. Set values persist for later reactions once this
                step is accepted; omitted fields keep the session's current values
                (initially the cfg's).

        Raises:
            ValueError: The session already performed its max_steps reactions.

        ONNX ignores dt during inference; its recorded time still advances.
        """
        return self._request(
            session_id, "react", time_config=time_config or TimeConfigRequest())

    def get_results(self, session_id: str) -> dict[str, Any]:
        """Return the latest initialized state without advancing the engine."""
        return self._request(session_id, "get_results")

    def _request(self, session_id: str, operation: str, **parameters) -> dict[str, Any]:
        """Validate lifecycle, invoke the engine, and persist completed operations."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ValueError("Unknown or closed session_id; call setup for a new session")
            if operation == "initialize" and session.history:
                raise ValueError("Session already initialized")
            if operation != "initialize" and not session.history:
                raise ValueError("Call initialize before react or get_results")
            if operation == "react":
                time_config = {
                    "dt": session.dt,
                    "max_steps": session.max_steps,
                    **parameters.pop("time_config").overrides(),
                }
                # Initialized result was added in the history
                if len(session.history) - 1 >= time_config["max_steps"]:
                    raise ValueError(
                        f"Session reached its max_steps ({time_config['max_steps']}); "
                        "close it or pass a larger time_config.max_steps to react")
                session.dt, session.max_steps = time_config["dt"], time_config["max_steps"]
                parameters["timestep"] = session.dt
                parameters["max_steps"] = session.max_steps
            try:
                result = session.process.request(operation, **parameters)
                if operation != "get_results":
                    session.history.append(result)
                    # Append the executed operation to a JSONL audit log for future replay and debugging.
                    with (session.run_dir / "operations.jsonl").open("a") as stream:
                        stream.write(json.dumps(
                            {"operation": operation, **parameters},
                            allow_nan=False) + "\n"
                        )
                    self._save(session_id, session, "initialized")
            except Exception as error:
                self._sessions.pop(session_id)
                self._record_failure(session.run_dir, operation, error, session.process)
                raise
            return {"session_id": session_id, "state": result}

    def close(self, session_id: str) -> dict[str, Any]:
        """Reap one process and finalize artifacts; repeated close is harmless.

        Returns:
            Session ID and closed=True if a live session was removed, otherwise
            closed=False. Previously saved artifacts are retained in both cases.
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return {"session_id": session_id, "closed": False}
            try:
                session.process.close()
                self._save(session_id, session, "closed")
            except Exception as error:
                self._record_failure(session.run_dir, "close", error, session.process)
                raise
            return {
                "session_id": session_id,
                "closed": True,
                "run_dir": str(session.run_dir)
            }

    def close_all(self) -> None:
        """Close every session on disconnect, continuing after individual failures."""
        with self._lock:
            for session_id in list(self._sessions):
                try:
                    self.close(session_id)
                except Exception:
                    LOGGER.exception("Failed to close Alquimia session %s", session_id)

    @staticmethod
    def _save(session_id: str, session: SimulationSession, status: str) -> None:
        """Persist the full history and latest snapshot after each state change."""
        write_json(
            session.run_dir / "simulation_results.json", 
            {
                "session_id": session_id,
                "case": session.case,
                "status": status,
                "metadata": session.metadata,
                "history": session.history,
                "final": session.history[-1] if session.history else None,
            }
        )

    @staticmethod
    def _record_failure(
        run_dir: Path,
        operation: str,
        error: Exception,
        process: AlquimiaEngineProcess | None
    ) -> None:
        """Release a failed process and save diagnostics without masking the cause."""
        if process is not None:
            try:
                process.close()
            except Exception:
                LOGGER.exception("Cleanup failed for %s", run_dir)
        if run_dir.is_dir():
            try:
                write_json(
                    run_dir / "simulation_failure.json", 
                    {
                        "operation": operation,
                        "error": str(error),
                        "log_path": str(run_dir / "engine_output.log"),
                    }
                )
            except OSError:
                LOGGER.exception("Could not save failure artifact in %s", run_dir)
