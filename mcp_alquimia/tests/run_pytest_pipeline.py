"""Build the engine process executable and run pytest with logs and a JUnit report."""

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from mcp_alquimia.utils.paths import find_repository


def main():
    """Run explicit engine process compilation followed by the selected pytest cases.

    Reads paths and optional pytest arguments from the command line. All
    chemistry engines must already be installed. Each invocation creates its
    own report directory, retaining test workspaces even when tests fail.

    Returns:
        int: The build or pytest exit code; zero means all selected tests passed.

    Raises:
        SystemExit: Help is requested, arguments are invalid, or pytest is missing.
        OSError: Report directory creation or launching a command fails.
    """
    # This script stays at the tests root; project holds build_tools/ and tests/
    project = Path(__file__).resolve().parents[1]
    repository = find_repository(project)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=repository / "build/install")
    parser.add_argument("--engine-process", type=Path,
                        default=repository / "build/mcp_alquimia/bin/alquimia_engine_process")
    parser.add_argument("--cc", default=os.environ.get("MPICC", "mpicc"))
    parser.add_argument("--report-dir", type=Path,
                        default=repository / "build/mcp_alquimia/test_reports")
    parser.add_argument("--skip-build", action="store_true",
                        help="Use an existing engine process, or run tests that do not need it")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER,
                        help="Additional pytest arguments after -- (for example, -- -m native)")
    args = parser.parse_args()
    
    if importlib.util.find_spec("pytest") is None:
        parser.error("pytest is missing; install the test group with uv sync --group test")
    
    prefix, engine_process = args.prefix.resolve(), args.engine_process.resolve()
    args.report_dir.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="run-", dir=args.report_dir.resolve()))
    
    print(f"Test artifacts: {artifacts}", flush=True)
    if not args.skip_build:
        command = [sys.executable, str(project / "build_tools/build_alquimia_engine_process.py"),
                   "--prefix", str(prefix), "--output", str(engine_process), "--cc", args.cc]
        # Run the build
        # Redirect to the targeted file
        with (artifacts / "engine_process_build.log").open("w") as log:
            build = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        # Build fail
        if build.returncode:
            print((artifacts / "engine_process_build.log").read_text(), file=sys.stderr)
            return build.returncode
        
    pytest_args = args.pytest_args
    
    # Strip the '--' separator safely without causing IndexError on empty lists.
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    # The explicit test path loads tests/conftest.py before custom CLI parsing.
    command = [sys.executable, "-m", "pytest", "tests", "--alquimia-prefix", str(prefix),
               "--alquimia-engine-process", str(engine_process), f"--junitxml={artifacts / 'pytest.xml'}",
               f"--basetemp={artifacts / 'test_runs'}", *pytest_args]
    return subprocess.run(command, cwd=project).returncode


if __name__ == "__main__":
    raise SystemExit(main())
