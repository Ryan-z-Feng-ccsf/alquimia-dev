"""Read and patch the initial chemistry of PFLOTRAN and CrunchFlow input files.

An input is parsed into a lossless block tree: every node keeps its original line,
so rendering an unedited tree reproduces the file byte for byte, and an edit
replaces one token without touching spacing, comments or other lines. Lookups are
scoped to a block, so a name is only matched where it belongs (e.g. Calcite in a
constraint's MINERALS, not in MINERAL_KINETICS).

Values are exchanged in engine-independent units and converted to each engine's
input units, so one request means the same on every engine:

- total/free concentrations: mol/L water, Alquimia's state unit (PFLOTRAN reads
  mol/L; CrunchFlow mol/kg, converted with the water density)
- surface area: m^2 mineral/m^3 bulk
- mineral rate constants: mol/m^2/s, linear (CrunchFlow and negative PFLOTRAN
  values are log10)
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
from typing import Any

from .agent_input_coercion import parse_number
from .response_contract import round_significant
from .setup_request import ChemistryRequest

# -------------------------------------------------------------------------
# Engine-independent standard units dictionary.
# This defines the base physical and chemical units used internally by the 
# Python API before applying any conversions for PFLOTRAN or CrunchFlow.
# -------------------------------------------------------------------------
UNITS = {
    # Aqueous concentrations (molarity), matching Alquimia's total_mobile.
    # CrunchFlow's mol/kg is converted in _read_crunchflow.
    "total": "mol/L water",           # Total dissolved concentration of a component
    "free": "mol/L water",            # Free (uncomplexed) primary species concentration
    "log": "mol/L water",             # Logarithmic concentration value
    
    # Master variables and thermodynamic activities (Standard scales)
    "pH": "pH",                       # Negative base-10 log of Hydrogen ion activity
    "pe": "pe",                       # Negative base-10 log of electron activity (redox)
    "activity": "activity",           # Thermodynamic activity of an aqueous species
    
    # Gas phase
    "gas": "bar",                     # Gas partial pressure
    
    # Solid phase / Mineral properties
    "volume_fraction": "m^3 mineral/m^3 bulk",  # Volume of mineral per unit of total bulk volume
    "surface_area": "m^2 mineral/m^3 bulk",     # Mineral specific surface area per unit of bulk volume
    
    # Reaction Kinetics
    "rates": "mol/m^2/s",             # Kinetic reaction rate per unit of mineral surface area
}

# Catch non-whitespace
_TOKEN = re.compile(r"\S+")
# PFloTran: catch # or ! or :
# CrunFlow: catch !
_COMMENT = {"PFloTran": re.compile(r"[#!:]"), "CrunchFlow": re.compile(r"!")}
_BLOCK_END = {"/", "END"}

# Maps PFLOTRAN single-letter constraints to standard, human-readable names.
# For example: 'T' maps to 'total' concentration, 
# 'P' fixes 'pH', 
# 'Z' is for 'charge' balance
_PFLOTRAN_CONSTRAINTS = {
    "T": "total",
    "F": "free",
    "L": "log",
    "P": "pH",
    "PE": "pe",
    "G": "gas",
    "M": "mineral",
    "Z": "charge",
}

# -------------------------------------------------------------------------
# Unit conversion factor dictionaries: converts engine-independent "standard units"
# into the specific input units required by different software configuration files.
# Core conversion formula: input value = value * factor
# -------------------------------------------------------------------------

# Specific Surface Area conversion for minerals
# Assumes engine-independent standard unit is m^2/m^3 (factor 1.0)
# Conversion logic: 1 m^2/m^3 = 10,000 cm^2 / 1,000,000 cm^3 = 10^-2 cm^2/cm^3
_PFLOTRAN_AREA_UNITS = {None: 1.0, "m^2/m^3": 1.0, "cm^2/cm^3": 1e-2}

# Kinetic reaction rate conversion for minerals
# Assumes engine-independent standard unit is mol/m^2/s (factor 1.0)
# Conversion logic: 1 mol/m^2/s = 1 mol / 10,000 cm^2 / s = 10^-4 mol/cm^2/s
# Note: Includes various time delimiters (-sec, -s, /s) supported by PFLOTRAN.
_PFLOTRAN_RATE_UNITS = {
    None: 1.0,
    "mol/m^2-sec": 1.0,
    "mol/m^2-s": 1.0,
    "mol/m^2/s": 1.0,
    "mol/cm^2-sec": 1e-4,
    "mol/cm^2-s": 1e-4,
    "mol/cm^2/s": 1e-4,
}

# CrunchFlow concentration magnitude conversion, relative to mol/kg; the
# mol/L standard is converted to mol/kg with the water density in _read_crunchflow
# Conversion logic: Millimoles (mmol) require scaling the standard molar value by 1,000 (1e3).
# Micromoles (umol/micromol) require scaling by 1,000,000 (1e6).
_CRUNCH_UNITS = {"mol/kg": 1.0, "mmol/kg": 1e3, "umol/kg": 1e6, "micromol/kg": 1e6}


def format_number(value: float) -> str:
    """Write a real that both engines' input readers accept."""
    return f"{value:.12g}"


