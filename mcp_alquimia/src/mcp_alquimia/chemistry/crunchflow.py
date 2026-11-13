"""Read a CrunchFlow condition's chemistry into engine-independent units."""

from ..utils.numerics import parse_number
from ..utils.units import molar_to_molal_factor
from .input_document import InputDocument, Line
from .model import Aqueous, Chemistry, Mineral, log_field, number_field

# CrunchFlow concentration magnitude conversion, relative to mol/kg; the
# mol/L standard is converted to mol/kg with the water density in read_crunchflow
# Conversion logic: Millimoles (mmol) require scaling the standard molar value by 1,000 (1e3).
# Micromoles (umol/micromol) require scaling by 1,000,000 (1e6).
_CRUNCH_UNITS = {"mol/kg": 1.0, "mmol/kg": 1e3, "umol/kg": 1e6, "micromol/kg": 1e6}


def read_crunchflow(
    document: InputDocument, condition: str, density: float
) -> Chemistry:
    """
    Read a CrunchFlow Condition and the -rate options of its MINERALS block.

    Extracts the aqueous species, mineral volume fractions, and surface areas 
    from the specified CONDITION block. It also scans the global MINERALS block 
    to extract kinetic reaction rates (-rate) for the minerals present. If a 
    mineral defines multiple parallel reaction mechanisms (e.g., acid and default), 
    its rate field is set to None (read-only).

    Args:
        document (InputDocument): The parsed CrunchFlow input document tree.
        condition (str): The name of the CONDITION block to extract (e.g., "initial").
        density (float): Water density in kg/m^3, converting the condition's
                         mol/kg (molality) to the mol/L standard.

    Returns:
        Chemistry: An engine-independent object representing the chemical state.

    Raises:
        ValueError: If the specified condition block is not found in the document.

    Example:
        >>> text = '''
        ... PRIMARY_SPECIES
        ... Ca++
        ... END
        ... 
        ... MINERALS
        ... Calcite -label default -rate -9.0
        ... Quartz -label default -rate -13.0
        ... Quartz -label acid -rate -10.0
        ... END
        ... 
        ... CONDITION initial
        ... units mol/kg
        ... pH 5.0
        ... Ca++ 1.0e-3
        ... Calcite 0.2 bsa 100.0
        ... END
        ... '''
        >>> doc = InputDocument(text, engine="CrunchFlow")
        >>> chem = read_crunchflow(doc, "initial", density=1000.0)
        
        # pH is mapped to H+ constraint
        >>> chem.aqueous["H+"].constraint
        'pH'
        >>> chem.aqueous["H+"].field.value
        5.0
        
        # Density of 1000 kg/m^3 makes mol/kg equal mol/L
        >>> chem.aqueous["Ca++"].field.value
        0.001
        
        # Mineral volume and surface area extracted
        >>> chem.minerals["Calcite"].volume_fraction.value
        0.2
        >>> chem.minerals["Calcite"].surface_area.value
        100.0
        
        # Single mineral rate decoded from log value (-9.0 -> 1e-9)
        >>> chem.rates["Calcite"].value
        1e-09
        
        # Quartz has parallel rates, so it becomes uneditable (None)
        >>> chem.rates["Quartz"] is None
        True
    """
    
    # Find "CONDITION" child block
    block = document.root.find("CONDITION", condition)
    if block is None:
        raise ValueError(f"No Condition {condition!r} in the CrunchFlow input")
    
    # Find "PRIMARY_SPECIES" child block
    primary_block = document.root.find("PRIMARY_SPECIES")
    primary_species = (
        {line.tokens[0] for line in primary_block.lines()} if primary_block else set()
    )
    
    # Find "MINERALS" child block
    minerals_block = document.root.find("MINERALS")
    
    # Get Lines under MINERALS block
    mineral_lines = list(minerals_block.lines()) if minerals_block else []
    
    # Get the mineral names
    mineral_names = {line.tokens[0] for line in mineral_lines}
    chemistry = Chemistry()
    unit_line = next((line for line in block.lines() if line.keyword == "UNITS"), None)
    unit = (
        unit_line.tokens[1].lower()
        if unit_line and len(unit_line.tokens) > 1
        else "mol/kg"
    )
    # input value = mol/L / (density [kg/L]) * unit scale
    unit_scale = _CRUNCH_UNITS.get(unit)
    concentration_factor = unit_scale * molar_to_molal_factor(density) if unit_scale else None
    
    # Within CONDITION block
    # Parse the primary species and mineral
    for line in block.lines():
        tokens = line.tokens
        name = tokens[0]
        if line.keyword == "PH" and len(tokens) > 1:
            
            # CrunchFlow fixes H+ through a pH line
            chemistry.aqueous["H+"] = Aqueous("pH", number_field(line, 1))
        elif name in primary_species and len(tokens) > 1:
            
            # Parse the primary species fields
            chemistry.aqueous[name] = _crunch_aqueous(
                line, mineral_names, concentration_factor
            )
        elif name in mineral_names and len(tokens) > 1:
            
            # Parse the mineral
            mineral = Mineral(volume_fraction=number_field(line, 1))
            option = tokens[2].lower() if len(tokens) > 2 else None
            if option in ("bulk_surface_area", "bsa") and len(tokens) > 3:
                mineral.surface_area = number_field(line, 3)
            elif option is not None and parse_number(tokens[2]) is not None:
                
                # e.g. Calcite 0.2 0.2
                mineral.surface_area = number_field(line, 2)
            chemistry.minerals[name] = mineral
    
    # Parse the minerals rate
    for line in mineral_lines:
        tokens = [token.lower() for token in line.tokens]
        # Only change the mineral rate
        if "-rate" not in tokens or tokens.index("-rate") + 1 >= len(tokens):
            continue
        # e.g. Calcite -label default -rate -9.0
        # e.g. name = Calcite
        name = line.tokens[0]
        
        # Parallel reactions of one mineral have one rate each; none is "the" rate
        # No access to change the rate
        # e.g. 
        # Calcite    -label default    -rate -8.79
        # Calcite    -label acid       -rate -0.30
        # Calcite    -label pco2       -rate -3.30
        repeated = name in chemistry.rates
        
        # log values
        chemistry.rates[name] = (
            None if repeated else log_field(line, tokens.index("-rate") + 1)
        )
    return chemistry


