"""Query the prebuilt SQLite FTS5 index of the engine manuals and Alquimia known issues."""

from contextlib import closing
from pathlib import Path
import re
import sqlite3
from typing import Annotated, Any, Literal, get_args

from pydantic import Field


# Document collections the index may hold; build_manual_index.py writes the same names.
Collection = Literal["pflotran", "crunchflow", "alquimia"]
COLLECTIONS: tuple[str, ...] = get_args(Collection)

# search_docs arguments, validated before the index is opened.
# Tool parameters are described by the search_docs docstring Args (tool_schema)
SearchQuery = Annotated[str, Field(min_length=1, max_length=500, pattern=r"\w")]
SearchCollection = Collection | None
SearchLimit = Annotated[int, Field(ge=1, le=20)]

# Section bodies are split near this size at build time; read_section never returns more.
MAX_SECTION_CHARS = 8000

# Clean the operators, punctuation
_TOKEN = re.compile(r"\w+")


def match_expression(query: str) -> str:
    """Turn free text into an FTS5 query that ranks sections by any matching word.

    Each word is quoted, so FTS5 operators and punctuation in the query are inert, and
    a keyword such as AQUEOUS_KINETICS becomes the phrase "aqueous kinetics".

    Raises:
        ValueError: The query contains no words.

    Example:
        "AQUEOUS-KINETICS (Part 1)?" -> "AQUEOUS" OR "KINETICS" OR "Part" OR "1"
        "AQUEOUS_KINETICS (Part 1)?" -> "AQUEOUS_KINETICS" OR "Part" OR "1"
    """
    words = _TOKEN.findall(query)
    if not words:
        raise ValueError("Query must contain at least one word.")
    return " OR ".join(f'"{word}"' for word in words)


class ManualIndex:
    """Read-only access to a manual index file.

    Attributes:
        path: SQLite file written by build_tools/build_manual_index.py.
    """

    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """Open the index read-only.

        Raises:
            FileNotFoundError: The index has not been built.
        """
        if not self.path.is_file():
            raise FileNotFoundError(
                f"Manual index not found: {self.path}. Build it with "
                "python mcp_alquimia/build_tools/build_manual_index.py."
            )
        # Read Only
        return sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)

    def search(
        self, query: str, collection: str | None = None, limit: int = 5
    ) -> dict[str, Any]:
        """Rank manual sections against a free-text query.

        Args:
            query: Words or input-deck keywords; matching is case-insensitive and stemmed.
            collection: Restrict results to one of COLLECTIONS, or None for all.
            limit: Maximum number of sections returned.

        Returns:
            {"results": [{doc_id, collection, title, snippet, score}]}, best match first;
            a higher score is a better match.

        Raises:
            FileNotFoundError: The index has not been built.
            ValueError: The query has no words or the index cannot be read.
        """
        # Example:
        # Phase query
        # Match will split the "AQUEOUS_KINETICS" to "aqueous kinetics"
        sql = (
            # doc_id, collection, title, body, source
            "SELECT doc_id, collection, title, snippet(sections, 3, '', '', ' ... ', 48), "
            # Title hits count five times as much as body hits.
            "bm25(sections, 0.0, 0.0, 5.0, 1.0, 0.0) AS rank "
            "FROM sections WHERE sections MATCH ?"
        )
        parameters: list[Any] = [match_expression(query)]
        if collection is not None:
            sql += " AND collection = ?"
            parameters.append(collection)
        # Set the limit for the sections
        # # Note: SQLite FTS5 BM25 scores are negative. Lower scores indicate higher relevance,
        # so we order in default ascending order.
        sql += " ORDER BY rank LIMIT ?"
        parameters.append(limit)
        # with in SQLite3 only manages the transaction
        # Close it manually
        with closing(self.connect()) as connection:
            try:
                rows = connection.execute(sql, parameters).fetchall()
            except sqlite3.Error as error:
                raise ValueError(f"Manual index query failed: {error}") from error
        return {
            "results": [
                {
                    "doc_id": doc_id,
                    "collection": collection_name,
                    "title": title,
                    "snippet": snippet,
                    "score": round(-rank, 3),   # Get the positive floating number
                }
                for doc_id, collection_name, title, snippet, rank in rows
            ]
        }

    def read_section(self, doc_id: str) -> dict[str, Any]:
        """Return one whole section by the doc_id that search reported.

        Raises:
            FileNotFoundError: The index has not been built.
            ValueError: No section has this doc_id.
        """
        with closing(self.connect()) as connection:
            row = connection.execute(
                # doc_id, collection, title, body, source
                "SELECT collection, title, source, body FROM sections WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        if row is None:
            raise ValueError(
                f"Unknown doc_id: {doc_id}. Use an id returned by search_docs."
            )
        collection, title, source, body = row
        return {
            "doc_id": doc_id,
            "collection": collection,
            "title": title,
            "source": source,
            "text": body[:MAX_SECTION_CHARS],
        }
