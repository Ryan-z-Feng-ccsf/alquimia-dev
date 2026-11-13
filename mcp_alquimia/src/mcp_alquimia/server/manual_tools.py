"""MCP tools that search and read the engine manuals and Alquimia known issues."""

from mcp.server.mcpserver import MCPServer

from ..manuals.search import ManualIndex, SearchCollection, SearchLimit, SearchQuery
from .protocol import READ_ONLY, invoke


def register_manual_tools(server: MCPServer, manuals: ManualIndex) -> None:
    """Register search_docs and read_doc.

    Args:
        server: Server receiving the tools.
        manuals: Index both tools read; a missing index is reported per call.
    """

    @server.tool(structured_output=False, annotations=READ_ONLY)
    async def search_docs(
        query: SearchQuery,
        collection: SearchCollection = None,
        limit: SearchLimit = 5,
    ) -> str:
        """Search the PFLOTRAN and CrunchFlow manuals and Alquimia known issues.
        Returns ranked section snippets with the doc_id read_doc takes.

        Args:
            query: Words or input-file keywords, e.g. "AQUEOUS_KINETICS rate constant";
                case-insensitive and stemmed.
            collection: "pflotran" or "crunchflow" manual, or "alquimia" known issues;
                all when omitted.
            limit: Maximum number of sections returned.
        """
        return await invoke(manuals.search, query, collection, limit)

    @server.tool(structured_output=False, annotations=READ_ONLY)
    async def read_doc(doc_id: str) -> str:
        """Return the full text of one manual section.

        Args:
            doc_id: doc_id from a search_docs result.
        """
        return await invoke(manuals.read_section, doc_id)