@dataclass
class Line:
    """
    Represents a single physical line from a .in file.

    This data class stores the original text and the positional coordinates (spans) 
    of its executable tokens. By storing spans rather than just the extracted words, 
    the line can be modified in-place while preserving its original whitespace, 
    indentation, and trailing comments.

    Attributes:
        index (int): The zero-based positional index of this line within the file.
        text (str): The raw text of the line (typically with trailing newlines removed).
        spans (list[tuple[int, int]]): A list of (start, end) character indices for each 
                                       valid token located before any inline comment.
    """

    index: int
    text: str
    spans: list[tuple[int, int]]

    @property
    def tokens(self) -> list[str]:
        """
        Extracts the actual string values of the tokens from the line.

        Slices the original text using the recorded character spans. The returned 
        list excludes any whitespace and inline comments.

        Returns:
            list[str]: A list of the parsed, whitespace-separated text tokens.
        """
        return [self.text[start:end] for start, end in self.spans]

    @property
    def keyword(self) -> str:
        """
        Identifies the primary command or keyword of the line.

        Extracts the very first token on the line and converts it to uppercase 
        for standardized comparison. This is typically used to identify block headers 
        (e.g., "CONSTRAINT", "TIME", "END").

        Returns:
            str: The uppercased first token, or an empty string if the line is 
                 blank or contains only a comment.
        """
        return self.text[slice(*self.spans[0])].upper() if self.spans else ""


@dataclass
class Block:
    """
    Represents a hierarchical keyword block within the .in file.

    A block consists of a header line (which initiates the block), a sequence of 
    child elements (which can be individual lines or nested sub-blocks), and an 
    implicit or explicit closing line. It is used to build the document tree for 
    organized parsing, querying, and modification.

    Attributes:
        header (Line): The `Line` object that opens this block (e.g., "CONSTRAINT initial").
        children (list[Block | Line]): A sequential list of the block's direct contents, 
                                       which can be individual `Line` objects or nested `Block` objects.
    """

    header: Line
    # Forward Reference
    children: list["Block | Line"] = field(default_factory=list)

    @property
    def keyword(self) -> str:
        """
        Retrieves the primary command or keyword of the block's header.

        Returns:
            str: The first token of the header line, converted to uppercase 
                 (e.g., "CONSTRAINT", "CHEMISTRY").
        """
        return self.header.keyword

    @property
    def name(self) -> str | None:
        """
        Retrieves the secondary identifier or name of the block, if present.

        Many configuration blocks use the second token as a custom identifier 
        (e.g., "initial" in "CONSTRAINT initial").

        Returns:
            str | None: The second token of the header line, or None if the 
                        header consists of only a single token.
        """
        tokens = self.header.tokens
        return tokens[1] if len(tokens) > 1 else None

    def blocks(self) -> Iterator["Block"]:
        """
        Iterates over the direct child elements that are nested blocks.

        Yields:
            Iterator[Block]: A generator yielding only the `Block` instances 
                             found in the immediate children list.
        """
        return (child for child in self.children if isinstance(child, Block))

    def lines(self) -> Iterator[Line]:
        """
        Iterates over the direct child elements that are executable lines.

        Filters out empty lines and pure comment lines by checking if the line 
        has valid token spans.

        Yields:
            Iterator[Line]: A generator yielding only the `Line` instances 
                            with parsed tokens found in the immediate children list.
        """
        return (
            child for child in self.children if isinstance(child, Line) and child.spans
        )

    def find(self, keyword: str, name: str | None = None) -> "Block | None":
        """
        Searches for a specific direct child block by its keyword and optional name.

        The search is case-insensitive for both the keyword and the name. It only 
        scans the immediate children of this block, not deeply nested descendants.

        Args:
            keyword (str): The primary command to look for (e.g., "CONCENTRATIONS").
            name (str | None, optional): The specific identifier of the block, if any. 
                                         Defaults to None.

        Returns:
            Block | None: The first matching `Block` instance found, or None if 
                          no child matches the given criteria.
        """
        for block in self.blocks():
            if block.keyword == keyword.upper() and (
                name is None or (block.name or "").lower() == name.lower()
            ):
                return block
        return None


