"""Build a small manual index and query it directly and through the MCP tools."""

import importlib.util
from pathlib import Path
import shutil
import sys

import anyio
from mcp import Client
import pytest

from mcp_alquimia.manual_search import MAX_SECTION_CHARS, ManualIndex
from mcp_alquimia.mcp_server import create_server
from mcp_alquimia.session_manager import SessionManager


PFLOTRAN_RST = """\
=========
Chemistry
=========

The CHEMISTRY card defines the reaction network.

MINERALS
--------

Lists the minerals, for example Calcite, that may dissolve or precipitate.

MINERAL_KINETICS
----------------

Gives each mineral a RATE_CONSTANT in mol/m^2-sec.
"""

CRUNCHFLOW_MD = """\
# Keyword blocks

## AQUEOUS_KINETICS

Rate laws for aqueous reactions such as methane oxidation.

```
# not a heading inside a fence
```

## ION_EXCHANGE

Exchange sites and selectivity coefficients.
"""


@pytest.fixture
def anyio_backend():
    """Use the asyncio backend supported by the project's installed MCP SDK."""
    return "asyncio"


@pytest.fixture(scope="module")
def builder():
    """Load the standalone index builder, which has no MCP package dependencies."""
    path = Path(__file__).resolve().parents[1] / "build_tools/build_manual_index.py"
    spec = importlib.util.spec_from_file_location("build_manual_index", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def index_path(builder, tmp_path):
    """Index one RST PFLOTRAN manual and one Markdown CrunchFlow manual."""
    (tmp_path / "pflotran").mkdir()
    (tmp_path / "pflotran/chemistry.rst").write_text(PFLOTRAN_RST)
    (tmp_path / "crunchflow").mkdir()
    (tmp_path / "crunchflow/keywords.md").write_text(CRUNCHFLOW_MD)
    output = tmp_path / "index/manual_index.sqlite"
    builder.write_index(builder.collect_rows([
        ("pflotran", tmp_path / "pflotran"), ("crunchflow", tmp_path / "crunchflow")]), output)
    return output


def test_headings_become_title_paths(builder):
    """RST levels follow adornment order; fenced Markdown lines are not headings."""
    rst = [(path, body.strip()) for path, body in builder.split_rst(PFLOTRAN_RST) if body.strip()]
    assert [path for path, _ in rst] == [
        ["Chemistry"], ["Chemistry", "MINERALS"], ["Chemistry", "MINERAL_KINETICS"]]
    markdown = [path for path, body in builder.split_markdown(CRUNCHFLOW_MD) if body.strip()]
    assert markdown == [["Keyword blocks", "AQUEOUS_KINETICS"], ["Keyword blocks", "ION_EXCHANGE"]]


def test_long_sections_split_on_paragraphs(builder):
    """No part exceeds the limit, and short paragraphs stay together."""
    # 462 chars
    body = "\n\n".join(["a" * 40] * 5 + ["b" * 250])
    parts = builder.split_long(body, limit=100)
    assert all(len(part) <= 100 for part in parts)
    assert parts[0] == "\n\n".join(["a" * 40] * 2)
    assert builder.SPLIT_CHARS <= MAX_SECTION_CHARS


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="needs poppler pdftotext")
def test_pdf_pages_become_sections(builder, monkeypatch, tmp_path):
    """Each form-feed separated page is its own titled section."""
    pdf = tmp_path / "CrunchFlowManual.pdf"
    # Creates the actual file at that path
    # If the file already exists
    # it simply updates its modification timestamp 
    # without changing its contents.
    pdf.touch()
    monkeypatch.setattr(builder.subprocess, "run", lambda *args, **kwargs: type(
        "Result", (), {"stdout": "Page one text\fPage two text"})())
    rows = builder.sections_from_file("crunchflow", pdf, pdf)
    assert [row["title"] for row in rows] == [
        "CrunchFlowManual page 1", "CrunchFlowManual page 2"]
    assert rows[1]["doc_id"] == "crunchflow/crunchflowmanual/1"


def test_search_ranks_keyword_sections(index_path):
    """Keywords with underscores and punctuation match their own section first."""
    manuals = ManualIndex(index_path)
    best = manuals.search("MINERAL_KINETICS rate constant?")["results"][0]
    assert best["title"] == "Chemistry > MINERAL_KINETICS"
    assert best["engine"] == "pflotran"
    only_crunch = manuals.search("kinetics", engine="crunchflow")["results"]
    assert only_crunch and {hit["engine"] for hit in only_crunch} == {"crunchflow"}
    assert manuals.search('methane* (oxidation) "NEAR', limit=1)["results"][0]["title"].endswith(
        "AQUEOUS_KINETICS")
    assert manuals.search("AND OR NOT")["results"] is not None
    section = manuals.read_section(best["doc_id"])
    assert "RATE_CONSTANT" in section["text"]
    assert section["source"] == "chemistry.rst"
    with pytest.raises(ValueError, match="at least one word"):
        manuals.search("?!")
    with pytest.raises(ValueError, match="Unknown doc_id"):
        manuals.read_section("pflotran/missing/0")


@pytest.mark.anyio
async def test_docs_tools_over_mcp(repository_root, index_path, tmp_path):
    """The tools work without engines, validate arguments, and explain a missing index."""
    manager = SessionManager(repository_root, Path(sys.executable), tmp_path / "runs")
    async with Client(create_server(manager, index_path)) as client:
        found = await client.call_tool("search_docs", {"query": "ion exchange", "limit": 1})
        assert not found.is_error, found
        hit, = found.structured_content["results"]
        assert hit["title"] == "Keyword blocks > ION_EXCHANGE"
        section = await client.call_tool("read_doc", {"doc_id": hit["doc_id"]})
        assert "selectivity" in section.structured_content["text"]
        for arguments in ({"query": "x", "engine": "onnx"}, {"query": "x", "limit": 0},
                          {"query": "x", "top_k": 3}):
            assert (await client.call_tool("search_docs", arguments)).is_error
    async with Client(create_server(manager, tmp_path / "absent.sqlite")) as client:
        missing = await client.call_tool("search_docs", {"query": "calcite"})
        assert missing.is_error
        assert "build_manual_index.py" in missing.content[0].text
