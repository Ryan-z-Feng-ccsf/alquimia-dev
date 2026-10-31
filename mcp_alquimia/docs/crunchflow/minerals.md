# CrunchFlow MINERALS

MINERALS covers any solid-aqueous heterogeneous reaction. Each reaction is the dissolution of
one mole of the solid (coefficient -1). All mineral reactions are kinetic, except in
initialization, where equilibrium with a solid can be imposed as a constraint. Equilibrium
during a run is approximated with rate coefficients large relative to transport rates.

Each mineral needs two database entries: a thermodynamic entry and a kinetic entry.

## Thermodynamic database entry

```
'MineralName'  <molar volume (cm3/mol)>  <n species>  <coeff> 'Species' ...  <log K array>  <molecular weight>
```

```
'Quartz'  22.6880 1  1.00 'SiO2(aq)'  -4.6319 -3.9993 -3.4734 -3.0782 -2.7191 -2.4378 -2.2057 -2.0168  60.0843
'Calcite' 36.9340 3 -1.00 'H+' 1.00 'Ca++' 1.00 'HCO3-'  2.2257 1.8487 1.3330 0.7743 0.0999 -0.5838 -1.3262 -2.2154  100.0872
```

## Kinetic database entry

The kinetic section begins with the unquoted line `Begin mineral kinetics`. Every reaction
needs an entry unless `generic_rates` is set in RUNTIME, even if some values are unused. Each
entry starts with a line beginning with `+`, then the solid name (which must match the input
file and the thermodynamic entry), then:

```
+----------------------------------------------------
Calcite
label = <name>
type = <type of reaction>
rate(25C) = <log10 rate at 25C, mol/m2/s>
activation = <activation energy (kcal/mole)>
```

Several entries for the same mineral with different labels are parallel reactions whose rates
are summed (e.g., pH-independent plus pH-dependent calcite dissolution).

### Rate law types

`type = tst | monod | irreversible | PrecipitationOnly | DissolutionOnly`

#### tst

$$R = A_m k_m \exp\left[\frac{-E_a}{RT}\right] \prod a_i^n
\left[1 - \exp\left(m_2 g^{m_3}\right)\right]^{m_1},
\qquad g \equiv \frac{\Delta G}{RT} = \ln\left[\frac{Q}{K_{eq}}\right]$$

$A_m$: mineral surface area; $k_m$: intrinsic rate constant (mol/m$^2$/s); $E_a$: activation
energy (kcal/mol); $Q_m$: ion activity product; $K_{eq}$: equilibrium constant;
$\prod a_i^n$: catalysis or inhibition by solution species, acting far from equilibrium
($Q_m/K_{eq}$ small). The far-from-equilibrium dependences follow the activation energy:

```
dependence :  <species name> <exponent> <species name> <exponent> ...
```

Example with pH-independent and pH-dependent calcite dissolution:

```
+----------------------------------------------------
Calcite
label = default
type  = tst
rate(25C) = -6.19
activation = 15.0  (kcal/mole)
dependence :
+----------------------------------------------------
Calcite
label = h+
type  = tst
rate(25C) = -3.00
activation = 15.0  (kcal/mole)
dependence :  H+ 1.0
```

Affinity dependence (Burch et al., 1993; Hellmann and Tisserand, 2006), in the more familiar
form

$$R = \left[1 - \left(\frac{Q}{K_{eq}}\right)^{m_2}\right]^{m_1}$$

is set in the database with `AffinityDependence = m1 m2 m3`. It is only possible in the
database and affects tst, PrecipitationOnly and DissolutionOnly. Albite (Hellmann and
Tisserand, 2006) as two parallel laws:

```
Albite
    label = HellmannFFE
    type = tst
    rate (25C) = -11.9897
    activation = 0.00 (kcal/mol)
    dependence :
    AffinityDependence = 1.00   0.0000798   3.81
+---------------------------------------------------------------
Albite
    label = HellmannCTE
    type = tst
    rate (25C) = -13.743
    activation = 0.00 (kcal/mol)
    dependence :
    AffinityDependence = 1.17   1.00   1.00
+---------------------------------------------------------------
```

#### monod

After the activation energy:

```
monod_terms :  <electron donor or acceptor> <half-saturation constant> ...
inhibition :   <inhibitor species> <inhibition constant> ...
```

