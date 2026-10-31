# CrunchFlow species and gases

## PRIMARY_SPECIES

A list of primary (component, basis) species names, one per line; names are case-sensitive.
The list defines the chemical space of the problem. Every geochemical condition must specify
every primary species listed here. Unlike PHREEQC, which presumably sets unmentioned
concentrations to 0, CrunchFlow requires a consistent set of primary species for every cell
and boundary.

Different basis sets can describe the same problem and are mathematically equivalent given the
right constraints. Some totals do not change between them (total HCO3- equals total CO2(aq)),
while others do, including H+. A Fe++/Fe+++ redox couple is an alternative to Fe++ with O2(aq);
the definition of total Fe changes with the choice.

```
PRIMARY_SPECIES          PRIMARY_SPECIES
Na+                      Na+
K+                       K+
Ca++                     Ca++
H+                       H+
Al+++                    Al+++
Fe++                     Fe++
SiO2(aq)                 SiO2(aq)
HCO3-                    CO2(aq)
SO4--                    SO4--
Cl-                      Cl-
O2(aq)                   Fe+++
END                      END
```

Species in an aqueous kinetic reaction must all be primary species; see
[aqueous_kinetics.md](aqueous_kinetics.md).

### Database format

Database primary species follow the temperature line (first line) and 3 lines of
temperature-dependent Debye-Hückel parameters, one per line in free format:

```
'SpeciesName'  <Debye-Huckel size parameter>  <charge>  <molecular weight>
```

The block ends with `'End of primary' 0.0 0.0 0.0`.

## SECONDARY_SPECIES

Secondary species are in equilibrium with the primary species and are eliminated through mass
action expressions. In CrunchFlow, "secondary species" means equilibrium only; a kinetically
controlled species must be primary.

Input is a list, one per line, in the same format as PRIMARY_SPECIES. Names are case-sensitive
and must exist in the database.

```
SECONDARY_SPECIES
OH-
AlOH++
HCO3-
END
```

With `database_sweep` on, all possible secondary species are loaded, the user's list is
ignored, and the code stops after speciation. Recommended practice: run once with
`database_sweep`, copy the species list from `<input prefix>.out` into this block, prune it
if needed, and turn the sweep off.

### Database format

Each entry is the destruction of one mole of the secondary species (its coefficient is
implicitly -1):

```
'SpeciesName'  <n species in reaction>  <coeff> 'Species' <coeff> 'Species' ...
               <log K array>  <Debye-Huckel size>  <charge>  <molecular weight>
```

The number of (coefficient, species) pairs must equal the count, or a read error results.
The log K array length is given by the first line of the database, normally 8 points:

```
'temperature points' 8 0. 25. 60. 100. 150. 200. 250. 300.
'CO2(aq)' 3 -1.00 'H2O' 1.00 'H+' 1.00 'HCO3-' -6.5804 -6.3447 -6.2684 -6.3882 -6.7235 -7.1969 -7.7868 -8.5280 3.0 0.0 44.0098
```

The block starts after `'End of primary'` and ends with
`'End of secondary' 1 0. '0' 0. 0. 0. 0. 0. 0. 0. 0. 0. 0. 0.`

## GASES

A list of gases, one per line. For fully saturated problems, gases are used only to constrain
geochemical conditions (their mass is not tracked), so they need to be listed only for that
purpose. O2(g) is loaded automatically if O2(aq) is a primary or secondary species. For
unsaturated transport and gas-liquid partitioning, gases must be listed and a gas mass balance
is carried out. With `database_sweep`, relevant gases are found automatically and this block
is ignored.

A gas can be kept in equilibrium with the solution during a run by defining a fictitious
"mineral" whose equilibrium constant corresponds to the aqueous concentration in equilibrium
with the desired partial pressure.

### Database format

```
'Gas'  <molar volume (unused)>  <n species>  <coeff> 'Species' ...  <log K array>  <molecular weight>
```
