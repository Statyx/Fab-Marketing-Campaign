"""Verify the supervisor by invoking it -- the only check that means anything here.

Creating the connection proves nothing: the service accepts a connection pointing at a
nonexistent host with HTTP 200. So every check below invokes the agent and reads the RAW
output items of the response, where a tool call appears as its own item. That is evidence the
tool fired, as opposed to the model producing a plausible sentence.

Every assertion is mechanical -- an item type, a substring, a digit. Nothing here asks a model
to grade another model's answer, because that only moves the question.

The checks map one-to-one onto failures actually observed on this project:
  routing    -- the supervisor must reach Fabric for a quantity, not answer from its weights;
  scope      -- the first live supervisor turned "825 customers at risk by risk_band" into
                "800 clients.", dropping the scope and changing the number;
  ambiguity  -- "at risk" has three legitimate readings returning three different totals, and
                silently picking one is what makes two correct answers look contradictory;
  verbatims  -- a "why" question must reach the corpus, not be answered from the numbers;
  registers  -- the provenance must land in the trailing block the app folds away, not in the
                lead sentence: a reply whose first line is built out of table and column names
                is true, sourced, and unreadable to the marketing lead it was written for.

Both subordinates are A2A tools, so a check must match the CONNECTION NAME on the tool item,
not the item type: matching the type alone would pass while the supervisor asked the corpus for
a figure. Each routing check therefore also asserts the OTHER tool stayed out of it.
"""
from __future__ import annotations

import re
import sys
import time

# Substrings that show the answer named the reading it used. Column and band names are schema,
# so matching on them is stable; matching on a figure would not be.
READING_MARKERS = ("risk_band", "lifecycle_stage", "churn_risk_score", "seuil", "threshold",
                   "critical", "high")
# Wording that shows the agent handed the choice back instead of picking one silently.
ASKBACK_MARKERS = ("laquelle", "quelle definition", "quelle définition", "which reading",
                   "which definition", "souhaitez-vous", "voulez-vous", "précisez", "precisez",
                   "?")
# Shapes that have no business in the PROSE. Not a jargon list: each of these is a thing the
# model writes when it is transcribing a query rather than answering a question. The snake_case
# names are this model's columns and the prefixes its tables; the rest is DAX punctuation.
#
# Note the overlap with READING_MARKERS, and that it is the whole point: the same word proves
# the provenance survived when it sits in the block, and proves the prose was written for the
# wrong reader when it sits in the sentence. Which register it landed in is the finding.
QUERY_SHAPES = ("crm_", "sm_", "churn_risk_score", "risk_band", "lifecycle_stage", "clv_eur",
                "[at risk", " in {", ">=", "countrows", "calculate(", "evaluate ")
# A share relayed with every decimal the engine produced. Mechanical, and it does not care which
# language or unit the model chose: four decimals is already past what anyone reads off a slide.
RAW_FLOAT = re.compile(r"\d[.,]\d{4,}")


def _items(resp) -> list[dict]:
    """Raw output items, normalised to plain dicts."""
    out = []
    for it in getattr(resp, "output", []) or []:
        d = it if isinstance(it, dict) else getattr(it, "model_dump", lambda: {})()
        out.append(d or {})
    return out


def _types(items: list[dict]) -> list[str]:
    return [str(i.get("type") or "") for i in items]


def _fired(items: list[dict], prefix: str) -> bool:
    return any(t.startswith(prefix) for t in _types(items))


def _tool_names(items: list[dict]) -> list[str]:
    """Connection names of the A2A calls that fired, in order.

    Both subordinates are a2a_preview tools now, so the item TYPE no longer says which one ran
    -- only `name` does. A check that matched on the type alone would pass while the supervisor
    asked the corpus for a number, which is precisely the failure being guarded against.
    """
    return [str(i.get("name") or "") for i in items
            if str(i.get("type") or "").startswith("a2a_preview_call")
            and not str(i.get("type") or "").endswith("_output")]


def _routed(items: list[dict], expected: str, forbidden: str) -> tuple[bool, str]:
    called = _tool_names(items)
    hit, miss = expected in called, forbidden in called
    return (hit and not miss,
            f"called={called or '(none)'}, {expected} fired={hit}, {forbidden} fired={miss}")


def _ask(client, agent_name: str, model: str, question: str) -> tuple[str, list[dict], float]:
    oai = client.get_openai_client(agent_name=agent_name)
    t0 = time.time()
    resp = oai.responses.create(model=model, input=question)
    return (getattr(resp, "output_text", "") or "",
            _items(resp), round(time.time() - t0, 2))


def _split_source(text: str) -> tuple[str, str | None]:
    """Split a reply into the prose and the trailing provenance block.

    Mirrors `app-v2/src/services/answer.ts`. Two implementations of one contract can drift, so
    both are pinned to the same cases in their own suite -- and this one exists so the harness
    checks what the READER sees, not what the whole string contains. Without the split, "the
    answer names the column" passes identically whether the column sat in a readable block at
    the end or in the middle of the lead sentence, which is the defect that was reported.

    Strict in the prompt, tolerant here: decoration is stripped rather than enumerated, and the
    LAST marker wins because "source" is an ordinary French word that may appear in prose.
    """
    lines = (text or "").split("\n")
    at = -1
    for i, line in enumerate(lines):
        bare = re.sub(r"[#*_\s:\u2014-]", "", line).lower()
        if bare in ("source", "sources"):
            at = i
    if at == -1:
        return (text or "").strip(), None
    body = "\n".join(lines[:at]).strip()
    block = "\n".join(lines[at + 1:]).strip()
    if not body or not block:
        return (text or "").strip(), None
    return body, block


