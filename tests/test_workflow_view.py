"""Gate for the portal's workflow diagram -- offline, no Foundry and no Fabric needed.

The diagram exists to answer one question in front of a room: *what is actually deployed?*
That makes its failure mode specific and nasty -- a picture that renders beautifully while
describing a chain that is not there. Nobody in the audience can tell, and neither can the
presenter, because the drawing is the only thing they are looking at.

So the rules under test are all about the gap between intent and reality:

1. `config.yaml` is the intent, `state.json` is the receipt. When they disagree, the receipt
   wins. A name typed in config but never deployed must NOT show up as a live node.
2. A node that was never deployed renders as such and raises a warning. It is never dropped:
   an absent box is a lie by omission, a grey box is a fact.
3. The two A2A connection names are on the edges, because they are the only thing telling
   the supervisor's two tools apart at runtime -- both emit `a2a_preview_call`.
4. The layout is derived from the edges, so adding a hop re-flows the drawing instead of
   stacking two nodes on the same spot.

Run with the rest of the gate:  python -m pytest tests/ -v --tb=short
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal" / "backend"))

from workflow import (  # noqa: E402
    FABRIC, FOUNDRY, MAX_DETAIL, MAX_EDGE_LABEL, MAX_LABEL, MISSING, build_workflow,
    count_crossings,
)

FULL_STATE = {
    "workspace_id": "ws-1",
    "lakehouse_id": "lh-1",
    "semantic_model_id": "sm-1",
    "ontology_id": "ont-1",
    "data_agent_id": "da-1",
    "data_agent_mode": "dual-source",
    "foundry_agent_name": "Marketing-Churn-Front-Door",
    "foundry_agent_version": 1,
    "foundry_binding": "fabric-iq",
    "foundry_project_endpoint": "https://x.services.ai.azure.com/api/projects/p",
    "foundry_voc_agent_name": "Voice-Of-Customer",
    "foundry_voc_agent_version": 1,
    "foundry_voc_vector_store_id": "vs-1",
    "foundry_voc_files": 1000,
    "foundry_supervisor_agent_name": "Marketing-Supervisor",
    "foundry_supervisor_agent_version": 3,
    "foundry_supervisor_connection": "FrontDoorA2A",
    "foundry_supervisor_voc_connection": "VoiceOfCustomerA2A",
}

FULL_CONFIG = {
    "lakehouse_name": "LH_Customer360",
    "ontology_name": "ONT_Customer360",
    "semantic_model_name": "SM_Marketing_Analytics",
    "data_agent_name": "Marketing_Churn_Agent",
    "foundry": {
        "model_deployment": "gpt-5.4",
        "binding": "fabric-iq",
        "fabric_iq_target": "data_agent",
        "fabric_iq_connection_name": "MarketingChurnAgent",
        "agent_name": "Marketing-Churn-Front-Door",
        "voc": {"agent_name": "Voice-Of-Customer", "vector_store_name": "voc-corpus"},
        "supervisor": {"agent_name": "Marketing-Supervisor",
                       "a2a_connection_name": "FrontDoorA2A",
                       "voc_connection_name": "VoiceOfCustomerA2A"},
    },
}


def _wf(state=None, config=None):
    return build_workflow(FULL_STATE if state is None else state,
                          FULL_CONFIG if config is None else config)


def _by_id(wf):
    return {n["id"]: n for n in wf["nodes"]}


# ── Shape ────────────────────────────────────────────────────────────────────
def test_the_four_agents_and_their_backends_are_all_present():
    ids = set(_by_id(_wf()))
    assert {"supervisor", "front_door", "voc", "corpus",
            "data_agent", "semantic_model", "ontology", "lakehouse"} <= ids


def test_every_edge_connects_two_declared_nodes():
    wf = _wf()
    ids = set(_by_id(wf))
    for e in wf["edges"]:
        assert e["from"] in ids, e
        assert e["to"] in ids, e


def test_planes_split_orchestration_from_data():
    """The colour split IS the architectural argument; it must not be the frontend's guess."""
    n = _by_id(_wf())
    assert n["supervisor"]["plane"] == FOUNDRY
    assert n["front_door"]["plane"] == FOUNDRY
    assert n["voc"]["plane"] == FOUNDRY
    assert n["data_agent"]["plane"] == FABRIC
    assert n["semantic_model"]["plane"] == FABRIC
    assert n["ontology"]["plane"] == FABRIC
    assert n["lakehouse"]["plane"] == FABRIC


