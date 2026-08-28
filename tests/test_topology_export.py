"""The architecture topology the V2 app ships is derived — these tests keep it that way.

`app-v2/src/data/topology.generated.json` is committed, so it can silently go stale: someone
edits `portal/backend/workflow.py` or `src/deploy_ontology.py`, the 34 tests in
`test_workflow_view.py` stay green because the *builder* is still correct, and the app keeps
drawing last month's chain. A build hook was the obvious fix and the weaker one — it only runs
where npm runs, and it fails quietly on a machine without Python on PATH. A test fails
everywhere, including CI on Linux.

The second job is the leak. That JSON is compiled into a bundle which is served from
`*.webapp.fabricapps.net` **without authentication**, so anything in it is public. The guard is
a shape rule, not a list of known ids, for the reason `.github/scripts/check_client_leak.py`
states: a file that enumerates the secrets it protects is itself the disclosure.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "app-v2" / "src" / "data" / "topology.generated.json"

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _load_generator():
    """Import the generator by path: `app-v2` is not an importable package name (hyphen)."""
    spec = importlib.util.spec_from_file_location(
        "gen_topology", ROOT / "app-v2" / "scripts" / "gen_topology.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shipped() -> dict:
    return json.loads(GENERATED.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def builder():
    sys.path.insert(0, str(ROOT / "portal" / "backend"))
    import workflow

    return workflow


def test_the_committed_topology_matches_its_generator(shipped):
    """The file on disk is what the generator produces today, not what it produced once.

    Compared against the generator rather than against hard-coded expectations, so an operator
    whose `config.yaml` names differ from the defaults still passes — as long as they
    regenerated. The thing being pinned is "regenerate after you edit", not the operator's
    choice of workspace names.
    """
    assert shipped == _load_generator().build(), (
        "topology.generated.json is stale — run `python app-v2/scripts/gen_topology.py`"
    )


def test_the_shipped_topology_carries_no_identifier(shipped):
    """Shape rule, applied to the serialised text so no nesting can hide a value."""
    found = GUID.findall(GENERATED.read_text(encoding="utf-8"))
    assert not found, f"identifier-shaped value in a publicly served asset: {found[:3]}"


def test_every_node_ships_unverified(shipped):
    """`deployed` must never be baked true.

    A build-time "déployé" is a claim nobody re-checks: it survives a deleted item, a renamed
    workspace, a whole tenant. The app decides this at runtime by asking Fabric and Foundry;
    this field exists only because the Python builder emits it, and it must stay false so a
    reader of the JSON is not told something the JSON cannot know.
    """
    assert [n["deployed"] for n in shipped["workflow"]["nodes"]] == [False] * len(
        shipped["workflow"]["nodes"]
    )


def test_the_structure_is_the_one_the_python_builder_declares(shipped, builder):
    """Ids, layers, rows and hops come from the tested builder — config only supplies names."""
    reference = builder.build_workflow({}, {})
    assert [(n["id"], n["layer"], n["row"]) for n in shipped["workflow"]["nodes"]] == [
        (n["id"], n["layer"], n["row"]) for n in reference["nodes"]
    ]
    assert [(e["from"], e["to"]) for e in shipped["workflow"]["edges"]] == [
        (e["from"], e["to"]) for e in reference["edges"]
    ]


def test_the_shipped_chain_has_no_crossing(shipped):
    """A crossing in a "who talks to whom" diagram reads as a wiring mistake, not as layout."""
    assert shipped["workflow"]["crossings"] == 0


def test_the_labels_fit_the_boxes_they_are_drawn_in(shipped, builder):
    """SVG text does not clip and does not wrap — it overflows and collides.

    The caps in `workflow.py` are computed against the geometry in `WorkflowDiagram.tsx`
    (box 214 wide in a 304 column). Checking them here means a name coming from `config.yaml`
    — which the builder's own tests never see — cannot overflow a box on stage.
    """
    for node in shipped["workflow"]["nodes"]:
        assert len(node["label"]) <= builder.MAX_LABEL, node["label"]
        assert len(node["detail"]) <= builder.MAX_DETAIL, node["detail"]
    for edge in shipped["workflow"]["edges"]:
        assert len(edge["short"]) <= builder.MAX_EDGE_LABEL, edge["short"]


def test_the_ontology_is_read_from_the_deploy_script(shipped):
    """The graph must describe the deploy script, not a second description of it.

    Counted against the source rather than pinned to a number, so adding an entity to
    `deploy_ontology.py` is a one-file change instead of a change plus a test edit that would
    teach the next person the test is a formality.
    """
    source = (ROOT / "src" / "deploy_ontology.py").read_text(encoding="utf-8")
    import ast

    tree = ast.parse(source)
    sizes = {
        target.id: len(node.value.elts)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple))
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"ENTITIES", "RELATIONSHIPS"}
    }
    assert len(shipped["ontology"]["entities"]) == sizes["ENTITIES"]
    assert len(shipped["ontology"]["relationships"]) == sizes["RELATIONSHIPS"]


def test_every_entity_the_graph_draws_has_both_endpoints(shipped):
    """A relationship pointing at an entity that is not drawn renders as a line to nowhere."""
    names = {e["name"] for e in shipped["ontology"]["entities"]}
    for rel in shipped["ontology"]["relationships"]:
        assert rel["from"] in names, f"{rel['name']} starts outside the graph"
        assert rel["to"] in names, f"{rel['name']} ends outside the graph"


def test_the_corpus_name_is_stated_identically_everywhere_it_is_stated():
    """Three files named the vector store, and they had drifted apart.

    `deploy_voc_agent.DEFAULT_VECTOR_STORE` said `voc-marketing-churn` — the store that actually
    exists, with 1000 files. `config.example.yaml` and the fallback in `workflow.py` both said
    `voc-corpus`, which has never existed in the tenant. Nothing failed: the deploy script
    resolves the store BY NAME and *creates* one when the lookup misses, so the wrong name
    would have built a second, empty store and rebound the agent to it, silently. The diagram,
    meanwhile, drew a healthy box for something unreachable.

    The bug was not any one of the three values. It was that a single fact had three owners and
    no one comparing them, which is what this test now does.
    """
    import ast

    src = (ROOT / "src" / "deploy_voc_agent.py").read_text(encoding="utf-8")
    default = next(
        node.value.value
        for node in ast.parse(src).body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "DEFAULT_VECTOR_STORE"
    )

    workflow_src = (ROOT / "portal" / "backend" / "workflow.py").read_text(encoding="utf-8")
    assert f'or "{default}"' in workflow_src, (
        f"workflow.py draws a different store name than deploy_voc_agent creates ({default})"
    )

    example = (ROOT / "src" / "config.example.yaml").read_text(encoding="utf-8")
    assert f'vector_store_name: "{default}"' in example, (
        f"config.example.yaml ships a store name deploy_voc_agent would not find ({default})"
    )
