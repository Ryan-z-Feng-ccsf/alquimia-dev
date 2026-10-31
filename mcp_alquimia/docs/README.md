# Knowledge base sources

These files are the sources of the `search_docs` and `read_doc` MCP tools. They are indexed
into one SQLite FTS5 file, `build/mcp_alquimia/manual_index.sqlite`, which the server reads at
run time. The container image builds and ships the index; the sources themselves are not
read by the server.

| Directory | `engine` in search_docs | Contents |
|---|---|---|
| `pflotran/` | `pflotran` | PFLOTRAN user guide CHEMISTRY and CONSTRAINT cards, geochemical database format, and the reactive transport theory guide, trimmed to what Alquimia uses (reStructuredText) |
| `crunchflow/` | `crunchflow` | CrunchFlow user's manual (2009), condensed to the chemistry keyword blocks, database formats and concepts (Markdown) |
| `alquimia/` | `alquimia` | Known issues and limitations of the engines as driven through Alquimia and this MCP server (Markdown) |

Each engine directory has a `SOURCE` file recording where its content came from and what was
removed. `pflotran/LICENSE` and `pflotran/COPYRIGHT` belong to the PFLOTRAN documentation.

## Rebuilding the index

From the repository root:

```bash
python mcp_alquimia/build_tools/build_manual_index.py \
  --source pflotran=mcp_alquimia/docs/pflotran \
  --source crunchflow=mcp_alquimia/docs/crunchflow \
  --source alquimia=mcp_alquimia/docs/alquimia
```

Each `--source` directory is indexed recursively. A running server picks up a rebuilt index on
its next tool call; the container image must be rebuilt.

## Writing for the index

- Every `.rst`, `.md`, `.txt` and `.pdf` file under a source directory is indexed. Keep notes
  that are not manual content in extensionless files such as `SOURCE`, or outside the source
  directories, like this README.
- Sections are split on headings, and the heading path becomes the result title (for example
  `CrunchFlow MINERALS > Input file entries`). Use one heading per keyword or topic, and put
  the keyword or the symptom a user would search for in the heading.
- Sections longer than 6000 characters are split into numbered parts, so prefer shorter
  sections.
- Prefer Markdown or reStructuredText over PDF: a PDF can only be split per page, and PDF
  equation fonts often extract as unreadable characters. Write equations in LaTeX.
- In `alquimia/known_issues.md`, title each issue with the symptom, then give the engines
  affected, the cause with `file:line` references, and a workaround. Only add issues that are
  confirmed in the code or by a run.