class InputDocument:
    """
    Represents a parsed native input configuration file for geochemical engines.

    This class reads the raw text of a PFLOTRAN or CrunchFlow configuration file,
    parses it into a sequence of lines and hierarchical blocks, and provides 
    methods to safely modify specific parameter values (tokens) without altering 
    the original file formatting or comments.

    Attributes:
        engine (str): The name of the active simulation engine (e.g., "PFloTran" or "CrunchFlow").
        lines (list[Line]): A sequentially ordered list of parsed Line objects representing 
                            every physical line in the configuration file.
        root (Block): The root node of the parsed hierarchical document tree. It contains 
                      all the top-level configuration blocks and plain lines as its children.
        _raw (list[str]): An internal list containing the raw string of each line, keeping 
                          original line endings. Used primarily for rendering and in-place token replacement.
    """

    def __init__(self, text: str, engine: str):
        """
        Initializes the document by parsing the provided text.

        Args:
            text (str): The complete raw text of the input configuration file.
            engine (str): The name of the simulation engine (e.g., "PFloTran" or "CrunchFlow").

        Raises:
            ValueError: If the specified engine is not supported (missing from _COMMENT).
        """
        if engine not in _COMMENT:
            raise ValueError(
                f"Chemistry editing supports PFloTran and CrunchFlow, not {engine}"
            )
        self.engine = engine
        self._raw = text.splitlines(keepends=True)
        self.lines = [
            self._parse_line(index, raw) for index, raw in enumerate(self._raw)
        ]
        self.root = Block(Line(-1, "", []))
        if engine == "PFloTran":
            self._parse_pflotran()
        else:
            self._parse_crunchflow()

    @classmethod
    def read(cls, path: Path, engine: str) -> "InputDocument":
        """
        Instantiates an InputDocument directly from a file path.

        Args:
            path (Path): The file system path to the configuration file.
            engine (str): The simulation engine format to parse ("PFloTran" or "CrunchFlow").

        Returns:
            InputDocument: A newly parsed document instance.
        """
        return cls(Path(path).read_text(), engine)

    def render(self) -> str:
        """
        Reconstructs the full file content as a single string.

        Returns:
            str: The complete configuration file text, incorporating any modifications 
                 made via token replacement, while preserving original whitespace and comments.
        """
        return "".join(self._raw)

    def replace_token(self, line: Line, position: int, text: str) -> None:
        """
        Overwrites a specific token within a line while preserving surrounding text.

        This method updates the raw text array and recalculates the token spans for 
        the modified line to maintain parsing consistency.

        Args:
            line (Line): The Line object containing the token to be replaced.
            position (int): The zero-based index of the token within the line's parsed spans.
            text (str): The new string value to insert in place of the target token.
        """
        start, end = line.spans[position]
        raw = self._raw[line.index]
        self._raw[line.index] = raw[:start] + text + raw[end:]
        updated_line = self._parse_line(line.index, self._raw[line.index])
        line.text, line.spans = updated_line.text, updated_line.spans

    def _parse_line(self, index: int, raw: str) -> Line:
        """
        Parses a raw line from the configuration file into a Line object.

        This method strips trailing newlines, identifies inline comments based on 
        the active simulation engine (using the _COMMENT regex mapping), and isolates 
        the executable content. It then identifies all valid tokens (words/values) 
        within the executable part and records their character spans.

        Args:
            index (int): The line number or positional index within the file.
            raw (str): The raw text of the line read directly from the file.

        Returns:
            Line: A Line data class/object containing the original line index, 
                  the full original text (without newlines), and a list of character 
                  spans (start, end) for all parsed tokens in the non-comment section.
        """
        # Windows / Linux / Mac
        text = raw.rstrip("\r\n")
        comment = _COMMENT[self.engine].search(text)
        # Remove comments
        content = text[: comment.start()] if comment else text
        # Record the character span
        return Line(index, text, [match.span() for match in _TOKEN.finditer(content)])

    def _parse_block(
        self, start: int, opens: Callable[[Line, int], bool], depth: int = 1
    ):
        """
        Constructs a hierarchical block starting at a specified line index.

        A new nested child block is initiated when the `opens` callback evaluates to True.
        The block (and any nested blocks) is closed when a line begins with a terminating 
        keyword (like "/" or "END"), or when the end of the file is reached.

        Args:
            start (int): The index in self.lines where the block begins (the header).
            opens (Callable[[Line, int], bool]): A function evaluating whether a given line 
                                                 opens a new nested sub-block at the current depth.
            depth (int): The current nesting depth level (defaults to 1).

        Returns:
            tuple[Block, int]: A tuple containing the parsed Block object and the integer 
                               index of the next line immediately following this block.
        """
        block = Block(self.lines[start])
        index = start + 1
        while index < len(self.lines):
            line = self.lines[index]
            # PFloTran: /, END
            # CrunchFlow: END
            if line.keyword in _BLOCK_END:
                block.children.append(line)
                return block, index + 1
            if line.spans and opens(line, depth):
                child, index = self._parse_block(index, opens, depth + 1)
                block.children.append(child)
                continue
            block.children.append(line)
            index += 1
        return block, index

    def _parse_pflotran(self) -> None:
        """
        Constructs the document tree structure specific to PFLOTRAN syntax.

        Extracts and nests `CONSTRAINT` cards and the `MINERAL_KINETICS` block. 
        Other global cards remain as unnested lines in the root. Inside a constraint, 
        a single keyword opens a sub-block (e.g., CONCENTRATIONS, MINERALS). Inside 
        MINERAL_KINETICS, each line initiates a mineral block, with PREFACTOR blocks 
        nesting inside.
        
        Example:
            >>> text = '''CHEMISTRY
            ... CONSTRAINT initial
            ...   CONCENTRATIONS
            ...     H+  5.0  P
            ...   /
            ... /'''
            >>> doc = InputDocument(text, engine="PFloTran")
            
            The resulting nested document tree structure will be:
            doc.root (Block)
            ├── Line(index=0, text="CHEMISTRY", spans=[(0, 9)])  <-- Global unnested line
            └── Block (keyword="CONSTRAINT", name="initial")
                ├── header: Line(index=1, text="CONSTRAINT initial", spans=[(0, 10), (11, 18)])
                └── children:
                    ├── Block (keyword="CONCENTRATIONS")
                    │   ├── header: Line(index=2, text="  CONCENTRATIONS", spans=[(2, 16)])
                    │   └── children:
                    │       ├── Line(index=3, text="    H+  5.0  P", spans=[(4, 6), (8, 11), (13, 14)])
                    │       └── Line(index=4, text="  /", spans=[(2, 3)])
                    └── Line(index=5, text="/", spans=[(0, 1)])
        """

        def constraint_opens(line, depth):
            return depth == 1 and len(line.spans) == 1

        def kinetics_opens(line, depth):
            return depth == 1 or line.keyword in ("PREFACTOR", "PREFACTOR_SPECIES")

        index = 0
        while index < len(self.lines):
            line = self.lines[index]
            if line.keyword == "CONSTRAINT" and len(line.spans) >= 2:
                block, index = self._parse_block(index, constraint_opens)
            elif line.keyword == "MINERAL_KINETICS":
                block, index = self._parse_block(index, kinetics_opens)
            else:
                self.root.children.append(line)
                index += 1
                continue
            self.root.children.append(block)

    def _parse_crunchflow(self) -> None:
        """
        Constructs the document tree structure specific to CrunchFlow syntax.

        Extracts flat, top-level keyword blocks. In CrunchFlow, each primary module 
        (e.g., TITLE, RUNTIME) opens a single-depth block that is strictly closed 
        by an "END" tag. Blank lines between blocks are preserved as empty Line 
        objects directly under the root to maintain original file spacing.

        Example:
            >>> text = '''TITLE
            ... Batch Model
            ... END
            ... 
            ... RUNTIME
            ... time_units minutes
            ... END'''
            >>> doc = InputDocument(text, engine="CrunchFlow")
            
            The resulting flat document tree structure will be:
            doc.root (Block)
            ├── Block (keyword="TITLE")
            │   ├── header: Line(index=0, text="TITLE", spans=[(0, 5)])
            │   └── children:
            │       ├── Line(index=1, text="Batch Model", spans=[(0, 5), (6, 11)])
            │       └── Line(index=2, text="END", spans=[(0, 3)])
            ├── Line(index=3, text="", spans=[])  <-- Preserved empty line
            └── Block (keyword="RUNTIME")
                ├── header: Line(index=4, text="RUNTIME", spans=[(0, 7)])
                └── children:
                    ├── Line(index=5, text="time_units minutes", spans=[(0, 10), (11, 18)])
                    └── Line(index=6, text="END", spans=[(0, 3)])
        """
        index = 0
        while index < len(self.lines):
            line = self.lines[index]
            if line.spans:
                # No recursion
                block, index = self._parse_block(index, lambda _line, _depth: False)
                self.root.children.append(block)
            else:
                self.root.children.append(line)
                index += 1


