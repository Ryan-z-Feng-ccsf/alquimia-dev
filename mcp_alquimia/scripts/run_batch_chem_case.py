"""Run any registered batch_chem case through the native engine process."""

import argparse
import json
from pathlib import Path

from mcp_alquimia.case_catalog import stage_case
from mcp_alquimia.case_registry import CaseSpec, get_case
from mcp_alquimia.setup_request import SetupRequest

from case_runner import run_case


def _resolve(case: str | CaseSpec, engine: str):
    """Return the case and its input for the engine."""
    spec = get_case(case) if isinstance(case, str) else case
    return spec, spec.input_for(engine)


def prepare_case(
    repository: str | Path,
    run_dir: str | Path,
    case: str | CaseSpec,
    engine: str,
    request: SetupRequest | None = None
):
    """Stage one engine's input and referenced files in a new run directory.

    Args:
        repository (str or Path): Alquimia checkout containing benchmarks/batch_chem.
        run_dir (str or Path): Destination directory, which must not exist.
        case (str or CaseSpec): Registered case name, or a case defined by the caller.
        engine (str): Engine to stage; one the case has an input for.
        request (SetupRequest, optional): Validated overrides of the case settings.

    Returns:
        dict: Native setup arguments. Density is kg/m³, temperature is degrees
        Celsius, pressure is Pa, and volume is m³; porosity and saturation are
        dimensionless.

    Raises:
        ValueError: The case or engine is unknown, or the request does not fit the input.
        FileNotFoundError: The input or a referenced file is missing.
        FileExistsError: The destination already exists.
        OSError: Creating the directory or copying an asset fails.
    """
    spec, engine_input = _resolve(case, engine)
    return stage_case(repository, spec, engine_input, run_dir, request)


def run_batch_chem_case(
    repository: str | Path,
    executable: str | Path,
    run_dir: str | Path,
    case: str | CaseSpec,
    engine: str,
    max_steps: int | None = None,
    dt: float | None = None,
    timeout: float = 60.0,
    request: SetupRequest | None = None
):
    """Run one engine of a case with step durations and response timeout in seconds.

    Args:
        repository (str or Path): Checkout supplying the benchmark assets.
        executable (str or Path): Precompiled native engine process.
        run_dir (str or Path): New directory for inputs, logs, and results.
        case (str or CaseSpec): Registered case name, or a case defined by the caller.
        engine (str): Engine to run; one the case has an input for.
        max_steps (int, optional): Nonnegative reaction count; zero only initializes.
            Defaults to the case's max_steps.
        dt (float, optional): Positive, finite seconds per reaction. Defaults to the
            case's dt.
        timeout (float): Positive, finite seconds per response.
        request (SetupRequest, optional): Validated overrides of the case settings.

    Returns:
        dict: Metadata, initial and reaction history, and final state.

    Raises:
        ValueError: A numerical option, the case or the engine is invalid.
        OSError: Assets are missing, the destination exists, or file I/O fails.
        EngineProcessError: Native execution or communication fails.
    """
    spec, engine_input = _resolve(case, engine)
    return run_case(
        repository,
        executable,
        run_dir,
        case=spec.name,
        prepare_case=lambda _repository, directory: stage_case(
            repository, spec, engine_input, directory, request),
        max_steps=spec.max_steps if max_steps is None else max_steps,
        dt=spec.dt if dt is None else dt,
        timeout=timeout
    )


def main():
    """Run a case from the command line, printing the final state and artifact directory.

    Raises:
        SystemExit: Help is requested, arguments are invalid, or a handled run
            failure is reported with a nonzero exit status.
    """
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="Case name (see list_cases)")
    parser.add_argument("--engine", required=True,
                        help="Engine to run, one list_cases shows the case under")
    parser.add_argument("--request",
                        help="JSON object overriding the case settings, e.g. '{\"temperature\": 40}'")
    parser.add_argument("--repository", type=Path, default=root)
    parser.add_argument("--executable", type=Path,
                        default=root / "build/mcp_alquimia/bin/alquimia_engine_process")
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="New directory for inputs, logs, and structured results")
    parser.add_argument("--max-steps", type=int, help="Reaction count (default: the case's)")
    parser.add_argument("--dt", type=float, help="Seconds per step (default: the case's)")
    parser.add_argument("--timeout", type=float, default=60.0, help="Seconds per engine process call")
    args = parser.parse_args()
    try:
        request = SetupRequest.model_validate_json(args.request) if args.request else None
    except ValueError as error:
        parser.error(f"--request: {error}")
    try:
        result = run_batch_chem_case(
            args.repository,
            args.executable,
            args.run_dir,
            args.case,
            args.engine,
            args.max_steps,
            args.dt,
            args.timeout,
            request
        )
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(
        {
            "run_dir": str(args.run_dir.resolve()),
            "final_result": result["final"]
        },
        indent=2,
        allow_nan=False
    ))


if __name__ == "__main__":
    main()
