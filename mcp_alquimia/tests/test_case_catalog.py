"""Verify database detection, listing, request overrides, and staging offline."""

import json

import pytest

from pydantic import ValidationError

from mcp_alquimia.case_catalog import (
    build_setup, get_chemistry, list_cases, missing_files, referenced_files,
    stage_case)
from mcp_alquimia.case_registry import CASES, CaseSpec, EngineInput, get_case
from mcp_alquimia.setup_request import SetupRequest
from mcp_alquimia.time_config_request import TimeConfigRequest

CASE = CaseSpec("case", (EngineInput("CrunchFlow", "case.in"),))


def write_inputs(repository, in_text="database  x.dbs\ndatabase_sweep false\n"):
    """Create a minimal repository holding CASE's CrunchFlow-style input and database."""
    directory = repository / "benchmarks/batch_chem"
    directory.mkdir(parents=True)
    (directory / "case.in").write_text(in_text)
    (directory / "x.dbs").write_text("db")
    return directory


def test_registered_cases_have_their_files(repository_root):
    """Every registered input and the databases it references are in the repository."""
    for spec in CASES.values():
        for engine_input in spec.inputs:
            assert not missing_files(repository_root, engine_input), (spec.name, engine_input)
    calcite = get_case("calcite-short")
    pflotran, crunchflow = calcite.inputs
    assert referenced_files(
        repository_root / "benchmarks/batch_chem" / pflotran.input_file, "PFloTran") == (
        "calcite.dat",)
    general = get_case("general-reaction").inputs[1]
    assert referenced_files(
        repository_root / "benchmarks/batch_chem" / general.input_file, "CrunchFlow") == (
        "general-reaction.dbs", "general-reaction.dbsx")


def test_only_registered_names_resolve():
    """Retired cfg stems and arbitrary paths are unknown cases."""
    assert get_case("calcite-short").name == "calcite-short"
    for name in ("calcite-short-pc-pflotran", "ex8-nn-batch1", "../x", ""):
        with pytest.raises(ValueError, match="Unknown case"):
            get_case(name)


def test_engine_input_lookup():
    """A case's input is found by engine name, ignoring case; a missing engine fails."""
    spec = get_case("calcite-short")
    assert spec.input_for("PFloTran").input_file == "calcite-short-pflotran.in"
    assert spec.input_for("crunchflow").input_file == "calcite-short-crunch.in"
    with pytest.raises(ValueError, match="no CrunchFlow input; available: PFloTran"):
        get_case("microbial").input_for("CrunchFlow")


def test_listing_prints_defaults_once(repository_root):
    """Time stepping appears once for all cases; physical defaults stay in setup's schema."""
    listing = list_cases(repository_root)
    assert listing["defaults"] == {"dt": 86400.0, "max_steps": 50, "initial_condition": "initial"}
    assert set(listing) == {"defaults", "engines", "cases"}
    assert listing["cases"]["calcite-short"] == get_case("calcite-short").description
    assert set(listing["cases"]) == set(CASES)
    # Each case is named under every engine that runs it
    assert listing["engines"]["PFloTran"][:2] == ["calcite-short", "calcite-vf"]
    assert "calcite-short" in listing["engines"]["CrunchFlow"]
    assert "microbial" not in listing["engines"]["CrunchFlow"]
    assert sorted(listing["engines"]) == ["CrunchFlow", "PFloTran"]


def test_database_is_read_from_the_input_file(tmp_path):
    """Database lines are parsed from the input; database_sweep is not mistaken for one."""
    directory = write_inputs(tmp_path)
    assert referenced_files(directory / "case.in", "CrunchFlow") == ("x.dbs",)
    pflotran = directory / "p.in"
    pflotran.write_text("CHEMISTRY\n  DATABASE ./other.dat\n  # DATABASE ignored.dat\nEND\n")
    assert referenced_files(pflotran, "PFloTran") == ("other.dat",)