@dataclass
class Field:
    """
    Represents an editable numerical parameter within the configuration file.

    This class acts as a precise pointer to a specific token in the document. 
    It stores the token's exact location, its current value in standardized units, 
    and the conversion logic required to write a new value back into the engine's 
    native format.

    Attributes:
        line (Line): The parsed Line object where this value resides.
        position (int): The zero-based token index within the line's parsed spans.
        value (float): The current numerical value, converted to engine-independent standard units.
        encode (Callable[[float], float]): A function that converts a standard-unit value 
                                           back into the engine-specific input unit.
    """

    line: Line
    position: int
    value: float
    encode: Callable[[float], float]

    def write(self, document: InputDocument, value: float) -> None:
        """
        Updates the document with a new value for this field.

        This method accepts a value in engine-independent units, applies the `encode` 
        function to convert it to the engine's expected units, formats it as a string, 
        and instructs the document to replace the original token in-place.

        Args:
            document (InputDocument): The parsed document object to be modified.
            value (float): The new numerical value in engine-independent standard units.
        """
        document.replace_token(
            self.line,
            self.position,
            format_number(self.encode(value))
        )


@dataclass
class Aqueous:
    """
    Configuration for an aqueous primary species constraint.

    Defines how an aqueous species is constrained in an initial or boundary condition 
    (e.g., set by total concentration, fixed pH, or equilibrium with a mineral/gas).

    Attributes:
        constraint (str): The standardized constraint type (e.g., "total", "pH", "mineral").
        field (Field | None): The editable numerical value for this constraint. 
                              It is None if the constraint does not carry an editable 
                              value (e.g., charge balance where the engine calculates it).
        associated (str | None): The name of a secondary species, mineral, or gas associated 
                                 with this constraint (e.g., "Calcite" if constraint is "mineral").
    """

    constraint: str
    field: Field | None = None
    associated: str | None = None


