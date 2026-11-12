"""Shape engine results into the compact, name-keyed JSON returned to MCP clients.

The engine process reports Alquimia's state as positional vectors with full
precision; those raw snapshots stay in the session artifacts. Clients receive
values keyed by species or mineral name, rounded, and with units sent once by
setup instead of in every response.
"""

import json
from typing import Any

# Significant digits kept for state and input chemistry values.
SIGNIFICANT_DIGITS = 6
# pH is reported to a fixed number of decimals rather than significant digits.
PH_DECIMALS = 3

# Units of every field in setup, initialize, react and get_results responses.
RESPONSE_UNITS = {
    "temperature": "degC",
    "density": "kg/m^3",
    "pressure": "Pa",
    "volume": "m^3",
    "time": "s",
    "dt": "s",
    "aqueous_total": "mol/L water",
    "immobile_total": "mol/m^3 bulk",
    "mineral_volume_fraction": "m^3 mineral/m^3 bulk",
}

# Response group, raw state vector, and the metadata list naming its entries.
_NAMED_GROUPS = (
    ("aqueous_total", "total_mobile", "primary_species"),
    ("immobile_total", "total_immobile", "primary_species"),
    ("mineral_volume_fraction", "mineral_volume_fraction", "minerals"),
)


def round_significant(value: float) -> float:
    """Round a value to SIGNIFICANT_DIGITS significant digits."""
    return float(f"{value:.{SIGNIFICANT_DIGITS}g}")


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


def shape_snapshot(raw: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """The initialize and get_results response: cumulative time, steps and state."""
    return {
        "time": raw["time"],
        "total_steps": raw["total_steps"],
        "state": shape_state(raw, metadata),
    }
