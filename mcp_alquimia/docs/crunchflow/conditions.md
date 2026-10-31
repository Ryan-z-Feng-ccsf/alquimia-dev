# CrunchFlow conditions

## CONDITION blocks

A `CONDITION <label>` block defines one geochemical condition: the concentrations of the
primary species and minerals used in a distribution-of-species calculation. Blocks may repeat.
A condition becomes an initial condition, boundary condition or source term only after it is
read and processed. Every primary species in PRIMARY_SPECIES must be constrained in every
condition.

### units

`units <concentration units>` applies only to its condition. Default: molality.

| Units | Keyword |
|---|---|
| moles/kg water | mol/kg |
| millimoles/kg water | mmol/kg |
| micromoles/kg water | umol/kg, micromol/kg |
| parts per million | ppm |

ppm uses the molecular weight of the primary species itself (for NO3-, of NO3-, not N).

### equilibrate_surface

On its own line, partitions every total-concentration constraint between the aqueous phase and
the solid (surface hydroxyl sites and exchangers); it does not partition mass through mineral
precipitation or dissolution. It can also be given per species (below). Without it, a total
concentration refers to the aqueous phase only, and exchanger and surface concentrations are
calculated from it.

### temperature

Syntax: `temperature value`. Overrides the TEMPERATURE keyword block for this condition.

### set_porosity

Syntax: `set_porosity value`. Overrides the POROSITY block. Works only if `fix_porosity` is set
in POROSITY; otherwise porosity is 1 minus the sum of the mineral volume fractions.

### set_saturation

Syntax: `set_saturation value`. Overrides the RUNTIME value, otherwise 1.0.

## Aqueous species constraints

| Constraint | Use | Example |
|---|---|---|
| Total concentration (default) | Mole balance on total aqueous, or total aqueous + sorbed | `Na+ 0.001` |
| Species concentration | Fix one primary species concentration | `Na+ 0.001 species` |
| Species activity | Fix one primary species activity | `Na+ 0.001 activity` |
| pH | Fix the hydrogen ion activity | `pH 7.8` |
| Gas | Equilibrate a primary species with a gas | `O2(aq) O2(g) 0.20` |
| Mineral | Equilibrate a primary species with a mineral | `O2(aq) Pyrite` |
| Charge | Charge balance the solution on a primary species | `Na+ charge` |

### Total concentration

`<PrimarySpeciesName> <Concentration> [total]`. Over the aqueous phase only (default):

$$T_j = C_j + \sum_{i=1}^{N_s} \nu_{ij} C_i$$

With `equilibrate_surface`, globally or after the value, exchange species $C_k$ and surface
complexes $C_l$ are included:

$$T_j = C_j + \sum_{i=1}^{N_s} \nu_{ij} C_i + \sum_{k=1}^{N_{exs}} \nu_{kj} C_k
+ \sum_{l=1}^{N_{surf}} \nu_{lj} C_l$$

```
Am+++        1.00e-10  equilibrate_surface
Np++++       1.00e-10  equilibrate_surface
Pu++++241    1.00e-10  equilibrate_surface
K+           8.70e-5
Mg++         2.06e-5
```

Here Am, Np and Pu are partitioned onto exchange and surface sites; K and Mg stay entirely
aqueous.

### Species concentration and activity

```
<PrimarySpeciesName> <Concentration> species
<PrimarySpeciesName> <Concentration> activity
pH  7.8
```

pH needs no trailing `activity`.

### Gas

`<primary species name> <gas name> <partial pressure (bars)>`. No fugacity corrections, so
partial pressure equals fugacity. Atmospheric oxygen: `O2(aq)  O2(g)  0.20`.

### Mineral

`<primary species name> <mineral name>`. The mineral activity is 1 (pure phase). Unlike
PHREEQC, no reaction path is followed; the mineral constraint is solved together with the other
constraints, with no path dependence.

### Charge

`<primary species name> charge`. Balancing on an anion when the solution is already negative
(or a cation when positive) drives the balancing species toward 0; the code then stops with an
error.

