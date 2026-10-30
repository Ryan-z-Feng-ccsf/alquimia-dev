"""Build the SQLite FTS5 index of engine manuals that the search_docs tool reads."""

import argparse
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile


# Must match MANUAL_ENGINES in src/mcp_alquimia/manual_search.py.
ENGINES = ("pflotran", "crunchflow")
SUFFIXES = (".rst", ".md", ".txt", ".pdf")

# Sections longer than this are split on paragraph boundaries into numbered parts;
# it stays below manual_search.MAX_SECTION_CHARS so read_doc returns whole parts.
SPLIT_CHARS = 6000

# Catch the .rst adornment ===, ---, etc
_RST_ADORNMENT = re.compile(r"""^([=\-~^*#"'+`:.])\1{2,}\s*$""")

# Catch # or ## or ### ... ######
# Catch ## Heading ## or ## Heading
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# Catch ``` ``` or ~~~ ~~~
_MARKDOWN_FENCE = re.compile(r"^\s*(```|~~~)")

# Catch \nbody\n
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def split_rst(text: str) -> list[tuple[list[str], str]]:
    """Split reStructuredText on headings into (title path, body) pairs.

    Heading levels follow the order in which adornment styles first appear, as in
    docutils; an overlined heading is a different style from an underlined one.
    
    Returns:
        list[tuple[list[str], str]]: A list of parsed sections.
        
        Examples:
        [
            (
                ["Python tutorial"], 
                "This is the preamble content under the top-level heading..."
            ),
            (
                ["Python tutorial", "Basic syntax"], 
                "Here is the beginning of the syntax introduction..."
            ),
            (
                ["Python tutorial", "Basic syntax", "Types"], 
                "Variables are placeholders in memory.\nThey can be defined like this..."
            )
        ]
    """
    # Initialize parser state:
    # styles: registered heading styles to infer level;
    # path: current breadcrumb path;
    # sections: final output of (path, body) tuples;
    # body: text accumulator for the current section
    styles, path, sections, body = [], [], [], []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        # Prevent out of bounds
        line, following = lines[i], lines[i + 1] if i + 1 < len(lines) else ""
        # Empty line can't be the title
        # Title -> line
        # ====== -> following
        # len(following.rstrip()) has to be greater than Title (rst)
        if (line.strip() and not _RST_ADORNMENT.match(line) and _RST_ADORNMENT.match(following)
                and len(following.rstrip()) >= len(line.strip())):
            # ======
            # Title
            # ======
            overlined = bool(body) and body[-1] == following
            # body[-1] -> ======
            if overlined:
                body.pop()
            # Deep copy list(heading)
            sections.append((list(path), "\n".join(body)))
            # ('=', True) ->
            # ======
            # Title
            # ======
            # ('=', False) ->
            # Title
            # ======
            style = (following[0], overlined)
            if style not in styles:
                styles.append(style)
            level = styles.index(style)
            # Return to the parent heading
            # Add the current heading
            path = path[:level] + [line.strip()]
            # Store new body
            body = []
            i += 2
            continue
        body.append(line)
        i += 1
    sections.append((path, "\n".join(body)))
    return sections


def split_markdown(text: str) -> list[tuple[list[str], str]]:
    """Split Markdown on ATX headings outside code fences into (title path, body) pairs.

    Returns:
        list[tuple[list[str], str]]: A list of parsed sections containing the breadcrumb path and text.
        
        Examples:
        [
            (
                [], 
                "This is the preamble text before any markdown heading appears..."
            ),
            (
                ["Getting Started"], 
                "Here is how you install the package.\nRun this command:"
            ),
            (
                ["Getting Started", "Configuration"], 
                "To configure the app, edit the `.env` file.\nIt supports multiple environments."
            )
        ]
    """
    path, sections, body, fenced = [], [], [], False
    for line in text.splitlines():
        # There is # in Python code
        # Guard to set heading to False
        if _MARKDOWN_FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else _MARKDOWN_HEADING.match(line)
        if heading:
            sections.append((list(path), "\n".join(body)))
            # Numbers of #
            level = len(heading.group(1))
            # group(2): heading
            # Return to the parent heading
            # Add the current heading
            path = path[:level - 1] + [heading.group(2)]
            body = []
        else:
            body.append(line)
    sections.append((path, "\n".join(body)))
    return sections


def split_pdf(path: Path) -> list[tuple[list[str], str]]:
    """Extract a PDF with poppler's pdftotext and return one section per page.

    Returns:
        list[tuple[list[str], str]]: A list containing the page path and extracted text.
        
        Examples:
        [
            (
                ["manual page 1"], 
                "This is the extracted text from the first page of the PDF document..."
            ),
            (
                ["manual page 2"], 
                "Content from the second page goes here.\nIt preserves line breaks..."
            )
        ]
    
    Raises:
        OSError: pdftotext is not installed.
        subprocess.CalledProcessError: pdftotext cannot read the file.
    """
    # "-" directs text output directly to the stout
    text = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"],
                          check=True, capture_output=True, text=True).stdout
    return [([f"{path.stem} page {page_number}"], body)
            for page_number, body in enumerate(text.split("\f"), start=1)]


