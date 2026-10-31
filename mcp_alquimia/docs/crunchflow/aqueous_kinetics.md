# CrunchFlow AQUEOUS_KINETICS

Aqueous (homogeneous) kinetic reactions must be written entirely in primary species. A species
that would be secondary at equilibrium has to be moved to PRIMARY_SPECIES, or the reaction is
not loaded.

Example, the Fe(II)/Fe(III) couple. If Fe++ oxidation by O2 is fast enough to be at
equilibrium, use primary species H+, Fe++, O2(aq) and list Fe+++ as a secondary species,
which loads

```
'Fe+++'  4  -0.50 'H2O'  0.25 'O2(aq)'  1.00 'Fe++'  1.00 'H+'  <log K array>
```

and Fe+++ contributes to the totals of Fe++, O2(aq) and H+. To treat the reaction
kinetically, list Fe+++ as a primary species too; its equilibrium reaction is then not loaded,
and Fe++ and Fe+++ behave like separate elements ("redox disequilibrium") until linked by a
reaction in AQUEOUS_KINETICS.

## Database format

The section follows the MINERALS section, begins with `Begin aqueous kinetics` and ends with
`End of aqueous kinetics`. Aqueous kinetics do not use the thermodynamic stoichiometry or
equilibrium constants; each entry carries its own:

```
<reaction label> <n parallel rate laws> '<ReactionType>' <n species> <coeff> 'Species' <coeff> 'Species' ... <log K>
```

followed by one line per parallel rate law. ReactionType is `tst`, `monod` or `irreversible`.
Prefix a species name with `tot_` to make the rate depend on its total concentration.

### tst

$$R_s = k_s\left(1 - \frac{Q_s}{K_{eq}}\right)\prod a_i^n$$

$k_s$ is in mol/kg water/yr. There is no surface area dependence and currently no temperature
dependence. Rate law line:

```
<rate (mol/kgw/yr)> <n species dependences> 'Species' <exponent> 'Species' <exponent> ...
```

Fe++ oxidation by O2 with pH-dependent and pH-independent parallel laws (Singer and Stumm,
1970):

```
FeII_oxidation 2 'tst' 5 1.0 'Fe+++' 0.500 'H2O' -0.25 'O2(aq)' -1.0 'Fe++' -1.0 'H+' 8.4887
   1.53e-06  3 'tot_Fe++' 1.0  'O2(aq)' 1.0  'H+' -2.0
   41.4848   2 'tot_Fe++' 1.0  'O2(aq)' 1.0
```

### monod

$$R_m = k_{max}\left(\frac{C_i}{C_i + K_{half}}\right)$$

$C_i$: electron acceptor or donor concentration; $K_{half}$: half-saturation constant
(mol/kg water), the concentration at which the rate is half of $k_{max}$. Rate law line:

```
<maximum rate> <n Monod terms> <acceptor or donor> <half-saturation constant> ...
```

Inhibition terms, as printed in the manual:

$$I_m = \frac{K_{in} + C_i}{K_{in}}$$

are given by the quoted string `'Inhibition'` followed on the same line by

```
<n inhibiting species> <SpeciesName> <inhibition constant> <SpeciesName> <inhibition constant> ...
```

### irreversible

$$R_s = k_s\prod a_i^n$$

No equilibrium constant is used. Useful for decay reactions and decay chains, e.g.
$^{241}$Pu → Am → Np:

```
Pu++++241_decay  1 'irreversible'  2 -1.0 'Pu++++241' 1.0 'Am+++'
   4.83E-2  1 'tot_Pu++++241' 1.0
Am+++_decay  1 'irreversible'  2 -1.0 'Am+++' 1.0 'Np++++'
   1.60E-3  1 'tot_Am+++' 1.0
```

## Input file entries

List the reaction labels from the database; no other options are allowed:

```
AQUEOUS_KINETICS
Am+++_decay
Pu++++241_decay
END
```
