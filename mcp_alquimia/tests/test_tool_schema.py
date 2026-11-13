"""Derive tool descriptions from docstrings and compact pydantic schemas for the model."""

from mcp.server.mcpserver import MCPServer
import pytest

from mcp_alquimia.tool_protocol import prepare_tool_schemas
from mcp_alquimia.tool_schema import compact_schema, split_docstring


def test_docstring_summary_and_args_are_split():
    """The first paragraph and Args entries are sent; developer notes and Returns are not."""
    docstring = """Run steps,
        continuing the state.

        Developer note that stays in the code.

        Args:
            session_id: ID returned
                by setup.
            limit (int): Maximum count.

        Returns:
            Not sent.
        """
    summary, arguments = split_docstring(docstring)
    assert summary == "Run steps, continuing the state."
    assert arguments == {
        "session_id": "ID returned by setup.",
        "limit": "Maximum count.",
    }
    assert split_docstring(None) == ("", {})


@pytest.mark.parametrize("width", [2, 4, 8])
def test_args_entries_follow_the_docstring_indentation(width):
    """Any consistent indentation works; a deeper `word:` line continues an entry."""
    entry, more = " " * width, " " * (2 * width)
    docstring = (
        "Summary.\n\nArgs:\n"
        f"{entry}query: Words to match,\n"
        f"{more}e.g.: AQUEOUS_KINETICS.\n"
        f"{entry}limit: Maximum count.\n"
    )
    assert split_docstring(docstring)[1] == {
        "query": "Words to match, e.g.: AQUEOUS_KINETICS.",
        "limit": "Maximum count.",
    }


def test_schema_is_compacted_without_touching_property_names():
    """Titles, propertyNames and extra paragraphs go; other keywords are kept."""
    schema = {
        "title": "setupArguments",
        "properties": {
            # A field may itself be named like a keyword
            "title": {"title": "Title", "type": "string"},
            "rate": {"exclusiveMinimum": 0, "maximum": 1, "type": "number"},
            "names": {"type": "object", "propertyNames": {"maxLength": 64}},
            "optional": {
                "anyOf": [{"$ref": "#/$defs/Model"}, {"type": "null"}],
                "default": None,
                "description": "Kept.",
            },
            "required_null": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "default": 1,
            },
        },
        "$defs": {
            "Model": {"description": "Summary.\n\nDeveloper notes.", "title": "Model"}
        },
    }
    assert compact_schema(schema) == {
        "properties": {
            "title": {"type": "string"},
            "rate": {"exclusiveMinimum": 0, "maximum": 1, "type": "number"},
            "names": {"type": "object"},
            "optional": {"$ref": "#/$defs/Model", "description": "Kept."},
            # A non-null default keeps the explicit null option
            "required_null": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "default": 1,
            },
        },
        "$defs": {"Model": {"description": "Summary."}},
    }


def test_args_must_name_exactly_the_parameters():
    """A missing or stale Args entry fails at startup instead of leaving a parameter bare."""
    server = MCPServer("check")

    @server.tool()
    async def lookup(session_id: str) -> str:
        """Look something up.

        Args:
            sesion_id: Misspelled.
        """
        return session_id

    with pytest.raises(ValueError, match="lookup.*must name exactly"):
        prepare_tool_schemas(server)
