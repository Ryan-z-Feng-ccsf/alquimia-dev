CHEMISTRY
=========
Specifies geochemistry details for multicomponent transport.

For help creating a list of SECONDARY_SPECIES, GAS_SPECIES, and MINERALS given a list of PRIMARY_SPECIES, use the `reaction species finder <https://reactions.gentleplants.com/>`__.

Required Cards:
---------------

PRIMARY_SPECIES
 List of primary aqueous or basis species for which concentrations will be
 solved.

Optional Cards:
---------------

**Basic Settings**

ACTIVE_GAS_SPECIES
 List of gas species where the gas phase mass is included in the
 mass conservation equations.

ACTIVITY_COEFFICIENTS <algorithm string> <frequency string>
 Specifies algorithms for calculating activity coefficients.
 (default = LAG TIMESTEP)

 - Algorithm:

  - OFF:
     Disables the calculation of activity coefficients. All activity
     coefficients are assume to be 1.
  - LAG:
     The update of activity coefficients is lagged by a
     TIMESTEP or NEWTON_ITERATION.
  - NEWTON
     Ionic strength and activity coefficient calculation iterates to
     convergence. **Computationally expensive.**

 - Update Frequency:

  - TIMESTEP
     Update after every time step
  - NEWTON_ITERATION
     Update after every Newton iteration

ACTIVITY_H2O, ACTIVITY_WATER
 Calculate activity of water.
 (default = False)

AQUEOUS_DIFFUSION_COEFFICIENTS
 List of primary species and diffusion coefficients for which the default
 aqueous diffusion coefficient will be overwritten. Species-dependent
 diffusion is not supported for simulations with aqueous complexation.
 (default units [m\ :sup:`2`\/s])

CALCULATE_INITIAL_POROSITY
 Calculate the initial porosity as the sum of the initial mineral volume
 fractions.

DATABASE <string>
 Path/filename for reaction database.  For descriptions of the database format, see the brief :ref:`geochemical-database`, or :ref:`thermodynamic-database`, a more indepth description from the theory guide.

DECOUPLED_EQUILIBRIUM_REACTIONS
 List of equilibrium reaction species from the database to be decoupled,
 likely so that the associated primary and secondary species in the
 database can be used in kinetic reactions.

ELECTRICAL_CONDUCTIVITY_METHOD
 Specifies the method used to calculate fluid electrical conductivity
 (e.g., for use in output, geophysics, etc.). Options include: LINEAR,
 PSEUDO_LINEAR, MOBILITY.

GAS_DIFFUSION_COEFFICIENTS
 List of gas species and diffusion coefficients for which the default
 gas diffusion coefficient will be overwritten.
 (default units [m\ :sup:`2`\/s])

:ref:`general-reaction-card`
 Parameters for general forward/reverse kinetic reactions.

:ref:`immobile-decay-reaction-card`
 Parameters for decay of immobile species (e.g. biomass).

IMMOBILE_SPECIES
 List of immobile species that are included as primary dependent variables
 (e.g. biomass, NAPL, customization in the reaction sandbox).

LOG_FORMULATION
 Flag for solving Newton-Raphson equations with derivatives computed with
 respect to the logarithm  (base 10) of the concentrations. Should not be
 used for linear equations (e.g. conservative tracers).

MAX_DLNC <float>
 Specifies maximum change in log concentration for a Newton Raphson iteration.
 Changes in concentration larger than this value will be truncated to this
 value.
 (default = 5)

MAX_RELATIVE_CHANGE_TOLERANCE <float>
 Specifies the maximum relative change in free ion concentration allowed for
 convergence (i.e. ||(c^k+1-c^k)/c^k||_inf).
 (default = 1.e-12)

MAX_RESIDUAL_TOLERANCE <float>
 Specified the maximum residual allowed for a primary species for convergence
 (i.e. ||f(c^k+1)||_inf).
 (default = 1.e-12)

:ref:`microbial-reaction-card`
 Parameters for microbially-mediated reactions.

:ref:`mineral-kinetics-card`
 Parameters for kinetic mineral precipitation/dissolution reactions.

MINERALS
 List of minerals.

MINIMUM_POROSITY <float>
 Float specifies minimum porosity to which porosity is truncated if
 below that value. (default = 0)

MOLAL, MOLALITY
 Print concentrations as molalities instead of molarities.

NO_BDOT
 Use Debye-Huckel formulation without B-dot.

NO_CHECKPOINT_ACT_COEFS
 Disables checkpointing of activity coefficients. Since activity
 coefficients are a function of the solution ionic strength from the
 previous time step, the original and restarted solutions will not
 match identically if such checkpointing is disabled.

NO_RESTART_MINERAL_VOL_FRAC
 Disables the reading of mineral volume fractions on restart. Mineral
 volume fractions specified in the input file will be used instead.

:ref:`nucleation-kinetics-card`
 Specifies nucleation reactions and associated parameters.

:ref:`output-chemistry-card`
 Specifies parameters for output.