## Solid phase inputs

Boundary conditions and source terms with only aqueous inflow can omit minerals. A mineral
loaded in MINERALS but absent from a condition gets volume fraction 0 and a default bulk surface
area of 100 m$^2$ mineral/m$^3$ porous medium, so it can form if supersaturated.

### Mineral volume fractions and surface area

```
<name of solid phase> <volume fraction> [bulk_surface_area | specific_surface_area] <value>
```

`bulk_surface_area` (`bsa`, m$^2$ mineral/m$^3$ porous medium) is the default when the option is
omitted; `specific_surface_area` (`ssa`, m$^2$/g). A bulk surface area gives

$$A_{specific} = \frac{A_{bulk} V_m}{\phi_m MW_m}$$

with molar volume $V_m$, initial volume fraction $\phi_m$ and molecular weight $MW_m$.

Bulk surface area evolves as

$$A_{bulk} = A_{bulk}^{initial}\left(\frac{\phi_m}{\phi_m^{initial}}\right)^{2/3}
\left(\frac{\phi}{\phi^{initial}}\right)^{2/3} \quad\text{(dissolution)}$$

$$A_{bulk} = A_{bulk}^{initial}\left(\frac{\phi}{\phi^{initial}}\right)^{2/3}
\quad\text{(precipitation)}$$

where $\phi$ is porosity and $\phi_m$ the mineral volume fraction. A precipitating secondary
mineral keeps its initial bulk area while precipitating; if it later dissolves, an arbitrary
initial volume fraction of 0.01 is used.

Specific surface area, preferred for precipitating secondary phases, makes the area follow the
volume fraction:

$$A_{bulk} = \frac{\phi_m A_{specific} MW_m}{V_m}$$

For a mineral initially absent, give a threshold volume fraction used until the computed volume
fraction exceeds it:

```
<name of solid phase> 0.0 specific_surface_area <value> <threshold volume fraction>
Calcite  0.0  specific_surface_area  2.0  0.0001
```

### Surface hydroxyl concentrations

Required in every condition when SURFACE_COMPLEXATION is used (no default): the site density
$\rho_{sites}$ in mol sites/m$^2$ mineral, combined with the specific surface area:

$$C_{sites} = \frac{\rho_{sites} A_{specific} MW_m \phi_m}{V_m}$$

```
<Surface complex>   Value
```

### Exchanger concentrations

The exchanger name must match one in ION_EXCHANGE. CEC is in equivalents per gram:

```
Exchanger   -cec   Value
```

Exchange on bulk material (no `on <mineral>`): the CEC is per gram of solid, and a solid
density is needed, given one of three ways:

```
SolidDensity  Value                    ! bulk solid density, kg/m3
SolidDensity  -ss_ratio  Value         ! solid:solution ratio, g/kg water (~g/L)
SolidDensity  CalculateFromMinerals    ! from the sum of mineral volume fractions
```

Exchange on a specific mineral: the CEC is per gram of that mineral, sites follow its volume
fraction, and SolidDensity is ignored. Porosity and liquid saturation still affect the result.
Mineral densities come from database molar volumes and molecular weights.

## INITIAL_CONDITIONS

Required (except with `speciate_only` or `database_sweep`). Assigns conditions to grid cells:

```
condition_name JX-JX [JY-JY] [JZ-JZ] [fix]
```

Later lines overwrite earlier ones. `fix` holds the condition for the whole run and goes
right after the last coordinate range. Condition names are checked, and every grid cell must be
covered.

```
INITIAL_CONDITIONS
quartz_zone        1-42    1-42
portlandite_zone   22-33   1-42
nonreactive_zone   42-42   42-42   fix
END
```

## TEMPERATURE

Sets temperature block by block through geochemical conditions or from a file.

### set_temperature

Syntax: `set_temperature value`. Default: 25 °C. Global temperature, overridden by a
condition's `temperature` where that condition is used as an initial condition.
