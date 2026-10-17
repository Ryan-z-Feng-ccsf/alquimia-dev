"""Discover benchmark cases from the repository and stage them for an engine process.

A case is one batch-chemistry driver ``.cfg`` file. It names the native input file
and carries the physical settings, so nothing about a benchmark is hardcoded here.
Files the input depends on (chemical databases, ONNX models) are read from the
input itself.
"""

import configparser
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping

from .setup_request import SetupRequest

LOGGER = logging.getLogger(__name__)

BATCH_CHEM_DIR = "benchmarks/batch_chem"

# Canonical engine names accepted by the native engine process, keyed by lowercase.
ENGINES = {"pflotran": "PFloTran", "crunchflow": "CrunchFlow", "onnx": "ONNX"}

_NUMBER = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-z]*)")
_SECONDS_PER_UNIT = {
    "": 1.0, "s": 1.0, "sec": 1.0, "second": 1.0, "seconds": 1.0,
    "hour": 3600.0, "hours": 3600.0, "day": 86400.0, "days": 86400.0,
}

# PFLOTRAN "DATABASE ./x.dat" and CrunchFlow "database x.dbs" / "kinetic_database x.dbsx".
# Requiring whitespace after the keyword excludes "database_sweep".
_DATABASE_LINE = re.compile(r"^\s*(?:kinetic_)?database\s+['\"]?([^\s'\"]+)", re.IGNORECASE)


@dataclass(frozen=True)
class CaseSpec:
    """One discovered benchmark and the files it needs, as described by its cfg."""

    name: str
    engine: str
    description: str
    input_file: Path
    initial_condition: str
    physical_condition: Mapping[str, float]
    default_dt: float
    default_max_steps: int
    # .dbs / .dbsx (CrunchFlow) .dat (PFLOTRAN) .onnx (ONNX)
    database_files: tuple[str, ...] = field(default_factory=tuple)
    undefined_condition: bool = False

    @property
    def missing_files(self) -> list[str]:
        """Paths, relative to the input's directory, that are required but absent."""
        required = [self.input_file.name, *self.database_files]
        return [name for name in required if not (self.input_file.parent / name).is_file()]


def _safe_relative(name: str) -> str | None:
    """Normalize a path from an input file; None if it could leave the input directory."""
    normalized = os.path.normpath(name)
    if os.path.isabs(normalized) or normalized == ".." or normalized.startswith(".." + os.sep):
        return None
    return normalized


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


def _leading_number(text: str, units: Mapping[str, float] | None = None) -> float:
    """Parse the leading number of a cfg value.

    The native driver reads these values with atof, which ignores anything after
    the number, so trailing text such as ';' is ignored. When ``units`` is given,
    the word after the number must be one of its keys and scales the result, so
    an unrecognized unit is rejected rather than misread.

    Raises:
        ValueError: There is no leading number, or a unit is given but not recognized.
    """
    
    # Extract the number from string 
    # [text, number, unit]
    # unit is only handled for timestep
    match = _NUMBER.match(text)
    if not match:
        raise ValueError(f"no leading number in {text!r}")
    value = float(match.group(1))
    if units is None:
        return value
    unit = match.group(2).lower()
    if unit not in units:
        raise ValueError(f"unsupported unit {unit!r} in {text!r}")
    return value * units[unit]


def read_case(cfg_path: Path) -> CaseSpec:
    """Parse one driver cfg into a CaseSpec.

    Raises:
        configparser.Error, KeyError, ValueError, OSError: The cfg is malformed or
        its input cannot be read.
    """
    parser = configparser.ConfigParser(inline_comment_prefixes=("#",))
    parser.read_string(cfg_path.read_text())
    engine = ENGINES[parser["chemistry"]["engine"].strip().lower()]
    input_file = (cfg_path.parent / parser["chemistry"]["input_file"]).resolve()
    values = {
        "density": parser["state"]["density"],
        "porosity": parser["state"]["porosity"],
        "temperature": parser["state"]["temperature"],
        "pressure": parser["state"]["pressure"],
        "volume": parser["material"]["volume"],
        "saturation": parser["material"]["saturation"],
    }
    simulation = parser["simulation"]
    condition = parser["chemistry"]["initial_condition"].strip()
    return CaseSpec(
        name=cfg_path.stem,
        engine=engine,
        input_file=input_file,
        initial_condition=condition,
        physical_condition={key: _leading_number(value) for key, value in values.items()},
        default_max_steps=int(_leading_number(simulation["max_steps"])),
        default_dt=_leading_number(simulation["timestep"], _SECONDS_PER_UNIT),
        description=simulation.get("description", "").strip().strip('"'),
        database_files=referenced_files(input_file, engine) if input_file.is_file() else (),
        undefined_condition=parser.has_section(condition),
    )


