"""Read a PFLOTRAN constraint's chemistry into engine-independent units."""

from ..utils.numerics import parse_number
from .input_document import InputDocument
from .model import Aqueous, Chemistry, Mineral, log_field, number_field

# Maps PFLOTRAN single-letter constraints to standard, human-readable names.
# For example: 'T' maps to 'total' concentration, 
# 'P' fixes 'pH', 
# 'Z' is for 'charge' balance
_PFLOTRAN_CONSTRAINTS = {
    "T": "total",
    "F": "free",
    "L": "log",
    "P": "pH",
    "PE": "pe",
    "G": "gas",
    "M": "mineral",
    "Z": "charge",
}


# -------------------------------------------------------------------------
# Unit conversion factor dictionaries: converts engine-independent "standard units"
# into the specific input units required by different software configuration files.
# Core conversion formula: input value = value * factor
# -------------------------------------------------------------------------

# Specific Surface Area conversion for minerals
# Assumes engine-independent standard unit is m^2/m^3 (factor 1.0)
# Conversion logic: 1 m^2/m^3 = 10,000 cm^2 / 1,000,000 cm^3 = 10^-2 cm^2/cm^3
_PFLOTRAN_AREA_UNITS = {None: 1.0, "m^2/m^3": 1.0, "cm^2/cm^3": 1e-2}

# Kinetic reaction rate conversion for minerals
# Assumes engine-independent standard unit is mol/m^2/s (factor 1.0)
# Conversion logic: 1 mol/m^2/s = 1 mol / 10,000 cm^2 / s = 10^-4 mol/cm^2/s
# Note: Includes various time delimiters (-sec, -s, /s) supported by PFLOTRAN.
_PFLOTRAN_RATE_UNITS = {
    None: 1.0,
    "mol/m^2-sec": 1.0,
    "mol/m^2-s": 1.0,
    "mol/m^2/s": 1.0,
    "mol/cm^2-sec": 1e-4,
    "mol/cm^2-s": 1e-4,
    "mol/cm^2/s": 1e-4,
}


def read_pflotran(document: InputDocument, condition: str) -> Chemistry:
    """
    Read a PFLOTRAN CONSTRAINT and the MINERAL_KINETICS rate constants.

    Extracts aqueous species concentrations and solid phase properties (volume 
    fraction, surface area) from the specified nested CONSTRAINT block. It also 
    parses the global MINERAL_KINETICS block to extract reaction rate constants. 
    PFLOTRAN constraint concentrations default to molarity (mol/L), the
    engine-independent standard, so they are read without conversion.

    Args:
        document (InputDocument): The parsed PFLOTRAN input document tree.
        condition (str): The name of the CONSTRAINT block to extract (e.g., "initial").

    Returns:
        Chemistry: An engine-independent object representing the chemical state.

    Raises:
        ValueError: If the specified CONSTRAINT block is not found in the document.

    Example:
        >>> text = '''
        ... CONSTRAINT initial
        ...   CONCENTRATIONS
        ...     H+   5.0   P
        ...     Ca++ 1e-3  T
        ...     Cl-  1e-5  M Halite
        ...   /
        ...   MINERALS
        ...     Halite 1.0e-5 1.0 cm^2/cm^3
        ...   /
        ... /
        ... MINERAL_KINETICS
        ...   Halite
        ...     RATE_CONSTANT -9.0 mol/cm^2-sec
        ...   /
        ... /
        ... '''
        >>> doc = InputDocument(text, engine="PFLOTRAN")
        >>> chem = read_pflotran(doc, "initial")
        >>> chem.aqueous["Ca++"].constraint
        'total'
        >>> chem.aqueous["Ca++"].field.value
        0.001
        
        # The 'M' flag is successfully mapped to 'mineral' with its associated phase
        >>> chem.aqueous["Cl-"].constraint
        'mineral'
        >>> chem.aqueous["Cl-"].associated
        'Halite'
        
        # Negative rate constants are properly treated as log10 values
        >>> chem.rates["Halite"].value
        1e-09
    """
    # Find the CONSTRAINT block
    constraint = document.root.find("CONSTRAINT", condition)
    if constraint is None:
        raise ValueError(f"No CONSTRAINT {condition!r} in the PFLOTRAN input")
    chemistry = Chemistry()

    # Find the CONCENTRATIONS block
    concentrations = constraint.find("CONCENTRATIONS")
    for line in concentrations.lines() if concentrations else ():
       
        # e.g.
        # HCO3- 1.d-5 T
        # Ca++ 1.d-5 T
        # H+ -1.d-5 T
        tokens = line.tokens
        
        # Invalid field
        if len(tokens) < 3:
            continue
        
        # Get the interface constraint type value
        constraint_type = _PFLOTRAN_CONSTRAINTS.get(tokens[2].upper(), tokens[2])
        # e.g.
        # Cl- 1.0e-5 M Halite
        entry = Aqueous(
            constraint_type, associated=tokens[3] if len(tokens) > 3 else None
        )
        if constraint_type in ("total", "free"):
            entry.field = number_field(line, 1)
        elif constraint_type == "log":
            entry.field = log_field(line, 1)
        elif constraint_type in ("pH", "pe", "gas"):
            # No factor
            entry.field = number_field(line, 1)
        chemistry.aqueous[tokens[0]] = entry
        
    # Find the MINERALS block
    minerals = constraint.find("MINERALS")
    for line in minerals.lines() if minerals else ():
        tokens = line.tokens
        
        # Invalid field
        if len(tokens) < 2:
            continue
        mineral = Mineral(volume_fraction=number_field(line, 1))
        # e.g.
        # Calcite 0.2 100.0 m^2/m^3
        unit = tokens[3] if len(tokens) > 3 else None
        if len(tokens) > 2 and unit in _PFLOTRAN_AREA_UNITS:
            mineral.surface_area = number_field(line, 2, _PFLOTRAN_AREA_UNITS[unit])
        chemistry.minerals[tokens[0]] = mineral
    
    # Find the MINERAL_KINETICS block
    kinetics = document.root.find("MINERAL_KINETICS")
    for block in kinetics.blocks() if kinetics else ():
        rate = next(
            (line for line in block.lines() if line.keyword == "RATE_CONSTANT"), None
        )
        # Invalid field
        if rate is None:
            continue
        
        # Find RATE_CONSTANT
        tokens = rate.tokens
        unit = tokens[2] if len(tokens) > 2 else None
        number = parse_number(tokens[1]) if len(tokens) > 1 else None
        factor = _PFLOTRAN_RATE_UNITS.get(unit)
        name = block.header.tokens[0]
        # Invalid number or factor
        if number is None or factor is None:
            chemistry.rates[name] = None
        elif number < 0:
            # PFLOTRAN reads a negative rate constant as log10
            chemistry.rates[name] = log_field(rate, 1, factor)
        else:
            chemistry.rates[name] = number_field(rate, 1, factor)
    return chemistry
