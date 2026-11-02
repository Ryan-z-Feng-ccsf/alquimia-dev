"""Describe registered cases and stage one engine's input for an engine process.

Cases are defined in case_registry. Files an input depends on (chemical
databases, ONNX models) are read from the input itself, and chemistry changes
are written into the staged copy, never into the benchmark.
"""

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from .case_registry import CASES, CaseSpec, EngineInput
from .chemistry_patch import UNITS, patch_chemistry, read_chemistry
from .setup_request import SetupRequest

BATCH_CHEM_DIR = "benchmarks/batch_chem"

# PFLOTRAN "DATABASE ./x.dat" and CrunchFlow "database x.dbs" / "kinetic_database x.dbsx".
# Requiring whitespace after the keyword excludes "database_sweep".
_DATABASE_LINE = re.compile(
    r"^\s*(?:kinetic_)?database\s+['\"]?([^\s'\"]+)", re.IGNORECASE
)


def _safe_relative(name: str) -> str | None:
    """Normalize a path from an input file; None if it could leave the input directory."""
    normalized = os.path.normpath(name)
    if os.path.isabs(normalized) or normalized == ".." or normalized.startswith(".." + os.sep):
        return None
    return normalized


def input_path(repository: str | Path, engine_input: EngineInput) -> Path:
    """Absolute path of an engine input in the repository's benchmarks."""
    return (Path(repository) / BATCH_CHEM_DIR / engine_input.input_file).resolve()


def referenced_files(input_file: Path, engine: str) -> tuple[str, ...]:
    """Read the files an input depends on, excluding the input file itself.

    ONNX configs name their model in a top-level ``model`` key. PFLOTRAN and
    CrunchFlow inputs name chemical databases on ``database`` lines.

    Args:
        input_file: Existing native input file.
        engine: Canonical engine name.

    Returns:
        Unique relative paths in the order they are referenced. A reference that
        would escape the input's directory is kept as given so that staging
        reports it instead of silently dropping it.
    """
    if engine == "ONNX":
        # ONNX
        model = json.loads(input_file.read_text()).get("model")
        names = [model] if isinstance(model, str) else []
    else:
        # Dynamically read the .dbs / .dat / .dbsx
        # Results (example): ch4-o2.dbsx
        names = [match.group(1)
                 for line in input_file.read_text(errors="replace").splitlines()
                 if (match := _DATABASE_LINE.match(line))]
    unique = []
    for name in names:
        name = _safe_relative(name) or name
        if name not in unique:
            unique.append(name)
    return tuple(unique)


def required_files(
    repository: str | Path, engine_input: EngineInput
) -> tuple[str, ...]:
    """The input's file name followed by the files it references."""
    path = input_path(repository, engine_input)
    referenced = referenced_files(path, engine_input.engine) if path.is_file() else ()
    return (path.name, *referenced)


def missing_files(repository: str | Path, engine_input: EngineInput) -> list[str]:
    """Paths, relative to the input's directory, that are required but absent."""
    directory = input_path(repository, engine_input).parent
    return [name for name in required_files(repository, engine_input)
            if not (directory / name).is_file()]


def list_cases(
    repository: str | Path, cases: Mapping[str, CaseSpec] = CASES
) -> dict[str, Any]:
    """Describe the cases compactly: shared defaults once, then cases by engine.

    Physical defaults are not repeated here; setup's request schema shows them.

    Args:
        repository: Checkout containing benchmarks/batch_chem.
        cases: Cases to describe, keyed by name.

    Returns:
        "defaults" (dt in s, max_steps, initial condition), "engines" (each
        engine's case names), "cases" (each case's description), and
        "missing_files" (case to engine to absent files) only if any is absent.
    """
    by_engine: dict[str, list[str]] = {}
    missing: dict[str, dict[str, list[str]]] = {}
    for spec in cases.values():
        for engine_input in spec.inputs:
            by_engine.setdefault(engine_input.engine, []).append(spec.name)
            if files := missing_files(repository, engine_input):
                missing.setdefault(spec.name, {})[engine_input.engine] = files
    listing = {
        "defaults": {
            "dt": CaseSpec.dt,
            "max_steps": CaseSpec.max_steps,
            "initial_condition": "initial",
        },
        "engines": by_engine,
        "cases": {spec.name: spec.description for spec in cases.values()},
    }
    if missing:
        listing["missing_files"] = missing
    return listing


def _condition(engine_input: EngineInput, request: SetupRequest | None) -> str:
    """The requested initial condition, or the input's default.

    Raises:
        ValueError: The requested condition is not defined by the input.
    """
    condition = (
        request.initial_condition if request and request.initial_condition else None
    )
    if condition is None:
        return engine_input.conditions[0]
    if condition.lower() not in (name.lower() for name in engine_input.conditions):
        raise ValueError(
            f"Unknown initial condition {condition!r} for {engine_input.engine}; "
            f"valid: {', '.join(engine_input.conditions)}"
        )
    return condition


