"""Lenient parsing of numbers and names that small agent models send as strings.

Local models often quote numbers ("1e-10") or wrap names in backticks ("`Ca++`").
These validators run before pydantic's strict checks, so the advertised JSON
schema stays `number`/`integer` while such inputs are still accepted. Booleans,
text with units, and non-finite values are left for the strict checks to reject.
"""

import math
from typing import Annotated, Any

from pydantic import BeforeValidator

# Catch ` | ' | "
_NAME_QUOTES = ("`", "'", '"')


def parse_number(token: str) -> float | None:
    """Read a Fortran-style real such as 1.d-5; None if the token is not a finite number."""
    try:
        value = float(token.replace("d", "e").replace("D", "E"))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


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


LenientFloat = Annotated[float, BeforeValidator(coerce_float)]
LenientInt = Annotated[int, BeforeValidator(coerce_int)]
