---
applyTo: '**'
description: >-
  Use code-review-graph MCP tools for token-efficient
  codebase exploration and code review.
---

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using file/search tools to
explore the codebase.** The graph is faster, cheaper (fewer
tokens), and gives you structural context (callers, dependents,
test coverage) that file scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool`
- **Understanding impact**: `get_impact_radius_tool`
- **Code review**: `detect_changes_tool` + `get_review_context_tool`
- **Finding relationships**: `query_graph_tool` callers_of/callees_of
- **Architecture questions**: `get_architecture_overview_tool`

Fall back to file/search tools **only** when the graph doesn't
cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Risk-scored change analysis |
| `get_review_context_tool` | Token-efficient source snippets |
| `get_impact_radius_tool` | Blast radius of a change |
| `get_affected_flows_tool` | Impacted execution paths |
| `query_graph_tool` | Trace callers, callees, imports, tests |
| `semantic_search_nodes_tool` | Find functions/classes by keyword |
| `get_architecture_overview_tool` | High-level structure |
| `refactor_tool` | Rename planning, dead code |
| `build_or_update_graph_tool` |	Build or incrementally update the graph |
| `run_postprocess_tool` |	Re-run flow detection, community detection, and FTS indexing |
| `get_minimal_context_tool` |	Ultra-compact context (~100 tokens) — call this first |
| `get_impact_radius_tool` |	Blast radius of changed files |
| `get_review_context_tool` |	Token-optimised review context with structural summary |
| `query_graph_tool` |	Callers, callees, tests, imports, inheritance queries |
| `traverse_graph_tool` |	BFS/DFS traversal from any node with token budget |
| `semantic_search_nodes_tool` |	Search code entities by name or meaning |
| `embed_graph_tool` |	Compute vector embeddings for semantic search |
| `list_graph_stats_tool` |	Graph size and health |
| `get_docs_section_tool` |	Retrieve documentation sections |
| `find_large_functions_tool` |	Find functions/classes exceeding a line-count threshold |
| `list_flows_tool` |	List execution flows sorted by criticality |
| `get_flow_tool` |	Get details of a single execution flow |
| `get_affected_flows_tool` |	Find flows affected by changed files |
| `list_communities_tool` |	List detected code communities |
| `get_community_tool` |	Get details of a single community |
| `get_architecture_overview_tool` |	Architecture overview from community structure |
| `detect_changes_tool` |	Risk-scored change impact analysis for code review |
| `get_hub_nodes_tool` |	Find most-connected nodes (architectural hotspots) |
| `get_bridge_nodes_tool` |	Find chokepoints via betweenness centrality |
| `get_knowledge_gaps_tool` |	Identify structural weaknesses and untested hotspots |
| `get_surprising_connections_tool` |	Detect unexpected cross-community coupling |
| `get_suggested_questions_tool` |	Auto-generated review questions from analysis |
| `refactor_tool` |	Rename preview, dead code detection, suggestions |
| `apply_refactor_tool` |	Apply a previously previewed refactoring |
| `generate_wiki_tool` |	Generate markdown wiki from communities |
| `get_wiki_page_tool` |	Retrieve a specific wiki page |
| `list_repos_tool` |	List registered repositories |
| `cross_repo_search_tool` |	Search across all registered repositories |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
