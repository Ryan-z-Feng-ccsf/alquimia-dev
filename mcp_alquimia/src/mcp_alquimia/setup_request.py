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

    def overrides(self) -> dict[str, float | str]:
        """Return only the native setup fields the caller set."""
        return self.model_dump(exclude_none=True)
