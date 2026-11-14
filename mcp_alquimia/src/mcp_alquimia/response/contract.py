"""Shape engine results into the compact, name-keyed JSON returned to MCP clients.

The engine process reports Alquimia's state as positional vectors with full
precision; those raw snapshots stay in the session artifacts. Clients receive
values keyed by species or mineral name, rounded, and with units sent once by
setup instead of in every response.
"""

import json
from typing import Any

from ..utils.numerics import PH_DECIMALS, round_significant
from ..utils.units import RESPONSE_UNITS

# Staged setup keys the engine needs but the client does not.
_SETUP_INTERNALS = ("engine", "input_file", "initial_condition")

# Response group, raw state vector, and the metadata list naming its entries.
_NAMED_GROUPS = (
    ("aqueous_total", "total_mobile", "primary_species"),
    ("immobile_total", "total_immobile", "primary_species"),
    ("mineral_volume_fraction", "mineral_volume_fraction", "minerals"),
)


def to_json(payload: dict[str, Any]) -> str:
    """Serialize a response without whitespace, rejecting non-finite numbers."""
    return json.dumps(payload, separators=(",", ":"), allow_nan=False)


def shape_state(raw: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Key a raw engine snapshot by name and round it.

    Args:
        raw: Engine snapshot with total_mobile, total_immobile and
            mineral_volume_fraction vectors, pH (None for ONNX),
            temperature_celsius and porosity.
        metadata: Engine setup metadata; primary_species and minerals give the
            order of the vectors.

    Returns:
        pH, temperature, porosity and the named groups of RESPONSE_UNITS. A null
        pH and empty groups are omitted.

    Raises:
        ValueError: A vector's length differs from its list of names.
    """
    state = {}
    if raw.get("pH") is not None:
        state["pH"] = round(raw["pH"], PH_DECIMALS)
    for name, source in (
        ("temperature", "temperature_celsius"),
        ("porosity", "porosity"),
    ):
        if source in raw:
            state[name] = round_significant(raw[source])
    for name, source, metadata_name in _NAMED_GROUPS:
        values = raw.get(source) or []
        if values:
            state[name] = {
                key: round_significant(value)
                for key, value in zip(metadata[metadata_name], values, strict=True)
            }
    return state


def shape_setup(result: dict[str, Any]) -> dict[str, Any]:
    """The setup response: session identity, physical settings, time and units.

    Args:
        result: SessionManager.setup's session_id, case, engine, staged setup and time.
    """
    return {
        "session_id": result["session_id"],
        "case": result["case"],
        "engine": result["engine"],
        "settings": {
            key: value for key, value in result["setup"].items()
            if key not in _SETUP_INTERNALS  # Physical condition
        },
        "time": result["time"],
        "units": RESPONSE_UNITS,
    }


def shape_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    """The initialize and get_results response: cumulative time, steps and state.

    Args:
        result: A SessionManager result with the raw state and its metadata.
    """
    raw = result["state"]
    return {
        "time": raw["time"],
        "total_steps": raw["total_steps"],
        "state": shape_state(raw, result["metadata"]),
    }


def shape_react(result: dict[str, Any]) -> dict[str, Any]:
    """The react response: this call's steps and dt, cumulative time and steps, state.

    Args:
        result: SessionManager.react's raw state, metadata, dt and max_steps.
    """
    raw = result["state"]
    return {
        "time": raw["time"],
        "total_steps": raw["total_steps"],
        "steps": result["max_steps"],
        "dt": result["dt"],
        "state": shape_state(raw, result["metadata"]),
    }