def test_missing_database_is_reported_and_blocks_staging(tmp_path):
    """A referenced database that is absent makes the case unavailable."""
    directory = write_inputs(tmp_path)
    (directory / "x.dbs").unlink()
    assert list_cases(tmp_path, {"case": CASE})["missing_files"] == {
        "case": {"CrunchFlow": ["x.dbs"]}}
    with pytest.raises(FileNotFoundError, match="x.dbs"):
        stage_case(tmp_path, CASE, CASE.inputs[0], tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_stage_case_copies_input_and_databases(tmp_path):
    """Staging copies the input plus what it references and returns native arguments."""
    write_inputs(tmp_path)
    setup = stage_case(
        tmp_path,
        CASE,
        CASE.inputs[0],
        tmp_path / "runs",
        SetupRequest(pressure=2e5)
    )
    assert sorted(path.name for path in (tmp_path / "runs").iterdir()) == ["case.in", "x.dbs"]
    assert setup["engine"] == "CrunchFlow" and setup["input_file"] == "case.in"
    assert setup["pressure"] == 2e5 and setup["density"] == 997.16


def test_reference_escaping_the_input_directory_is_rejected(tmp_path):
    """An input cannot make staging read outside its own directory."""
    write_inputs(tmp_path, in_text="database ../../../etc/passwd\n")
    with pytest.raises(ValueError, match="relative to the input directory"):
        stage_case(tmp_path, CASE, CASE.inputs[0], tmp_path / "runs")


def test_staged_chemistry_is_patched_and_benchmark_untouched(repository_root, tmp_path):
    """Chemistry changes land in the staged copy only, in each engine's own units."""
    spec = get_case("calcite-short")
    benchmark = (repository_root / "benchmarks/batch_chem/calcite-short-pflotran.in").read_text()
    request = SetupRequest.model_validate(
        {"chemistry": {"aqueous": {"Ca++": 1e-3}, "rates": {"Calcite": 1e-8}}})
    for engine_input in spec.inputs:
        stage_case(
            repository_root,
            spec,
            engine_input,
            tmp_path / engine_input.engine,
            request
        )
    pflotran = (tmp_path / "PFloTran/calcite-short-pflotran.in").read_text()
    crunchflow = (tmp_path / "CrunchFlow/calcite-short-crunch.in").read_text()
    # PFLOTRAN reads mol/L: 1e-3 mol/kg * 0.99716 kg/L; and mol/cm^2-sec
    assert "Ca++    0.00099716      T" in pflotran
    assert "RATE_CONSTANT 1e-12 mol/cm^2-sec" in pflotran
    # CrunchFlow reads mol/kg and log10 rates
    assert "Ca++             0.001\n" in crunchflow
    assert "-rate  -8\n" in crunchflow
    # There is no change in original .in file
    assert (repository_root / "benchmarks/batch_chem/calcite-short-pflotran.in").read_text() == (
        benchmark)


def test_chemistry_summary_agrees_across_engines(repository_root):
    """Both engines report the same calcite system in the same units."""
    spec = get_case("calcite-short")
    pflotran = get_chemistry(repository_root, spec, "PFloTran")
    crunchflow = get_chemistry(repository_root, spec, "CrunchFlow")
    assert (pflotran["case"], pflotran["engine"]) == ("calcite-short", "PFloTran")
    assert pflotran["minerals"] == crunchflow["minerals"] == {
        "Calcite": {"volume_fraction": 0.2, "surface_area": 100.0}}
    assert pflotran["rates"] == crunchflow["rates"] == {"Calcite": 1e-9}
    assert pflotran["aqueous"]["Ca++"]["value"] == pytest.approx(
        crunchflow["aqueous"]["Ca++"]["value"], rel=1e-4)
    assert pflotran["units"]["total"] == "mol/kg water"


@pytest.mark.parametrize("request_body", [
    {"input_file": "x"},    # Not in the field
    {"engine": "ONNX"}, # Not in the field
    {"density": True},
    {"density": "heavy"},
    {"density": float("nan")},
    {"density": float("inf")},
    {"density": 0},
    {"porosity": 1.5},
    {"saturation": 0},
    {"temperature": -273.15},
    {"pressure": -1},
    {"initial_condition": ""},
    {"initial_condition": "x" * 256},
    {"initial_condition": 1},
    {"dt": 5.0},    # Belongs to TimeConfigRequest
    {"max_steps": 2},
    {"chemistry": {"aqueous": {"Ca++": "1"}}},
    {"chemistry": {"aqueous": {"Ca++": float("nan")}}},
    {"chemistry": {"minerals": {"Calcite": {"volume_fraction": 2.0}}}},
    {"chemistry": {"minerals": {"Calcite": {"rate": 1.0}}}},
    {"chemistry": {"rates": {"Calcite": -9.0}}},   # Linear, not log10
    {"chemistry": {"species": {}}},
])
def test_invalid_requests_are_rejected(request_body):
    """Unknown, mistyped, non-finite and out-of-range fields never become a request."""
    with pytest.raises(ValidationError):
        SetupRequest.model_validate(request_body)


def test_request_defaults_fill_what_was_not_set():
    """Unset fields keep the request defaults, and the setup is JSON serializable."""
    request = SetupRequest.model_validate({"initial_condition": "INITIAL", "porosity": 1})
    setup = build_setup(CASE.inputs[0], request)
    assert setup == {
        "engine": "CrunchFlow",
        "input_file": "case.in",
        "initial_condition": "INITIAL",
        "density": 997.16,
        "porosity": 1,
        "temperature": 25.0,
        "pressure": 101325.0,
        "volume": 1.0,
        "saturation": 1.0
    }
    # Only what the client set is recorded as its request
    assert request.model_dump(exclude_unset=True) == {"initial_condition": "INITIAL", "porosity": 1}
    json.dumps(setup, allow_nan=False)
    assert build_setup(CASE.inputs[0]) == build_setup(CASE.inputs[0], SetupRequest())


def test_unknown_initial_condition_is_rejected():
    """Only conditions the input defines can be requested; the error lists them."""
    with pytest.raises(ValueError, match="valid: initial"):
        build_setup(CASE.inputs[0], SetupRequest(initial_condition="driver_ic"))


@pytest.mark.parametrize("time_config", [
    {"dt": 0}, {"dt": -1.0}, {"dt": "5"}, {"dt": True}, {"dt": float("nan")},
    {"dt": float("inf")}, {"max_steps": 0}, {"max_steps": 1.5}, {"max_steps": True},
    {"timestep": 5.0},
])
def test_invalid_time_configs_are_rejected(time_config):
    """Unknown, mistyped, non-finite and out-of-range time fields never become a request."""
    with pytest.raises(ValidationError):
        TimeConfigRequest.model_validate(time_config)


def test_time_config_overrides_only_what_was_set():
    """Omitted time fields are left to the session's current values."""
    assert TimeConfigRequest().overrides() == {}
    assert TimeConfigRequest(dt=2.5).overrides() == {"dt": 2.5}
    assert TimeConfigRequest.model_validate_json('{"max_steps": 3}').model_dump_json() == (
        '{"dt":null,"max_steps":3}')
