# Alquimia known issues

Behaviors of the engines as driven through Alquimia and this MCP server that can surprise a
user or give unexpected results. Each issue names the symptom, the engines affected, the cause
with source references, and a workaround. Paths: `IF-P` is
`alquimia/pflotran_alquimia_interface.F90`, `IF-C` is `alquimia/crunch_alquimia_interface.F90`,
`helpers.c` is `mcp_alquimia/engine_process/alquimia_engine_process_helpers.c`. PFLOTRAN
sources are under `build/install/include/pflotran/`, CrunchFlow sources under
`build/external/crunchflow/src/crunchflow/source/`.

## Settings that have no effect

### Temperature override has no effect; results report 25 °C

Engines: PFLOTRAN and CrunchFlow (verified by running calcite-vf-pc-pflotran and
calcite-vf-cc-crunch at 25 and 60 °C: identical results, `temperature_celsius` 25 in both).

`setup` accepts `temperature` and copies it into the Alquimia state, but `initialize`
overwrites it with the input file's value, and the MCP restores only porosity afterwards
(`helpers.c:195`). Water density is reset the same way.

- PFLOTRAN: `ProcessPFLOTRANConstraint` calls `ReactionPrintConstraint` (IF-P:1399), which
  in PFLOTRAN's NULL flow mode sets temperature to the 25 °C reference, density to the
  reference density and saturation to 1 (`reaction.F90:2189-2195`); these are copied back to
  the state (IF-P:1781-1783). Saturation is re-read from properties every step, so it is not
  affected.
- CrunchFlow: the named condition sets `t = tempcond(nco)` from the condition's `temperature`
  in the input file (IF-C:1555, 1743, 1781), which is copied back to the state
  (IF-C:2508-2509).

Even if the temperature reached the engine:

- PFLOTRAN runs isothermally: `use_isothermal = PETSC_TRUE` (IF-P:1032) unless the input has a
  NONISOTHERMAL card (IF-P:1176-1185), so log K values stay at 25 °C.
- CrunchFlow input files with `RunIsothermal true` keep log K at the input temperature:
  calcite-short, general-reaction, ion-exchange-valocchi, isotherms, surface-complexation-2.
  calcite-co2, calcite-vf and ch4-o2 evaluate log K from the database temperature fit
  (`keqcalc2.F90:88-96`).
- Both engines apply the Arrhenius factor to mineral rates from the current temperature
  even in isothermal mode (PFLOTRAN `reaction_mineral.F90:850-866`, CrunchFlow
  `reactionTope.F90:315-318`), so rates and log K would be inconsistent.

Workaround: none through `setup`. For CrunchFlow, the temperature must be changed in the
input file's condition (`temperature`) and `RunIsothermal` must be false; this path has not
been tested through the MCP. Do not report a temperature effect from an override.

### Porosity stays constant during a run

Engines: all. The MCP resets `state.porosity` to the setup value after every step
(`helpers.c:195`), like BatchChemDriver (`drivers/BatchChemDriver.c:609-610`). Mineral
dissolution or precipitation therefore never feeds back into porosity, and the returned
`porosity` is always the setup value. In PFLOTRAN the porosity written back by the interface
is an unassigned local variable (IF-P:374, 504, 1785), which is why drivers overwrite it.

### Rate constants, Kd, CEC and surface site densities cannot be changed

Engines: PFLOTRAN and CrunchFlow. The MCP always sets up the engine with `hands_off = true`
(`helpers.c:323`), so mineral and aqueous rate constants, isotherm Kd (and Langmuir/Freundlich
parameters), cation exchange capacity and surface site densities always come from the input
and database files (IF-P:1677-1740, IF-C:2391-2440). There is no setup field for them, and the
cfg keys `isotherm_kd`, `langmuir_b`, `freundlich_n`, `cation_exchange_capacity` and
`surface_site_density` are not read. Workaround: edit the input or database file.

### Pressure and volume overrides have no chemical effect

Engines: PFLOTRAN (`pressure_dependent = .false.`, IF-P:1053), CrunchFlow (pressure and volume
are not used, IF-C:2310, 2312).

## Failures that end a session

### A step that does not converge ends the session and loses that react call

Engines: PFLOTRAN and CrunchFlow. The MCP stops at the first non-converged step
(`helpers.c:460-468`) and the Python session manager drops the session on any error
(`mcp_alquimia/src/mcp_alquimia/session_manager.py:231-234`). Steps completed earlier in the
same `react` call are not returned. The MCP does not cut dt or retry.

- PFLOTRAN halves dt internally up to 10 times (`reaction_aux.F90:427`), then calls Fortran
  `stop` because `stop_on_rreact_failure` defaults to true (`reaction.F90:3669`,
  `reaction_aux.F90:430`): the engine process exits. A large dt alone is usually sub-stepped
  successfully.
- CrunchFlow has no internal sub-stepping (commented out, IF-C:785-791). A failed Newton solve
  returns `converged = false` (IF-C:847-853).

