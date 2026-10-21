"""Validate all native engines through shared parity and lifecycle checks."""

import json
import shutil

import pytest

from mcp_alquimia.run_batch_chem_case import prepare_case
from mcp_alquimia.subprocess_client import AlquimiaEngineProcess, EngineCommandError
from subprocess_test_utils import assert_history_matches_driver, run_original_driver


pytestmark = pytest.mark.native


def test_matches_original_driver(
    repository_root, 
    engine_process_executable,
    batch_chem_executable,
    tmp_path,
    engine_case
    ):
    """Compare each benchmark's history, chemistry invariants, and saved results."""
    run_dir = tmp_path / "subprocess"
    # Run the engine subprocess
    result = engine_case.run(repository_root, engine_process_executable, run_dir)
    baseline = run_original_driver(engine_case, 
                                   repository_root, 
                                   batch_chem_executable,
                                   tmp_path / "batch_chem")
    history = result["history"]
    assert len(history) == engine_case.max_steps + 1
    assert result["final"] == history[-1]
    assert result["metadata"]["units"]["total_mobile"] == "mol/L water"
    # Compare original driver with the subprocess results
    assert_history_matches_driver(result, baseline)

    if engine_case.name == "onnx-ex8":
        assert result["metadata"]["has_pH"] is False
        assert result["metadata"]["minerals"] == []
        assert all(row["pH"] is None for row in history)
        assert history[0]["total_mobile"] == pytest.approx([1e-5, 1e-7])
        assert history[0]["total_immobile"] == [0, 0]
        assert history[-1]["total_immobile"] != history[0]["total_immobile"]
        # Native immobile totals use mol/m^3 bulk, mobile totals mol/L water.
        for row in history:
            for index, initial in enumerate(history[0]["total_mobile"]):
                total = row["total_mobile"][index] + row["total_immobile"][index] / 250
                assert total == pytest.approx(initial, rel=1e-10, abs=1e-16)
    else:
        assert result["metadata"]["has_pH"] is True
        if engine_case.name == "pflotran-calcite":
            assert history[-1]["total_immobile"] == []
            assert history[-1]["total_mobile"][0] < 0  # Valid H+ total.
            assert history[0]["pH"] != history[-1]["pH"]
    assert json.loads((run_dir / "simulation_results.json").read_text()) == result


def test_persistent_reactions_and_initialization_only(
    repository_root,
    engine_process_executable,
    tmp_path,
    engine_case,
    staged_engine_case
    ):
    """Retain native state, honor custom timesteps, and support zero-step runs."""
    initial_only = engine_case.run(repository_root,
                                   engine_process_executable,
                                   tmp_path / "initial",
                                   max_steps=0)
    assert initial_only["history"] == [initial_only["final"]]
    run_dir, setup = staged_engine_case
    with AlquimiaEngineProcess(engine_process_executable, run_dir) as process:
        process.request("setup", **setup)
        initial = process.request("initialize")
        assert initial == initial_only["final"]
        first = process.request("react", dt=2, max_steps=1)
        final = process.request("react", dt=3, max_steps=1)
        assert (initial["time"], first["time"], final["time"], final["completed_steps"]) == (0, 2, 5, 2)
        assert final == process.request("get_results")
        if engine_case.name == "pflotran-calcite":
            assert final["total_mobile"][2] > initial["total_mobile"][2]
    assert process.process.returncode == 0


def test_looped_react_matches_single_steps(engine_process_executable, staged_engine_case):
    """One request with max_steps=n reproduces n single-step requests exactly."""
    run_dir, setup = staged_engine_case
    # Each engine process needs its own log, so the second run uses a copied directory
    single_step_dir = run_dir.with_name("single_steps")
    shutil.copytree(run_dir, single_step_dir)
    states = []
    for directory, requests in ((run_dir, [{"dt": 2.5, "max_steps": 3}]),
                                (single_step_dir, [{"dt": 2.5, "max_steps": 1}] * 3)):
        with AlquimiaEngineProcess(engine_process_executable, directory) as process:
            process.request("setup", **setup)
            process.request("initialize")
            # directory = single_step_dir, requests * 3
            for parameters in requests:
                state = process.request("react", **parameters)
            states.append(state)
    assert states[0] == states[1]
    assert (states[0]["time"], states[0]["completed_steps"]) == (7.5, 3)


