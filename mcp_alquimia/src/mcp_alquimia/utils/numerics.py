"""Number precision rules: reading and writing engine input reals, rounding responses."""

import math

# Significant digits kept for state and input chemistry values.
SIGNIFICANT_DIGITS = 6
# pH is reported to a fixed number of decimals rather than significant digits.
PH_DECIMALS = 3


def parse_number(token: str) -> float | None:
    """Read a Fortran-style real such as 1.d-5; None if the token is not a finite number."""
    try:
        value = float(token.replace("d", "e").replace("D", "E"))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def format_number(value: float) -> str:
    """Write a real that both engines' input readers accept."""
    return f"{value:.12g}"


def round_significant(value: float) -> float:
    """Round a value to SIGNIFICANT_DIGITS significant digits."""
    return float(f"{value:.{SIGNIFICANT_DIGITS}g}")
