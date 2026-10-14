"""Typed, validated overrides an agent may send with a setup request."""

from typing import Annotated

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
Timestep = Annotated[
    float, Field(gt=0, allow_inf_nan=False, description="Default seconds per react call.")]
MaxSteps = Annotated[
    int, Field(ge=1, description="Maximum number of react calls allowed in the session.")]

# Session controls handled by the server; every other field is a native setup argument.
CONTROL_FIELDS = {"dt", "max_steps"}


class SetupRequest(BaseModel):
    """Overrides for a case's benchmark defaults; omitted fields keep the cfg values.

    The input file and engine are deliberately not fields, so a request can never
    point the engine at another file. Unknown fields, booleans or strings in place
    of numbers, and non-finite or out-of-range values are rejected.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    density: Density | None = None
    porosity: Porosity | None = None
    temperature: Temperature | None = None
    pressure: Pressure | None = None
    volume: Volume | None = None
    saturation: Saturation | None = None
    initial_condition: InitialCondition | None = None
    dt: Timestep | None = None
    max_steps: MaxSteps | None = None

    def overrides(self) -> dict[str, float | str]:
        """Return only the native setup fields the caller set."""
        return self.model_dump(exclude_none=True, exclude=CONTROL_FIELDS)

    def controls(self) -> dict[str, float | int]:
        """Return only the session controls (dt, max_steps) the caller set."""
        return self.model_dump(exclude_none=True, include=CONTROL_FIELDS)