@dataclass
class Mineral:
    """
    Configuration for a solid phase mineral in an initial condition.

    Tracks the physical properties of a mineral within a specific spatial zone 
    or condition block.

    Attributes:
        volume_fraction (Field | None): The editable volume fraction of the mineral 
                                        (m^3 mineral/m^3 bulk).
        surface_area (Field | None): The editable specific surface area of the mineral 
                                     (m^2 mineral/m^3 bulk).
    """

    volume_fraction: Field | None = None
    surface_area: Field | None = None


@dataclass
class Chemistry:
    """
    Represents the editable chemical configuration of a specific simulation condition.

    This data class acts as a unified container for all parsed chemical parameters 
    (aqueous species, solid minerals, and kinetic rates) belonging to a single 
    initial condition, boundary condition, or global setting. It abstracts away 
    the underlying engine syntax (PFLOTRAN or CrunchFlow) into an engine-independent 
    Python object.

    Attributes:
        aqueous (dict[str, Aqueous]): A mapping of aqueous primary species names 
                                      to their corresponding `Aqueous` configuration objects.
        minerals (dict[str, Mineral]): A mapping of mineral names to their corresponding 
                                       `Mineral` configuration objects (handling volume fractions and area).
        rates (dict[str, Field | None]): A mapping of kinetic reaction/mineral names 
                                         to their reaction rate `Field` objects. A value of None 
                                         indicates a rate exists but cannot be safely edited.
    """

    aqueous: dict[str, Aqueous] = field(default_factory=dict)
    minerals: dict[str, Mineral] = field(default_factory=dict)
    rates: dict[str, Field | None] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        """
        Generates a JSON-serializable dictionary summarizing the chemical state.

        Extracts the current values and constraints of all aqueous species, minerals, 
        and kinetic rates, outputting them in engine-independent standard units. 
        Numerical values are formatted to 6 significant digits to keep the summary 
        concise for UI, logging, or API responses. 

        Missing or uneditable values are returned as null (None).

        Returns:
            dict[str, Any]: A nested dictionary containing 'aqueous', 'minerals', 
                            and 'rates' configuration summaries.

        Example:
            >>> chem.describe()
            {
                "aqueous": {
                    "H+": {"constraint": "pH", "value": 5.0},
                    "Ca+2": {"constraint": "total", "value": 0.001, "associated": "Cl-"}
                },
                "minerals": {
                    "Calcite": {"volume_fraction": 0.2, "surface_area": 100.0}
                },
                "rates": {
                    "Calcite": 1.2e-13
                }
            }
        """

        def value(item):
            # Rounded to keep the summary short; edits use exact values
            return round_significant(item.value) if item else None

        aqueous = {}
        for name, entry in self.aqueous.items():
            aqueous[name] = {
                "constraint": entry.constraint,
                "value": value(entry.field),
            }
            if entry.associated:
                aqueous[name]["associated"] = entry.associated
        return {
            "aqueous": aqueous,
            "minerals": {
                name: {
                    "volume_fraction": value(mineral.volume_fraction),
                    "surface_area": value(mineral.surface_area),
                }
                for name, mineral in self.minerals.items()
            },
            "rates": {name: value(rate) for name, rate in self.rates.items()},
        }


