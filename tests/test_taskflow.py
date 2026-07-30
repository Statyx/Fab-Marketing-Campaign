"""Task flow gate for Fab-Marketing-Campaign -- offline, no Fabric needed.

The task flow JSON is imported by hand into the workspace, so nothing at runtime catches a
malformed file: you only find out mid-demo when the import silently does nothing. These tests
are that catch.

They enforce:
  - the schema shape a real Fabric export uses (name/description/tasks/edges),
  - only task types Fabric accepts,
  - edges that reference real tasks, no self-loops, no duplicates, acyclic,
  - every task reachable (nothing floating off the canvas),
  - the committed file stays in sync with config.yaml item names,
  - the dual-source contract is visible: the Data Agent is fed by BOTH the semantic model
    and the graph.

Run with the rest of the gate:  python -m pytest tests/ -v --tb=short
"""
import json
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FLOW_PATH = ROOT / "taskflow" / "marketing_taskflow.json"

sys.path.insert(0, str(SRC))
from build_taskflow import (TASK_TYPES, AGENT, MODEL, ONTOLOGY, INGEST,  # noqa: E402
                           STEP_TO_TASK, STEPS_WITHOUT_ITEM)
from deploy_all import STEP_NAMES  # noqa: E402


@pytest.fixture(scope="module")
def flow():
    assert FLOW_PATH.exists(), f"{FLOW_PATH} missing -- run: python src/build_taskflow.py"
    return json.loads(FLOW_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load((SRC / "config.yaml").read_text(encoding="utf-8"))


# -- schema -------------------------------------------------------------------
def test_top_level_keys(flow):
    assert set(flow) == {"name", "description", "tasks", "edges"}
    assert flow["name"].strip() and flow["description"].strip()


def test_tasks_well_formed(flow):
    assert flow["tasks"], "a task flow with no task imports as an empty canvas"
    for t in flow["tasks"]:
        assert set(t) == {"type", "id", "name", "description"}, t
        assert t["name"].strip() and t["description"].strip(), t
        # Fabric renders the id as a GUID; a malformed one drops the task on import.
        assert len(t["id"]) == 36 and t["id"].count("-") == 4, t["id"]


def test_task_types_are_valid(flow):
    unknown = {t["type"] for t in flow["tasks"]} - TASK_TYPES
    assert not unknown, f"task types Fabric will not accept: {unknown}"


def test_task_ids_unique(flow):
    ids = [t["id"] for t in flow["tasks"]]
    assert len(ids) == len(set(ids))


def test_task_names_unique(flow):
    names = [t["name"] for t in flow["tasks"]]
    assert len(names) == len(set(names)), "duplicate task names make the canvas unreadable"


# -- graph shape --------------------------------------------------------------
def test_edges_reference_existing_tasks(flow):
    ids = {t["id"] for t in flow["tasks"]}
    for e in flow["edges"]:
        assert set(e) == {"source", "target"}, e
        assert e["source"] in ids and e["target"] in ids, e


def test_no_self_loops_or_duplicate_edges(flow):
    pairs = [(e["source"], e["target"]) for e in flow["edges"]]
    assert all(s != t for s, t in pairs), "self-loop connector"
    assert len(pairs) == len(set(pairs)), "duplicate connector"


def test_flow_is_acyclic(flow):
    adj = {t["id"]: [] for t in flow["tasks"]}
    for e in flow["edges"]:
        adj[e["source"]].append(e["target"])
    state = {}

    def visit(n):
        if state.get(n) == "done":
            return
        assert state.get(n) != "open", "cycle in the task flow -- it stops reading as a flow"
        state[n] = "open"
        for m in adj[n]:
            visit(m)
        state[n] = "done"

    for node in adj:
        visit(node)


def test_every_task_is_connected(flow):
    used = {e["source"] for e in flow["edges"]} | {e["target"] for e in flow["edges"]}
    floating = {t["name"] for t in flow["tasks"] if t["id"] not in used}
    assert not floating, f"tasks with no connector drift to default positions: {floating}"


def test_single_entry_point(flow):
    targets = {e["target"] for e in flow["edges"]}
    roots = [t["id"] for t in flow["tasks"] if t["id"] not in targets]
    assert roots == [INGEST], "ingest should be the only start of the flow"


# -- the design rules this demo must keep visible ------------------------------
def test_data_agent_is_fed_by_both_sources(flow):
    """Dual-source is the inherited lesson: DAX for numbers, GQL for relationships.

    The graph has no task of its own (it is underlying to the ontology), so the ontology
    task is what carries the GQL side on the canvas.
    """
    feeders = {e["source"] for e in flow["edges"] if e["target"] == AGENT}
    assert MODEL in feeders, "Data Agent must be fed by the semantic model (numbers / DAX)"
    assert ONTOLOGY in feeders, "Data Agent must be fed by the ontology/graph (relationships / GQL)"


def test_item_names_track_config(flow, cfg):
    """A renamed item in config.yaml must not leave a stale name on the canvas."""
    blob = json.dumps(flow, ensure_ascii=False)
    for key in ("lakehouse_name", "ontology_name", "semantic_model_name",
                "report_name", "data_agent_name"):
        assert cfg[key] in blob, f"{key} ('{cfg[key]}') missing from the task flow"


# -- drift alarm against the actual deploy pipeline ----------------------------
def test_every_deploy_step_is_on_the_canvas():
    """Add a step to deploy_all.py that creates an item, and this fails until it has a task.

    Without this, the canvas silently goes stale the moment the pipeline grows.
    """
    unaccounted = set(STEP_NAMES) - set(STEP_TO_TASK) - STEPS_WITHOUT_ITEM
    assert not unaccounted, (
        f"deploy step(s) {sorted(unaccounted)} produce Fabric items with no task on the "
        f"canvas -- add them to STEP_TO_TASK in src/build_taskflow.py (or to "
        f"STEPS_WITHOUT_ITEM if they create no item)"
    )


def test_step_mapping_has_no_dangling_entries(flow):
    """STEP_TO_TASK must point at real tasks and real deploy steps."""
    ids = {t["id"] for t in flow["tasks"]}
    for step, task_id in STEP_TO_TASK.items():
        assert step in STEP_NAMES, f"STEP_TO_TASK references unknown deploy step '{step}'"
        assert task_id in ids, f"STEP_TO_TASK['{step}'] points at a task that no longer exists"


def test_every_task_is_backed_by_a_deploy_step(flow):
    """No decorative tasks: each one must correspond to something the pipeline deploys."""
    mapped = set(STEP_TO_TASK.values())
    unbacked = {t["name"] for t in flow["tasks"] if t["id"] not in mapped}
    assert not unbacked, f"tasks with no deploy step behind them: {unbacked}"


def test_committed_file_is_not_stale():
    """Regenerating must be a no-op -- otherwise the committed file lies about config.yaml."""
    r = subprocess.run([sys.executable, str(SRC / "build_taskflow.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
