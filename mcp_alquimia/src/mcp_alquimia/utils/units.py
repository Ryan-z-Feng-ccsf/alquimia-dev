"""Engine-independent units exchanged with the model, and the conversions they need.

Engine-specific input syntax (e.g. PFLOTRAN's cm^2/cm^3 or CrunchFlow's mmol/kg)
stays with each engine's reader in the chemistry package.
"""

# Shared unit strings, written once.
MOLARITY = "mol/L water"  # Alquimia's state unit for total_mobile
MINERAL_VOLUME_FRACTION = "m^3 mineral/m^3 bulk"

# Units of the chemistry that get_chemistry reports and setup's request.chemistry takes.
CHEMISTRY_UNITS = {
    # Aqueous concentrations (molarity), matching Alquimia's total_mobile.
    # CrunchFlow's mol/kg is converted with molar_to_molal_factor.
    "total": MOLARITY,                # Total dissolved concentration of a component
    "free": MOLARITY,                 # Free (uncomplexed) primary species concentration
    "log": MOLARITY,                  # Logarithmic concentration value

    # Master variables and thermodynamic activities (Standard scales)
    "pH": "pH",                       # Negative base-10 log of Hydrogen ion activity
    "pe": "pe",                       # Negative base-10 log of electron activity (redox)
    "activity": "activity",           # Thermodynamic activity of an aqueous species

    # Gas phase
    "gas": "bar",                     # Gas partial pressure

    # Solid phase / Mineral properties
    "volume_fraction": MINERAL_VOLUME_FRACTION,  # Volume of mineral per unit of total bulk volume
    "surface_area": "m^2 mineral/m^3 bulk",      # Mineral specific surface area per unit of bulk volume

    # Reaction Kinetics
    "rates": "mol/m^2/s",             # Kinetic reaction rate per unit of mineral surface area
}

# Units of every field in setup, initialize, react and get_results responses.
RESPONSE_UNITS = {
    "temperature": "degC",
    "density": "kg/m^3",
    "pressure": "Pa",
    "volume": "m^3",
    "time": "s",
    "dt": "s",
    "aqueous_total": MOLARITY,
    "immobile_total": "mol/m^3 bulk",
    "mineral_volume_fraction": MINERAL_VOLUME_FRACTION,
}


def molar_to_molal_factor(density: float) -> float:
    """Factor converting mol/L water to mol/kg water.

    Args:
        density: Water density in kg/m^3.

    Returns:
        1000 / density, i.e. the litres per kilogram of water.
    """
    return 1000.0 / density
