"""Typed, validated time-stepping overrides an agent may send with a react request."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from .coercion import lenient_float, lenient_int

# Mirrors kMaxReactSteps in engine_process/alquimia_engine_process_helpers.c.
MAX_REACT_STEPS = 10000

Timestep = Annotated[lenient_float(gt=0), Field(description="Seconds per reaction step.")]
MaxSteps = Annotated[
    lenient_int(ge=1, le=MAX_REACT_STEPS),
    Field(description="Number of reaction steps one react call runs in the engine."),
]


class TimeConfigRequest(BaseModel):
    """Overrides of dt and max_steps for this and later react calls; omitted fields are kept.

    One react call runs max_steps reaction steps of dt seconds each inside the
    engine. Values start from the defaults list_cases shows and, once set, persist
    for later react calls in the same session. Numeric strings are
    accepted; unknown fields, booleans, non-numeric strings, non-integral max_steps,
    and non-finite or out-of-range values are rejected.
    """

    # Reject unknown fields and disable automatic type conversion
    model_config = ConfigDict(extra="forbid", strict=True)

    dt: Timestep | None = None
    max_steps: MaxSteps | None = None

    def overrides(self) -> dict[str, float | int]:
        """Return only the fields the caller set."""
        return self.model_dump(exclude_none=True)