def _number_field(line: Line, position: int, factor: float = 1.0) -> Field | None:
    """
    Extracts a numerical token from a line and wraps it in an editable Field object.

    This helper function parses the token at the specified position. It applies a 
    scaling conversion (using the provided factor) to translate the engine-specific 
    input value into an engine-independent standard unit for internal storage. It also 
    injects an `encode` lambda function into the Field, ensuring that any future 
    edits to this value are correctly scaled back to the engine's native units 
    before being written to the file.

    Args:
        line (Line): The parsed Line object containing the text and tokens.
        position (int): The zero-based index of the token to evaluate.
        factor (float, optional): The conversion multiplier (engine unit = standard unit * factor). 
                                  Defaults to 1.0 (no conversion).

    Returns:
        Field | None: A populated Field instance pointing to the exact token if parsing 
                      is successful, or None if the token is missing or not a valid number.
    """
    tokens = line.tokens
    number = parse_number(tokens[position]) if position < len(tokens) else None
    if number is None:
        return None
    return Field(line, position, number / factor, lambda value: value * factor)


def _log_field(line: Line, position: int, factor: float = 1.0) -> Field | None:
    """A field whose input value is log10 of the engine-independent value times factor."""
    number = parse_number(line.tokens[position])
    if number is None:
        return None
    return Field(
        line, position, 10.0**number / factor, lambda value: math.log10(value * factor)
    )


def _read_pflotran(document: InputDocument, condition: str) -> Chemistry:
    """
    Read a PFLOTRAN CONSTRAINT and the MINERAL_KINETICS rate constants.

    Extracts aqueous species concentrations and solid phase properties (volume 
    fraction, surface area) from the specified nested CONSTRAINT block. It also 
    parses the global MINERAL_KINETICS block to extract reaction rate constants. 
    PFLOTRAN constraint concentrations default to molarity (mol/L), the
    engine-independent standard, so they are read without conversion.

    Args:
        document (InputDocument): The parsed PFLOTRAN input document tree.
        condition (str): The name of the CONSTRAINT block to extract (e.g., "initial").

    Returns:
        Chemistry: An engine-independent object representing the chemical state.

    Raises:
        ValueError: If the specified CONSTRAINT block is not found in the document.

    Example:
        >>> text = '''
        ... CONSTRAINT initial
        ...   CONCENTRATIONS
        ...     H+   5.0   P
        ...     Ca++ 1e-3  T
        ...     Cl-  1e-5  M Halite
        ...   /
        ...   MINERALS
        ...     Halite 1.0e-5 1.0 cm^2/cm^3
        ...   /
        ... /
        ... MINERAL_KINETICS
        ...   Halite
        ...     RATE_CONSTANT -9.0 mol/cm^2-sec
        ...   /
        ... /
        ... '''
        >>> doc = InputDocument(text, engine="PFLOTRAN")
        >>> chem = _read_pflotran(doc, "initial")
        >>> chem.aqueous["Ca++"].constraint
        'total'
        >>> chem.aqueous["Ca++"].field.value
        0.001
        
        # The 'M' flag is successfully mapped to 'mineral' with its associated phase
        >>> chem.aqueous["Cl-"].constraint
        'mineral'
        >>> chem.aqueous["Cl-"].associated
        'Halite'
        
        # Negative rate constants are properly treated as log10 values
        >>> chem.rates["Halite"].value
        1e-09
    """
    # Find the CONSTRAINT block
    constraint = document.root.find("CONSTRAINT", condition)
    if constraint is None:
        raise ValueError(f"No CONSTRAINT {condition!r} in the PFLOTRAN input")
    chemistry = Chemistry()

    # Find the CONCENTRATIONS block
    concentrations = constraint.find("CONCENTRATIONS")
    for line in concentrations.lines() if concentrations else ():
       
        # e.g.
        # HCO3- 1.d-5 T
        # Ca++ 1.d-5 T
        # H+ -1.d-5 T
        tokens = line.tokens
        
        # Invalid field
        if len(tokens) < 3:
            continue
        
        # Get the interface constraint type value
        constraint_type = _PFLOTRAN_CONSTRAINTS.get(tokens[2].upper(), tokens[2])
        # e.g.
        # Cl- 1.0e-5 M Halite
        entry = Aqueous(
            constraint_type, associated=tokens[3] if len(tokens) > 3 else None
        )
        if constraint_type in ("total", "free"):
            entry.field = _number_field(line, 1)
        elif constraint_type == "log":
            entry.field = _log_field(line, 1)
        elif constraint_type in ("pH", "pe", "gas"):
            # No factor
            entry.field = _number_field(line, 1)
        chemistry.aqueous[tokens[0]] = entry
        
    # Find the MINERALS block
    minerals = constraint.find("MINERALS")
    for line in minerals.lines() if minerals else ():
        tokens = line.tokens
        
        # Invalid field
        if len(tokens) < 2:
            continue
        mineral = Mineral(volume_fraction=_number_field(line, 1))
        # e.g.
        # Calcite 0.2 100.0 m^2/m^3
        unit = tokens[3] if len(tokens) > 3 else None
        if len(tokens) > 2 and unit in _PFLOTRAN_AREA_UNITS:
            mineral.surface_area = _number_field(line, 2, _PFLOTRAN_AREA_UNITS[unit])
        chemistry.minerals[tokens[0]] = mineral
    
    # Find the MINERAL_KINETICS block
    kinetics = document.root.find("MINERAL_KINETICS")
    for block in kinetics.blocks() if kinetics else ():
        rate = next(
            (line for line in block.lines() if line.keyword == "RATE_CONSTANT"), None
        )
        # Invalid field
        if rate is None:
            continue
        
        # Find RATE_CONSTANT
        tokens = rate.tokens
        unit = tokens[2] if len(tokens) > 2 else None
        number = parse_number(tokens[1]) if len(tokens) > 1 else None
        factor = _PFLOTRAN_RATE_UNITS.get(unit)
        name = block.header.tokens[0]
        # Invalid number or factor
        if number is None or factor is None:
            chemistry.rates[name] = None
        elif number < 0:
            # PFLOTRAN reads a negative rate constant as log10
            chemistry.rates[name] = _log_field(rate, 1, factor)
        else:
            chemistry.rates[name] = _number_field(rate, 1, factor)
    return chemistry