def build_setup(
    engine_input: EngineInput, request: SetupRequest | None = None
) -> dict[str, Any]:
    """Turn a validated request into native setup arguments for one engine input.

    Args:
        engine_input: The engine and input to set up.
        request: Optional validated settings; its defaults apply when omitted.
            Physical settings are density (kg/m^3), porosity, temperature (degC),
            pressure (Pa), volume (m^3) and saturation; initial_condition names a
            condition in the input.

    Returns:
        The engine, input file name, initial condition, and the six numeric settings.

    Raises:
        ValueError: The requested initial condition is not defined by the input.
    """
    request = request or SetupRequest()
    return {
        "engine": engine_input.engine,
        "input_file": Path(engine_input.input_file).name,
        "initial_condition": _condition(engine_input, request),
        **request.physical(),
    }


def get_case(repository: str | Path, case_name: str) -> CaseSpec:
    """Look up one discovered case by its identifier.

    Raises:
        ValueError: The identifier is not a discovered case, or its cfg is malformed.
    """
    cfg_path = case_cfg_path(repository, case_name)
    if not cfg_path.is_file():
        raise ValueError(f"Unknown case {case_name!r}; call list_cases")
    try:
        return read_case(cfg_path)
    except (configparser.Error, KeyError, ValueError, OSError) as error:
        raise ValueError(f"Case {case_name!r} has an unreadable cfg: {error!r}") from error


def describe_case(spec: CaseSpec) -> dict[str, Any]:
    """Summarize a case for list_cases without starting an engine."""
    missing = spec.missing_files
    return {
        "case": spec.name,
        "engine": spec.engine,
        "description": spec.description,
        "input_file": spec.input_file.name,
        "database_files": list(spec.database_files),
        "initial_condition": spec.initial_condition,
        "physical_condition": dict(spec.physical_condition),
        "default_max_steps": spec.default_max_steps,
        "default_dt": spec.default_dt,
        "dt_units": "s",
        "available": not missing,
        "missing_files": missing,
        "requires_initial_condition": spec.undefined_condition,
        "notes": " ".join(filter(None, [
            "Inference ignores dt; pH is null." if spec.engine == "ONNX" else "",
            f"The default condition {spec.initial_condition!r} is only a name in the cfg and is "
            "not defined in the input; setup needs request.initial_condition, e.g. 'initial'."
            if spec.undefined_condition else "",
        ])),
    }


def build_setup(spec: CaseSpec, request: SetupRequest | None = None) -> dict[str, Any]:
    """Merge a validated request over the case defaults into native setup arguments.

    Args:
        spec: Case supplying the defaults.
        request: Optional overrides, already validated by SetupRequest. Physical
            settings are density (kg/m^3), porosity, temperature (degC), pressure (Pa),
            volume (m^3) and saturation; initial_condition names a condition in the input.

    Returns:
        The engine, input file name, initial condition, and the six numeric settings.
    """
    return {
        "engine": spec.engine,
        "input_file": spec.input_file.name,
        "initial_condition": spec.initial_condition,
        **spec.physical_condition,
        **(request.overrides() if request else {}),
    }


def default_time_config(spec: CaseSpec) -> dict[str, Any]:
    """Return the case's cfg dt (seconds) and max_steps, the session's starting values."""
    return {"dt": spec.default_dt, "max_steps": spec.default_max_steps}


def stage_case(
    repository: str | Path,
    spec: CaseSpec,
    engine_input: EngineInput,
    run_dir: str | Path,
    request: SetupRequest | None = None
) -> dict[str, Any]:
    """Copy one engine's input and the files it references into a new run directory.

    Files keep the layout the input expects, because the engine process runs with
    the run directory as its working directory. Chemistry changes in the request
    are then written into the staged input.

    Args:
        repository: Checkout containing benchmarks/batch_chem.
        spec: Case being staged, named in error messages.
        engine_input: The engine and input to stage.
        run_dir: Destination directory, which must not exist.
        request: Optional validated client settings; see build_setup.

    Returns:
        Native setup arguments for the engine process.

    Raises:
        ValueError: The request is invalid for this input, or a referenced path
            leaves the input's directory.
        FileNotFoundError: The input or a file it references is missing.
        FileExistsError: The destination already exists.
        OSError: Creating the directory or copying a file fails.
    """
    setup = build_setup(engine_input, request)
    # batch_chem folder
    source = input_path(repository, engine_input).parent
    # .in, database files
    names = required_files(repository, engine_input)
    # Validate the database files
    for name in names[1:]:
        if _safe_relative(name) != name:
            raise ValueError(f"Referenced file {name!r} must be relative to the input directory")
    missing = missing_files(repository, engine_input)
    if missing:
        raise FileNotFoundError(
            f"Case {spec.name!r} is missing {', '.join(missing)} next to "
            f"{input_path(repository, engine_input)}")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in names:
        destination = run_dir / name
        # Databases may sit in a subdirectory of the input's directory
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    return setup
