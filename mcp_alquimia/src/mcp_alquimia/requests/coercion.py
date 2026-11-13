"""Lenient parsing of numbers and names that small agent models send as strings.

Local models often quote numbers ("1e-10") or wrap names in backticks ("`Ca++`").
These validators run before pydantic's strict checks, so the advertised JSON
schema stays `number`/`integer` while such inputs are still accepted. Booleans,
text with units, and non-finite values are left for the strict checks to reject.
"""

from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from ..utils.numerics import parse_number

# Catch ` | ' | "
_NAME_QUOTES = ("`", "'", '"')


def coerce_float(value: Any) -> Any:
    """Convert a numeric string to a float; pass every other value through unchanged.

    Raises:
        ValueError: The string is not a bare finite number (e.g. it carries a unit).
    """
    if not isinstance(value, str):
        return value
    number = parse_number(value.strip())
    if number is None:
        raise ValueError(
            f"expected a bare number in the field's documented unit, e.g. 1e-10; got {value!r}"
        )
    return number


def coerce_int(value: Any) -> Any:
    """Convert a numeric string or an integral float to an int; pass the rest through.

    Raises:
        ValueError: The string is not a bare finite number.
    """
    value = coerce_float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def strip_name_quotes(value: Any) -> Any:
    """Remove surrounding whitespace and one matching pair of backticks or quotes."""
    if not isinstance(value, str):
        return value
    name = value.strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in _NAME_QUOTES:
        name = name[1:-1].strip()
    return name


def lenient_float(**constraints: Any) -> Any:
    """A finite float that also accepts numeric strings, with Field constraints.

    The constraints must sit on the float, before the validator: placed on top of
    a BeforeValidator-wrapped type they are still enforced, but pydantic writes
    them into the JSON schema as non-standard keys (gt, le) instead of
    exclusiveMinimum and maximum.

    Args:
        **constraints: Field arguments such as gt, ge, le.
    """
    return Annotated[
        float, Field(allow_inf_nan=False, **constraints), BeforeValidator(coerce_float)]


def lenient_int(**constraints: Any) -> Any:
    """An int that also accepts numeric strings and integral floats, with Field constraints.

    Args:
        **constraints: Field arguments such as ge, le; see lenient_float for placement.
    """
    return Annotated[int, Field(**constraints), BeforeValidator(coerce_int)]
