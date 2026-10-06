---
name: alphaxiv
description: Use alphaxiv to search for relevant scholarly literature and extract structured evidence.
---

# AlphaXiv

Use AlphaXiv's MCP server to search for relevant scholarly literature and
extract structured evidence.

## Check MCP setup first

Before using AlphaXiv, check that the active harness exposes AlphaXiv MCP tools.
Installing this skill does not configure or connect the MCP server.

If the tools are unavailable, do not claim to search AlphaXiv or silently
substitute another source. Tell the user the AlphaXiv MCP server is not
configured for this harness and direct them to the [official setup guide](https://www.alphaxiv.org/docs/mcp).
Ask them to configure the server for their client and reload or restart the
harness, then continue once the tools are available.

The server endpoint is `https://api.alphaxiv.org/mcp/v1` and uses Streamable
HTTP. The default authentication flow is OAuth 2.1, which prompts the user to
sign in on first use. For headless use, the setup guide describes API-key
authentication. Do not ask the user to paste an API key into the conversation
or store one in project files.

AlphaXiv supports native MCP clients. Browser-hosted assistants cannot connect
directly; follow the setup guide's instructions for a local `mcp-remote` bridge
when using a browser-hosted assistant.

## Research

Use AlphaXiv to discover and inspect primary literature. Ground summaries and
claims in retrieved sources, and distinguish source evidence from your
synthesis.

## Common workflows

### Literature review

1. Use `discover_papers` to find relevant candidates.
2. Search again with varied keywords or questions, or increase `difficulty`,
   to fill topic gaps and improve coverage.
3. For each selected paper, use one `answer_pdf_queries` call with several
   focused questions, such as its research question, method, data, results, and
   limitations. The tool returns page-level excerpts suitable for citations;
   retain the paper and page identifiers with each excerpt.
4. Synthesize evidence across papers, noting consensus, disagreement,
   methodological differences, and open questions instead of listing papers.

### Code analysis

1. Use `discover_papers` to identify the paper.
2. Find its GitHub URL in the results or paper metadata.
3. Call `read_files_from_github_repository` with `path="/"` for the repository
   overview, then inspect relevant directories and files.
4. Ground implementation claims in the files inspected and distinguish them
   from claims made in the paper.

### Deep research

1. Search with multiple framings using `discover_papers`; run independent
   searches in parallel when the harness supports it.
2. Use `get_paper_content` for a structured report, or request `fullText` when
   the report is insufficient.
3. Use `answer_pdf_queries` for citation-ready, page-level support for key
   claims.
4. Use `read_files_from_github_repository` to check implementation claims
   against the paper's code.

### Researcher discovery

1. Use `find_researchers` with a topic and review the defining papers returned
   with the researchers.
2. Use `get_researcher` for positions, education, and collaborators.
3. Use `get_researcher_papers` with `sort="recent"` to inspect current work.
4. Use `follow_researcher` only when the user asks to follow someone; it changes
   the user's AlphaXiv account.

### Library management

1. Start with `list_library` and use the returned `folder_id` values.
2. When requested, use `create_folder` and `save_papers_to_folder` to organize
   papers.
3. Use `move_papers_between_folders` when the user asks to move papers between
   reading-status folders.
4. `edit_private_paper_metadata` edits only papers uploaded by the user, not
   arXiv papers. Use it to correct upload-date defaults or other metadata only
   when requested.

Library tools change the user's AlphaXiv account. Do not create folders, save,
move, follow, or edit papers unless that action is part of the user's request.