:ref:`override-mineral-mass-action-card`
 Specifies reaction expressions and log Ks that will override what is
 defined in the database.

PASSIVE_GAS_SPECIES
 List of gas species used as reference species (e.g. reaction database,
 transport constraints, basis swapping, etc.).

:ref:`radioactive-decay-reaction-card`
 Parameters for kinetic radioactive decay reactions.

:ref:`reaction-sandbox-card`
  Reaction sandbox for custom, user-defined reactions.

SECONDARY_SPECIES
 List of secondary aqueous species or complexes.

:ref:`sorption-card`
 Sorption reactions.

TRUNCATE_CONCENTRATION <float>
 Specify a minimum concentration below which free-ion concentration may not
 fall.  (Due to the molality/molarity conversion, the concentration may fall
 slightly below the prescribed value when the water density is less than 1000
 kg/m\ :sup:`3`\)

UPDATE_PERMEABILITY
 Update permeabilty after every time step.
 See :ref:`material-property-updates` in the :ref:`theory-guide`.

UPDATE_POROSITY
 Update porosity after every time step.  The default minimum porosity is 0,
 and one can make minimum porosity non-zero using MINIMUM_POROSITY.
 The initial porosity is calculated as a function of initial mineral volume
 fractions.
 See :ref:`material-property-updates` in the :ref:`theory-guide`.

UPDATE_TORTUOSITY
 Update tortuosity after every time step.
 See :ref:`material-property-updates` in the :ref:`theory-guide`.

USE_FULL_GEOCHEMISTRY
 Forces full geochemistry calculation even if only tracers are specified.

--------------------

**Expert Settings**

GEOTHERMAL_HPT
 Turns on the geothermal high pressure-temperature capability.

NO_CHECK_UPDATE
 Disables (pre)check on solution update.

Examples
--------

 ::

  CHEMISTRY
    PRIMARY_SPECIES
      H+
      HCO3-
      Ca++
    /
    SECONDARY_SPECIES
      OH-
      CO3--
      CO2(aq)
      CaOH+
      CaHCO3+
      CaCO3(aq)
    /
    MINERALS
      Calcite
    /
    MINERAL_KINETICS
      Calcite
        RATE_CONSTANT 1.d-13
      /
    /
    DATABASE ./calcite.dat
    LOG_FORMULATION
    ACTIVITY_COEFFICIENTS TIMESTEP
  END

 ::

  CHEMISTRY
    PRIMARY_SPECIES
      H+
      Ca++
      Cu++
      Mg++
      UO2++
      K+
      Na+
      HCO3-
      Cl-
      F-
      HPO4--
      NO3-
      SO4--
      Tracer
      Tracer2
    /
    SECONDARY_SPECIES
      OH-
      CO3--
      CO2(aq)
      CaCO3(aq)
      CaHCO3+
      CaSO4(aq)
      CaCl+
      CaCl2(aq)
      CaF+
      CaH2PO4+
      CaHPO4(aq)
      CaNO3+
      CaPO4-
      MgCO3(aq)
      MgHCO3+
      MgSO4(aq)
      MgCl+
      MgF+
      UO2(H2PO4)(H3PO4)+
      UO2(H2PO4)2(aq)
      UO2HPO4(aq)
      UO2H2PO4+
      UO2H3PO4++
      UO2PO4-
    /
    GAS_SPECIES
      CO2(g)
    /
    MINERALS
      Calcite
      Magnesite
      Dolomite
      Dolomite-dis
      Dolomite-ord
      Brucite
      Nesquehonite
      Gypsum
      Schoepite
      UO2CO3
      UO2(PO3)2
      (UO2)3(PO4)2
      (UO2)3(PO4)2.4H2O
      CaUO4
      UO2SO4
      UOF4
      UO3.2H2O
      UO3.0.9H2O(alpha)
      Saleeite
      Sylvite
      Metatorbernite
      Whitlockite
      Chalcanthite
      Brochantite
      Tenorite
      Malachite
      Fluorapatite
      Fluorite
      Hydroxylapatite
      Torbernite
    /
  :
    MINERAL_KINETICS
      Calcite
        RATE_CONSTANT 1.e-12 mol/cm^2-sec
      /
      Metatorbernite
        RATE_CONSTANT 2.e-17 mol/cm^2-sec
      /
    /
    SORPTION
      JUMPSTART_KINETIC_SORPTION
      SURFACE_COMPLEXATION_RXN
        MINERAL Calcite
        SITE >SOH 15.264 ! 20 m^2/g, por = 0.25
        COMPLEXES
          >SOUO2OH
          >SOHUO2CO3
        /
      /
    /
    DATABASE ../../../hanford.dat
    LOG_FORMULATION
    MAX_RELATIVE_CHANGE_TOLERANCE 1.d-10
    ACTIVITY_COEFFICIENTS NEWTON_ITERATION
    OUTPUT
      UO2++
      Tracer
    /
  END
