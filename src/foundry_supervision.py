#!/usr/bin/env python3
"""
Supervision loop for the Foundry agent grounded on Fabric.

    python foundry_supervision.py --dry-run   # print the golden set + local truth, call nothing
    python foundry_supervision.py             # replay the golden set through Foundry
    python foundry_supervision.py --only sup-003
    python foundry_supervision.py --repeat 4  # same question N times -- does the figure hold?

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT
    It replays a fixed set of questions, records what came back, and compares the figures to
    the truth recomputed from data/raw. That catches four things: an agent that answers
    nothing, an agent that errors, an agent that invents, and data drift between the local
    draw and the deployed Lakehouse.

    The RUN ITSELF does not tell you how the answer was produced, and the gap differs by
    binding:

      fabric-data-agent -- the answer text cannot say whether the ontology (GQL) or the
          semantic model (DAX) answered. A coherent paragraph from the wrong source is
          indistinguishable from a correct one at the output.

      fabric-iq -- the text cannot say whether the tool returned an AGGREGATE or a set of
          records the model then counted. Those two produce the same sentence, and on this
          dataset they can produce the same number. A PASS is therefore weaker evidence under
          this binding than it looks: it says the figure was right, not that a measure was
          evaluated.

    THE TELEMETRY DOES SAY IT -- measured 2026-08-05, not read in a doc. With Application
    Insights connected to the project, one question emits five nested dependency spans, and
    the chain names every hop:

        invoke_agent Marketing-Churn-Front-Door:1                          (Foundry agent)
        `- execute_tool mcp_fabriciq-dataagent.DataAgent_Marketing_Churn_Agent
           `- invoke_agent Marketing_Churn_Agent                           (Fabric data agent)
              `- execute_tool analyze_semantic_model                       (DAX side)
                 `- chat gpt-5.4-2026-03-05

    So `expected_source` IS falsifiable after the fact: the tool span under the Fabric agent
    names the side that ran. Observed on 4 of 4 replays of sup-001, all success=True.

    Two things this does NOT establish, and neither should be assumed:
      - the ONTOLOGY span name. No graph question has been traced yet, so the GQL-side span
        is unnamed here on purpose rather than guessed.
      - that the tool span returning successfully means it returned the RIGHT rows. A span
        with an empty result still reads success=True.

    Query used (needs the App Insights component name, not its appId, when -g is passed):
        az monitor app-insights query --app <name> -g <rg> --analytics-query \
          "dependencies | where timestamp > ago(4h) | project timestamp, operation_Id, name, success, duration"

    Nothing here EMITS OpenTelemetry from this script -- the spans above are produced service
    side by Foundry and Fabric. Client instrumentation remains unexercised.

THE ONE UNVERIFIED SURFACE IN THIS FILE
    `_ask()`. The SDK gives `get_openai_client(agent_name=...)`, which points the OpenAI
    client at `{endpoint}/agents/{name}/endpoint/protocols/openai` -- that much is read off
    the installed SDK. What the `model` field must carry on that endpoint (the agent name or
    the model deployment) was NOT verified against a live project, so the call tries both and
    records which one worked, in the run log, under `invocation_shape`. First real run, read
    that field and pin it.

OUTPUT
    data/supervision/run_<timestamp>.json  -- answers, latencies, verdicts, response ids.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from helpers import load_config, load_state, output_path, print_step, raw_dir
from deploy_foundry_agent import foundry_config, project_client, app_insights_status

ROOT = Path(__file__).resolve().parent.parent
RAW = raw_dir()
OUT_DIR = output_path(ROOT / "data" / "supervision", "supervision")

ONTOLOGY = "ontology"          # relationships, root cause, impact -- GQL
SEMANTIC_MODEL = "semantic_model"  # every number -- DAX

# `expected_source` means two different things depending on the binding, and conflating them
# is how a green run gets over-read:
#   binding = fabric-data-agent -> a ROUTING claim. The Fabric agent chose GQL or DAX, and the
#       trace can confirm which. The label is falsifiable.
#   binding = fabric-iq         -> a QUESTION KIND, nothing more. There is no routing to check:
#       Foundry reasons over retrieved content, and SEMANTIC_MODEL simply marks the questions
#       that need a real aggregate -- exactly the ones where a retrieval can be turned into a
#       plausible total. Under this binding those rows are the ones to read hardest.


# -- the golden set -----------------------------------------------------------
# Questions only. No expected figure is written here: the numbers are recomputed from
# data/raw at run time, so this file cannot go stale and cannot leak a stale fact into the
# demo. `expected_source` is a claim about routing that only a trace can settle -- it is
# printed as a checklist, never asserted here.
GOLDEN = [
    # The two "at risk" questions NAME the definition, and that is not padding. Asked without
    # it, the Fabric data agent was measured picking a different one on roughly one call in
    # four -- risk_band (825) or lifecycle_stage = at_risk (593). Both figures are correct;
    # the QUESTION was wrong. Naming the column costs nothing and removes the coin flip.
    # No digit here: the threshold lives in config, and a number written into a question is a
    # fact that goes stale silently.
    {"id": "sup-001", "expected_source": SEMANTIC_MODEL,
     "question": "Combien de clients sont a risque de churn, en te basant sur la bande de "
                 "risque (risk_band) et non sur le lifecycle_stage ?",
     "expect": [{"kind": "number", "metric": "customers_at_risk"}]},

    {"id": "sup-002", "expected_source": SEMANTIC_MODEL,
     "question": "Quelle est la CLV totale exposee sur la cohorte a risque, cohorte definie "
                 "par la bande de risque (risk_band) et non par le lifecycle_stage ?",
     "expect": [{"kind": "number", "metric": "clv_at_risk", "tol_pct": 20}]},

    {"id": "sup-003", "expected_source": SEMANTIC_MODEL,
     "question": "Quelle campagne a la pression marketing la plus forte, "
                 "en nombre d'envois par client ?",
     "expect": [{"kind": "mentions", "any": ["culprit_campaign_name", "culprit_campaign_id"]},
                {"kind": "number", "metric": "culprit_sends_per_customer", "tol_pct": 15}]},

    {"id": "sup-004", "expected_source": SEMANTIC_MODEL,
     "question": "Combien de desabonnements la campagne la plus intense a-t-elle generes ?",
     "expect": [{"kind": "number", "metric": "culprit_unsubscribes", "tol_pct": 20}]},

    {"id": "sup-005", "expected_source": ONTOLOGY,
     "question": "Quels segments la campagne Black Friday Blast a-t-elle cibles, "
                 "et quels clients a-t-elle touches ?",
     "expect": [{"kind": "mentions", "any": ["victim_segment_id", "victim_segment_name"]}]},

    {"id": "sup-006", "expected_source": ONTOLOGY,
     "question": "Pour un client a risque, quelles campagnes l'ont touche "
                 "et qu'a-t-il achete ?",
     "expect": [{"kind": "non_empty"}]},

    {"id": "sup-007", "expected_source": SEMANTIC_MODEL,
     "question": "Quel est le chiffre d'affaires total et le panier moyen ?",
     "expect": [{"kind": "number", "metric": "revenue_total", "tol_pct": 15},
                {"kind": "number", "metric": "aov", "tol_pct": 15}]},

    {"id": "sup-008", "expected_source": SEMANTIC_MODEL,
     "question": "Repartis les clients par bande de risque.",
     "expect": [{"kind": "number", "metric": "band_high"},
                {"kind": "number", "metric": "band_critical"}]},

    # The refusal case. A contact who never ordered has a conversion problem, not a churn
    # problem. An agent that happily scores prospects has stopped reading the model.
    {"id": "sup-009", "expected_source": SEMANTIC_MODEL,
     "question": "Quel est le score de churn moyen des contacts qui n'ont jamais commande ?",
     "expect": [{"kind": "mentions",
                 "any_literal": ["prospect", "jamais command", "pas de score",
                                 "conversion", "non applicable", "aucun score"]}]},

    # The honesty case. Nothing in this dataset answers it. "I don't know" is the pass.
    {"id": "sup-010", "expected_source": SEMANTIC_MODEL,
     "question": "Quel est le taux de churn de notre concurrent principal ?",
     "expect": [{"kind": "mentions",
                 "any_literal": ["pas", "aucun", "non disponible", "ne dispose",
                                 "introuvable", "n'est pas"]}]},
]

DEFAULT_TOL_PCT = 10.0


# -- local truth --------------------------------------------------------------
def local_truth(cfg) -> dict:
    """Recompute the demo's facts from data/raw. Never sent to the agent -- only compared.

    READ THE VERDICTS CAREFULLY. On an aged project this is a STALE reference: data/raw and
    the deployed Lakehouse hold different generations, and they cannot be reconciled by
    regenerating (the generator has moved on since the load). A figure landing outside
    tolerance is therefore reported as DRIFT, meaning "the local reference disagrees", not
    "the agent is wrong" -- what Fabric returns is the reference that counts.

    The LITERALS below (campaign name, segment id) come from config.yaml, not from the CSVs,
    so those checks stay valid regardless of the drift. For a verdict that depends on no
    local reference at all, use --repeat and read `stability`.
    """
    if not (RAW / "crm" / "crm_customer_profile.csv").exists():
        raise SystemExit(f"No generated dataset in {RAW}. Run: python src/generate_data.py")

    st = cfg["storyline"]
    threshold = cfg["churn_model"]["at_risk_threshold"]

    profile = pd.read_csv(RAW / "crm" / "crm_customer_profile.csv")
    sends = pd.read_csv(RAW / "marketing" / "marketing_sends.csv")
    events = pd.read_csv(RAW / "marketing" / "marketing_events.csv")
    orders = pd.read_csv(RAW / "commerce" / "orders.csv")
    lines = pd.read_csv(RAW / "commerce" / "order_lines.csv")

    buyers = profile[profile["is_customer"]]
    at_risk = buyers[buyers["churn_risk_score"] >= threshold]

    culprit = st["culprit_campaign_id"]
    unsub = events[events["event_type"] == "unsubscribe"]

    revenue = float(lines["line_total_eur"].sum())
    n_orders = int(len(orders))

    # Marketing pressure is a PER-CAMPAIGN ratio, and it has to be computed that way.
    # Pooling the other campaigns first gives sends per customer across all of them at once
    # (~5 for nineteen campaigns), which is larger than the culprit's own ~3.8 and quietly
    # inverts the comparison the entire storyline rests on.
    per_campaign = (sends.groupby("campaign_id")
                    .agg(sends=("send_id", "size"), customers=("customer_id", "nunique")))
    per_campaign["spc"] = per_campaign["sends"] / per_campaign["customers"]
    culprit_spc = float(per_campaign["spc"].get(culprit, 0.0))
    others = per_campaign["spc"].drop(index=culprit, errors="ignore")
    other_spc = float(others.mean()) if len(others) else 0.0

    bands = profile["risk_band"].value_counts()

    return {
        "customers_at_risk": float(len(at_risk)),
        "clv_at_risk": float(at_risk["clv_eur"].sum()),
        "culprit_sends_per_customer": culprit_spc,
        "other_sends_per_customer": other_spc,
        "culprit_unsubscribes": float(len(unsub[unsub["campaign_id"] == culprit])),
        "revenue_total": revenue,
        "total_orders": float(n_orders),
        "aov": revenue / n_orders if n_orders else 0.0,
        "band_high": float(bands.get("High", 0)),
        "band_critical": float(bands.get("Critical", 0)),
        "band_prospect": float(bands.get("Prospect", 0)),
        # literals used by `mentions` checks, resolved from config -- never hardcoded here
        "culprit_campaign_id": culprit,
        "culprit_campaign_name": st["culprit_campaign_name"],
        "victim_segment_id": st["victim_segment_id"],
        "victim_segment_name": next(
            (s["name"] for s in cfg["segments"] if s["id"] == st["victim_segment_id"]),
            st["victim_segment_id"]),
    }


# -- answer parsing -----------------------------------------------------------
_NUM = re.compile(r"(\d[\d\s\u00a0.,]*)\s*(M|k|K)?", re.UNICODE)


def numbers_in(text: str) -> list[float]:
    """Pull every plausible figure out of an answer, French or English formatted.

    '1 234,56' / '1,234.56' / '4,96 M' / '186 792 EUR' all have to land on the same value,
    otherwise a correct answer scores as a miss because of a thousands separator.
    """
    out = []
    for raw, suffix in _NUM.findall(text or ""):
        s = raw.strip().replace("\u00a0", "").replace(" ", "")
        if not s or not s[0].isdigit():
            continue
        if "," in s and "." in s:                      # 1,234.56  or  1.234,56
            s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
        elif "," in s:
            head, _, tail = s.rpartition(",")
            s = f"{head}.{tail}" if len(tail) <= 2 else s.replace(",", "")
        elif s.count(".") > 1 or (s.count(".") == 1 and len(s.rpartition(".")[2]) == 3):
            s = s.replace(".", "")                     # 1.234 / 1.234.567 are thousands
        try:
            v = float(s)
        except ValueError:
            continue
        mult = {"M": 1e6, "k": 1e3, "K": 1e3}.get(suffix, 1.0)
        out.append(v * mult)
        if mult != 1.0:
            out.append(v)
    return out


# -- stability ----------------------------------------------------------------
def headline_number(text: str) -> float | None:
    """The figure the answer LEADS with.

    Deliberately naive: French answers to these questions open with the claim ("Il y a 593
    clients a risque..."), so the first number is the answer and the rest is commentary. An
    answer opening with a year or a threshold will be read wrong -- but this value is only
    ever compared against ITSELF across repeats, never against a truth, so a systematic
    misread stays systematic and costs nothing.
    """
    nums = numbers_in(text)
    return nums[0] if nums else None


def stability_of(answers: list[str], tol_pct: float = 2.0) -> dict:
    """Same question, N times: does the leading figure hold?

    This needs NO reference data, which is exactly the point. The Fabric data agent was
    measured oscillating on its own, without Foundry in the loop, between two legitimate
    readings of "at risk" -- risk_band (825) and lifecycle_stage (593) -- on roughly one call
    in four. Against a reference that moves, a single comparison proves nothing; but an agent
    contradicting ITSELF is a defect under any definition, and it needs no ground truth to see.

    Returns stable=None when no repeat produced a figure at all -- absence is not agreement.
    """
    heads = [headline_number(a) for a in answers]
    seen = [h for h in heads if h is not None]
    if not seen:
        return {"runs": len(answers), "headline": heads, "stable": None,
                "spread_pct": None, "note": "no figure in any answer"}

    lo, hi = min(seen), max(seen)
    spread = 0.0 if hi == 0 else abs(hi - lo) / abs(hi) * 100.0
    complete = len(seen) == len(heads)
    stable = complete and spread <= tol_pct

    if not complete:
        note = f"{len(heads) - len(seen)} of {len(heads)} repeats returned no figure"
    elif not stable:
        note = f"the leading figure moved between {lo:,.2f} and {hi:,.2f}"
    else:
        note = ""
    return {"runs": len(answers), "headline": heads, "stable": stable,
            "spread_pct": round(spread, 2), "note": note}


def check_answer(answer: str, expects: list[dict], truth: dict) -> tuple[str, list[str]]:
    """Score one answer. Returns (verdict, notes)."""
    notes, worst = [], "PASS"

    def demote(level):
        nonlocal worst
        rank = {"PASS": 0, "DRIFT": 1, "MISS": 2}
        if rank[level] > rank[worst]:
            worst = level

    text = (answer or "").strip()
    if not text:
        return "EMPTY", ["no answer text returned"]

    found = numbers_in(text)
    low = text.lower()

    for exp in expects:
        kind = exp["kind"]
        if kind == "non_empty":
            if len(text) < 40:
                notes.append("answer suspiciously short")
                demote("MISS")

        elif kind == "number":
            metric = exp["metric"]
            want = truth[metric]
            tol = exp.get("tol_pct", DEFAULT_TOL_PCT) / 100.0
            hit = any(abs(v - want) <= max(abs(want) * tol, 0.5) for v in found)
            if not hit:
                closest = min(found, key=lambda v: abs(v - want), default=None)
                notes.append(f"{metric}: expected ~{want:,.2f}, closest in answer "
                             f"{'none' if closest is None else format(closest, ',.2f')}")
                # A number that is merely off is drift until a trace says otherwise; a
                # number-free answer to a number question is a miss.
                demote("DRIFT" if found else "MISS")

        elif kind == "mentions":
            tokens = [str(truth[k]) for k in exp.get("any", [])] + exp.get("any_literal", [])
            if not any(t.lower() in low for t in tokens):
                notes.append(f"none of {tokens} appear in the answer")
                demote("MISS")

    return worst, notes


# -- invocation ---------------------------------------------------------------
def _ask(client, agent_name: str, model_deployment: str, question: str) -> dict:
    """Ask the Foundry agent one question.

    See the module docstring: the `model` field on the per-agent endpoint is the one shape
    not verified against a live project. Both candidates are tried, and the one that worked
    is recorded so the next run can stop guessing.
    """
    oai = client.get_openai_client(agent_name=agent_name)
    last = None
    for shape, model in (("agent_name", agent_name), ("model_deployment", model_deployment)):
        t0 = time.time()
        try:
            resp = oai.responses.create(model=model, input=question)
        except Exception as e:  # noqa: BLE001 -- the point is to record, then try the other
            last = f"{type(e).__name__}: {e}"
            continue
        return {
            "answer": _text_of(resp),
            "response_id": getattr(resp, "id", None),
            "invocation_shape": shape,
            "latency_s": round(time.time() - t0, 2),
            "error": None,
        }
    return {"answer": "", "response_id": None, "invocation_shape": None,
            "latency_s": None, "error": last}


def _text_of(resp) -> str:
    """Best-effort text extraction, tolerant of the response shape."""
    txt = getattr(resp, "output_text", None)
    if txt:
        return txt
    chunks = []
    for item in getattr(resp, "output", []) or []:
        for c in getattr(item, "content", []) or []:
            t = getattr(c, "text", None)
            if isinstance(t, str):
                chunks.append(t)
            elif t is not None and hasattr(t, "value"):
                chunks.append(t.value)
    return "\n".join(chunks)


# -- reporting ----------------------------------------------------------------
def print_truth(truth: dict):
    print("   Local truth recomputed from data/raw (never sent to the agent):")
    for k in ("customers_at_risk", "clv_at_risk", "revenue_total", "aov",
              "culprit_sends_per_customer", "other_sends_per_customer",
              "culprit_unsubscribes", "band_high", "band_critical"):
        print(f"      {k:<28} {truth[k]:,.2f}")
    print(f"      {'culprit':<28} {truth['culprit_campaign_name']} "
          f"({truth['culprit_campaign_id']}) -> {truth['victim_segment_id']}")


def is_raw_retrieval(binding: str, target: str | None = None) -> bool:
    """Does the grounding hand back rows, or an answer someone else already computed?

    fabric-iq is a ROUTER. Fronting the published Fabric data agent it inherits that agent's
    semantics -- the churn threshold, "buyers only", the DAX measures -- and a matching
    figure really was measured. Fronting an ontology or a semantic model it returns matched
    records, and a matching figure may be arithmetic the model did on top of a partial set.
    Same binding, opposite reading of the same verdict.
    """
    return binding == "fabric-iq" and (target or "ontology") != "data_agent"


def print_report(results: list[dict], binding: str = "fabric-iq", target: str | None = None):
    raw = is_raw_retrieval(binding, target)
    print(f"\n{'id':<9}{'verdict':<9}{'expected source':<17}{'lat':>6}  response id")
    print("-" * 78)
    for r in results:
        lat = f"{r['latency_s']:.1f}s" if r.get("latency_s") else "  -"
        print(f"{r['id']:<9}{r['verdict']:<9}{r['expected_source']:<17}{lat:>6}  "
              f"{r.get('response_id') or '-'}")
        for n in r.get("notes", []):
            print(f"          - {n}")
        st = r.get("stability")
        if st:
            flag = {True: "stable", False: "UNSTABLE", None: "no figure"}[st["stable"]]
            print(f"          ~ {st['runs']} repeats: {flag}"
                  + (f" -- {st['note']}" if st["note"] else ""))
        if r.get("error"):
            print(f"          ! {r['error']}")

    tally = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("\n   " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))

    if tally.get("DRIFT"):
        print("\n   DRIFT = the figure came back but does not match the LOCAL draw in data/raw.")
        print("   On an aged project that is expected and not actionable: the Lakehouse was")
        print("   loaded from an earlier generation, and regenerating does not reconcile them")
        print("   (the generator has since moved on). Read DRIFT as 'local reference is stale',")
        print("   NOT as 'the agent is wrong'. What Fabric returns is the reference that counts.")
        print("   For a verdict that needs no local reference at all, use --repeat.")

    unstable = [r for r in results if (r.get("stability") or {}).get("stable") is False]
    if unstable:
        print(f"\n   UNSTABLE on {len(unstable)} question(s): the same question got different")
        print("   leading figures. Before blaming this agent, ask the Fabric data agent the")
        print("   same question directly N times -- it was measured oscillating on its own")
        print("   between two legitimate definitions (risk_band vs lifecycle_stage) with no")
        print("   Foundry in the loop. If Fabric oscillates too, the fix is the QUESTION (name")
        print("   the measure), not the agent.")
    if tally.get("EMPTY") or tally.get("ERROR"):
        print("\n   EMPTY / ERROR on every row usually means the tool is waiting for an approval")
        print("   that has nowhere to appear.")
        if binding == "fabric-iq":
            print("   Set foundry.require_approval: never in config.yaml and redeploy the agent --")
            print("   unset, the SDK omits the field and the service defaults to 'always'.")
        else:
            print("   Open the agent alone in the playground, ask it something that forces the")
            print("   tool call, and choose 'Always approve this tool'.")

    print("\n   What this run CANNOT settle -- take these to the Foundry trace,")
    print("   one Conversation ID at a time:")
    if raw:
        # There is no routing to confirm here. The open question is completeness: what the
        # tool actually returned, and whether the model added arithmetic on top of it.
        for r in results:
            need = ("did the tool return an AGGREGATE, or rows the model then counted?"
                    if r["expected_source"] == SEMANTIC_MODEL
                    else "did a tool-call span exist, and did it return the relationships?")
            print(f"      {r['id']}  {need}")
        print("      A figure matching the local truth is NOT proof a measure was evaluated:")
        print("      the model may have summed what it received and landed on the same number.")
    else:
        for r in results:
            print(f"      {r['id']}  did a tool-call span exist, and did it hit the "
                  f"{r['expected_source'].replace('_', ' ')}?")
        if binding == "fabric-iq":
            print("      Fabric IQ is in front of the data agent here, so there IS a routing")
            print("      claim to check -- and it is now TWO hops. A wrong figure can come from")
            print("      the Fabric agent's own routing, not from this agent.")
    print("      Absent tool-call span + confident answer = the model answered from its prompt.")
    print("      Tool-call span with an empty result = grounding wired, RBAC or query wrong.")
    print("\n   The spans exist and name every hop -- this is measured, not assumed:")
    print("      az monitor app-insights query --app <component-name> -g <rg> \\")
    print("        --analytics-query \"dependencies | where timestamp > ago(4h) \\")
    print("          | project timestamp, operation_Id, name, success, duration\"")
    print("      Expect per question: invoke_agent <foundry agent> -> execute_tool mcp_...")
    print("      -> invoke_agent <fabric agent> -> execute_tool analyze_semantic_model -> chat.")
    print("      Pass the component NAME, not the appId, whenever -g is supplied.")


# -- main ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Replay the golden set through the Foundry agent")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the golden set and the local truth, call nothing")
    ap.add_argument("--only", help="run a single question id (e.g. sup-003)")
    ap.add_argument("--repeat", type=int, default=1, metavar="N",
                    help="ask each question N times and report whether the leading figure "
                         "holds. Needs no reference data -- an agent that contradicts itself "
                         "is a defect under any definition of truth.")
    args = ap.parse_args()
    if args.repeat < 1:
        raise SystemExit("--repeat must be >= 1")

    cfg = load_config()
    truth = local_truth(cfg)
    questions = [q for q in GOLDEN if not args.only or q["id"] == args.only]
    if not questions:
        raise SystemExit(f"No question with id '{args.only}'. Ids: {[q['id'] for q in GOLDEN]}")

    print_step(1, 3, "Local truth")
    print_truth(truth)

    if args.dry_run:
        print_step(2, 3, f"Golden set ({len(questions)} questions) -- dry run, nothing called")
        for q in questions:
            print(f"   {q['id']}  [{q['expected_source']:<14}] {q['question']}")
        print("\nOK  dry run complete.")
        return

    fnd = foundry_config(cfg)
    state = load_state()
    agent_name = state.get("foundry_agent_name") or fnd["agent_name"]

    print_step(2, 3, f"Asking '{agent_name}' ({len(questions)} questions)")
    iq_target = fnd.get("fabric_iq_target", "ontology")
    label = fnd["binding"]
    if fnd["binding"] == "fabric-iq":
        label += f" -> {iq_target}"
    print(f"   Binding             : {label}")
    if is_raw_retrieval(fnd["binding"], iq_target):
        print("   !  Fabric IQ owns no semantics of its own here: a number that matches the")
        print("      local truth proves the retrieval AND the model's arithmetic were both")
        print("      right, not that a measure was evaluated. Read the trace, not the score.")
    elif fnd["binding"] == "fabric-iq":
        print("   !  Fabric IQ is fronting the published data agent, so the churn semantics")
        print("      ARE inherited -- but the chain is two hops. A wrong figure may originate")
        print("      in the Fabric agent's own routing. Read both spans before blaming this one.")
    client = project_client(fnd)
    ai = app_insights_status(client)
    print(f"   Application Insights: {ai}")
    if ai.startswith("NOT"):
        print("   !  Telemetry is emitted as the run happens. Whatever you run now will not")
        print("      be retroactively traceable. Connect it first if you intend to cite it.")

    results = []
    for q in questions:
        print(f"   {q['id']} ...", end="", flush=True)
        tries = []
        for _ in range(args.repeat):
            tries.append(_ask(client, agent_name, fnd["model_deployment"], q["question"]))
            if args.repeat > 1:
                print(".", end="", flush=True)
        got = tries[0]
        if got["error"]:
            verdict, notes = "ERROR", []
        else:
            verdict, notes = check_answer(got["answer"], q["expect"], truth)
        row = {**q, **got, "verdict": verdict, "notes": notes}
        suffix = ""
        if args.repeat > 1:
            st = stability_of([t["answer"] for t in tries])
            row["stability"] = st
            row["repeats"] = [{"answer": t["answer"], "response_id": t["response_id"],
                               "latency_s": t["latency_s"], "error": t["error"]}
                              for t in tries]
            suffix = "  [" + {True: "stable", False: "UNSTABLE",
                              None: "no figure"}[st["stable"]] + "]"
        print(f" {verdict}{suffix}")
        results.append(row)
    client.close()

    print_step(3, 3, "Report")
    print_report(results, fnd["binding"], iq_target)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"run_{stamp}.json"
    path.write_text(json.dumps(
        {"run_at": stamp, "agent_name": agent_name,
         "project_endpoint": fnd["project_endpoint"],
         # The binding decides what a verdict means. A run log without it is uncomparable
         # against the next one, and the whole point is to compare them. Under fabric-iq the
         # TARGET carries as much of that meaning as the binding does.
         "binding": fnd["binding"],
         "fabric_iq_target": iq_target if fnd["binding"] == "fabric-iq" else None,
         "raw_retrieval": is_raw_retrieval(fnd["binding"], iq_target),
         "app_insights": ai, "repeat": args.repeat,
         "local_truth": truth, "results": results},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n   run log: {path}")

    shapes = {r.get("invocation_shape") for r in results if r.get("invocation_shape")}
    if shapes:
        print(f"   invocation_shape that worked: {sorted(shapes)} "
              f"-- pin this in _ask() and record it in the brain.")

    # Self-contradiction is a failure on its own terms: it needs no reference to judge, and a
    # demo that answers differently on the second ask is not demoable.
    unstable = any((r.get("stability") or {}).get("stable") is False for r in results)
    sys.exit(1 if unstable or any(
        r["verdict"] in ("MISS", "EMPTY", "ERROR") for r in results) else 0)


if __name__ == "__main__":
    main()