# ── The receipt beats the intent ─────────────────────────────────────────────
def test_a_name_only_in_config_is_not_reported_as_deployed():
    """config.yaml names an agent nobody deployed -- the node shows, marked non deploye."""
    state = {k: v for k, v in FULL_STATE.items() if k != "foundry_supervisor_agent_name"}
    wf = _wf(state=state)
    sup = _by_id(wf)["supervisor"]
    assert sup["deployed"] is False
    assert sup["label"] == "Marketing-Supervisor"      # still named, still drawn
    assert any(MISSING in w for w in wf["warnings"])


def test_state_wins_over_config_when_the_two_disagree():
    cfg = {**FULL_CONFIG,
           "foundry": {**FULL_CONFIG["foundry"],
                       "supervisor": {"agent_name": "Un-Nom-Jamais-Deploye",
                                      "a2a_connection_name": "FrontDoorA2A",
                                      "voc_connection_name": "VoiceOfCustomerA2A"}}}
    assert _by_id(_wf(config=cfg))["supervisor"]["label"] == "Marketing-Supervisor"


def test_nothing_deployed_still_renders_the_whole_chain():
    """An empty state must not produce an empty picture -- it must produce a grey one."""
    wf = _wf(state={})
    assert len(wf["nodes"]) == 8
    assert all(n["deployed"] is False for n in wf["nodes"])
    assert len(wf["warnings"]) >= 8


def test_no_node_claims_deployment_without_an_id_or_a_name():
    for n in _wf(state={}, config=FULL_CONFIG)["nodes"]:
        assert n["deployed"] is False, n


# ── The two A2A connections ──────────────────────────────────────────────────
def test_each_a2a_edge_carries_its_connection_name():
    """Both hops emit `a2a_preview_call`; the name is the only thing that tells them apart."""
    edges = {(e["from"], e["to"]): e for e in _wf()["edges"]}
    assert edges[("supervisor", "front_door")]["label"] == "FrontDoorA2A"
    assert edges[("supervisor", "voc")]["label"] == "VoiceOfCustomerA2A"
    assert edges[("supervisor", "front_door")]["protocol"] == "A2A"
    assert edges[("supervisor", "voc")]["protocol"] == "A2A"


def test_identical_connection_names_raise_a_warning():
    state = {**FULL_STATE, "foundry_supervisor_voc_connection": "FrontDoorA2A"}
    warnings = _wf(state=state)["warnings"]
    assert any("même nom" in w for w in warnings), warnings


def test_distinct_connection_names_raise_no_such_warning():
    assert not any("même nom" in w for w in _wf()["warnings"])


def test_a_supervisor_without_subordinates_is_flagged():
    state = {k: v for k, v in FULL_STATE.items() if k != "foundry_voc_agent_name"}
    assert any("subordonné" in w for w in _wf(state=state)["warnings"])


# ── Layout is derived, not typed ─────────────────────────────────────────────
def test_layers_follow_the_chain():
    n = _by_id(_wf())
    assert n["supervisor"]["layer"] == 0
    assert n["front_door"]["layer"] == 1
    assert n["voc"]["layer"] == 1
    assert n["data_agent"]["layer"] == 2
    assert n["semantic_model"]["layer"] == 3
    assert n["ontology"]["layer"] == 3
    assert n["lakehouse"]["layer"] == 4


def test_the_lakehouse_sits_past_every_engine_that_reads_it():
    """Longest path, not shortest: with the shortest it would overlap the semantic model."""
    n = _by_id(_wf())
    assert n["lakehouse"]["layer"] > n["semantic_model"]["layer"]
    assert n["lakehouse"]["layer"] > n["ontology"]["layer"]


def test_layering_terminates_on_a_cycle():
    """A malformed topology must render badly, never hang the portal."""
    import workflow as wf_mod
    nodes = [{"id": "a"}, {"id": "b"}]
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]
    wf_mod._layers(nodes, edges)
    assert all("layer" in n for n in nodes)


# ── Contracts the labels must keep stating ───────────────────────────────────
def test_the_supervisor_says_it_does_not_compute():
    assert "calcule" in _by_id(_wf())["supervisor"]["contract"]


def test_the_verbatim_agent_says_it_carries_no_figure():
    assert "chiffre" in _by_id(_wf())["voc"]["contract"]