def _crunch_aqueous(line: Line, minerals: set[str], factor: float | None) -> Aqueous:
    """
    Classifies and parses a CrunchFlow primary species constraint line.

    Analyzes the arrangement of tokens to deduce the specific thermodynamic constraint 
    applied to the aqueous species (e.g., charge balance, mineral equilibrium, gas 
    partial pressure, or fixed concentration). 

    If a valid `factor` is provided, numerical values are converted to standard units 
    (mol/L) and wrapped in an editable `Field`. A `None` factor indicates that the 
    condition's units (e.g., ppm) lack a straightforward mol/L conversion, rendering 
    the concentration read-only (i.e., the `field` property is set to None).

    Args:
        line (Line): The parsed line representing the aqueous species constraint.
        minerals (set[str]): A set of known mineral names defined in the configuration, 
                             used to detect mineral-equilibrium constraints.
        factor (float | None): Input value per standard mol/L. 
                               Pass None if the units cannot be safely converted.

    Returns:
        Aqueous: A standardized constraint object containing the constraint type, 
                 the editable Field (if applicable), and any associated phase.
    """
    tokens = line.tokens
    second = tokens[1]
    option = tokens[2].lower() if len(tokens) > 2 else None
    
    # e.g. Na+ charge 
    if second.lower() == "charge":
        return Aqueous("charge")
    
    # e.g. Cl- Halite
    if second in minerals:
        return Aqueous("mineral", associated=second)
    if parse_number(second) is None:
        # e.g. "HCO3- CO2(g) 3.15E-4": equilibrium with a gas partial pressure
        # e.g. CH4(aq) CH4(g) 0.10
        return Aqueous(
            "gas", number_field(line, 2) if len(tokens) > 2 else None, second
        )

    # e.g. Ca++ 1.0e-3 activity
    if option == "activity":
        return Aqueous("activity", number_field(line, 1))

    # Units factor only works for free and total
    # e.g. Ca++ 1.0e-3 species / Ca++ 1.0e-3 total
    constraint = "free" if option == "species" else "total"
    
    # e.g. Ca++ 1.0e-3
    return Aqueous(constraint, number_field(line, 1, factor) if factor else None)
