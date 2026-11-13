"""Shared pytest paths and fixtures; testing never rebuilds chemistry engines."""

import os
from pathlib import Path

import pytest

from mcp_alquimia.utils.paths import find_repository
from subprocess_test_utils import ENGINE_CASES


def pytest_addoption(parser: pytest.Parser):
    """Register paths to the prebuilt runtime and engine process.

    Args:
        parser (pytest.Parser): Pytest's command-line option parser.
    """
    group = parser.getgroup("alquimia")
    group.addoption("--alquimia-prefix", help="Installed Alquimia prefix (default: build/install)")
    group.addoption("--alquimia-engine-process", help="Precompiled Alquimia subprocess engine process path")


@pytest.fixture(scope="session")
def repository_root():
    """Return the checkout containing the existing chemistry benchmarks.

    Returns:
        Path: Absolute repository root.
    """
    return find_repository(Path(__file__))


@pytest.fixture(scope="session")
def project_root():
    """Return the mcp_alquimia project holding containers/, build_tools/ and tests/.

    Returns:
        Path: Absolute project directory.
    """
    return Path(__file__).resolve().parents[1]


def require_executable(path: Path, instruction: str):
    """Fail explicitly when a required native executable is unavailable.

    Args:
        path (Path): Candidate executable path.
        instruction (str): Action explaining how to provide the executable.

    Returns:
        Path: Validated absolute executable path.
    """
    path = path.resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        pytest.fail(f"Required executable is missing or not executable: {path}. {instruction}",
                    pytrace=False)
    return path


@pytest.fixture(scope="session")
def engine_process_executable(pytestconfig, repository_root):
    """Locate the precompiled engine process, failing instead of skipping native tests.

    Args:
        pytestconfig (pytest.Config): Session options with an optional engine process path.
        repository_root (Path): Checkout containing the default engine process build.

    Returns:
        Path: Validated native engine process executable.
    """
    configured = pytestconfig.getoption("--alquimia-engine-process")
    path = Path(configured) if configured else (
        repository_root / "build/mcp_alquimia/bin/alquimia_engine_process")
    return require_executable(path, "Run build_tools/run_pytest_pipeline.py to build the engine process.")


@pytest.fixture(scope="session")
def batch_chem_executable(pytestconfig: pytest.Config, repository_root: Path):
    """Locate the original driver used only for baseline comparison.

    Args:
        pytestconfig (pytest.Config): Session options with an optional install prefix.
        repository_root (Path): Checkout containing the default installation.

    Returns:
        Path: Validated installed batch_chem executable.
    """
    configured = pytestconfig.getoption("--alquimia-prefix")
    prefix = Path(configured) if configured else repository_root / "build/install"
    return require_executable(prefix / "bin/batch_chem",
                              "Select an existing installation with --alquimia-prefix.")


@pytest.fixture(params=ENGINE_CASES, ids=lambda case: case.name)
def engine_case(request):
    """Select each supported benchmark for shared native and client tests."""
    return request.param


@pytest.fixture
def staged_engine_case(engine_case, repository_root: Path, tmp_path: Path):
    """Stage an independent copy of the selected benchmark.

    Args:
        engine_case (EngineCase): Benchmark selected by parametrization.
        repository_root (Path): Checkout supplying benchmark assets.
        tmp_path (Path): Isolated, pytest-managed directory for this test.

    Returns:
        tuple[Path, dict]: Run directory and native setup arguments.
    """
    run_dir = tmp_path / "engine_case"
    return run_dir, engine_case.prepare(repository_root, run_dir)
