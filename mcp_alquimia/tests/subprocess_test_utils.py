"""Shared benchmark cases and original-driver comparisons for subprocess tests."""

import ast
from collections.abc import Callable
import configparser
from dataclasses import dataclass
import functools
import subprocess

import pytest

from mcp_alquimia.case_registry import CaseSpec, EngineInput, get_case
from run_batch_chem_case import prepare_case, run_batch_chem_case
from mcp_alquimia.setup_request import SetupRequest

# ONNX models are not served by list_cases; this mirrors ex8-nn-batch1.cfg for tests.
ONNX_EX8 = CaseSpec(
    "ex8-nn-batch1",
    (EngineInput("ONNX", "../../models/ex8_nn/ex8_nn_batch1.json"),),
    dt=864000.0,
    max_steps=100)


@dataclass(frozen=True)
class EngineCase:
    """Describe a benchmark independently of its native or Python test layer.

    Attributes:
        name: Readable pytest case ID.
        case: Registered case name or test-defined case.
        engine: Engine of the case to run.
        config: Original-driver cfg stem under benchmarks/batch_chem.
        max_steps: Expected default number of reaction steps.
        input_path: Repository-relative input to stage without its companion asset.
        request: Settings the benchmark needs beyond the defaults.
    """

    name: str
    case: str | CaseSpec
    engine: str
    config: str
    max_steps: int
    input_path: str
    request: SetupRequest | None = None

    @property
    def spec(self):
        """The case definition, registered or test-defined."""
        return get_case(self.case) if isinstance(self.case, str) else self.case

    @property
    def prepare(self):
        """Stage inputs and return native setup arguments: (repository, run_dir)."""
        return functools.partial(
            prepare_case, case=self.case, engine=self.engine, request=self.request)

    @property
    def run(self):
        """Execute the benchmark: (repository, executable, run_dir, **options)."""
        return functools.partial(
            run_batch_chem_case, case=self.case, engine=self.engine, request=self.request)

    @property
    def config_name(self):
        """Original-driver config under benchmarks/batch_chem."""
        return f"{self.config}.cfg"


ENGINE_CASES = (
    EngineCase("pflotran-calcite", "calcite-short", "PFloTran", "calcite-short-pc-pflotran", 50,
               "benchmarks/batch_chem/calcite-short-pflotran.in"),
    EngineCase("crunchflow-calcite", "calcite-short", "CrunchFlow", "calcite-short-cc-crunch", 50,
               "benchmarks/batch_chem/calcite-short-crunch.in"),
    # ex8-nn-batch1.cfg runs at porosity 0.25
    EngineCase("onnx-ex8", ONNX_EX8, "ONNX", "ex8-nn-batch1", 100,
               "models/ex8_nn/ex8_nn_batch1.json", SetupRequest(porosity=0.25)),
)


def run_original_driver(engine_case, repository, executable, run_dir):
    """Run an independently staged batch_chem baseline and parse its literal output.

    Args:
        engine_case: EngineCase describing the benchmark.
        repository: Checkout supplying benchmark assets and configs.
        executable: Precompiled batch_chem executable.
        run_dir: New directory for staged inputs, output, and driver.log.

    Returns:
        dict: Original-driver history parsed without executing generated Python.

    Raises:
        subprocess.SubprocessError: The driver fails or exceeds 60 seconds.
        OSError: Staging, launching, or reading output fails.
    """
    setup = engine_case.prepare(repository, run_dir)
    # Config Parse (INI/.cfg)
    config = configparser.ConfigParser()
    config.read(repository / "benchmarks/batch_chem" / engine_case.config_name)
    config["chemistry"]["input_file"] = setup["input_file"]
    # The driver must see the same physical state and time stepping as the engine process
    config["simulation"]["timestep"] = repr(engine_case.spec.dt)
    config["simulation"]["max_steps"] = str(engine_case.spec.max_steps)
    for section, keys in (("state", ("density", "porosity", "temperature", "pressure")),
                          ("material", ("volume", "saturation"))):
        for key in keys:
            config[section][key] = repr(setup[key])
    config["output"] = {"type": "python", "filename": "batch_chem_output.py"}
    # Write the config file
    with (run_dir / "batch_chem.cfg").open("w") as stream:
        config.write(stream)
    # Run the original driver
    with (run_dir / "batch_chem_driver.log").open("w") as log:
        subprocess.run([str(executable), "batch_chem.cfg"],
                       cwd=run_dir,
                       stdout=log,
                       stderr=subprocess.STDOUT,
                       timeout=60,
                       check=True)
    # Parse literal output without executing generate Python
    return ast.literal_eval(ast.parse((run_dir / "batch_chem_output.py").read_text()).body[0].value)


def assert_history_matches_driver(result, baseline):
    """Compare native histories and available final pH with driver precision.

    Args:
        result: Subprocess result containing history and species metadata.
        baseline: Parsed original-driver output, rounded to seven significant digits.

    Raises:
        AssertionError: Times, species histories, or final pH differ.
    """
    history = result["history"]
    assert [row["time"] for row in history] == baseline["time"]
    for field, names, key in [
        ("total_mobile", result["metadata"]["primary_species"], "total_mobile"),
        ("total_immobile", range(len(history[0]["total_immobile"])), "total_immobile"),
        ("mineral_volume_fraction", result["metadata"]["minerals"], "mineral_volume_fractions"),
    ]:
        for index, name in enumerate(names):
            assert [row[field][index] for row in history] == pytest.approx(
                baseline[f"{key}[{name}]"], rel=1e-6, abs=1e-14), (field, name)
    if history[-1]["pH"] is not None:
        # The original driver repeats final pH in every history row.
        assert history[-1]["pH"] == pytest.approx(baseline["pH"][-1], rel=1e-6, abs=1e-14)
