"""Shape raw engine snapshots into the compact, name-keyed client contract."""

import pytest

from mcp_alquimia.response.contract import (
    shape_react,
    shape_setup,
    shape_snapshot,
    shape_state,
    to_json,
)
from mcp_alquimia.utils.units import RESPONSE_UNITS

METADATA = {"primary_species": ["H+", "HCO3-", "Ca++"], "minerals": ["Calcite"]}
RAW = {
    "total_mobile": [0.009137804638908734, 0.010862200452877968, 0.0008621981879584278],
    "total_immobile": [],
    "mineral_volume_fraction": [0.19996815567537513],
    "time": 4320000,
    "total_steps": 50,
    "pH": 5.613082397032818,
    "temperature_celsius": 25,
    "porosity": 0.5,
}


def test_state_is_keyed_by_name_and_rounded():
    """Vectors become name maps; values keep 6 significant digits and pH 3 decimals."""
    assert shape_state(RAW, METADATA) == {
        "pH": 5.613,
        "temperature": 25,
        "porosity": 0.5,
        "aqueous_total": {"H+": 0.00913780, "HCO3-": 0.0108622, "Ca++": 0.000862198},
        "mineral_volume_fraction": {"Calcite": 0.199968},
    }


def test_null_ph_and_empty_groups_are_omitted():
    """ONNX reports no pH and minerals-free cases no volume fractions; neither is sent."""
    raw = {**RAW, "pH": None, "mineral_volume_fraction": []}
    state = shape_state(raw, {**METADATA, "minerals": []})
    assert set(state) == {"temperature", "porosity", "aqueous_total"}


def test_snapshot_reports_cumulative_time_and_steps():
    """initialize and get_results report time and total_steps beside the state."""
    snapshot = shape_snapshot({"state": RAW, "metadata": METADATA})
    assert (snapshot["time"], snapshot["total_steps"]) == (4320000, 50)
    assert snapshot["state"] == shape_state(RAW, METADATA)


def test_react_response_reports_this_call_and_cumulative_steps():
    """steps and dt are this call's; time and total_steps count from initialization."""
    response = shape_react(
        {"state": RAW, "metadata": METADATA, "dt": 86400.0, "max_steps": 10}
    )
    assert response == {
        "time": 4320000,
        "total_steps": 50,
        "steps": 10,
        "dt": 86400.0,
        "state": shape_state(RAW, METADATA),
    }


def test_setup_response_hides_engine_internals_and_sends_units():
    """The staged engine, input file and condition stay server-side; units are sent once."""
    response = shape_setup(
        {
            "session_id": "abc",
            "case": "calcite-short",
            "engine": "PFloTran",
            "setup": {
                "engine": "PFloTran",
                "input_file": "/runs/abc/calcite-short-pflotran.in",
                "initial_condition": "initial",
                "density": 997.16,
                "temperature": 25.0,
            },
            "time": {"dt": 86400.0, "max_steps": 10},
        }
    )
    assert response == {
        "session_id": "abc",
        "case": "calcite-short",
        "engine": "PFloTran",
        "settings": {"density": 997.16, "temperature": 25.0},
        "time": {"dt": 86400.0, "max_steps": 10},
        "units": RESPONSE_UNITS,
    }


def test_vector_and_name_lengths_must_agree():
    """A mismatched vector is an error instead of mislabelled species."""
    with pytest.raises(ValueError):
        shape_state({**RAW, "total_mobile": [1.0]}, METADATA)


def test_json_is_compact_and_finite():
    """Responses carry no whitespace and reject NaN."""
    assert to_json({"a": [1, 2]}) == '{"a":[1,2]}'
    with pytest.raises(ValueError):
        to_json({"a": float("nan")})
