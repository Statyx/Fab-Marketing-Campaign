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
  verbatims  -- a "why" question must reach the corpus, not be answered from the numbers.

Both subordinates are A2A tools, so a check must match the CONNECTION NAME on the tool item,
not the item type: matching the type alone would pass while the supervisor asked the corpus for
a figure. Each routing check therefore also asserts the OTHER tool stayed out of it.
"""
from __future__ import annotations

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

    print("\n" + "=" * 74)
    print("VERIFY -- invoking the supervisor; creating it proved nothing")
    print("=" * 74)

    # 1. A quantity must reach Fabric over A2A, and come back carrying its scope.
    q = ("Combien de clients ont un churn_risk_score superieur ou egal au seuil de la cohorte "
         "actionnable ? Precise la colonne et le filtre utilises.")
    ans, items, dt = _ask(client, name, model, q)
    routed, detail = _routed(items, data_tool, voc_tool)
    has_digit = any(c.isdigit() for c in ans)
    scoped = any(m in ans.lower() for m in READING_MARKERS)
    ok = routed and has_digit and scoped
    _report("quantity routed to Fabric, answer carries its scope", ok,
            f"{detail}, figure present={has_digit}, scope named={scoped}",
            ans, _types(items), dt)
    results.append(ok)

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

    # 3. A motive question must reach the corpus, not the numbers.
    q = ("Que reprochent les clients qui se plaignent de la pression email ? "
         "Cite des verbatims.")
    ans, items, dt = _ask(client, name, model, q)
    ok, detail = _routed(items, voc_tool, data_tool)
    _report("motive question reaches the verbatim corpus", ok, detail,
            ans, _types(items), dt)
    results.append(ok)

    passed = sum(1 for r in results if r)
    print("\n" + "-" * 74)
    print(f"   {passed}/{len(results)} checks passed")
    if passed != len(results):
        print("   A failure here is not cosmetic: it means the supervisor answered without")
        print("   the source it was supposed to use, or dropped the scope from a figure.")
    return 0 if passed == len(results) else 1
