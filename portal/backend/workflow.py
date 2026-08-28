"""The deployed agent chain, derived — never drawn by hand.

Same discipline as `_ontology_graph()` next door, and for the same reason: a picture that
can disagree with what is deployed is worse than no picture. The diagram in the portal is
built from `state.json` (what the deploy scripts actually wrote) and `config.yaml` (what
they were told to write), so a node nobody deployed renders **"non déployé"** instead of
quietly disappearing — an absent box is a lie by omission, a grey one is a fact.

Nothing here touches the network. Reachability is a different claim from deployment and
belongs to `verify_supervisor.py`, which asks the live service; this module only reports
what the deployment left behind. Keeping the two apart is what stops the portal from
showing a green chain because a JSON file said so.

The topology is NOT hardcoded as columns: `layer` is the longest path from a root, computed
from the edges. Add a hop and the drawing re-flows instead of overlapping the one before.
"""
from __future__ import annotations

# Foundry plane = orchestration. Fabric plane = semantics. The colour split in the portal
# is the whole architectural argument ("the data world stays on the data side") made visible,
# so it is data, not styling: the frontend must not be free to decide a node is "Fabric-ish".
FOUNDRY, FABRIC = "foundry", "fabric"

MISSING = "non déployé"

# Budget for the two lines of text drawn INSIDE a node box, in characters.
#
# SVG text does not clip and does not wrap: it overflows and collides with the neighbouring
# box, which is worse than Power BI's silent truncation because the collision looks like a
# rendering bug rather than missing information. So the fit is computed, not eyeballed --
# same discipline as deploy_report.validate_layout(), one storey down.
#
#   box 214px - 2 x 8px padding = 198px usable
#   label  at 13px, average glyph ~0.52em = 6.76px  ->  198 / 6.76 = 29 characters
#   detail at 9.5px,                        4.94px  ->  198 / 4.94 = 40 characters
#
# The label cap lands exactly on its budget because names come from config and state and we
# do not get to shorten them; the detail cap is held four characters short of its own so the
# wide glyphs an average cannot see coming still fit. Anything longer belongs in the list
# under the diagram, where the browser wraps it properly. test_workflow_view.py enforces
# both on every node, so a future string cannot quietly overflow.
#
# These are the other half of a contract whose first half lives in buildWorkflowSvg(): move
# NW or a font-size there and these two numbers must move with them.
MAX_DETAIL = 34
MAX_LABEL = 29

# Same argument for the text drawn in the gap BETWEEN two columns, which is 304 - 214 = 90px
# wide: at 8.5px that is 20 characters. A connection name is not ours to shorten, so beyond
# that the drawing keeps the protocol and drops the name rather than letting it run under the
# neighbouring box -- the list under the diagram carries every name in full and is the
# authoritative reading. Same detail/contract split as a node, one level down.
MAX_EDGE_LABEL = 20


def _node(nid: str, label: str, plane: str, kind: str, *, detail: str = "",
          contract: str = "", deployed: bool = True, ref: str = "") -> dict:
    """One box.

    `detail` is what the component IS -- terse, drawn inside the box, length-capped.
    `contract` is what it PROMISES -- the sentence that keeps the architecture honest
    ("routes, never computes"). It is deliberately NOT drawn in the box: it is the longest
    string here and the box is the one place that cannot hold it. It renders in the list
    below the diagram, where text wraps.
    """
    return {"id": nid, "label": label, "plane": plane, "kind": kind,
            "detail": detail, "contract": contract,
            "deployed": bool(deployed), "ref": ref}


def _layers(nodes: list[dict], edges: list[dict]) -> None:
    """Assign each node its depth = longest path from any root. Mutates in place.

    Longest path, not shortest: with the shortest, `lakehouse` would sit next to the data
    agent on the branch that reaches it in one hop and overlap the semantic model. The
    chain is a DAG by construction; a cycle would loop forever, so the walk is bounded by
    the node count and a leftover cycle simply stops progressing instead of hanging.
    """
    depth = {n["id"]: 0 for n in nodes}
    for _ in range(len(nodes)):
        changed = False
        for e in edges:
            src, dst = e["from"], e["to"]
            if src in depth and dst in depth and depth[dst] < depth[src] + 1:
                depth[dst] = depth[src] + 1
                changed = True
        if not changed:
            break
    for n in nodes:
        n["layer"] = depth[n["id"]]


def count_crossings(nodes: list[dict], edges: list[dict]) -> int:
    """How many pairs of edges cross, given the rows currently assigned.

    Two edges leaving the same layer cross exactly when their endpoints are in the opposite
    vertical order at each end, so the product of the two differences is negative. Edges that
    start in different layers are drawn in different horizontal bands and cannot meet.

    This is a metric, not a drawing: it is what makes "no arrow crosses another" an assertion
    a test can make instead of something a human has to squint at.
    """
    row = {n["id"]: n.get("row", 0) for n in nodes}
    lay = {n["id"]: n.get("layer", 0) for n in nodes}
    segs = [(lay[e["from"]], row[e["from"]], row[e["to"]])
            for e in edges if e["from"] in row and e["to"] in row]
    total = 0
    for i, (la, a1, b1) in enumerate(segs):
        for lb, a2, b2 in segs[i + 1:]:
            if la == lb and (a1 - a2) * (b1 - b2) < 0:
                total += 1
    return total