def split_long(body: str, limit: int = SPLIT_CHARS) -> list[str]:
    """Split a body at paragraph breaks into parts of at most `limit` characters."""
    parts, current = [], ""
    for paragraph in _PARAGRAPH_BREAK.split(body):
        while len(paragraph) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(paragraph[:limit])
            paragraph = paragraph[limit:]
            
        if current and len(current) + 2 + len(paragraph) > limit:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph

    if current:
        parts.append(current)
    return parts


def sections_from_file(engine: str, path: Path, root: Path) -> list[dict[str, str]]:
    """Read one manual file into index rows with stable ids and readable titles.

    Args:
        engine: One of ENGINES.
        path: Manual file with a suffix in SUFFIXES.
        root: Source directory; ids use the path relative to it.

    Returns:
        list[dict[str, str]]: Rows with doc_id, engine, title, body, and source; empty sections are dropped.
        
        Examples:
        [
            {
                "doc_id": "pflotran/getting-started/0",
                "engine": "pflotran",
                "title": "Introduction > Installation",
                "body": "To install PFLOTRAN, you must first install PETSc...",
                "source": "getting-started.md"
            },
            {
                "doc_id": "pflotran/getting-started/1",
                "engine": "pflotran",
                "title": "Introduction > Installation (part 2)",
                "body": "Next, configure the environment variables...",
                "source": "getting-started.md"
            }
        ]
    """
    # PDF
    if path.suffix == ".pdf":
        pieces = split_pdf(path)
    # .rst, .md
    else:
        # error = "replace" prevents raising the error (UnicodeDecodeError)
        text = path.read_text(encoding="utf-8", errors="replace")
        # Shared type list[tuple(list[], str)]
        pieces = {".rst": split_rst, ".md": split_markdown}.get(
            path.suffix, lambda text: [([], text)])(text)
    relative_path = path.relative_to(root) if path != root else Path(path.name)
    # Convert to slug
    # Example: Hello world/python -> hello-world-python
    # Catch non-numbers or non-letters
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(relative_path.with_suffix(""))).strip("-").lower()
    rows = []
    for title_path, body in pieces:
        parts = split_long(body.strip())
        for number, part in enumerate(parts, start=1):
            title = " > ".join(title_path or [path.stem])
            if len(parts) > 1:
                title += f" (part {number})"
            rows.append(
                {
                    "doc_id": f"{engine}/{slug}/{len(rows)}",
                    "engine": engine,
                    "title": title,
                    "body": part,
                    "source": str(relative_path)
                }
            )
    return rows


def collect_rows(sources: list[tuple[str, Path]]) -> list[dict[str, str]]:
    """Gather rows from every supported file under each engine's source path."""
    rows = []
    for engine, source in sources:
        files = [source] if source.is_file() else sorted(
            path for path in source.rglob("*") if path.suffix in SUFFIXES   # Recursive searching for files
        )
        for path in files:
            rows.extend(sections_from_file(engine, path, source if source.is_dir() else path))
    return rows


def write_index(rows: list[dict[str, str]], output: Path) -> None:
    """Write rows to a new index, replacing `output` only once it is complete (Atomic)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(suffix=".sqlite", dir=output.parent)
    os.close(handle)
    try:
        # Auto SQL commit (will not close automatically)
        # Atomic
        with sqlite3.connect(temporary) as connection:
            # Create FTS5 table
            # Tokenize based on unicode 6.1 (Only keep letters and numbers)
            # Case folding (Only keep the lowercase letters)
            # Only tokenize the title, body
            connection.execute(
                "CREATE VIRTUAL TABLE sections USING fts5(doc_id UNINDEXED, engine UNINDEXED, "
                "title, body, source UNINDEXED, tokenize='porter unicode61')"
            )
            # Insert the value based on the rows dict
            connection.executemany(
                "INSERT INTO sections VALUES (:doc_id, :engine, :title, :body, :source)", rows
            )
            # Optimize the FTS5 B Tree
            connection.execute("INSERT INTO sections(sections) VALUES ('optimize')")
        connection.close()
        # Atomic
        os.replace(temporary, output)
    except BaseException:
        # If catches error
        # Remove the file
        Path(temporary).unlink(missing_ok=True)
        raise


def parse_source(value: str) -> tuple[str, Path]:
    """Parse an ENGINE=PATH command-line source."""
    engine, separator, path = value.partition("=")
    if not separator or engine.lower() not in ENGINES:
        raise argparse.ArgumentTypeError(f"expected ENGINE=PATH with ENGINE in {ENGINES}")
    path = Path(path).resolve()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"source does not exist: {path}")
    return engine.lower(), path


def main():
    """Index manual sources given as ENGINE=PATH and print the index path and row count.

    Raises:
        SystemExit: Arguments are invalid or no sections were found.
        OSError: A source cannot be read, pdftotext is missing, or the index cannot be written.
        subprocess.CalledProcessError: pdftotext fails on a PDF.
    """
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=parse_source, action="append", required=True,
                        metavar="ENGINE=PATH",
                        help=f"Manual file or directory ({', '.join(SUFFIXES)}); repeatable")
    parser.add_argument("--output", type=Path,
                        default=root / "build/mcp_alquimia/manual_index.sqlite")
    args = parser.parse_args()
    rows = collect_rows(args.source)
    if not rows:
        parser.error("No manual sections found in the given sources.")
    write_index(rows, args.output.resolve())
    print(f"{args.output.resolve()}: {len(rows)} sections")


if __name__ == "__main__":
    main()
