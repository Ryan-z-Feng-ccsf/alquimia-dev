"""Editable chemistry read from an input: values in engine-independent units, each
linked to the token it was read from."""

from collections.abc import Callable
from dataclasses import dataclass, field
import math
from typing import Any

from ..utils.numerics import format_number, parse_number, round_significant
from .input_document import InputDocument, Line

@dataclass
class Field:
    """
    Represents an editable numerical parameter within the configuration file.

    This class acts as a precise pointer to a specific token in the document. 
    It stores the token's exact location, its current value in standardized units, 
    and the conversion logic required to write a new value back into the engine's 
    native format.

    Attributes:
        line (Line): The parsed Line object where this value resides.
        position (int): The zero-based token index within the line's parsed spans.
        value (float): The current numerical value, converted to engine-independent standard units.
        encode (Callable[[float], float]): A function that converts a standard-unit value 
                                           back into the engine-specific input unit.
    """

    line: Line
    position: int
    value: float
    encode: Callable[[float], float]

    def write(self, document: InputDocument, value: float) -> None:
        """
        Updates the document with a new value for this field.

        This method accepts a value in engine-independent units, applies the `encode` 
        function to convert it to the engine's expected units, formats it as a string, 
        and instructs the document to replace the original token in-place.

        Args:
            document (InputDocument): The parsed document object to be modified.
            value (float): The new numerical value in engine-independent standard units.
        """
        document.replace_token(
            self.line,
            self.position,
            format_number(self.encode(value))
        )


@dataclass
class Aqueous:
    """
    Configuration for an aqueous primary species constraint.

    Defines how an aqueous species is constrained in an initial or boundary condition 
    (e.g., set by total concentration, fixed pH, or equilibrium with a mineral/gas).

    Attributes:
        constraint (str): The standardized constraint type (e.g., "total", "pH", "mineral").
        field (Field | None): The editable numerical value for this constraint. 
                              It is None if the constraint does not carry an editable 
                              value (e.g., charge balance where the engine calculates it).
        associated (str | None): The name of a secondary species, mineral, or gas associated 
                                 with this constraint (e.g., "Calcite" if constraint is "mineral").
    """

    constraint: str
    field: Field | None = None
    associated: str | None = None


@dataclass
class Mineral:
    """
    Configuration for a solid phase mineral in an initial condition.

    Tracks the physical properties of a mineral within a specific spatial zone 
    or condition block.

    Attributes:
        volume_fraction (Field | None): The editable volume fraction of the mineral 
                                        (m^3 mineral/m^3 bulk).
        surface_area (Field | None): The editable specific surface area of the mineral 
                                     (m^2 mineral/m^3 bulk).
    """

    volume_fraction: Field | None = None
    surface_area: Field | None = None


@dataclass
class Chemistry:
    """
    Represents the editable chemical configuration of a specific simulation condition.

    This data class acts as a unified container for all parsed chemical parameters 
    (aqueous species, solid minerals, and kinetic rates) belonging to a single 
    initial condition, boundary condition, or global setting. It abstracts away 
    the underlying engine syntax (PFLOTRAN or CrunchFlow) into an engine-independent 
    Python object.

    Attributes:
        aqueous (dict[str, Aqueous]): A mapping of aqueous primary species names 
                                      to their corresponding `Aqueous` configuration objects.
        minerals (dict[str, Mineral]): A mapping of mineral names to their corresponding 
                                       `Mineral` configuration objects (handling volume fractions and area).
        rates (dict[str, Field | None]): A mapping of kinetic reaction/mineral names 
                                         to their reaction rate `Field` objects. A value of None 
                                         indicates a rate exists but cannot be safely edited.
    """

    aqueous: dict[str, Aqueous] = field(default_factory=dict)
    minerals: dict[str, Mineral] = field(default_factory=dict)
    rates: dict[str, Field | None] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        """
        Generates a JSON-serializable dictionary summarizing the chemical state.

        Extracts the current values and constraints of all aqueous species, minerals, 
        and kinetic rates, outputting them in engine-independent standard units. 
        Numerical values are formatted to 6 significant digits to keep the summary 
        concise for UI, logging, or API responses. 

        Missing or uneditable values are returned as null (None).

        Returns:
            dict[str, Any]: A nested dictionary containing 'aqueous', 'minerals', 
                            and 'rates' configuration summaries.

        Example:
            >>> chem.describe()
            {
                "aqueous": {
                    "H+": {"constraint": "pH", "value": 5.0},
                    "Ca+2": {"constraint": "total", "value": 0.001, "associated": "Cl-"}
                },
                "minerals": {
                    "Calcite": {"volume_fraction": 0.2, "surface_area": 100.0}
                },
                "rates": {
                    "Calcite": 1.2e-13
                }
            }
        """

        def value(item):
            # Rounded to keep the summary short; edits use exact values
            return round_significant(item.value) if item else None

        aqueous = {}
        for name, entry in self.aqueous.items():
            aqueous[name] = {
                "constraint": entry.constraint,
                "value": value(entry.field),
            }
            if entry.associated:
                aqueous[name]["associated"] = entry.associated
        return {
            "aqueous": aqueous,
            "minerals": {
                name: {
                    "volume_fraction": value(mineral.volume_fraction),
                    "surface_area": value(mineral.surface_area),
                }
                for name, mineral in self.minerals.items()
            },
            "rates": {name: value(rate) for name, rate in self.rates.items()},
        }


def number_field(line: Line, position: int, factor: float = 1.0) -> Field | None:
    """
    Extracts a numerical token from a line and wraps it in an editable Field object.

    This helper function parses the token at the specified position. It applies a 
    scaling conversion (using the provided factor) to translate the engine-specific 
    input value into an engine-independent standard unit for internal storage. It also 
    injects an `encode` lambda function into the Field, ensuring that any future 
    edits to this value are correctly scaled back to the engine's native units 
    before being written to the file.

    Args:
        line (Line): The parsed Line object containing the text and tokens.
        position (int): The zero-based index of the token to evaluate.
        factor (float, optional): The conversion multiplier (engine unit = standard unit * factor). 
                                  Defaults to 1.0 (no conversion).

    Returns:
        Field | None: A populated Field instance pointing to the exact token if parsing 
                      is successful, or None if the token is missing or not a valid number.
    """
    tokens = line.tokens
    number = parse_number(tokens[position]) if position < len(tokens) else None
    if number is None:
        return None
    return Field(line, position, number / factor, lambda value: value * factor)


def log_field(line: Line, position: int, factor: float = 1.0) -> Field | None:
    """A field whose input value is log10 of the engine-independent value times factor."""
    number = parse_number(line.tokens[position])
    if number is None:
        return None
    return Field(
        line, position, 10.0**number / factor, lambda value: math.log10(value * factor)
    )