def _read_crunchflow(
    document: InputDocument, condition: str, density: float
) -> Chemistry:
    """
    Read a CrunchFlow Condition and the -rate options of its MINERALS block.

    Extracts the aqueous species, mineral volume fractions, and surface areas 
    from the specified CONDITION block. It also scans the global MINERALS block 
    to extract kinetic reaction rates (-rate) for the minerals present. If a 
    mineral defines multiple parallel reaction mechanisms (e.g., acid and default), 
    its rate field is set to None (read-only).

    Args:
        document (InputDocument): The parsed CrunchFlow input document tree.
        condition (str): The name of the CONDITION block to extract (e.g., "initial").
        density (float): Water density in kg/m^3, converting the condition's
                         mol/kg (molality) to the mol/L standard.

    Returns:
        Chemistry: An engine-independent object representing the chemical state.

    Raises:
        ValueError: If the specified condition block is not found in the document.

    Example:
        >>> text = '''
        ... PRIMARY_SPECIES
        ... Ca++
        ... END
        ... 
        ... MINERALS
        ... Calcite -label default -rate -9.0
        ... Quartz -label default -rate -13.0
        ... Quartz -label acid -rate -10.0
        ... END
        ... 
        ... CONDITION initial
        ... units mol/kg
        ... pH 5.0
        ... Ca++ 1.0e-3
        ... Calcite 0.2 bsa 100.0
        ... END
        ... '''
        >>> doc = InputDocument(text, engine="CrunchFlow")
        >>> chem = _read_crunchflow(doc, "initial", density=1000.0)
        
        # pH is mapped to H+ constraint
        >>> chem.aqueous["H+"].constraint
        'pH'
        >>> chem.aqueous["H+"].field.value
        5.0
        
        # Density of 1000 kg/m^3 makes mol/kg equal mol/L
        >>> chem.aqueous["Ca++"].field.value
        0.001
        
        # Mineral volume and surface area extracted
        >>> chem.minerals["Calcite"].volume_fraction.value
        0.2
        >>> chem.minerals["Calcite"].surface_area.value
        100.0
        
        # Single mineral rate decoded from log value (-9.0 -> 1e-9)
        >>> chem.rates["Calcite"].value
        1e-09
        
        # Quartz has parallel rates, so it becomes uneditable (None)
        >>> chem.rates["Quartz"] is None
        True
    """
    
    # Find "CONDITION" child block
    block = document.root.find("CONDITION", condition)
    if block is None:
        raise ValueError(f"No Condition {condition!r} in the CrunchFlow input")
    
    # Find "PRIMARY_SPECIES" child block
    primary_block = document.root.find("PRIMARY_SPECIES")
    primary_species = (
        {line.tokens[0] for line in primary_block.lines()} if primary_block else set()
    )
    
    # Find "MINERALS" child block
    minerals_block = document.root.find("MINERALS")
    
    # Get Lines under MINERALS block
    mineral_lines = list(minerals_block.lines()) if minerals_block else []
    
    # Get the mineral names
    mineral_names = {line.tokens[0] for line in mineral_lines}
    chemistry = Chemistry()
    unit_line = next((line for line in block.lines() if line.keyword == "UNITS"), None)
    unit = (
        unit_line.tokens[1].lower()
        if unit_line and len(unit_line.tokens) > 1
        else "mol/kg"
    )
    # input value = mol/L / (density [kg/L]) * unit scale
    unit_scale = _CRUNCH_UNITS.get(unit)
    concentration_factor = unit_scale * 1000.0 / density if unit_scale else None
    
    # Within CONDITION block
    # Parse the primary species and mineral
    for line in block.lines():
        tokens = line.tokens
        name = tokens[0]
        if line.keyword == "PH" and len(tokens) > 1:
            
            # CrunchFlow fixes H+ through a pH line
            chemistry.aqueous["H+"] = Aqueous("pH", _number_field(line, 1))
        elif name in primary_species and len(tokens) > 1:
            
            # Parse the primary species fields
            chemistry.aqueous[name] = _crunch_aqueous(
                line, mineral_names, concentration_factor
            )
        elif name in mineral_names and len(tokens) > 1:
            
            # Parse the mineral
            mineral = Mineral(volume_fraction=_number_field(line, 1))
            option = tokens[2].lower() if len(tokens) > 2 else None
            if option in ("bulk_surface_area", "bsa") and len(tokens) > 3:
                mineral.surface_area = _number_field(line, 3)
            elif option is not None and parse_number(tokens[2]) is not None:
                
                # e.g. Calcite 0.2 0.2
                mineral.surface_area = _number_field(line, 2)
            chemistry.minerals[name] = mineral
    
    # Parse the minerals rate
    for line in mineral_lines:
        tokens = [token.lower() for token in line.tokens]
        # Only change the mineral rate
        if "-rate" not in tokens or tokens.index("-rate") + 1 >= len(tokens):
            continue
        # e.g. Calcite -label default -rate -9.0
        # e.g. name = Calcite
        name = line.tokens[0]
        
        # Parallel reactions of one mineral have one rate each; none is "the" rate
        # No access to change the rate
        # e.g. 
        # Calcite    -label default    -rate -8.79
        # Calcite    -label acid       -rate -0.30
        # Calcite    -label pco2       -rate -3.30
        repeated = name in chemistry.rates
        
        # log values
        chemistry.rates[name] = (
            None if repeated else _log_field(line, tokens.index("-rate") + 1)
        )
    return chemistry