def _registers(answer: str) -> tuple[bool, str]:
    """Did the provenance land in the block rather than in the sentence?

    Applied to EVERY answer, not to a question of its own. The contract is universal -- one
    question that happens to comply proves nothing about the next -- and the first version of
    this harness learned that the expensive way: it checked one reply and passed, while another
    check in the same run was busy REWARDING a reply whose lead sentence read
    "825 clients acheteurs ont un churn_risk_score superieur ou egal au seuil".
    """
    body, block = _split_source(answer)
    leaked = [s for s in QUERY_SHAPES if s in body.lower()]
    raw = RAW_FLOAT.search(body)
    ok = block is not None and not leaked and raw is None
    return ok, (f"block={'yes' if block else 'MISSING'}, "
                f"identifiers in prose={', '.join(leaked) or 'none'}, "
                f"undigested float={raw.group(0) if raw else 'none'}")


def _safe(text: str) -> str:
    """Console-proof. Real answers carry narrow no-break spaces and typographic quotes, and a
    UnicodeEncodeError in the reporter would kill the run after the evidence was gathered."""
    enc = sys.stdout.encoding or "utf-8"
    return (text or "").encode(enc, errors="replace").decode(enc, errors="replace")


def _report(name: str, passed: bool, detail: str, answer: str, types: list[str], dt: float):
    print(f"\n   [{'PASS' if passed else 'FAIL'}] {name}   ({dt}s)")
    print(f"      items  : {', '.join(t for t in types if t) or '(none)'}")
    print(f"      check  : {detail}")
    body = _safe(answer).strip().replace("\n", "\n               ")
    print(f"      answer : {body[:500] or '(empty)'}")


def run_verification(client, fnd: dict) -> int:
    """Invoke the supervisor once per behaviour. Returns 0 if every check passed."""
    name = fnd["supervisor_agent_name"]
    model = fnd["model_deployment"]
    data_tool = fnd["supervisor_a2a_connection"]
    voc_tool = fnd["supervisor_voc_connection"]
    results: list[bool] = []
    replies: list[tuple[str, str]] = []

    print("\n" + "=" * 74)
    print("VERIFY -- invoking the supervisor; creating it proved nothing")
    print("=" * 74)

    # 1. A quantity must reach Fabric over A2A, and come back carrying its scope -- in the block.
    #    Asserting the scope against the WHOLE reply was the weaker form: it passed identically
    #    whether the column name sat in a foldable block or in the middle of the lead sentence.
    #
    #    The question is asked in the words a marketing lead would use, and names no column. It
    #    used to say "quel churn_risk_score, precise la colonne utilisee" -- written when naming
    #    the column WAS the desired outcome. Under the two registers that question guarantees its
    #    own failure: the reply echoes the identifier it was handed, and the harness books it as a
    #    leak. A check must be phrased in the contract it is testing, or it measures the question.
    q = "Combien de clients acheteurs relevent d'une action de retention prioritaire ?"
    ans, items, dt = _ask(client, name, model, q)
    routed, detail = _routed(items, data_tool, voc_tool)
    _, block = _split_source(ans)
    has_digit = any(c.isdigit() for c in ans)
    scoped = block is not None and any(m in block.lower() for m in READING_MARKERS)
    ok = routed and has_digit and scoped
    _report("quantity routed to Fabric, scope kept in the source block", ok,
            f"{detail}, figure present={has_digit}, scope in block={scoped}",
            ans, _types(items), dt)
    results.append(ok)
    replies.append(("quantity", ans))

    # 2. A vague magnitude word must NOT be resolved silently.
    q = "Combien de clients sont a risque ?"
    ans, items, dt = _ask(client, name, model, q)
    low = ans.lower()
    named = any(m in low for m in READING_MARKERS)
    asked = any(m in low for m in ASKBACK_MARKERS)
    ok = named or asked
    _report("ambiguous 'at risk' is not resolved silently", ok,
            f"reading named={named}, choice handed back={asked}",
            ans, _types(items), dt)
    results.append(ok)
    replies.append(("ambiguity", ans))

    # 3. A motive question must reach the corpus, not the numbers.
    q = ("Que reprochent les clients qui se plaignent de la pression email ? "
         "Cite des verbatims.")
    ans, items, dt = _ask(client, name, model, q)
    ok, detail = _routed(items, voc_tool, data_tool)
    _report("motive question reaches the verbatim corpus", ok, detail,
            ans, _types(items), dt)
    results.append(ok)
    replies.append(("verbatims", ans))

    # 4. A share, which is where the reported defect actually appeared: a lead sentence built out
    #    of table and column identifiers, then the same fields again as a six-bullet form. The
    #    provenance is not the problem and must still be there -- it belongs in the block the app
    #    folds behind a button. So the register check below asks WHERE it landed, never whether
    #    it exists. A share is also the shape that arrives as an undigested ratio.
    q = "Quelle part de la base client est a risque d'attrition ?"
    ans, items, dt = _ask(client, name, model, q)
    ok, detail = _routed(items, data_tool, voc_tool)
    _report("a share is asked of Fabric, not computed here", ok, detail,
            ans, _types(items), dt)
    results.append(ok)
    replies.append(("share", ans))

    # 5. The two registers, over every answer above rather than a question of its own.
    print("\n   -- prose and provenance are in two registers (every answer) --")
    for label, reply in replies:
        ok, detail = _registers(reply)
        print(f"   [{'PASS' if ok else 'FAIL'}] {label:<10} {detail}")
        results.append(ok)

    passed = sum(1 for r in results if r)
    print("\n" + "-" * 74)
    print(f"   {passed}/{len(results)} checks passed")
    if passed != len(results):
        print("   A failure here is not cosmetic: it means the supervisor answered without")
        print("   the source it was supposed to use, or dropped the scope from a figure.")
    return 0 if passed == len(results) else 1
