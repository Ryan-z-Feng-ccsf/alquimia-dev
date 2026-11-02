"""Test validation and subprocess failures without an installed chemistry runtime."""

import sys
from pathlib import Path

import pytest

from mcp_alquimia.subprocess_client import AlquimiaEngineProcess, EngineProcessError


@pytest.mark.parametrize("options", [
    pytest.param({"max_steps": -1}, id="negative-step-count"),
    pytest.param({"dt": float("nan")}, id="nonfinite-timestep"),
    pytest.param({"timeout": 0}, id="zero-timeout"),
])
def test_invalid_parameters_do_not_create_run_directory(repository_root, tmp_path,
                                                       options, engine_case):
    """Reject invalid arguments before staging files or launching a process."""
    run_dir = tmp_path / "invalid"
    with pytest.raises(ValueError):
        engine_case.run(repository_root, tmp_path / "unused_engine_process", run_dir, **options)
    assert not run_dir.exists()


def test_existing_run_directory_is_preserved(repository_root, engine_case, staged_engine_case):
    """Reject an existing run directory without modifying its input files."""
    run_dir, _ = staged_engine_case
    before = {path.name: path.read_bytes() for path in run_dir.iterdir()}
    # The Python executable satisfies the file check but must never be launched.
    with pytest.raises(FileExistsError):
        engine_case.run(repository_root, sys.executable, run_dir)
    assert {path.name: path.read_bytes() for path in run_dir.iterdir()} == before


def test_missing_companion_asset_does_not_stage(tmp_path, engine_case):
    """Require the database/model before creating a partially staged run."""
    source = tmp_path / "repository" / engine_case.input_path
    source.parent.mkdir(parents=True)
    # An input whose referenced database or model was not copied alongside it
    source.write_text({"PFloTran": "DATABASE ./calcite.dat\n",
                       "CrunchFlow": "database calcite.dbs\n",
                       "ONNX": '{"model": "ex8_nn_batch1.onnx"}'}[engine_case.engine])
    run_dir = tmp_path / "run"
    with pytest.raises(FileNotFoundError):
        engine_case.prepare(tmp_path / "repository", run_dir)
    assert not run_dir.exists()


@pytest.fixture
def failing_engine_process_executable(tmp_path: Path, mode: str):
    """Create a controllable subprocess that acknowledges startup, then fails.

    Args:
        tmp_path (Path): Isolated directory for the fake executable and logs.
        mode (str): Parameter selecting a timeout or abrupt process exit.

    Returns:
        Path: Executable Python script implementing the selected failure.
    """
    # Define the path for our fake engine executable in the temporary directory
    executable = tmp_path / "failing_engine_process"
    # Write the mock script content
    executable.write_text(
        #Shebang: Tell the OS to run this text file using the current Python interpreter
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        # Extract the file descriptor (FD) passed by the parent process via command line args
        "fd = int(sys.argv[2])\n"
        "os.write(fd, b'{\"success\":true,\"result\":\"ready\"}\\n')\n"
        "sys.stdin.readline()\n"
        # Inject the requested failure mode (either hang indefinitely or crash abruptly)
        + ("time.sleep(30)\n" if mode == "timeout" else "os._exit(7)\n"))
    # Grant execute permissions (rwxr-xr-x) so the OS can invoke it directly like a real binary
    executable.chmod(0o755)
    return executable


@pytest.mark.parametrize("mode, message", [
    pytest.param("timeout", "timed out", id="response-timeout"),
    pytest.param("crash", "exited", id="abrupt-engine-process-exit"),
])
def test_timeout_and_crash_are_reaped(failing_engine_process_executable, tmp_path: Path, message):
    """Report stalled and crashed engine processes and reap their processes."""
    with AlquimiaEngineProcess(failing_engine_process_executable, tmp_path, timeout=5) as engine_process:
        engine_process.timeout = 0.1
        with pytest.raises(EngineProcessError, match=message):
            engine_process.request("get_results")
        assert engine_process.process.poll() is not None