def _crunch_aqueous(line: Line, minerals: set[str], factor: float | None) -> Aqueous:
    """
    Classifies and parses a CrunchFlow primary species constraint line.

    Analyzes the arrangement of tokens to deduce the specific thermodynamic constraint 
    applied to the aqueous species (e.g., charge balance, mineral equilibrium, gas 
    partial pressure, or fixed concentration). 

    If a valid `factor` is provided, numerical values are converted to standard units 
    (mol/L) and wrapped in an editable `Field`. A `None` factor indicates that the 
    condition's units (e.g., ppm) lack a straightforward mol/L conversion, rendering 
    the concentration read-only (i.e., the `field` property is set to None).

    Args:
        line (Line): The parsed line representing the aqueous species constraint.
        minerals (set[str]): A set of known mineral names defined in the configuration, 
                             used to detect mineral-equilibrium constraints.
        factor (float | None): Input value per standard mol/L. 
                               Pass None if the units cannot be safely converted.

    Returns:
        Aqueous: A standardized constraint object containing the constraint type, 
                 the editable Field (if applicable), and any associated phase.
    """
    tokens = line.tokens
    second = tokens[1]
    option = tokens[2].lower() if len(tokens) > 2 else None
    
    # e.g. Na+ charge 
    if second.lower() == "charge":
        return Aqueous("charge")
    
    # e.g. Cl- Halite
    if second in minerals:
        return Aqueous("mineral", associated=second)
    if parse_number(second) is None:
        # e.g. "HCO3- CO2(g) 3.15E-4": equilibrium with a gas partial pressure
        # e.g. CH4(aq) CH4(g) 0.10
        return Aqueous(
            "gas", _number_field(line, 2) if len(tokens) > 2 else None, second
        )

    # e.g. Ca++ 1.0e-3 activity
    if option == "activity":
        return Aqueous("activity", _number_field(line, 1))

    # Units factor only works for free and total
    # e.g. Ca++ 1.0e-3 species / Ca++ 1.0e-3 total
    constraint = "free" if option == "species" else "total"
    
    # e.g. Ca++ 1.0e-3
    return Aqueous(constraint, _number_field(line, 1, factor) if factor else None)


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
        return _read_pflotran(document, condition)
    return _read_crunchflow(document, condition, density)


def read_chemistry(
    path: Path, engine: str, condition: str, density: float
) -> dict[str, Any]:
    """Summarize an input's initial chemistry in engine-independent units (see UNITS).

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
        changes: Validated values in engine-independent units (see UNITS). (e.g. 
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
