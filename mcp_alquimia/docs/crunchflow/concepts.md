# CrunchFlow chemistry concepts

## Units

All distances are in meters. Mineral reaction rates are input in mol m$^{-2}$ s$^{-1}$ (converted
internally to mol m$^{-2}$ yr$^{-1}$). Diffusion coefficients are in m$^2$ s$^{-1}$. All other
references to time (plot files, time steps) are in years, unless `time_units` is set in a
keyword block. Aqueous concentrations are in mol kg$^{-1}$ water (molality). Gas partial
pressures are in bars.

## Change of basis and total concentrations

The choice of primary species is not unique; any mathematically equivalent set can be used.
Changing the basis (writing the reactions differently) changes the total concentrations of
some components. For example, with H2O, H+ and Al+++ as primary species versus H2O, H+ and
Al(OH)4- as primary species:

$$U_{H_2O} = C_{H_2O} - 4C_{Al^{+++}} + C_{OH^-}$$
$$U_{H^+} = C_{H^+} + 4C_{Al^{+++}} - C_{OH^-}$$
$$U_{Al(OH)_4^-} = C_{Al(OH)_4^-} + C_{Al^{+++}}$$

so the total concentrations of H2O and H+ change even though the total Al is the same.

## Mineral reaction rate laws

Rate laws assume surface reaction control and are based loosely on transition state theory
(Lasaga, 1981; Aagaard and Helgeson, 1982; Lasaga, 1984). The ion activity product of the
mineral dissolution reaction is

$$Q_s = \prod_{j=1}^{N_c} a_j^{\nu_{js}}$$

where $a_j$ are the activities of the primary species in the dissolution reaction. Parallel
rate laws are summed to give the overall rate for a mineral:

$$r_s = -A_s \left\{ \sum_{l=1}^{N_{rs}} k_l \left( \prod_{i=1}^{N_c+N_x} a_i^{p^s_{il}} \right) \right\}
\left[ 1 - \left( \frac{Q_s}{K_s} \right)^M \right]^n$$

- $k_l$: far-from-equilibrium rate constant of the $l$th parallel reaction.
- $p^s_{il}$: reaction order with respect to species $i$ in the $l$th parallel reaction
  (catalysis or inhibition, most commonly by H+ or OH-).
- $K_s$: equilibrium constant; $N_{rs}$: number of parallel reactions.
- $A_s$: mineral surface area in the rock matrix or fracture.
- $n$, $M$: nonlinear dependence on the affinity term, normally from experiments.

Example, kaolinite (Nagy, 1995) with H+ and OH- dependent rates:

$$r_{kaol} = -A_{kaol}\left\{ k_{H^+} a_{H^+}^{0.17} + k_{OH^-} a_{OH^-}^{0.54} \right\}
\left[ 1 - \frac{Q_{kaol}}{K_{kaol}} \right]$$

A rate constant large relative to transport rates gives local-equilibrium behavior.

### Temperature dependence (Arrhenius)

$$k = k_{25} \exp\left[ \frac{-E_a}{R} \left( \frac{1}{T} - \frac{1}{298.15} \right) \right]$$

$E_a$ is the activation energy, $k_{25}$ the rate constant at 25 °C, $T$ in kelvin. To input a
rate constant directly at temperature, give it as the 25 °C value and set the activation
energy to 0.

### Surface area versus porosity

So that $A_s \to 0$ as porosity $\phi \to 0$:

$$A_s(t) = A_s^{\circ} \left( \frac{\phi(t)}{\phi^{\circ}} \right)$$

where $A_s^{\circ}$ and $\phi^{\circ}$ are the initial surface area and porosity. See also the
bulk and specific surface area options in [conditions.md](conditions.md).

## Choice of primary and secondary species

The primary species define the independent chemical components. The user chooses which
secondary species to include. One must include, among the primary or secondary species, the
species used to write the reaction in the (reformatted EQ3/EQ6) database. For example, to
include aluminum, Al+++ must appear as a primary or secondary species, because it is the basis
species in the database. Secondary species that cannot be written entirely in terms of the
chosen primary species must not be included. Check the database to see which species write
the reactions of a species of interest.

H2O is a special case. To fix its activity at 1, leave it out of the primary and secondary
species lists; reactions are still balanced as if it were present. To carry a mass balance on
H2O, list it as a primary species; its activity is then its mole fraction in solution.

## Redox reactions

All redox reactions are written as whole-cell reactions: electrons are not used as primary or
secondary species. O2(g) is the redox basis species in the EQ3/EQ6 database. A gas cannot be a
primary species, because gases are not in the mass balances; use O2(aq) or another redox couple
instead (for example Fe++/Fe+++ or HS-/SO4--). O2(g) is loaded automatically when O2(aq) is a
primary or secondary species.

## Acid-base reactions

No special treatment is needed except in initialization, where pH is normally fixed or
calculated rather than an H+ concentration. After initialization, H+ is treated like any other
component.

## Activity coefficient model

Either unit activity coefficients or the extended Debye-Hückel model (selected with
`debye-huckel` in RUNTIME):

$$\log \gamma_i = -\frac{A z_i^2 \sqrt{I}}{1 + B \mathring{a}_i \sqrt{I}} + \dot{b} I$$

Temperature-dependent coefficients come from the EQ3 database.

## Initialization procedure

Initial primary species concentrations in each geochemical condition can be set by:

- a mass balance on a total concentration (e.g., aluminum),
- a charge balance on a species (e.g., Cl-),
- a mineral equilibrium constraint (e.g., H+ in equilibrium with calcite),
- a fixed gas partial pressure (e.g., CO2(g)),
- a fixed activity (e.g., pH),
- a fixed species concentration,
- surface hydroxyl site density per mole of mineral plus the mineral's specific surface area
  (m$^2$/g), which sets the surface complex concentrations. If the mineral is initially absent,
  its surface hydroxyl concentration is set to a small number ($10^{-10}$ mol/kg) and builds up
  only once the mineral precipitates.

The input syntax for each constraint is in [conditions.md](conditions.md).

## Temperature and the thermodynamic database

Unless another database is given, CrunchFlow uses equilibrium constants at 0, 25, 60, 100,
150, 200, 250 and 300 °C along the water saturation curve and fits a polynomial to them, so log K
can be evaluated at any temperature between 0 and 300 °C. Other pressures and temperatures need a
user-supplied database in the same format. Temperature also affects the rate constants (Arrhenius,
above), diffusion coefficients and fluid density.