Constants are in mol/kg water. Each Monod term is

$$R_m = k_{max}\left(\frac{C_i}{C_i + K_{half}}\right)$$

and each inhibition term, as printed in the manual, is

$$I_m = \frac{K_{in} + C_i}{K_{in}}$$

where $K_{in}$ is the inhibition constant and $C_i$ the inhibiting species. Monod reactions
are irreversible (equilibrium constants are not used). Dual Monod (electron donor and
acceptor) is the most common form.

```
+---------------------------------------------------
CH2O
label = aerobic_respiration
type  = monod
rate(25C) = -9.699
activation = 0.0
monod_terms :  O2(aq)  15.0e-06
inhibition :
+---------------------------------------------------
CH2O
label = denitrification
type  = monod
rate(25C) = -10.00          !  Regnier and Steefel (1999)
activation = 0.0  (kcal/mole)
monod_terms :  NO3-  45.0e-06
inhibition :  O2(aq)  30.0e-06
```

#### irreversible

No saturation-state dependence:

$$R_m = A_m k_m \exp\left(\frac{-E_a}{RT}\right)\prod a_i^n$$

```
+---------------------------------------------------
Iron
label = tce
type  = irreversible
rate(25C) = -6.92
activation = 15.0 (kcal/mole)
dependence :   TCE(aq) 1.0
```

#### PrecipitationOnly / DissolutionOnly

Suppress dissolution or precipitation respectively. Used together to give one mineral
different rate laws for precipitation and dissolution:

```
KaoliniteYang
  label = Yang
  type = PrecipitationOnly
  rate(25C) = -13.47
  activation = 0.0 (kcal/mole)
  dependence :
  AffinityDependence = 1.00   2.07468   -1.00000
+---------------------------------------------------
KaoliniteYang
  label = Yang
  type = DissolutionOnly
  rate(25C) = -12.9393
  activation = 0.0 (kcal/mole)
  dependence :
  AffinityDependence = 1.00   0.5   1.00
+---------------------------------------------------
```

## Input file entries

The simplest entry is the mineral name alone on a line, which loads the kinetic entry labeled
`default`. Options that may follow the name:

```
-label <label>
-rate <rate>
-activation <activation energy>
-suppress_precipitation <epsilon>
-local_equilibrium
-associate <mineral_name>
```

- `-label <label>`: load the kinetic entry with this label.
- `-rate <rate>`: override the database rate; log10 of the rate in mol/m$^2$/s at 25 °C.
- `-activation <Ea>`: override the activation energy, in kcal/mol.
- `-suppress_precipitation <epsilon>`: suppress precipitation; $\epsilon$ is the absolute
  undersaturation, as log(Q/Keq), at which the dissolution rate is half its maximum. For linear
  kinetics a continuously differentiable approach to equilibrium is used:
  $R_d = 0.5 - 0.75\xi + 0.25\,\xi^3/\epsilon^3$, with $\xi = \log(Q/K_{eq}) + \epsilon$.
  For nonlinear kinetics $\epsilon$ is not used; precipitation is simply suppressed.
  DissolutionOnly in the database is normally preferred.
- `-local_equilibrium`: keep the initial reactive surface area fixed while the mineral is
  present, instead of reducing it as the volume fraction goes to 0. Combined with a log rate of
  about -3.00 to -5.00 and a large surface area, this gives equilibrium within about $10^{-4}$
  log(Q/Keq) units. Rates set arbitrarily high cause poor convergence (stiffness).

  ```
  MINERALS
  Portlandite       -rate -3.00  -local_equilibrium
  Ca-Oxalate        -rate -3.00  -local_equilibrium
  END
  ```

- `-associate <mineral_name>`: attach a second reaction stoichiometry and equilibrium
  constant to the same mineral, so only the associated mineral's volume fraction and surface
  area are updated. For example, reductive dissolution of FeOOH by Geobacter ("Iron_DIRB") and
  abiotic reduction by sulfide ("Iron_H2S"):

  ```
  MINERALS
  Iron_DIRB
  Iron_H2S            -associate Iron_DIRB
  END
  ```

A database sweep may list minerals with no kinetic entry yet, and phases (e.g., garnets) that
are supersaturated at 25 °C but only form at high temperature, so the swept list must be
edited.