def _order_rows(nodes: list[dict], edges: list[dict]) -> None:
    """Assign each node its row inside its layer, so that as few edges cross as possible.

    Ordering by insertion produced a chain that was correct and unreadable: `voc -> corpus`
    climbed while `front_door -> data_agent` descended, and the two arrows met in the middle
    of the picture. A crossing in a diagram whose entire job is "who talks to whom" reads as
    a wiring mistake, so it is a defect even though the data behind it was right.

    This is the barycentre heuristic (the ordering half of Sugiyama): sweep forwards putting
    each node next to the average row of its predecessors, sweep backwards using successors,
    and keep whichever pass scored fewest crossings. It is a heuristic -- it does not promise
    zero -- so `count_crossings()` is what the test actually asserts on, never this function.
    Ties fall back to the previous position, which keeps the layout stable between refreshes.
    """
    layers = sorted({n["layer"] for n in nodes})
    order = {lay: [n["id"] for n in nodes if n["layer"] == lay] for lay in layers}
    pos = {nid: i for lay in layers for i, nid in enumerate(order[lay])}
    layer_of = {n["id"]: n["layer"] for n in nodes}

    preds: dict[str, list[str]] = {}
    succs: dict[str, list[str]] = {}
    for e in edges:
        if e["from"] in layer_of and e["to"] in layer_of:
            preds.setdefault(e["to"], []).append(e["from"])
            succs.setdefault(e["from"], []).append(e["to"])

    def _write() -> int:
        for n in nodes:
            n["row"] = pos[n["id"]]
        return count_crossings(nodes, edges)

    best_order = {lay: list(v) for lay, v in order.items()}
    best = _write()

    for _ in range(4):
        for down in (True, False):
            sweep = layers[1:] if down else layers[-2::-1]
            rel = preds if down else succs
            for lay in sweep:
                keyed = []
                for i, nid in enumerate(order[lay]):
                    near = [pos[x] for x in rel.get(nid, [])]
                    keyed.append(((sum(near) / len(near)) if near else float(i), i, nid))
                order[lay] = [nid for _, _, nid in sorted(keyed)]
                for i, nid in enumerate(order[lay]):
                    pos[nid] = i
            score = _write()
            if score < best:
                best, best_order = score, {lay: list(v) for lay, v in order.items()}
        if best == 0:
            break

    for lay in layers:
        for i, nid in enumerate(best_order[lay]):
            pos[nid] = i
    _write()


