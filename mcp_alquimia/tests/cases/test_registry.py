"""Keep the code-defined cases in step with the benchmark inputs and driver cfgs."""

import configparser
import re

import pytest

from mcp_alquimia.cases.registry import CASES, ENGINES

# The driver cfg each registered engine input was copied from
CFG = {
    ("calcite-short", "PFloTran"): "calcite-short-pc-pflotran",
    ("calcite-short", "CrunchFlow"): "calcite-short-cc-crunch",
    ("calcite-vf", "PFloTran"): "calcite-vf-pc-pflotran",
    ("calcite-vf", "CrunchFlow"): "calcite-vf-cc-crunch",
    ("calcite-vf-sa", "PFloTran"): "calcite-vf-sa-pc-pflotran",
    ("calcite-co2", "CrunchFlow"): "calcite-co2-crunch-cc",
    ("ch4-o2", "CrunchFlow"): "ch4-o2-crunch-cc",
    ("general-reaction", "PFloTran"): "general-reaction-pc-pflotran",
    ("general-reaction", "CrunchFlow"): "general-reaction-cc-crunch",
    ("ion-exchange-valocchi", "PFloTran"): "ion-exchange-valocchi-pc-pflotran",
    ("ion-exchange-valocchi", "CrunchFlow"): "ion-exchange-valocchi-cc-crunch",
    ("isotherms", "PFloTran"): "isotherms-pc-pflotran",
    ("isotherms", "CrunchFlow"): "isotherms-cc-crunch",
    ("surface-complexation-2", "PFloTran"): "surface-complexation-2-pc-pflotran",
    ("surface-complexation-2", "CrunchFlow"): "surface-complexation-2-cc-crunch",
    ("microbial", "PFloTran"): "microbial-pflotran",
}


def registered_inputs():
    """Every (case, engine input) pair, for parametrization."""
    return [(spec, engine_input) for spec in CASES.values() for engine_input in spec.inputs]


def test_every_registered_input_has_a_source_cfg():
    """The parity table covers the registry exactly."""
    assert set(CFG) == {(spec.name, engine_input.engine) for spec, engine_input in registered_inputs()}


@pytest.mark.parametrize("spec, engine_input", registered_inputs(),
                         ids=lambda value: getattr(value, "name", None) or value.engine)
def test_registry_matches_its_driver_cfg(repository_root, spec, engine_input):
    """Engine and input file match the cfg they replace.

    Physical state and time stepping are not compared: every case uses the shared
    defaults, which setup's request and react's time_config override.
    """
    parser = configparser.ConfigParser(inline_comment_prefixes=("#",))
    parser.read(repository_root / "benchmarks/batch_chem" / f"{CFG[spec.name, engine_input.engine]}.cfg")
    assert ENGINES[parser["chemistry"]["engine"].strip().lower()] == engine_input.engine
    assert parser["chemistry"]["input_file"].strip() == engine_input.input_file


def test_cases_use_the_shared_defaults():
    """No case carries its own physical state or time stepping."""
    for spec in CASES.values():
        assert spec == type(spec)(spec.name, spec.inputs, spec.description), spec.name


@pytest.mark.parametrize("spec, engine_input", registered_inputs(),
                         ids=lambda value: getattr(value, "name", None) or value.engine)
def test_registered_conditions_are_defined_by_the_input(repository_root, spec, engine_input):
    """Each listed condition appears as a PFLOTRAN CONSTRAINT or CrunchFlow Condition."""
    text = (repository_root / "benchmarks/batch_chem" / engine_input.input_file).read_text()
    keyword = "CONSTRAINT" if engine_input.engine == "PFloTran" else "CONDITION"
    for condition in engine_input.conditions:
        assert re.search(rf"^\s*{keyword}\s+{re.escape(condition)}\s*$", text,
                         re.IGNORECASE | re.MULTILINE), condition
