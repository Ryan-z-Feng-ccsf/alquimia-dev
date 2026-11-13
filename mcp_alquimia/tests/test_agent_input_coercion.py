"""Unit tests for the lenient number and name parsing applied to agent requests."""

import json
import re

from pydantic import BaseModel, ValidationError
import pytest

from mcp_alquimia.agent_input_coercion import (
    coerce_float,
    coerce_int,
    lenient_float,
    lenient_int,
    strip_name_quotes,
)
from mcp_alquimia.setup_request import SetupRequest
from mcp_alquimia.time_config_request import TimeConfigRequest


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1e-10", 1e-10),
        (" 0.0000000001 ", 1e-10),
        ("1.d-5", 1e-5),
        ("3600", 3600.0),
        (2.5, 2.5),
        (3, 3),
        (True, True),
        (None, None),
    ],
)
def test_coerce_float_parses_numeric_strings_only(raw, expected):
    """Numeric strings become floats; other values pass through for strict checking."""
    result = coerce_float(raw)
    assert result == expected and type(result) is type(expected)


@pytest.mark.parametrize(
    "raw", ["3600 s", "1e-10 mol/kg", "heavy", "true", "", "inf", "nan"]
)
def test_coerce_float_rejects_text_units_and_non_finite(raw):
    """Anything but a bare finite number asks the agent for one."""
    with pytest.raises(ValueError, match="bare number"):
        coerce_float(raw)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("100", 100),
        ("1e2", 100),
        ("100.0", 100),
        (100.0, 100),
        (7, 7),
        (1.5, 1.5),
        ("1.5", 1.5),
        (True, True),
    ],
)
def test_coerce_int_converts_only_integral_values(raw, expected):
    """Integral floats become ints; fractional ones and booleans are left to be rejected."""
    result = coerce_int(raw)
    assert result == expected and type(result) is type(expected)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("`Ca++`", "Ca++"),
        ("'Calcite'", "Calcite"),
        ('"H+"', "H+"),
        ("  ` Ca++ `  ", "Ca++"),
        ("Ca++", "Ca++"),
        ("`Ca++'", "`Ca++'"),
        ("`", "`"),
        (5, 5),
    ],
)
def test_strip_name_quotes_removes_one_matching_pair(raw, expected):
    """Only whitespace and one matching outer pair of quotes are removed."""
    assert strip_name_quotes(raw) == expected


def test_lenient_numbers_keep_their_ranges_in_the_schema_and_in_validation():
    """Ranges are advertised with standard keywords and still enforced on numeric strings."""

    class Bounded(BaseModel):
        fraction: lenient_float(gt=0, le=1)
        steps: lenient_int(ge=1, le=10)

    properties = Bounded.model_json_schema()["properties"]
    assert (properties["fraction"]["exclusiveMinimum"], properties["fraction"]["maximum"]) == (0, 1)
    assert (properties["steps"]["minimum"], properties["steps"]["maximum"]) == (1, 10)
    assert Bounded(fraction="0.5", steps="2.0").model_dump() == {"fraction": 0.5, "steps": 2}
    for fraction, steps in (("2", 1), (0.5, "11"), ("inf", 1)):
        with pytest.raises(ValidationError):
            Bounded(fraction=fraction, steps=steps)


@pytest.mark.parametrize("model", [SetupRequest, TimeConfigRequest])
def test_request_schemas_use_only_standard_range_keywords(model):
    """pydantic writes gt/ge/lt/le when a range sits on top of a BeforeValidator."""
    text = json.dumps(model.model_json_schema())
    assert not re.search(r'"(gt|ge|lt|le)":', text), text