def discover_cases(repository: str | Path) -> dict[str, CaseSpec]:
    """Scan the repository's batch-chemistry cfg files for runnable cases.

    A cfg that is malformed, or that names an engine this server does not drive,
    is skipped with a log message so one bad file cannot hide the others.

    Args:
        repository: Alquimia checkout containing benchmarks/batch_chem.

    Returns:
        Cases keyed by cfg file stem, sorted by name. Empty if the directory is absent.
    """
    cases = {}
    for cfg_path in sorted((Path(repository) / BATCH_CHEM_DIR).glob("*.cfg")):
        try:
            cases[cfg_path.stem] = read_case(cfg_path)
        except (configparser.Error, KeyError, ValueError, OSError) as error:
            LOGGER.warning("Skipping case %s: %r", cfg_path.name, error)
    return cases


def case_cfg_path(repository: str | Path, case_name: str) -> Path:
    """Return the cfg path for a case name, without requiring that it exists.

    Raises:
        ValueError: The name is empty or contains a path separator or dot segment, so
            it cannot select anything outside the batch-chemistry directory.
    """
    if case_name in ("", ".", "..") or case_name != Path(case_name).name:
        raise ValueError(f"Invalid case name {case_name!r}; call list_cases")
    return Path(repository) / BATCH_CHEM_DIR / f"{case_name}.cfg"


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


def simulation_controls(spec: CaseSpec, request: SetupRequest | None = None) -> dict[str, Any]:
    """Return the session's dt (seconds) and max_steps: cfg defaults, then request overrides."""
    return {
        "dt": spec.default_dt,
        "max_steps": spec.default_max_steps,
        **(request.controls() if request else {}),
    }


def stage_case(
    spec: CaseSpec,
    run_dir: str | Path,
    request: SetupRequest | None = None
) -> dict[str, Any]:
    """Copy a case's input and the files it references into a new run directory.

    Files keep the layout the input expects, because the engine process runs with
    the run directory as its working directory.

    Args:
        spec: Case to stage.
        run_dir: Destination directory, which must not exist.
        request: Optional validated client overrides; see build_setup.

    Returns:
        Native setup arguments for the engine process.

    Raises:
        ValueError: The case's default initial condition is undefined and the
            request supplies none, or a referenced path leaves the input's directory.
        FileNotFoundError: The input or a file it references is missing.
        FileExistsError: The destination already exists.
        OSError: Creating the directory or copying a file fails.
    """
    setup = build_setup(spec, request)
    if spec.undefined_condition and (request is None or request.initial_condition is None):
            raise ValueError(
            f"Case {spec.name!r} names initial condition {spec.initial_condition!r} only in its "
            "cfg, but the input file does not define it. Pass request.initial_condition "
            "with a condition from the input file, e.g. 'initial'.")
    source = spec.input_file.parent
    for name in spec.database_files:
        if _safe_relative(name) != name:
            raise ValueError(f"Referenced file {name!r} must be relative to the input directory")
    missing = spec.missing_files
    if missing:
        raise FileNotFoundError(
            f"Case {spec.name!r} is missing {', '.join(missing)} next to {spec.input_file}")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in (spec.input_file.name, *spec.database_files):
        destination = run_dir / name
        # Guard
        # If the database files are under batch_chem/subfolder
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, destination)
    return setup
