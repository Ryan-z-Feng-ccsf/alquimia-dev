"""Precision rules shared by input parsing, input writing and responses."""

import math

import pytest

from mcp_alquimia.utils.numerics import format_number, parse_number, round_significant
from mcp_alquimia.utils.units import CHEMISTRY_UNITS, RESPONSE_UNITS, molar_to_molal_factor


@pytest.mark.parametrize(
    ("token", "expected"),
    [("1.d-5", 1e-5), ("2.5D3", 2500.0), ("-7", -7.0), ("1e-10", 1e-10)],
)
def test_fortran_reals_are_parsed(token, expected):
    """Fortran d/D exponents read like e."""
    assert parse_number(token) == pytest.approx(expected)


@pytest.mark.parametrize("token", ["Calcite", "nan", "inf", "1.0e-3mol", ""])
def test_non_numbers_and_non_finite_values_are_none(token):
    """Names, units glued to numbers and non-finite values are not numbers."""
    assert parse_number(token) is None


@pytest.mark.parametrize("value", [1.0e-10, 0.15, 2.718281828459045, 123456789.0])
def test_written_reals_read_back(value):
    """Values written into an input keep 12 significant digits and parse back."""
    assert parse_number(format_number(value)) == pytest.approx(value, rel=1e-12)


def test_responses_keep_six_significant_digits():
    """Rounding is relative to magnitude, not a fixed number of decimals."""
    assert round_significant(1.23456789e-7) == 1.23457e-7
    assert round_significant(987654321.0) == 987654000.0
    assert round_significant(0.0) == 0.0


def test_molarity_converts_with_the_water_density():
    """mol/L becomes mol/kg by litres per kilogram; 1000 kg/m^3 is the identity."""
    assert molar_to_molal_factor(1000.0) == 1.0
    assert math.isclose(molar_to_molal_factor(997.16), 1000.0 / 997.16)


def test_concentrations_share_one_unit():
    """Inputs, get_chemistry and responses report concentrations in the same unit."""
    assert CHEMISTRY_UNITS["total"] == CHEMISTRY_UNITS["free"] == RESPONSE_UNITS["aqueous_total"]
    assert CHEMISTRY_UNITS["volume_fraction"] == RESPONSE_UNITS["mineral_volume_fraction"]
