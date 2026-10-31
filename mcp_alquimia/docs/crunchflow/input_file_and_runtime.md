# CrunchFlow input and RUNTIME

## Keyword blocks

Each keyword block starts with its keyword (case insensitive) on a line of its own and ends
with `END` on a line of its own. Blank lines inside a block are ignored. Lines beginning with
`!` are comments. Species, mineral and gas names are case-sensitive (e.g., CO versus Co).

The chemistry blocks are PRIMARY_SPECIES, SECONDARY_SPECIES, GASES, MINERALS,
AQUEOUS_KINETICS, ION_EXCHANGE, SURFACE_COMPLEXATION, CONDITION, INITIAL_CONDITIONS and
TEMPERATURE. Blocks for flow, transport, discretization and output are not covered here.

## Space and time units

```
time_units   unit
space_units  unit
```

A unit setting applies only to the keyword block it appears in.

| Time unit | Also accepted |
|---|---|
| years (default) | year |
| days | day |
| hours | hour |
| minutes | minute |
| seconds | second |

| Space unit | Also accepted |
|---|---|
| meters (default) | meter, m |
| kilometers | kilometer, km |
| centimeters | centimeter, cm |
| millimeters | millimeter, mm |
| micrometers | micrometer, micron, microns, um |

## RUNTIME: chemistry-related keywords

### database

Syntax: `database [filename]` (up to 132 characters). Default: `datacom.dbs`. May also be
given in a DATABASE keyword block.

### database_sweep

Syntax: `database_sweep logical`. Default: false.
When true, sweeps the whole thermodynamic database and loads every species and mineral that
can be written with the chosen primary species, ignoring the user's secondary species, gases
and minerals. Each geochemical condition is then speciated and the code stops; the loaded lists
are written to `<input prefix>.out` to copy into the input file. Supersedes `speciate_only`.

### debye-huckel

Syntax: `debye-huckel logical`. Default: true.
true: extended Debye-Hückel (Helgeson) activity coefficients. false: activity coefficients
equal to 1.

### lag_activity

Syntax: `lag_activity logical`. Default: true.
When true, activity coefficients are updated only at the start of each time step, from the
previous step's ionic strength, outside the Newton loop. Usually accurate and converges better;
may be less accurate where ionic strength contrasts are large, depending on the time step.

### master

Syntax: `master name`. Default: O2(aq) if present, otherwise H+, otherwise species 1.
The species to which `time_tolerance` is applied for time step control. Normally O2(aq) for
redox problems and H+ otherwise. The name is case-sensitive.

### speciate_only

Syntax: `speciate_only logical`. Default: false.
When true, speciates the geochemical conditions and stops without reactive transport, using
the user's lists of secondary species and minerals (unlike `database_sweep`).