def test_unknown_condition_shuts_down(engine_process_executable, staged_engine_case):
    """Preserve initialization errors and shut down every engine cleanly."""
    run_dir, setup = staged_engine_case
    setup["initial_condition"] = "missing_condition"
    with AlquimiaEngineProcess(engine_process_executable, run_dir) as process:
        process.request("setup", **setup)
        with pytest.raises(EngineCommandError, match="missing_condition") as caught:
            process.request("initialize")
        assert str(run_dir / "engine_output.log") in str(caught.value)
        assert process.process.poll() == 0


@pytest.mark.parametrize("operation, arguments, message", [
    pytest.param("setup", {"engine": "missing_engine"}, "missing_engine", id="unknown-engine"),
    pytest.param("react", {"dt": 5, "max_steps": 1}, "Call setup first", id="react-before-setup"),
])
def test_protocol_errors_shutdown(
    repository_root,
    engine_process_executable,
    tmp_path,
    operation,
    arguments,
    message
    ):
    """Reject invalid engines and command order before native initialization."""
    run_dir = tmp_path / "protocol"
    if operation == "setup":
        arguments = {**prepare_case(repository_root, run_dir, "calcite-short-pc-pflotran"), **arguments}
    else:
        run_dir.mkdir()
    with AlquimiaEngineProcess(engine_process_executable, run_dir) as process:
        with pytest.raises(EngineCommandError, match=message) as caught:
            process.request(operation, **arguments)
        assert str(run_dir / "engine_output.log") in str(caught.value)
        assert process.process.poll() == 0


@pytest.mark.parametrize("parameters, message", [
    pytest.param({"dt": -5, "max_steps": 1}, "dt must be", id="negative-dt"),
    pytest.param({"dt": "5", "max_steps": 1}, "dt must be", id="string-dt"),
    pytest.param({"max_steps": 1}, "dt must be", id="missing-dt"),
    pytest.param({"timestep": 5, "max_steps": 1}, "dt must be", id="retired-timestep"),
    pytest.param({"dt": 1e308, "max_steps": 2}, "too large", id="overflowing-time"),
    pytest.param({"dt": 5}, "max_steps must be", id="missing-max-steps"),
    pytest.param({"dt": 5, "max_steps": 0}, "max_steps must be", id="zero-max-steps"),
    pytest.param({"dt": 5, "max_steps": 1.5}, "max_steps must be", id="fractional-max-steps"),
    pytest.param({"dt": 5, "max_steps": "2"}, "max_steps must be", id="string-max-steps"),
    pytest.param({"dt": 5, "max_steps": True}, "max_steps must be", id="boolean-max-steps"),
    pytest.param({"dt": 5, "max_steps": 10001}, "max_steps must be", id="too-many-steps"),
])
def test_invalid_react_parameters_shut_down(
    engine_process_executable,
    staged_engine_case,
    parameters,
    message
    ):
    """Reject invalid loop parameters before any step and retain the native diagnosis."""
    run_dir, setup = staged_engine_case
    with AlquimiaEngineProcess(engine_process_executable, run_dir) as process:
        process.request("setup", **setup)
        process.request("initialize")
        with pytest.raises(EngineCommandError, match=message) as caught:
            process.request("react", **parameters)
        assert str(run_dir / "engine_output.log") in str(caught.value)
        assert process.process.poll() == 0


def test_malformed_protocol_is_reported(engine_process_executable, tmp_path):
    """Report malformed JSON as a structured error and permit close."""
    with AlquimiaEngineProcess(engine_process_executable, tmp_path) as process:
        process.process.stdin.write(b"not-json\n")
        process.process.stdin.flush()
        with pytest.raises(EngineCommandError, match="Expected a JSON object"):
            process._receive()
    assert process.process.returncode == 0


def test_onnx_inference_failure_is_reported(repository_root, engine_process_executable, tmp_path):
    """ONNX inference errors remain fatal despite having no convergence flag."""
    run_dir = tmp_path / "runtime_failure"
    setup = prepare_case(repository_root, run_dir, "ex8-nn-batch1")
    source = repository_root / "unit_tests/onnx_test_cases/deterministic"
    config = json.loads((source / "runtime_failure.json").read_text())
    config["conditions"] = {"initial": {"runtime_input_0": 1, "runtime_input_1": 2}}
    (run_dir / setup["input_file"]).write_text(json.dumps(config))
    shutil.copy2(source / "runtime_failure.onnx", run_dir / "runtime_failure.onnx")
    with AlquimiaEngineProcess(engine_process_executable, run_dir) as process:
        process.request("setup", **setup)
        process.request("initialize")
        with pytest.raises(EngineCommandError, match="react failed") as caught:
            process.request("react", dt=1, max_steps=1)
        assert "did not converge" not in str(caught.value)
        assert process.process.poll() == 0