def test_the_two_fabric_engines_state_their_language():
    n = _by_id(_wf())
    assert "DAX" in n["semantic_model"]["detail"]
    assert "GQL" in n["ontology"]["detail"]


def test_corpus_size_comes_from_the_deployment_not_from_a_constant():
    assert "1000" in _by_id(_wf())["corpus"]["detail"]
    other = {**FULL_STATE, "foundry_voc_files": 42}
    assert "42" in _by_id(_wf(state=other))["corpus"]["detail"]


# ── Geometry: the box cannot hold what it is not measured for ────────────────
# SVG text does not clip and does not wrap -- it overflows into the next box, which reads
# as a rendering bug rather than as missing information. The fit is therefore computed,
# never eyeballed, exactly like deploy_report.validate_layout(). This is the test that
# stops a future one-word addition from silently colliding two nodes on stage.
@pytest.mark.parametrize("state,config", [(FULL_STATE, FULL_CONFIG), ({}, FULL_CONFIG),
                                          ({}, {})])
def test_no_detail_can_overflow_its_box(state, config):
    for n in build_workflow(state, config)["nodes"]:
        assert len(n["detail"]) <= MAX_DETAIL, (n["id"], n["detail"], len(n["detail"]))


def test_the_missing_marker_itself_fits_the_box():
    """The grey state draws MISSING instead of the detail; it is subject to the same box."""
    assert len(MISSING) <= MAX_DETAIL


def test_labels_stay_within_the_box_too():
    """Node labels are names we control (config + state), at a larger font: MAX_LABEL."""
    for n in build_workflow(FULL_STATE, FULL_CONFIG)["nodes"]:
        assert len(n["label"]) <= MAX_LABEL, (n["id"], n["label"], len(n["label"]))


# ── Geometry: no arrow may cross another ─────────────────────────────────────
# The first version ordered rows by insertion, which put `corpus` above `data_agent` and made
# `voc -> corpus` climb straight through `front_door -> data_agent`. The data was right and
# the picture was wrong: in a diagram whose whole job is "who talks to whom", a crossing reads
# as a wiring mistake. Crossings are now counted, so this is an assertion rather than a squint.
def test_no_arrow_crosses_another():
    wf = build_workflow(FULL_STATE, FULL_CONFIG)
    assert wf["crossings"] == 0, [(e["from"], e["to"]) for e in wf["edges"]]


def test_the_two_subordinate_branches_stay_on_their_own_row():
    """The exact crossing that shipped: the Fabric branch and the corpus branch swapping."""
    n = _by_id(build_workflow(FULL_STATE, FULL_CONFIG))
    assert n["front_door"]["row"] == n["data_agent"]["row"]
    assert n["voc"]["row"] == n["corpus"]["row"]
    assert n["front_door"]["row"] != n["voc"]["row"]


def test_rows_are_dense_and_unique_inside_a_layer():
    """Two nodes on the same row of the same layer would be drawn on top of each other."""
    seen: dict[int, list[int]] = {}
    for n in build_workflow(FULL_STATE, FULL_CONFIG)["nodes"]:
        seen.setdefault(n["layer"], []).append(n["row"])
    for layer, rows in seen.items():
        assert sorted(rows) == list(range(len(rows))), (layer, rows)


def test_the_crossing_counter_is_not_vacuously_zero():
    """A metric that can only return 0 proves nothing about the layout it is guarding."""
    crossed = [{"id": "a", "layer": 0, "row": 0}, {"id": "b", "layer": 0, "row": 1},
               {"id": "c", "layer": 1, "row": 0}, {"id": "d", "layer": 1, "row": 1}]
    assert count_crossings(crossed, [{"from": "a", "to": "d"},
                                     {"from": "b", "to": "c"}]) == 1
    assert count_crossings(crossed, [{"from": "a", "to": "c"},
                                     {"from": "b", "to": "d"}]) == 0


def test_a_half_deployed_chain_still_lays_out_without_crossings():
    """The grey path is the one nobody rehearses; it must not be the ugly one either."""
    assert build_workflow({}, FULL_CONFIG)["crossings"] == 0


def test_ordering_survives_a_node_nothing_points_at():
    """An orphan has no barycentre; it must keep a row instead of collapsing onto another."""
    import workflow as wf_mod
    nodes = [{"id": "a", "layer": 0}, {"id": "b", "layer": 1}, {"id": "orphan", "layer": 1}]
    wf_mod._order_rows(nodes, [{"from": "a", "to": "b"}])
    assert sorted(n["row"] for n in nodes if n["layer"] == 1) == [0, 1]


