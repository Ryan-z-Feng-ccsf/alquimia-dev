"""Verify the lossless input tree and chemistry edits on every benchmark input."""

import difflib

import pytest

from mcp_alquimia.chemistry_patch import InputDocument, patch_chemistry, read_chemistry
from mcp_alquimia.setup_request import ChemistryRequest

DENSITY = 997.16


def benchmark_inputs(repository_root):
    """Every PFLOTRAN and CrunchFlow input under benchmarks/batch_chem with its engine."""
    for path in sorted((repository_root / "benchmarks/batch_chem").glob("*.in")):
        yield path, "CrunchFlow" if "crunch" in path.name else "PFloTran"


def changed_lines(before: str, after: str) -> list[str]:
    """Lines removed or added between two texts.
    
    Examples:
        >>> - Calcite  0.2  1.0
        ... ?                ^
        ... + Calcite  0.2  10.0
        ... ?                ^^
        
        Only keep the "+ " and "- " lines
    """
    return [line for line in difflib.ndiff(before.splitlines(), after.splitlines())
            if line[:2] in ("- ", "+ ")]


def test_every_input_round_trips_byte_for_byte(repository_root):
    """Parsing and rendering an unedited input reproduces it exactly."""
    inputs = list(benchmark_inputs(repository_root))
    # 16 .in files in batch_chem
    assert len(inputs) == 16
    for path, engine in inputs:
        assert InputDocument.read(path, engine).render() == path.read_text(), path.name


def test_one_change_rewrites_one_token(repository_root, tmp_path):
    """Each editable value of every input changes exactly one line when patched."""
    for path, engine in benchmark_inputs(repository_root):
        summary = read_chemistry(path, engine, "initial", DENSITY)
        edits = [{"aqueous": {name: 0.5}} for name, entry in summary["aqueous"].items()
                 if entry["value"] is not None]
        edits += [{"minerals": {name: {key: 0.05}}} for name, mineral in summary["minerals"].items()
                  for key, value in mineral.items() if value is not None]
        edits += [{"rates": {name: 1e-7}} for name, value in summary["rates"].items()
                  if value is not None]
        for edit in edits:
            staged_path = tmp_path / path.name
            staged_path.write_text(path.read_text())
            patch_chemistry(
                staged_path,
                engine,
                "initial",
                ChemistryRequest.model_validate(edit),
                DENSITY
            )
            assert len(changed_lines(path.read_text(), staged_path.read_text())) == 2, (
                path.name, edit)
            # Reading the patched file back gives the requested value
            # {
            #   "aqueous": {"Ca++": 0.5},  
            # }
            # "aqueous": {"Ca++": 0.5}
            (section, changes), = edit.items()
            # Ca++: 0.5
            (name, change), = changes.items()
            reread = read_chemistry(staged_path, engine, "initial", DENSITY)[section][name]
            if section == "minerals":
                (key, change), = change.items()
                reread = reread[key]
            elif section == "aqueous":
                reread = reread["value"]
            assert reread == pytest.approx(change, rel=1e-5), (path.name, edit)


def test_scoped_lookup_ignores_other_blocks(tmp_path):
    """A name outside the constraint's block is not edited by a constraint change."""
    staged_path = tmp_path / "p.in"
    staged_path.write_text(
        "CHEMISTRY\n"
        "  MINERAL_KINETICS\n"
        "    Calcite\n"
        "      RATE_CONSTANT -13.d0 ! log10\n"
        "    /\n"
        "  /\n"
        "END\n"
        "CONSTRAINT other\n"
        "  MINERALS\n"
        "    Calcite 0.5 1.0\n"
        "  /\n"
        "END\n"
        "CONSTRAINT initial\n"
        "  CONCENTRATIONS\n"
        "    Ca++  1.d-3  T\n"
        "  /\n"
        "  MINERALS\n"
        "    Calcite 0.2 1.0 cm^2/cm^3\n"
        "  /\n"
        "END\n")
    summary = read_chemistry(staged_path, "PFloTran", "initial", 1000.0)
    assert summary["minerals"] == {"Calcite": {"volume_fraction": 0.2, "surface_area": 100.0}}
    assert summary["rates"] == {"Calcite": 1e-13}
    patch_chemistry(staged_path, "PFloTran", "initial", ChemistryRequest.model_validate(
        {"minerals": {"Calcite": {"volume_fraction": 0.3, "surface_area": 50.0}},
         "rates": {"Calcite": 1e-10}}), 1000.0)
    text = staged_path.read_text()
    assert "Calcite 0.5 1.0\n" in text
    assert "Calcite 0.3 0.5 cm^2/cm^3\n" in text
    # Log-encoded rates stay log-encoded; the comment survives
    assert "RATE_CONSTANT -10 ! log10\n" in text


@pytest.mark.parametrize("edit, message", [
    ({"aqueous": {"Mg++": 1.0}}, "no aqueous species 'Mg\\+\\+'.*valid: HCO3-, Ca\\+\\+, H\\+"),
    ({"minerals": {"Dolomite": {"volume_fraction": 0.1}}}, "no mineral 'Dolomite'"),
    ({"rates": {"Dolomite": 1e-9}}, "no mineral rate 'Dolomite'"),
])
def test_unknown_names_are_rejected_without_writing(repository_root, tmp_path, edit, message):
    """An unknown name fails and leaves the staged input unchanged."""
    source = repository_root / "benchmarks/batch_chem/calcite-short-crunch.in"
    staged_path = tmp_path / source.name
    staged_path.write_text(source.read_text())
    with pytest.raises(ValueError, match=message):
        patch_chemistry(staged_path, "CrunchFlow", "initial",
                        ChemistryRequest.model_validate(edit), DENSITY)
    assert staged_path.read_text() == source.read_text()


def test_constraints_without_a_value_cannot_be_edited(repository_root, tmp_path):
    """Charge balance and mineral equilibrium lines carry no value to change."""
    source = repository_root / "benchmarks/batch_chem/surface-complexation-2-crunch.in"
    staged_path = tmp_path / source.name
    staged_path.write_text(source.read_text())
    summary = read_chemistry(staged_path, "CrunchFlow", "initial", DENSITY)
    assert summary["aqueous"]["Na+"] == {"constraint": "charge", "value": None}
    assert summary["aqueous"]["Cl-"] == {
        "constraint": "mineral", "value": None, "associated": "Halite"
    }
    # The pH line is reported under H+, as PFLOTRAN does
    assert summary["aqueous"]["H+"] == {"constraint": "pH", "value": 6.0}
    for name in ("Na+", "Cl-"):
        with pytest.raises(ValueError, match="no editable value"):
            patch_chemistry(
                staged_path,
                "CrunchFlow",
                "initial",
                ChemistryRequest(aqueous={name: 1e-3}),
                DENSITY
            )
    patch_chemistry(
        staged_path,
        "CrunchFlow",
        "initial",
        ChemistryRequest(aqueous={"H+": 7.5}),
        DENSITY
    )
    assert changed_lines(source.read_text(), staged_path.read_text()) == [
        "- pH               6.0", "+ pH               7.5"]


def test_unknown_condition_and_engine(repository_root):
    """Reading a condition the input lacks, or an ONNX config, fails clearly."""
    source = repository_root / "benchmarks/batch_chem/calcite-short-pflotran.in"
    with pytest.raises(ValueError, match="No CONSTRAINT 'driver_ic'"):
        read_chemistry(source, "PFloTran", "driver_ic", DENSITY)
    with pytest.raises(ValueError, match="not ONNX"):
        read_chemistry(source, "ONNX", "initial", DENSITY)
