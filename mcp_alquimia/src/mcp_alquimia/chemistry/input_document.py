"""Lossless block tree of a PFLOTRAN or CrunchFlow input file.

Every node keeps its original line, so rendering an unedited tree reproduces the
file byte for byte, and an edit replaces one token without touching spacing,
comments or other lines. Lookups are scoped to a block, so a name is only matched
where it belongs (e.g. Calcite in a constraint's MINERALS, not in MINERAL_KINETICS).
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
import re

# Catch non-whitespace
_TOKEN = re.compile(r"\S+")
# PFloTran: catch # or ! or :
# CrunFlow: catch !
_COMMENT = {"PFloTran": re.compile(r"[#!:]"), "CrunchFlow": re.compile(r"!")}
_BLOCK_END = {"/", "END"}


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
