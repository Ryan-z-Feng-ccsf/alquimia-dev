"""Read and patch the initial chemistry of PFLOTRAN and CrunchFlow input files.

An input is parsed into a lossless block tree (input_document), read per engine
(pflotran, crunchflow) into the chemistry model, and edited token by token.

Values are exchanged in engine-independent units and converted to each engine's
input units, so one request means the same on every engine:

- total/free concentrations: mol/L water, Alquimia's state unit (PFLOTRAN reads
  mol/L; CrunchFlow mol/kg, converted with the water density)
- surface area: m^2 mineral/m^3 bulk
- mineral rate constants: mol/m^2/s, linear (CrunchFlow and negative PFLOTRAN
  values are log10)
"""

from pathlib import Path
from typing import Any

from ..requests.setup import ChemistryRequest
from .crunchflow import read_crunchflow
from .input_document import InputDocument
from .model import Chemistry, Field
from .pflotran import read_pflotran

def read_document(document: InputDocument, condition: str, density: float) -> Chemistry:
    """Read one condition's editable chemistry from a parsed input.

    Args:
        document: Parsed PFLOTRAN or CrunchFlow input tree.
        condition: Constraint (PFLOTRAN) or Condition (CrunchFlow) name.
        density: Water density in kg/m^3, converting CrunchFlow's mol/kg to mol/L.

    Raises:
        ValueError: The input does not define the condition.
    """
    if document.engine == "PFloTran":
        return read_pflotran(document, condition)
    return read_crunchflow(document, condition, density)


def read_chemistry(
    path: Path, engine: str, condition: str, density: float
) -> dict[str, Any]:
    """Summarize an input's initial chemistry in engine-independent units (see utils.units.CHEMISTRY_UNITS).

    Raises:
        ValueError: The engine cannot be edited or the condition is not defined.
        OSError: The input cannot be read.
    """
    return read_document(
        InputDocument.read(path, engine), condition, density
    ).describe()


def patch_chemistry(
    path: Path, engine: str, condition: str, changes: ChemistryRequest, density: float
) -> None:
    """Write new constraint values, mineral amounts and rates into an input file.

    Every change is validated before the file is rewritten, so a rejected request
    leaves the file unchanged. Only the edited tokens change.

    Args:
        path: Input to rewrite in place; pass the staged copy, never a benchmark.
        engine: PFloTran or CrunchFlow.
        condition: Name of the condition to change.
        changes: Validated values in engine-independent units (see utils.units.CHEMISTRY_UNITS). (e.g. 
            ChemistryRequest(
                aqueous={"Ca++": 2.5e-3},
                minerals={"Calcite": {"volume_fraction"=0.15, "surface_area"=None}},
                rates={"Calcite": 1.0e-10}
            )
            )
        
        density: Water density in kg/m^3 of the run, for CrunchFlow's mol/kg.

    Raises:
        ValueError: A name is not in the condition or input, a value cannot be
            edited (e.g. a charge balance or mineral equilibrium constraint), or a
            log-encoded value is not positive.
        OSError: The input cannot be read or written.
    """
    # Parse .in file
    document = InputDocument.read(path, engine)
    # Read parsed tree
    chemistry = read_document(document, condition, density)
    # Message queue
    edits = []

    def lookup(kind: str, entries: dict, name: str):
        """Retrieve a chemical component by name, raising a formatted error if missing.

        Acts as a strict validator to ensure the requested chemical entity exists in the 
        currently parsed Condition. If the user attempts to modify a non-existent species 
        or mineral, it aborts early and lists all valid options.

        Args:
            kind: A descriptive label for the component type (e.g., "mineral") used in errors.
            entries: The dictionary of parsed chemistry objects to search within.
            name: The specific key to find in the entries dictionary.

        Returns:
            The requested chemistry object (e.g., Aqueous, Mineral, or Field).

        Raises:
            ValueError: If the requested name is not found in the entries.
        """
        if name not in entries:
            valid = ", ".join(entries) or "none"
            raise ValueError(
                f"{engine} input {Path(path).name} has no {kind} {name!r} in condition "
                f"{condition!r}; valid: {valid}"
            )
        return entries[name]

    def edit(target: Field | None, value: float, what: str):
        """Queue a validated field modification for batch rewriting.

        Verifies that the requested parameter is physically and mathematically editable. 
        For example, charge balances or parallel kinetic rates yield a None `target`. 
        Valid edits are buffered into the outer `edits` list to ensure the file is only 
        modified if all requested changes are legal (atomic transaction).

        Args:
            target: The Field pointer mapping to the exact text token, or None if uneditable.
            value: The new physical value to inject.
            what: A descriptive label of the target (e.g., "Ca++ (total constraint)") 
                  for detailed error reporting.

        Raises:
            ValueError: If the target field is None (implying it is a read-only constraint).
        """
        if target is None:
            raise ValueError(
                f"{engine} input {Path(path).name}: {what} has no editable value"
            )
        edits.append((target, value))

    for name, value in changes.aqueous.items():
        # Aqueous
        # Get the requested Field
        entry = lookup("aqueous species", chemistry.aqueous, name)
        edit(
            entry.field,
            value,
            f"{name} ({entry.constraint} constraint)"
        )
    for name, value in changes.minerals.items():
        # Minerals
        # Get the requested Field
        mineral = lookup("mineral", chemistry.minerals, name)
        if value.volume_fraction is not None:
            edit(
                mineral.volume_fraction,
                value.volume_fraction,
                f"{name} volume fraction",
            )
        if value.surface_area is not None:
            edit(
                mineral.surface_area,
                value.surface_area,
                f"{name} bulk surface area"
            )
    for name, value in changes.rates.items():
        # Rates
        edit(
            lookup(
                "mineral rate",
                chemistry.rates,
                name
            ),
            value,
            f"{name} rate constant",
        )
    for target, value in edits:
        try:
            target.write(document, value)
        except ValueError:
            raise ValueError(
                f"{engine} input {Path(path).name}: {value} must be positive "
                f"for a log10 value on line {target.line.index + 1}"
            ) from None
    Path(path).write_text(document.render())