def build_workflow(state: dict, config: dict) -> dict:
    """Return {nodes, edges, warnings, planes} for the supervised chain.

    `state` and `config` are injected rather than read here so the tests can describe a
    half-deployed environment without writing files — the half-deployed case is the one
    the honesty rule exists for, and it must be cheap to test.
    """
    state = state or {}
    fnd = (config or {}).get("foundry") or {}
    sup_cfg = fnd.get("supervisor") or {}
    voc_cfg = fnd.get("voc") or {}

    # state.json wins over config.yaml everywhere: config is the intent, state is the
    # receipt. Showing the intent as if it were deployed is exactly the lie to avoid.
    sup_name = state.get("foundry_supervisor_agent_name") or sup_cfg.get("agent_name") or ""
    fd_name = state.get("foundry_agent_name") or fnd.get("agent_name") or ""
    voc_name = state.get("foundry_voc_agent_name") or voc_cfg.get("agent_name") or ""
    data_conn = state.get("foundry_supervisor_connection") or \
        sup_cfg.get("a2a_connection_name") or ""
    voc_conn = state.get("foundry_supervisor_voc_connection") or \
        sup_cfg.get("voc_connection_name") or ""

    sup_live = bool(state.get("foundry_supervisor_agent_name"))
    fd_live = bool(state.get("foundry_agent_name"))
    voc_live = bool(state.get("foundry_voc_agent_name"))

    model = str(fnd.get("model_deployment") or "")
    binding = state.get("foundry_binding") or fnd.get("binding") or ""
    target = fnd.get("fabric_iq_target") or ""

    def _ver(key: str) -> str:
        v = state.get(key)
        return f"v{v}" if v not in (None, "") else ""

    nodes: list[dict] = [
        _node("supervisor", sup_name or "Marketing-Supervisor", FOUNDRY, "agent",
              detail=" · ".join(x for x in (model,
                                            _ver("foundry_supervisor_agent_version")) if x),
              contract="route et relaie mot pour mot, ne calcule jamais",
              deployed=sup_live),
        _node("front_door", fd_name or "Marketing-Churn-Front-Door", FOUNDRY, "agent",
              detail=" · ".join(x for x in (binding, target,
                                            _ver("foundry_agent_version")) if x),
              contract="porte d'entrée vers Fabric, hérite de sa sémantique",
              deployed=fd_live),
        _node("voc", voc_name or "Voice-Of-Customer", FOUNDRY, "agent",
              detail=" · ".join(x for x in ("file_search",
                                            _ver("foundry_voc_agent_version")) if x),
              contract="le motif, jamais un chiffre",
              deployed=voc_live),
        # Fallback is `deploy_voc_agent.DEFAULT_VECTOR_STORE`, not a name invented here. It was
        # "voc-corpus" and no such store has ever existed in the tenant: a clone without a
        # config.yaml drew a healthy-looking box for something unreachable. A default that
        # disagrees with the script that creates the thing is worse than no default.
        _node("corpus", str(voc_cfg.get("vector_store_name") or "voc-marketing-churn"), FOUNDRY,
              "store",
              detail=(f"{state['foundry_voc_files']} verbatims"
                      if state.get("foundry_voc_files") else "verbatims clients"),
              contract="ce que les clients ont écrit, sans agrégat",
              deployed=bool(state.get("foundry_voc_vector_store_id")),
              ref=str(state.get("foundry_voc_vector_store_id") or "")),
        _node("data_agent", str((config or {}).get("data_agent_name")
                                or "Marketing_Churn_Agent"), FABRIC, "data_agent",
              detail=str(state.get("data_agent_mode") or ""),
              contract="choisit DAX ou GQL selon la question",
              deployed=bool(state.get("data_agent_id")),
              ref=str(state.get("data_agent_id") or "")),
        _node("semantic_model", str((config or {}).get("semantic_model_name")
                                    or "SM_Marketing_Analytics"), FABRIC, "semantic_model",
              detail="tout chiffre (DAX)",
              contract="les mesures testées, seule source des totaux",
              deployed=bool(state.get("semantic_model_id")),
              ref=str(state.get("semantic_model_id") or "")),
        _node("ontology", str((config or {}).get("ontology_name") or "ONT_Customer360"),
              FABRIC, "ontology", detail="les liens (GQL)",
              contract="qui est relié à quoi, les causes, les chemins",
              deployed=bool(state.get("ontology_id")),
              ref=str(state.get("ontology_id") or "")),
        _node("lakehouse", str((config or {}).get("lakehouse_name") or "LH_Customer360"),
              FABRIC, "lakehouse", detail="15 tables Delta",
              contract="la vérité transactionnelle",
              deployed=bool(state.get("lakehouse_id")),
              ref=str(state.get("lakehouse_id") or "")),
    ]

    # The connection NAME is on the edge because it is the only thing that tells the two
    # A2A tools apart at runtime — both emit `a2a_preview_call`, so an edge labelled just
    # "A2A" would describe a chain nobody can debug.
    edges: list[dict] = [
        {"from": "supervisor", "to": "front_door", "protocol": "A2A",
         "label": data_conn or MISSING, "note": "les chiffres"},
        {"from": "supervisor", "to": "voc", "protocol": "A2A",
         "label": voc_conn or MISSING, "note": "les verbatims"},
        {"from": "front_door", "to": "data_agent", "protocol": "MCP",
         "label": str(fnd.get("fabric_iq_connection_name") or ""), "note": binding},
        {"from": "voc", "to": "corpus", "protocol": "file_search", "label": "", "note": ""},
        {"from": "data_agent", "to": "semantic_model", "protocol": "DAX", "label": "",
         "note": ""},
        {"from": "data_agent", "to": "ontology", "protocol": "GQL", "label": "", "note": ""},
        {"from": "semantic_model", "to": "lakehouse", "protocol": "Direct Lake", "label": "",
         "note": ""},
        {"from": "ontology", "to": "lakehouse", "protocol": "Delta", "label": "", "note": ""},
    ]

    warnings: list[str] = []
    # `short` is the name as the drawing can hold it; `label` stays whole for the list.
    for e in edges:
        e["short"] = e["label"] if len(e["label"]) <= MAX_EDGE_LABEL else ""

    # The failure this catches is silent and total: one connection name for both tools and
    # the supervisor can no longer tell the corpus from the front door.
    if data_conn and voc_conn and data_conn == voc_conn:
        warnings.append(
            f"Les deux connexions A2A portent le même nom ({data_conn}) : le superviseur "
            "ne peut plus distinguer ses deux outils.")
    if sup_live and not (fd_live and voc_live):
        warnings.append(
            "Le superviseur est déployé mais un subordonné manque : les questions routées "
            "vers lui échoueront à l'invocation.")
    for n in nodes:
        if not n["deployed"]:
            warnings.append(f"{n['label']} : {MISSING}.")

    _layers(nodes, edges)
    _order_rows(nodes, edges)
    return {"nodes": nodes, "edges": edges, "warnings": warnings,
            "crossings": count_crossings(nodes, edges),
            "planes": {FOUNDRY: "Foundry — orchestration",
                       FABRIC: "Fabric — sémantique et données"},
            "projectEndpoint": str(state.get("foundry_project_endpoint")
                                   or fnd.get("project_endpoint") or "")}
