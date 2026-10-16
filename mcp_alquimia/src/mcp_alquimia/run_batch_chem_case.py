"""Run any batch_chem benchmark case through the native engine process."""

import argparse
import json
from pathlib import Path

from .case_catalog import case_cfg_path, read_case, simulation_controls, stage_case
from .case_runner import run_case
from .setup_request import SetupRequest


def prepare_case(
    repository: str | Path,
    run_dir: str | Path,
    case: str,
    request: SetupRequest | None = None
):
    """Stage a benchmark's input and referenced files in a new run directory.

    The physical settings, input file and databases or models come from the case's
    cfg and input via the case catalog; nothing is hardcoded per benchmark.

    Args:
        repository (str or Path): Alquimia checkout containing benchmarks/batch_chem.
        run_dir (str or Path): Destination directory, which must not exist.
        case (str): Case name, the stem of a cfg under benchmarks/batch_chem.
        request (SetupRequest, optional): Validated overrides of the cfg settings.

    Returns:
        dict: Native setup arguments. Density is kg/m³, temperature is degrees
        Celsius, pressure is Pa, and volume is m³; porosity and saturation are
        dimensionless.

    Raises:
        ValueError: The case name is invalid, or its default initial condition is
            undefined and the request supplies none.
        FileNotFoundError: The cfg, input, or a referenced file is missing.
        FileExistsError: The destination already exists.
        OSError: Creating the directory or copying an asset fails.
    """
    return stage_case(read_case(case_cfg_path(repository, case)), run_dir, request)


def run_batch_chem_case(
    repository: str | Path,
    executable: str | Path,
    run_dir: str | Path,
    case: str,
    max_steps: int | None = None,
    dt: float | None = None,
    timeout: float = 60.0,
    request: SetupRequest | None = None
):
    """Run a batch_chem case with step durations and response timeout in seconds.

    Args:
        repository (str or Path): Checkout supplying the benchmark assets.
        executable (str or Path): Precompiled native engine process.
        run_dir (str or Path): New directory for inputs, logs, and results.
        case (str): Case name, the stem of a cfg under benchmarks/batch_chem.
        max_steps (int, optional): Nonnegative reaction count; zero only initializes.
            Defaults to the cfg's max_steps.
        dt (float, optional): Positive, finite seconds per reaction. Defaults to the
            request's dt, then the cfg's timestep.
        timeout (float): Positive, finite seconds per response.
        request (SetupRequest, optional): Validated overrides of the cfg settings.

    Returns:
        dict: Metadata, initial and reaction history, and final state.

    Raises:
        ValueError: A numerical option or the case name is invalid.
        OSError: Assets are missing, the destination exists, or file I/O fails.
        EngineProcessError: Native execution or communication fails.
    """
    spec = read_case(case_cfg_path(repository, case))
    controls = simulation_controls(spec, request)
    return run_case(
        repository,
        executable,
        run_dir,
        case=case,
        prepare_case=lambda _repository, directory: stage_case(spec, directory, request),
        max_steps=controls["max_steps"] if max_steps is None else max_steps,
        dt=controls["dt"] if dt is None else dt,
        timeout=timeout
    )


def main():
    """Run a case from the command line, printing the final state and artifact directory.

    Raises:
        SystemExit: Help is requested, arguments are invalid, or a handled run
            failure is reported with a nonzero exit status.
    """
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True,
                        help="Case name, a cfg stem under benchmarks/batch_chem (see list_cases)")
    parser.add_argument("--request",
                        help="JSON object overriding the case settings, e.g. '{\"temperature\": 40}'")
    parser.add_argument("--repository", type=Path, default=root)
    parser.add_argument("--executable", type=Path,
                        default=root / "build/mcp_alquimia/bin/alquimia_engine_process")
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="New directory for inputs, logs, and structured results")
    parser.add_argument("--max-steps", type=int, help="Reaction count (default: the cfg's max_steps)")
    parser.add_argument("--dt", type=float, help="Seconds per step (default: the cfg's timestep)")
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