# ── Geometry: the gap between two columns is a box too ───────────────────────
def test_an_edge_name_too_long_for_the_gap_is_dropped_from_the_drawing():
    """`short` is what the gap can hold; `label` stays whole for the list underneath.

    Dropping it is the honest move: a connection name running under the neighbouring box is
    unreadable *and* misattributed, whereas the list below carries every name in full.
    """
    long_name = "A" * (MAX_EDGE_LABEL + 5)
    cfg = {**FULL_CONFIG,
           "foundry": {**FULL_CONFIG["foundry"],
                       "supervisor": {**FULL_CONFIG["foundry"]["supervisor"],
                                      "a2a_connection_name": long_name}}}
    edge = [e for e in build_workflow({}, cfg)["edges"] if e["to"] == "front_door"][0]
    assert edge["label"] == long_name
    assert edge["short"] == ""


def test_a_name_that_fits_is_still_drawn():
    edges = build_workflow(FULL_STATE, FULL_CONFIG)["edges"]
    assert any(e["short"] for e in edges)
    for e in edges:
        assert len(e["short"]) <= MAX_EDGE_LABEL, (e["from"], e["to"], e["short"])


# ── The two halves of the geometry contract must not drift apart ─────────────
def test_the_drawing_and_the_character_budgets_describe_the_same_box():
    """MAX_* live in Python; NW, colW and the font sizes live in the SVG builder.

    Nothing in either file makes them agree, so a box widened in the JS while the caps stay
    put -- or worse, a font bumped for readability -- would silently reintroduce the overflow
    these constants exist to prevent. This reads the numbers back out of index.html and
    recomputes the budget, so the two halves can only drift with a test failing.
    """
    import re
    js = (ROOT / "portal" / "static" / "index.html").read_text(encoding="utf-8")
    body = js[js.index("function buildWorkflowSvg"):]
    body = body[:body.index("\nfunction ")]
    geom = re.search(r"var NW=(\d+),NH=(\d+),colW=(\d+)", body)
    assert geom, "buildWorkflowSvg no longer declares NW/NH/colW"
    nw, colw = int(geom.group(1)), int(geom.group(3))
    fonts = [float(f) for f in re.findall(r'font-size="([\d.]+)"', body)]
    label_pt, detail_pt = max(fonts), sorted(fonts)[1]

    # 8px padding each side; average glyph ~0.52em, the same figure the modules document.
    def budget(width: int, pt: float) -> int:
        return int((width - 16) / (pt * 0.52))

    assert MAX_LABEL <= budget(nw, label_pt), (MAX_LABEL, nw, label_pt)
    assert MAX_DETAIL <= budget(nw, detail_pt), (MAX_DETAIL, nw, detail_pt)
    assert MAX_EDGE_LABEL <= budget(colw - nw + 16, min(fonts)), (MAX_EDGE_LABEL, colw - nw)


def test_the_diagram_reads_the_row_the_backend_computed():
    """If the JS ever goes back to insertion order, the crossing test above stops meaning
    anything: it would be asserting on a field nobody draws with."""
    js = (ROOT / "portal" / "static" / "index.html").read_text(encoding="utf-8")
    body = js[js.index("function buildWorkflowSvg"):]
    body = body[:body.index("\nfunction ")]
    assert "n.row" in body, "the SVG builder no longer positions nodes by their computed row"


def test_the_contract_sentence_lives_outside_the_box():
    """It is the longest string here; the box is the one place that cannot hold it.

    If someone ever shortens a contract to make it fit a box, that is the wrong fix -- the
    sentence is the architecture, the box is a rectangle. This test asserts the sentences
    stay long enough to say something, so the pressure goes to the layout, not the meaning.
    """
    contracts = [n["contract"] for n in build_workflow(FULL_STATE, FULL_CONFIG)["nodes"]]
    assert all(c for c in contracts)
    assert any(len(c) > MAX_DETAIL for c in contracts)


# ── Serialisation ────────────────────────────────────────────────────────────
def test_the_payload_is_json_serialisable():
    import json
    json.dumps(_wf())


@pytest.mark.parametrize("bad", [None, {}, {"foundry": None}])
def test_a_missing_config_does_not_explode(bad):
    wf = build_workflow({}, bad)
    assert len(wf["nodes"]) == 8
