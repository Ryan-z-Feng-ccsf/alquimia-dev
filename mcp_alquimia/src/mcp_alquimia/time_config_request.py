"""Typed, validated time-stepping overrides an agent may send with a react request."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# Mirrors kMaxReactSteps in engine_process/alquimia_engine_process_helpers.c.
MAX_REACT_STEPS = 10000

Timestep = Annotated[
    float, Field(gt=0, allow_inf_nan=False, description="Seconds per reaction step.")]
MaxSteps = Annotated[
    int,
    Field(ge=1, le=MAX_REACT_STEPS, allow_inf_nan=False,
          description="Number of reaction steps one react call runs in the engine."),
]


class TimeConfigRequest(BaseModel):
    """Overrides of the session's dt and max_steps; omitted fields keep current values.

    One react call runs max_steps reaction steps of dt seconds each inside the
    engine. Values start from the case's cfg (`timestep` and `max_steps`) and, once
    set, persist for later react calls in the same session. Unknown fields, booleans
    or strings in place of numbers, and non-finite or out-of-range values are rejected.
    """

    # Reject unknown fields and disable automatic type conversion
    model_config = ConfigDict(extra="forbid", strict=True)

    dt: Timestep | None = None
    max_steps: MaxSteps | None = None

    def overrides(self) -> dict[str, float | int]:
        """Return only the fields the caller set."""
        return self.model_dump(exclude_none=True)
