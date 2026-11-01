"""Benchmark cases served by the MCP server, defined in code instead of driver cfg files.

A case separates what to run (one native input per engine) from the physical
environment shared by every engine in a session. Every case starts from the same
defaults, which clients override per session. The batch_chem cfg files remain for
the original C driver and are not read here.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from .setup_request import Density, Porosity, Pressure, Saturation, Temperature, Volume

# Canonical engine names accepted by the native engine process, keyed by lowercase.
ENGINES = {"pflotran": "PFloTran", "crunchflow": "CrunchFlow", "onnx": "ONNX"}


class PhysicalCondition(BaseModel):
    """Physical state applied to every engine of a session; defaults suit a batch benchmark.

    Attributes:
        density: Water density in kg/m^3.
        porosity: Porosity, dimensionless.
        temperature: Temperature in degrees Celsius.
        pressure: Aqueous pressure in Pa.
        volume: Material volume in m^3.
        saturation: Water saturation, dimensionless.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    density: Density = 997.16
    porosity: Porosity = 0.5
    temperature: Temperature = 25.0
    pressure: Pressure = 101325.0
    volume: Volume = 1.0
    saturation: Saturation = 1.0


@dataclass(frozen=True)
class EngineInput:
    """One engine's native input for a case.

    Attributes:
        engine: Canonical engine name, a value of ENGINES.
        input_file: Input path relative to benchmarks/batch_chem.
        conditions: Initial conditions the input defines; the first is the default.
    """

    engine: str
    input_file: str
    conditions: tuple[str, ...] = ("initial",)


@dataclass(frozen=True)
class CaseSpec:
    """A chemistry system, its engine inputs, and the defaults a session starts from.

    Attributes:
        name: Identifier shown by list_cases.
        inputs: Engine inputs, in the order a session runs them.
        description: What the system models, for the agent choosing a case.
        physical: Physical state shared by the engines.
        dt: Seconds per reaction step.
        max_steps: Reaction steps per react call.
    """

    name: str
    inputs: tuple[EngineInput, ...]
    description: str = ""
    physical: PhysicalCondition = field(default_factory=PhysicalCondition)
    dt: float = 86400.0
    max_steps: int = 50

    @property
    def engines(self) -> list[str]:
        """Engine names in run order."""
        return [engine_input.engine for engine_input in self.inputs]

    def select(self, engines: Sequence[str] | None = None) -> tuple[EngineInput, ...]:
        """Return the inputs for the requested engines, or all of them when omitted.

        Raises:
            ValueError: An engine is repeated, unknown, or not provided by this case.
        """
        if engines is None:
            return self.inputs
        names = [ENGINES.get(engine.lower(), engine) for engine in engines]
        if not names or len(set(names)) != len(names):
            raise ValueError("engines must name each engine at most once")
        by_engine = {engine_input.engine: engine_input for engine_input in self.inputs}
        unknown = [name for name in names if name not in by_engine]
        if unknown:
            raise ValueError(
                f"Case {self.name!r} has no {', '.join(unknown)} input; "
                f"available: {', '.join(self.engines)}"
            )
        return tuple(by_engine[name] for name in names)


def _pair(stem: str) -> tuple[EngineInput, ...]:
    """Inputs for a system benchmarked on both engines as <stem>-pflotran/-crunch.in."""
    return (
        EngineInput("PFloTran", f"{stem}-pflotran.in"),
        EngineInput("CrunchFlow", f"{stem}-crunch.in"),
    )


# Inputs come from the benchmarks' pflotran (pc) and crunch (cc) driver cfgs; every case
# uses the shared PhysicalCondition, dt and max_steps defaults.
CASES = {
    case.name: case
    for case in (
        CaseSpec(
            "calcite-short",
            _pair("calcite-short"),
            "Kinetic calcite dissolution in dilute water over seconds.",
        ),
        CaseSpec(
            "calcite-vf",
            _pair("calcite-vf"),
            "Calcite dissolution in acidic water over years, tracking mineral volume fraction.",
        ),
        CaseSpec(
            "calcite-vf-sa",
            (EngineInput("PFloTran", "calcite-vf-sa-pflotran.in"),),
            "calcite-vf with the reactive surface area updated from the volume fraction.",
        ),
        CaseSpec(
            "calcite-co2",
            (EngineInput("CrunchFlow", "calcite-co2-crunch.in"),),
            "Calcite precipitation from water equilibrated with CO2(g), charge balanced on H+.",
        ),
        CaseSpec(
            "ch4-o2",
            (EngineInput("CrunchFlow", "ch4-o2-crunch.in"),),
            "Aqueous kinetic CH4 oxidation by O2, both equilibrated with their gases.",
        ),
        CaseSpec(
            "general-reaction",
            _pair("general-reaction"),
            "First-order aqueous kinetic reaction A(aq) -> B(aq).",
        ),
        CaseSpec(
            "ion-exchange-valocchi",
            _pair("ion-exchange-valocchi"),
            "Na-Ca-Mg cation exchange on a fixed exchange capacity.",
        ),
        CaseSpec(
            "isotherms",
            _pair("isotherms"),
            "Linear, Langmuir and Freundlich sorption isotherms.",
        ),
        CaseSpec(
            "surface-complexation-2",
            _pair("surface-complexation-2"),
            "Zn sorption by equilibrium surface complexation on weak and strong sites.",
        ),
        CaseSpec(
            "microbial",
            (EngineInput("PFloTran", "ABCD_microbial.in"),),
            "Monod microbial reaction A + 2B -> 1.5C with product inhibition and biomass decay.",
        ),
    )
}


def get_case(name: str) -> CaseSpec:
    """Look up a case by name.

    Raises:
        ValueError: The name is not a served case.
    """
    try:
        return CASES[name]
    except KeyError:
        raise ValueError(f"Unknown case {name!r}; call list_cases") from None