Workaround: start a new session with a smaller dt or fewer `max_steps` per `react` call, and
call `react` repeatedly.

### CrunchFlow: a mineral running out is reported as "did not converge"

When a step would make a mineral volume fraction negative, CrunchFlow needs a time step cut;
the interface only prints "time step cut required" and sets no status (IF-C:858-897), so the
MCP reports "Reaction step N of M did not converge" and ends the session. Workaround: use a
smaller dt as a mineral approaches exhaustion.

### Long react calls can time out

All `max_steps` of one `react` call run as a single native request, and the server waits at
most `--timeout` seconds (default 60) for it. A timeout ends the session. Workaround: fewer
`max_steps` per call.

### Bad input aborts the engine process instead of returning an error

- PFLOTRAN: unknown constraint types or a wrong number of constraints call `PrintErrMsg`,
  which exits the process (IF-P:1463-1468, 1541-1544). An unknown constraint name returns a
  proper error (IF-P:442-445).
- CrunchFlow: Fortran `STOP` on metadata size mismatches (IF-C:1128, 1149), isotherm count
  mismatch (IF-C:2410-2414) and unsupported exchange on bulk material (IF-C:2125-2137).

The session ends with little detail; check `engine_output.log` in the session run directory.

### An invalid initial_condition passes setup and fails at initialize

`initial_condition` is not validated at setup
(`mcp_alquimia/src/mcp_alquimia/case_catalog.py:293-297`). The `*-ac-*` cases name an empty
`driver_ic` condition and require `initial_condition` in the setup request; use a condition
name defined in the input file (for example `initial`). PFLOTRAN then fails at initialize with
"Could not find pflotran constraint" (IF-P:440-444) and the session ends.

## Results that look wrong

### PFLOTRAN: a species that starts at zero stays at zero

PFLOTRAN raises any component total at or below 1e-40 to 1e-40 for the solve, then restores
the initial value at the end of every step (`reaction.F90:3616-3629, 3725-3734`). A reaction
product initialized at 0 stays exactly 0, so the reaction appears not to happen. Workaround:
initialize products at a small positive value such as 1e-20 in the input constraint.

### pH is -100

Engines: PFLOTRAN (IF-P:660-664) and CrunchFlow (IF-C:1006-1011) report pH = -100 when H+ is
not a primary species. `has_pH` is still true. Treat pH below -50 as not available. ONNX
returns `pH: null`.

### Results contain only totals, at the end of each react call

`get_results` and `react` return `time` (seconds since initialize), `completed_steps`, `pH`,
`temperature_celsius`, `porosity`, `total_mobile`, `total_immobile` and
`mineral_volume_fraction` (`helpers.c:246-270`). Names are only in the setup metadata
(`primary_species`, `minerals`). Not returned: free-ion concentrations, activity coefficients,
saturation indices, mineral reaction rates, secondary species, surface sites and CEC. Only
the final state of each `react` call is kept. Workaround: for a time series, call `react`
repeatedly with a small `max_steps`.

### Engine-side output limitations

- PFLOTRAN: aqueous kinetic rates are not reported (IF-P:666-671); gases are not supported
  (`num_gases = 0`, IF-P:1088); only one ion exchange site is supported, more fails at setup
  (IF-P:247-255).
- CrunchFlow: the mineral saturation index is log10 for the first rate pathway only
  (IF-C:1029) and is -500 for monod or irreversible minerals (`reactionTope.F90:341-343`).

### CrunchFlow time units

The MCP dt is in seconds; CrunchFlow converts it to years of 365 days (IF-C:740). The input
file's `timestep_max` and `timestep_init` are not used; only the MCP dt controls the step.

### calcite-co2-crunch-cc default dt differs from the reference driver

The cfg says `timestep = 0.1 days`. The MCP reads it as 8640 s, while BatchChemDriver reads
0.1 s (`drivers/BatchChemDriver.c:124`). Results with default settings will not match
reference output produced by BatchChemDriver; pass `dt = 0.1` to reproduce it.

### A case is missing from list_cases

`ion-exchange-valocchi-ac-crunch.cfg` has the typo `timstep` and a `[material_properties]`
section instead of `[material]`, so it fails to load and is skipped with only a log warning
(`case_catalog.py:184-185`).

## ONNX surrogate models

- Temperature, pressure, density and volume are not inputs of any model; porosity and
  saturation are used only for unit conversion. Overrides have no effect.
- dt is ignored by inference (`onnx_alquimia_interface.c:2553`) but time still advances by
  dt; repeated steps apply the same map, not a time integration.
- There is no pH and no auxiliary output (`onnx_alquimia_interface.c:2683-2700`).
- Some lsurf model features are log10 values (for example `uranium_total = -6.68` in
  `models/lsurf/lsurf_model_*_float_64.json`) although the reported units are mol/L, and the
  ex8 models can return negative immobile values. Interpret values through the model JSON's
  feature names.
