"""Read, from a Data Agent run trace, which source actually answered — and how.

Only `analyze.database.execute` is authoritative. Every other step is emitted for
BOTH sources within a single run: an ontology answer captured on 3 Aug 2026 ended
its trace with `fewshots.loading` for the *semantic model*. Scanning the whole
trace for a datasource name therefore reports the wrong source roughly half the
time, and it does so most often on exactly the graph questions the demo exists to
show. So: the execute call, or nothing.

"Or nothing" is a real outcome, not a fallback. When the agent replays a cached
answer no query runs, the trace carries no execute call, and the source that
produced the original answer is genuinely unknown. Saying so is correct; guessing
from the remaining steps is how you end up captioning an ontology answer with the
name of the semantic model.
"""

from __future__ import annotations

import json
import re

_EXECUTE = "analyze.database.execute"

# datasource_type as Fabric spells it -> (our key, the language of `code`)
_SOURCES = {
    "Ontology": ("ontology", "GQL"),
    "SemanticModel": ("semantic_model", "DAX"),
}

_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.DOTALL)

_UNKNOWN = {"source": "", "sourceName": "", "queryLanguage": "", "generatedQuery": ""}


def _unfence(code: str) -> str:
    """Return the body of a ``` fenced block, or the text unchanged."""
    m = _FENCE.search(code or "")
    return m.group(1).strip() if m else (code or "").strip()


def _extract_gql(body: str) -> str:
    """Pull the GQL out of the ontology payload.

    The ontology `code` is not a query, it is a JSON envelope that *carries* one:
    {"entitySelector": {"queryType": "GQL", "query": "MATCH ..."}}. Showing the
    envelope on stage shows plumbing; showing the MATCH shows the graph.
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return body
    if isinstance(payload, dict):
        selector = payload.get("entitySelector")
        if isinstance(selector, dict) and selector.get("query"):
            return str(selector["query"]).strip()
    return body


def describe(query_trace) -> dict:
    """Describe the source behind an answer.

    `query_trace` is the list of tool calls the portal collected for one run,
    each a mapping (or object) with `tool` and `arguments`. Returns the source
    key, the datasource display name, the query language and the query the agent
    actually wrote — all empty when no query ran.
    """
    for entry in query_trace or []:
        tool = entry.get("tool") if isinstance(entry, dict) else getattr(entry, "tool", "")
        if tool != _EXECUTE:
            continue
        raw = entry.get("arguments") if isinstance(entry, dict) else getattr(entry, "arguments", "")
        try:
            args = json.loads(raw or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(args, dict):
            continue
        mapped = _SOURCES.get(args.get("datasource_type", ""))
        if not mapped:
            continue
        key, language = mapped
        body = _unfence(args.get("code", ""))
        return {
            "source": key,
            "sourceName": args.get("datasource_name", ""),
            "queryLanguage": language,
            "generatedQuery": _extract_gql(body) if language == "GQL" else body,
        }
    return dict(_UNKNOWN)
