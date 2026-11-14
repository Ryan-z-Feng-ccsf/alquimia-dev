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

from ..cases.catalog import list_cases as describe_cases, stage_case
from ..cases.registry import get_case
from ..requests.setup import SetupRequest
from ..requests.time_config import TimeConfigRequest
from .engine_client import AlquimiaEngineProcess


LOGGER = logging.getLogger(__name__)


def list_cases(repository: str | Path) -> dict[str, Any]:
    """Describe every registered case without starting an engine.

    Args:
        repository: Alquimia checkout supplying the benchmark inputs.

    Returns:
        The shared defaults, each engine's case names, and each case's description.
    """
    return describe_cases(repository)


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace a JSON artifact, rejecting non-finite numbers."""
    temporary: Path = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


@dataclass
class SimulationSession:
    """Native process, lifecycle, and history belonging to one session ID."""

    case: str
    engine: str
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

    def setup(
        self,
        case: str,
        engine: str,
        request: SetupRequest | None = None
    ) -> dict[str, Any]:
        """Stage a case's input, start a persistent engine process and initialize it.

        Args:
            case: Identifier from list_cases.
            engine: Engine to run, one list_cases shows for the case.
            request: Optional validated overrides of the case's setup fields.
                Omitted fields keep the defaults shown by list_cases.

        Returns:
            session_id, case, engine, setup (the staged settings sent to the
            engine, including engine, input_file and initial_condition),
            time_config (dt and max_steps), and the initialized raw state with its
            metadata. No reaction step is run.

        Raises:
            ValueError: The case or engine is unknown, the request does not fit the
                engine's input, or the session limit is reached.
            OSError: The engine process or inputs are missing, or artifact I/O fails.
            EngineProcessError: Native startup, setup or initialization fails.
        """
        with self._lock:
            spec = get_case(case)
            # Get the specific .in file
            engine_input = spec.input_for(engine)
            if len(self._sessions) >= self.max_sessions:
                raise ValueError("Session limit reached; close a session before setup")
            if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
                raise FileNotFoundError(
                    f"Engine process is missing or not executable: {self.executable}. "
                    "Build it explicitly with build_tools/build_alquimia_engine_process.py.")

            session_id = uuid.uuid4().hex
            run_dir = self.run_root / session_id
            process = None
            operation = "setup"
            try:
                setup = stage_case(self.repository, spec, engine_input, run_dir, request)
                # Starting dt and max_steps; react's TimeConfigRequest may change them
                time_step_config = {"dt": spec.dt, "max_steps": spec.max_steps}
                write_json(
                    run_dir / "simulation_request.json",
                    {
                        "session_id": session_id,
                        "case": spec.name,
                        "engine": engine_input.engine,
                        "request": request.model_dump(exclude_unset=True) if request else {},
                        "setup": setup,
                        "simulation": time_step_config,
                        "engine_process": str(self.executable),
                    }
                )
                process = AlquimiaEngineProcess(self.executable, run_dir, self.timeout)
                metadata = process.request("setup", **setup)
                operation = "initialize"
                initial = process.request("initialize")
                session = SimulationSession(
                    spec.name,
                    engine_input.engine,
                    run_dir,
                    process,
                    metadata,
                    time_step_config["dt"],
                    time_step_config["max_steps"],
                    [initial]
                )
                self._log_operation(run_dir, "initialize", {})
                self._save(session_id, session, "initialized")
                self._sessions[session_id] = session
            except Exception as error:
                self._record_failure(run_dir, operation, error, process)
                raise
            return {
                "session_id": session_id,
                "case": spec.name,
                "engine": engine_input.engine,
                "setup": setup,
                "time_config": time_step_config,
                "state": initial,
                "metadata": metadata,
            }

    def react(
        self,
        session_id: str,
        time_config: TimeConfigRequest | None = None
    ) -> dict[str, Any]:
        """Run max_steps reaction steps of dt seconds in one engine request.

        Args:
            session_id: ID returned by setup.
            time_config: Optional validated overrides of the session's dt (seconds)
                and max_steps (steps per call). Set values persist for later calls
                once the engine completes them; omitted fields keep the session's
                current values (initially the defaults).

        Returns:
            state (raw engine snapshot after the call), metadata, and the dt
            (seconds) and max_steps the engine ran.
        """
        time_config = time_config or TimeConfigRequest()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ValueError("Unknown or closed session_id; call setup for a new session")
            # The engine runs max_steps steps of dt in one request
            parameters = {
                "dt": session.dt,
                "max_steps": session.max_steps,
                **time_config.overrides(),
            }
            try:
                result = session.process.request("react", **parameters)
                session.history.append(result)
                self._log_operation(session.run_dir, "react", parameters)
                self._save(session_id, session, "initialized")
                # Overrides persist only once the engine has completed them
                session.dt, session.max_steps = parameters["dt"], parameters["max_steps"]
            except Exception as error:
                self._sessions.pop(session_id)
                self._record_failure(session.run_dir, "react", error, session.process)
                raise
            return {"state": result, "metadata": session.metadata, **parameters}

    def close(self, session_id: str) -> dict[str, Any]:
        """Reap one process and finalize artifacts; repeated close is harmless.

        Returns:
            closed=True if a live session was removed, otherwise closed=False.
            Previously saved artifacts are retained in both cases.
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return {"closed": False}
            try:
                session.process.close()
                self._save(session_id, session, "closed")
            except Exception as error:
                self._record_failure(session.run_dir, "close", error, session.process)
                raise
            return {"closed": True}

    def close_all(self) -> None:
        """Close every session on disconnect, continuing after individual failures."""
        with self._lock:
            for session_id in list(self._sessions):
                try:
                    self.close(session_id)
                except Exception:
                    LOGGER.exception("Failed to close Alquimia session %s", session_id)

    @staticmethod
    def _log_operation(run_dir: Path, operation: str, parameters: dict[str, Any]) -> None:
        """Append a completed engine operation to the JSONL log kept for replay and debugging."""
        with (run_dir / "operations.jsonl").open("a") as stream:
            stream.write(json.dumps({"operation": operation, **parameters}, allow_nan=False) + "\n")

    @staticmethod
    def _save(session_id: str, session: SimulationSession, status: str) -> None:
        """Persist the full history and latest snapshot after each state change."""
        write_json(
            session.run_dir / "simulation_results.json",
            {
                "session_id": session_id,
                "case": session.case,
                "engine": session.engine,
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
