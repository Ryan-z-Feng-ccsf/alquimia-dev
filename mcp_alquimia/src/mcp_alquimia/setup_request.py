"""Typed, validated overrides an agent may send with a setup request."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Ranges mirror the native checks in engine_process/alquimia_engine_process_helpers.c,
# so a bad request is rejected before any process starts.
Density = Annotated[float, Field(gt=0, allow_inf_nan=False, description="Water density in kg/m^3.")]
Porosity = Annotated[
    float, Field(gt=0, le=1, allow_inf_nan=False, description="Porosity, dimensionless, in (0, 1].")]
Temperature = Annotated[
    float, Field(gt=-273.15, allow_inf_nan=False, description="Temperature in degrees Celsius.")]
Pressure = Annotated[float, Field(gt=0, allow_inf_nan=False, description="Aqueous pressure in Pa.")]
Volume = Annotated[float, Field(gt=0, allow_inf_nan=False, description="Material volume in m^3.")]
Saturation = Annotated[
    float, Field(gt=0, le=1, allow_inf_nan=False, description="Water saturation, dimensionless, in (0, 1].")]
InitialCondition = Annotated[
    str,
    Field(
        min_length=1,
        max_length=255,
        description="Name of an initial condition defined in the case's input file."),
]

Engine = Annotated[
    Literal["PFloTran", "CrunchFlow"],
    Field(description="Chemistry engine, spelled exactly as list_cases shows it."),
]
Name = Annotated[str, Field(min_length=1, max_length=64)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class MineralChange(BaseModel):
    """New values for one mineral of the initial condition; omitted fields are kept."""

    model_config = ConfigDict(extra="forbid", strict=True)

    volume_fraction: Annotated[float, Field(
        ge=0, le=1, allow_inf_nan=False,
        description="Mineral volume fraction, m^3 mineral/m^3 bulk.")] | None = None
    surface_area: Annotated[float, Field(
        ge=0, allow_inf_nan=False,
        description="Bulk reactive surface area, m^2 mineral/m^3 bulk.")] | None = None


class ChemistryRequest(BaseModel):
    """Changes to the initial condition and mineral rates, written into the staged input.

    Values use engine-independent units and are converted to each engine's input
    units, so one request means the same on every engine of a session. Names and
    current values come from get_chemistry; a constraint's type is never changed.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    aqueous: dict[Name, Finite] = Field(
        default_factory=dict,
        description="Species to new constraint value, in the units get_chemistry reports for "
                    "its constraint: mol/kg water (total, free), pH, or bar (gas).")
    minerals: dict[Name, MineralChange] = Field(default_factory=dict)
    rates: dict[Name, Annotated[float, Field(gt=0, allow_inf_nan=False)]] = Field(
        default_factory=dict,
        description="Mineral to kinetic rate constant in mol/m^2/s (not log10).")


class SetupRequest(BaseModel):
    """Session settings; omitted fields keep the defaults shown in this schema.

    The physical defaults suit a batch benchmark and are the same for every case.
    The input file and engine are deliberately not fields, so a request can never
    point the engine at another file. Time stepping (dt, max_steps) belongs to
    TimeConfigRequest, sent with react. Unknown fields, booleans or strings in place
    of numbers, and non-finite or out-of-range values are rejected.
    """

    # Reject unknown fields and disable automatic type conversion
    model_config = ConfigDict(extra="forbid", strict=True)

    density: Density = 997.16
    porosity: Porosity = 0.5
    temperature: Temperature = 25.0
    pressure: Pressure = 101325.0
    volume: Volume = 1.0
    saturation: Saturation = 1.0
    initial_condition: InitialCondition | None = None
    chemistry: ChemistryRequest | None = None

    def physical(self) -> dict[str, float]:
        """The six physical settings sent to the engine, defaults included."""
        return self.model_dump(exclude={"initial_condition", "chemistry"})
