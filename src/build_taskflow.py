#!/usr/bin/env python3
"""
Build the Fabric workspace Task flow for Fab-Marketing-Campaign.

WHY A GENERATOR AND NOT A DEPLOY SCRIPT
Task flows have no public Fabric REST API. The only automatable path is the
workspace UI's "Import task flow", which consumes a .json file. So this script
emits that file from config.yaml (single source of truth for item names) and you
import it once per workspace. See taskflow/README.md for the 3-click import.

SCHEMA (taken from a real Fabric task flow export, not invented):
    { "name": str, "description": str,
      "tasks": [{"type": str, "id": guid, "name": str, "description": str}],
      "edges": [{"source": guid, "target": guid}] }

The exported/imported file carries NO item assignments -- Fabric drops them. The
mapping table in taskflow/README.md is what you replay in the UI.

USAGE
    python src/build_taskflow.py            # write taskflow/marketing_taskflow.json
    python src/build_taskflow.py --check    # fail if the file is stale (CI / test gate)
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "taskflow" / "marketing_taskflow.json"

# Task types accepted by Fabric. Values are the lowercase forms the product writes
# into the export file -- note "visualize", not "visualize data" as the docs table reads.
TASK_TYPES = {
    "general", "get data", "mirror data", "store data", "prepare data",
    "analyze and train data", "track data", "visualize", "distribute data", "develop data",
}

# Readable deterministic GUIDs (v4-shaped: version nibble 4, variant nibble a).
# c360 = Customer 360. Stable ids keep re-imports diff-free -- which is why the numbering
# has gaps: 3 (CSV-to-Delta prepare) and 6 (Knowledge Graph) were removed from the canvas
# and their ids are deliberately not reused.
def _gid(n: int) -> str:
    return f"c3600000-0000-4000-a000-{n:012d}"


INGEST, LAKEHOUSE, MODEL, ONTOLOGY, REPORT, AGENT = (_gid(i) for i in (1, 2, 4, 5, 7, 8))

# Which task carries the item a deploy_all.py step produces. This is the drift alarm: add a
# step to deploy_all that creates a Fabric item and forget to place it, and the test suite
# fails instead of the canvas quietly going stale.
#
# Note two steps are folded into another task rather than getting their own:
#   - setup_notebook: the notebook IS the ingest, so it sits on the Get data task.
#   - graph: the graph model is underlying to the ontology, not a stage of its own.
STEP_TO_TASK = {
    "lakehouse": LAKEHOUSE,
    "setup_notebook": INGEST,
    "semantic_model": MODEL,
    "ontology": ONTOLOGY,
    "graph": ONTOLOGY,
    "report": REPORT,
    "data_agent": AGENT,
}
STEPS_WITHOUT_ITEM = {
    "generate_data",  # writes CSVs locally
    "workspace",      # the workspace is the container, not an item in it
}


def build(cfg: dict) -> dict:
    lh = cfg["lakehouse_name"]
    ont = cfg["ontology_name"]
    sm = cfg["semantic_model_name"]
    rpt = cfg["report_name"]
    agent = cfg["data_agent_name"]
    camp = cfg["storyline"]["culprit_campaign_name"]
    seg = cfg["storyline"]["victim_segment_id"]

    tasks = [
        {"type": "get data", "id": INGEST,
         "name": "Ingest CRM / Marketing / Commerce",
         "description": "Spark notebook landing 15 source CSVs (accounts, customers, segments, "
                        "campaigns, sends, events, orders, order lines, returns, products, "
                        "interactions) plus the text corpus (customer notes, email bodies) into "
                        "Delta, and building the curated views v_churn_cohort (actionable at-risk "
                        "customers and their drivers) and v_campaign_pressure (sends per customer "
                        "per campaign - exposes the over-mailing)."},

        {"type": "store data", "id": LAKEHOUSE,
         "name": f"Lakehouse - {lh}",
         "description": "Single Customer 360 store: CRM + Marketing + Commerce as Delta tables, "
                        "plus the unstructured corpus used for AI transformations and RAG."},

        {"type": "prepare data", "id": MODEL,
         "name": f"Semantic Model - {sm}",
         "description": "Direct Lake model over the Lakehouse. Owns every NUMBER: revenue, AOV, "
                        "revenue at risk, churn cohort size, sends per customer, unsubscribes. "
                        "This is the DAX side of the dual-source Data Agent."},

        {"type": "analyze and train data", "id": ONTOLOGY,
         "name": f"Ontology - {ont}",
         "description": "Customer 360 semantic layer: Customer, Account, Segment, Campaign, Send, "
                        "Event, Order, Product, bound to the Delta tables. Its underlying graph "
                        "model owns every RELATIONSHIP: multi-hop root-cause from an at-risk "
                        f"customer back to '{camp}' and {seg}, and blast-radius traversal."},

        {"type": "analyze and train data", "id": AGENT,
         "name": f"Data Agent - {agent}",
         "description": "DUAL-SOURCE by design. Routes relationship, root-cause and impact "
                        "questions to the graph (GQL), and every value question to the semantic "
                        "model (DAX). The ontology measure path returns empty for numbers - "
                        "the routing is explicit in aiInstructions."},

        {"type": "visualize", "id": REPORT,
         "name": f"Report - {rpt}",
         "description": "Demo arc in four moves: detect (who is at risk) -> diagnose (which "
                        f"campaign) -> quantify (revenue at risk, which VIPs) -> act "
                        "(suppress, throttle, win back)."},
    ]

    edges = [
        (INGEST, LAKEHOUSE),
        (LAKEHOUSE, ONTOLOGY),
        (LAKEHOUSE, MODEL),
        (MODEL, REPORT),
        # The two arrows that matter: they make the dual-source contract visible on the canvas.
        (MODEL, AGENT),
        (ONTOLOGY, AGENT),
    ]

    return {
        "name": "Customer 360 - Churn & Campaign Pressure",
        "description": (
            "Customer 360 on Fabric (CRM + Marketing + Commerce) with a churn signal derived from "
            f"behaviour, not invented. Storyline: campaign '{camp}' over-mails the {seg} segment, "
            "triggering an unsubscribe spike, an engagement collapse and lost orders. The semantic "
            "model answers the numbers, the graph answers the relationships, and the Data Agent "
            "queries both."
        ),
        "tasks": tasks,
        "edges": [{"source": s, "target": t} for s, t in edges],
    }


def main():
    p = argparse.ArgumentParser(description="Build the Fabric task flow JSON")
    p.add_argument("--check", action="store_true",
                   help="verify the committed file matches config.yaml instead of writing it")
    args = p.parse_args()

    cfg = yaml.safe_load((ROOT / "src" / "config.yaml").read_text(encoding="utf-8"))
    flow = build(cfg)
    text = json.dumps(flow, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not OUT.exists():
            sys.exit(f"MISSING {OUT.relative_to(ROOT)} - run: python src/build_taskflow.py")
        if OUT.read_text(encoding="utf-8") != text:
            sys.exit(f"STALE {OUT.relative_to(ROOT)} - run: python src/build_taskflow.py")
        print(f"OK  {OUT.relative_to(ROOT)} is in sync with config.yaml")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"OK  wrote {OUT.relative_to(ROOT)}  ({len(flow['tasks'])} tasks, {len(flow['edges'])} connectors)")
    print("    Import: workspace -> task flow details pane -> Import and export task flow -> Import")
    print("    Then replay the item assignments from taskflow/README.md (the file cannot carry them).")


if __name__ == "__main__":
    main()
