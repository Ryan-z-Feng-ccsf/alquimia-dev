"""Shared execution and artifact handling for supported chemistry cases."""

import json
import math
from pathlib import Path

from .subprocess_client import AlquimiaEngineProcess


def run_case(
    repository: str | Path,
    executable: str | Path,
    run_dir: str | Path,
    *,
    case: str,
    prepare_case,
    max_steps: int,
    dt: float,
    timeout: float
    ):
    """Run a staged benchmark case and save its inputs, log, and state history.

    Writes simulation_request.json before launching the engine process and
    simulation_results.json after successful shutdown. Run failures are recorded
    in simulation_failure.json when possible.

    Args:
        repository (str or Path): Alquimia checkout supplying the case assets.
        executable (str or Path): Precompiled native engine process executable.
        run_dir (str or Path): New directory for staged inputs and run artifacts.
        case (str): Identifier stored in request and result artifacts.
        prepare_case (Callable): Stages assets and returns native setup arguments.
        max_steps (int): Nonnegative number of reactions; zero returns only
            the initialized state.
        dt (float): Positive, finite duration of each step in seconds.
        timeout (float): Positive, finite timeout per engine process response in seconds.

    Returns:
        dict: Case identifier, metadata with species/mineral order and units,
        history including the initial state, and the final state snapshot.

    Raises:
        ValueError: Step count, dt, or timeout is invalid.
        FileNotFoundError: The engine process or required case assets are missing.
        FileExistsError: The run directory already exists.
        OSError: Staging, launching, or writing run artifacts fails.
        EngineProcessError: Native setup, stepping, communication, or shutdown fails.
        subprocess.TimeoutExpired: The engine process does not exit after acknowledging
            shutdown within the configured timeout.
    """
    if type(max_steps) is not int or max_steps < 0:
        raise ValueError("max_steps must be a nonnegative integer")
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("dt must be finite and positive")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    executable = Path(executable).resolve()
    if not executable.is_file():
        raise FileNotFoundError(
            f"Engine process not built: {executable}. "
            "Run build_tools/build_alquimia_engine_process.py first.")

    setup = prepare_case(repository, run_dir)
    run_dir = Path(run_dir).resolve()
    request = {
        "case": case,
        "setup": setup,
        "max_steps": max_steps,
        "timestep": dt,
        "engine_process": str(executable)
    }
    (run_dir / "simulation_request.json").write_text(json.dumps(request, indent=2) + "\n")
    try:
        with AlquimiaEngineProcess(executable, run_dir, timeout) as engine_process:
            metadata = engine_process.request("setup", **setup)
            history = [engine_process.request("initialize")]
            # One step per request keeps every step in the history
            for _ in range(max_steps):
                history.append(engine_process.request("react", dt=dt, max_steps=1))
            final = engine_process.request("get_results")
        result = {
            "case": request["case"],
            "metadata": metadata,
            "history": history,
            "final": final
        }
        (run_dir / "simulation_results.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n"
        )
        return result
    except Exception as error:
        (run_dir / "simulation_failure.json").write_text(
            json.dumps({"error": str(error)}, indent=2) + "\n")
        raise
