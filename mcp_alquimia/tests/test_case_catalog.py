"""Verify case discovery, database detection, request overrides, and staging offline."""

import json

import pytest

from pydantic import ValidationError

from mcp_alquimia.case_catalog import (
    build_setup, describe_case, discover_cases, get_case, referenced_files, stage_case)
from mcp_alquimia.setup_request import SetupRequest
from mcp_alquimia.time_config_request import TimeConfigRequest

# Pytest fixture
def write_case(repository, cfg_extra="", in_database="database  x.dbs\ndatabase_sweep false\n"):
    """Create a minimal repository with one CrunchFlow-style case."""
    directory = repository / "benchmarks/batch_chem"
    directory.mkdir(parents=True)
    (directory / "case.in").write_text(in_database)
    (directory / "x.dbs").write_text("db")
    (directory / "case.cfg").write_text(
        "[simulation]\ntimestep = 0.5 days\nmax_steps = 4;\n"
        "[state]\ndensity = 1000\nporosity = 0.5\ntemperature = 25\npressure = 1e5\n"
        "[material]\nvolume = 1\nsaturation = 1\n"
        "[chemistry]\nengine = crunchflow\ninput_file = case.in\ninitial_condition = initial\n"
        + cfg_extra)
    return directory


def test_discovers_every_runnable_cfg_in_the_repository(repository_root):
    """All benchmark cfgs are listed, each with its input and database files present."""
    cases = discover_cases(repository_root)
    assert len(cases) >= 35
    assert not [name for name, spec in cases.items() if spec.missing_files]
    undefined = {name for name, spec in cases.items() if spec.undefined_condition}
    assert "calcite-short-ac-pflotran" in undefined and "calcite-short-pc-pflotran" not in undefined
    assert cases["calcite-short-pc-pflotran"].database_files == ("calcite.dat",)
    assert cases["calcite-short-cc-crunch"].database_files == ("calcite.dbs",)
    assert cases["general-reaction-ac-crunch"].database_files == ("general-reaction.dbs", "general-reaction.dbsx")
    assert cases["ex8-nn-batch1"].database_files == ("ex8_nn_batch1.onnx",)
    assert cases["calcite-co2-crunch-cc"].default_dt == 0.1 * 86400


def test_only_real_case_names_resolve(repository_root):
    """Cases are found by their cfg stem; the retired short names are unknown."""
    assert get_case(repository_root, "calcite-short-pc-pflotran").name == "calcite-short-pc-pflotran"
    assert "aliases" not in describe_case(get_case(repository_root, "ex8-nn-batch1"))
    for name in ("calcite-pflotran", "calcite-crunchflow", "ex8-nn-batch1-onnx", "nope"):
        with pytest.raises(ValueError, match="Unknown case"):
            get_case(repository_root, name)
    for name in ("../x", "a/b", "..", ""):
        with pytest.raises(ValueError, match="Invalid case name"):
            get_case(repository_root, name)


def test_database_is_read_from_the_input_file(tmp_path):
    """Database lines are parsed from the input; database_sweep is not mistaken for one."""
    directory = write_case(tmp_path)
    assert referenced_files(directory / "case.in", "CrunchFlow") == ("x.dbs",)
    pflotran = directory / "p.in"
    pflotran.write_text("CHEMISTRY\n  DATABASE ./other.dat\n  # DATABASE ignored.dat\nEND\n")
    assert referenced_files(pflotran, "PFloTran") == ("other.dat",)


def test_malformed_cfg_is_skipped_not_fatal(tmp_path):
    """One bad cfg must not hide the valid cases."""
    directory = write_case(tmp_path)
    (directory / "bad.cfg").write_text("[simulation]\n")
    assert list(discover_cases(tmp_path)) == ["case"]


def test_missing_database_is_reported_and_blocks_staging(tmp_path):
    """A referenced database that is absent makes the case unavailable."""
    directory = write_case(tmp_path)
    # Remove the database file
    (directory / "x.dbs").unlink()
    spec = get_case(tmp_path, "case")
    assert describe_case(spec)["available"] is False
    assert describe_case(spec)["missing_files"] == ["x.dbs"]
    with pytest.raises(FileNotFoundError, match="x.dbs"):
        stage_case(spec, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_stage_case_copies_input_and_databases(tmp_path):
    """Staging copies the input plus what it references and returns native arguments."""
    write_case(tmp_path)
    setup = stage_case(get_case(tmp_path, "case"), tmp_path / "runs", SetupRequest(pressure=2e5))
    assert sorted(path.name for path in (tmp_path / "runs").iterdir()) == ["case.in", "x.dbs"]
    assert setup["engine"] == "CrunchFlow" and setup["input_file"] == "case.in"
    assert setup["pressure"] == 2e5 and setup["density"] == 1000.0


def test_reference_escaping_the_input_directory_is_rejected(tmp_path):
    """An input cannot make staging read outside its own directory."""
    write_case(tmp_path, in_database="database ../../../etc/passwd\n")
    with pytest.raises(ValueError, match="relative to the input directory"):
        stage_case(get_case(tmp_path, "case"), tmp_path / "runs")


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
])
def test_invalid_requests_are_rejected(request_body):
    """Unknown, mistyped, non-finite and out-of-range fields never become a request."""
    with pytest.raises(ValidationError):
        SetupRequest.model_validate(request_body)


def test_request_overrides_only_what_was_set(tmp_path):
    """Unset fields keep the cfg defaults, and the merged setup is JSON serializable."""
    write_case(tmp_path)
    spec = get_case(tmp_path, "case")
    request = SetupRequest.model_validate({"initial_condition": "other", "porosity": 1})
    setup = build_setup(spec, request)
    assert setup["initial_condition"] == "other" and setup["porosity"] == 1
    assert setup["density"] == 1000.0 and setup["engine"] == "CrunchFlow"
    assert request.overrides() == {"initial_condition": "other", "porosity": 1}
    json.dumps(setup, allow_nan=False)
    assert build_setup(spec) == build_setup(spec, SetupRequest())


def test_undefined_default_condition_needs_a_request(tmp_path):
    """A cfg that only names its condition stays listed; setup asks for a real one."""
    write_case(tmp_path, cfg_extra="[initial]\n")
    spec = get_case(tmp_path, "case")
    listed = describe_case(spec)
    assert listed["available"] and listed["requires_initial_condition"]
    assert "request.initial_condition" in listed["notes"]
    with pytest.raises(ValueError, match="Pass request.initial_condition"):
        stage_case(spec, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()
    stage_case(spec, tmp_path / "runs", SetupRequest(initial_condition="initial"))
    assert (tmp_path / "runs" / "case.in").is_file()


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
