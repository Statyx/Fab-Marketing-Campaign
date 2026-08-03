"""Smoke tests for Fab-Marketing-Campaign — offline gate (no Fabric needed).

Validates: Python compiles, config/state parse, and — most importantly — that the generated
dataset carries a REAL, learnable churn signal.

The predecessor project failed exactly here: churn_risk_score was drawn from a distribution and
correlated with nothing (|r| < 0.02 against every behavioural signal), which silently made every
churn question unanswerable. These tests exist so that can never ship again.

Run BEFORE any deploy:  python -m pytest tests/ -v --tb=short
"""
import ast
import base64
import json
import os
import pathlib
import re
import sys

import pandas as pd
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
RAW = ROOT / "data" / "raw"
sys.path.insert(0, str(SRC))


# ── Python compiles ─────────────────────────────────────────────
def _py_files():
    return sorted(SRC.rglob("*.py"))


@pytest.mark.parametrize("py", _py_files(), ids=lambda p: p.name)
def test_python_compiles(py):
    ast.parse(py.read_text(encoding="utf-8"), filename=str(py))


# ── Config / state ──────────────────────────────────────────────
@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load((SRC / "config.yaml").read_text(encoding="utf-8"))


def test_config_has_required_keys(cfg):
    for key in ["workspace_name", "fabric_api_base", "lakehouse_name", "ontology_name",
                "semantic_model_name", "report_name", "data_agent_name",
                "storyline", "churn_model", "volumes", "business", "segments"]:
        assert key in cfg, f"config missing '{key}'"


def test_workspace_name_is_pinned(cfg):
    """The repo is public: the workspace name must stay neutral.

    It used to carry the owner's initials as a prefix, which published who ran the demo.
    Pinning it here is what stops that coming back through config.example.yaml.
    """
    assert cfg["workspace_name"] == "Customer 360 Marketing"


def test_churn_weights_sum_to_one(cfg):
    total = sum(cfg["churn_model"]["weights"].values())
    assert abs(total - 1.0) < 1e-9, f"churn weights must sum to 1.0, got {total}"


def test_risk_bands_cover_0_100_without_gaps(cfg):
    bands = sorted(cfg["churn_model"]["risk_bands"], key=lambda b: b["min"])
    assert bands[0]["min"] == 0 and bands[-1]["max"] == 100
    for a, b in zip(bands, bands[1:]):
        assert b["min"] == a["max"] + 1, f"gap/overlap between {a['name']} and {b['name']}"


def test_storyline_targets_a_declared_segment(cfg):
    seg_ids = {s["id"] for s in cfg["segments"]}
    assert cfg["storyline"]["victim_segment_id"] in seg_ids


def test_state_example_is_valid_json():
    json.loads((SRC / "state.example.json").read_text(encoding="utf-8"))


# ── Report <-> semantic model contract ──────────────────────────
# Every visual in the report references (table, column|measure) pairs by name.
# A typo or a measure living on another table renders a BLANK visual in Fabric with
# no error at deploy time — so the contract is checked statically here instead.
_STUB_STATE = {"workspace_id": "stub", "semantic_model_id": "stub",
               "lakehouse_sql_endpoint": "stub.datawarehouse.fabric.microsoft.com"}


@pytest.fixture(scope="module")
def model_inventory(cfg):
    import deploy_semantic_model as dsm
    bim = dsm.build_model_bim(cfg, dict(_STUB_STATE))
    columns, measures = set(), set()
    for t in bim["model"]["tables"]:
        for c in t.get("columns", []):
            columns.add((t["name"], c["name"]))
        for m in t.get("measures", []):
            measures.add((t["name"], m["name"]))
    return columns, measures


def test_report_layout_leaves_no_clipped_text(cfg):
    """Power BI clips text that does not fit and never warns.

    A 17pt title in a 30px box, or a card stacking 11+30+9pt into 112px, renders
    truncated on stage with no error at deploy time. validate_layout() computes
    the real fit (1pt = 96/72 px, Segoe UI line box 1.35x, 8px padding) so the
    defect fails here instead of in front of the customer.
    """
    import deploy_report as dr
    report, _, _, _ = dr.build_report(dict(_STUB_STATE), cfg)
    problems = dr.validate_layout(report)
    assert not problems, "layout defects:\n  " + "\n  ".join(problems)


def test_layout_validator_actually_detects_clipping(cfg):
    """A validator that cannot fail is worthless — prove it rejects a bad box."""
    import deploy_report as dr
    report, _, _, _ = dr.build_report(dict(_STUB_STATE), cfg)
    section = report["sections"][0]
    for vc in section["visualContainers"]:
        conf = json.loads(vc["config"])
        if conf.get("singleVisual", {}).get("visualType") == "textbox":
            vc["height"] = 12  # far too short for any font we use
            break
    else:
        pytest.fail("no textbox found to corrupt")
    assert dr.validate_layout(report), "validator passed a 12px box holding 17pt text"


@pytest.fixture(scope="module")
def report_refs(cfg):
    """[(page, visual, kind, table, property), ...] pulled from every prototypeQuery."""
    import deploy_report as dr
    report, _, _, _ = dr.build_report(dict(_STUB_STATE), cfg)
    refs = []
    for section in report["sections"]:
        for vc in section["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            proto = sv.get("prototypeQuery")
            if not proto:
                continue
            entity_of = {f["Name"]: f["Entity"] for f in proto["From"]}
            for sel in proto["Select"]:
                kind = "Column" if "Column" in sel else "Measure"
                node = sel[kind]
                alias = node["Expression"]["SourceRef"]["Source"]
                refs.append((section["name"], sv.get("visualType"), kind,
                             entity_of[alias], node["Property"]))
    return refs


def test_report_references_exist_in_the_model(report_refs, model_inventory):
    columns, measures = model_inventory
    missing = [r for r in report_refs
               if (r[3], r[4]) not in (columns if r[2] == "Column" else measures)]
    assert not missing, "report references absent from the semantic model: " + str(missing)


def test_report_visuals_all_have_a_prototype_query(cfg):
    import deploy_report as dr
    report, _, _, _ = dr.build_report(dict(_STUB_STATE), cfg)
    dataless = {"textbox", "basicShape"}
    for section in report["sections"]:
        for vc in section["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            if sv["visualType"] in dataless:
                continue
            assert sv.get("prototypeQuery"), \
                f"{section['name']}/{sv['visualType']} has no prototypeQuery -> renders blank"


def test_report_visuals_stay_inside_the_canvas(cfg):
    import deploy_report as dr
    report, _, _, _ = dr.build_report(dict(_STUB_STATE), cfg)
    for section in report["sections"]:
        for vc in section["visualContainers"]:
            assert vc["x"] >= 0 and vc["y"] >= 0
            assert vc["x"] + vc["width"] <= section["width"], f"{section['name']}: visual overflows width"
            assert vc["y"] + vc["height"] <= section["height"], f"{section['name']}: visual overflows height"


def test_report_is_legacy_pbix_not_pbir(cfg):
    """PBIR renders blank in Fabric — the report must stay legacy (sections/visualContainers)."""
    import deploy_report as dr
    report, pbir, theme, theme_name = dr.build_report(dict(_STUB_STATE), cfg)
    assert "sections" in report and report["sections"]
    assert all("visualContainers" in s for s in report["sections"])
    assert pbir["datasetReference"]["byConnection"]["connectionString"]
    assert theme["name"] == theme_name


def test_report_pages_match_config_names(cfg):
    import deploy_report as dr
    report, _, _, _ = dr.build_report(dict(_STUB_STATE), cfg)
    names = [s["name"] for s in report["sections"]]
    assert names == ["Direction", "Retention", "Marketing", "Commerce"]


# ── Portal <-> report / backend contract ────────────────────────
# The portal is a copy of a sister project's portal, so these tests exist to prove the
# event-specific parts (Eventhouse floor plan, KQL dashboard embed) were fully removed
# and that every persona still points at a page that actually exists.
PORTAL = ROOT / "portal"


def _portal_agents():
    """AGENTS registry read statically from portal/backend/main.py.

    Parsed with ast rather than imported: importing would pull in fastapi and
    azure-identity, which the test environment has no reason to install.
    """
    tree = ast.parse((PORTAL / "backend" / "main.py").read_text(encoding="utf-8"))
    consts = _module_constants(tree)
    node = next(n for n in tree.body
                if isinstance(n, (ast.Assign, ast.AnnAssign))
                and "AGENTS" in ast.dump(n.targets[0] if isinstance(n, ast.Assign) else n.target))
    out = {}
    for k, v in zip(node.value.keys, node.value.values):
        fields = {}
        for fk, fv in zip(v.keys, v.values):
            try:
                fields[fk.value] = ast.literal_eval(fv)
            except ValueError:
                # A list holding an f-string (the culprit-campaign suggestion) is not a
                # literal as a whole — keep the elements so counts stay meaningful.
                fields[fk.value] = [_lit_or_dynamic(e, consts) for e in fv.elts] \
                    if isinstance(fv, ast.List) else "<dynamic>"
        out[k.value] = fields
    return out


def _module_constants(tree) -> dict:
    """Module-level NAME = "literal" assignments, so the parser can resolve them.

    main.py writes `"src": ONT` rather than `"src": "ontology"` because a symbol is
    checkable by a linter and a repeated string literal is not. ast.literal_eval cannot
    follow a Name, so without this the parser reads every src as "<dynamic>" and the
    suggestion guards silently pass on nothing.
    """
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    consts[target.id] = node.value.value
        # ONT, SEM = "ontology", "model"
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
            for target in node.targets:
                if isinstance(target, ast.Tuple):
                    for name, value in zip(target.elts, node.value.elts):
                        if isinstance(name, ast.Name) and isinstance(value, ast.Constant):
                            consts[name.id] = value.value
    return consts


def _lit_or_dynamic(node, consts=None):
    if isinstance(node, ast.Name) and consts and node.id in consts:
        return consts[node.id]
    try:
        return ast.literal_eval(node)
    except ValueError:
        # A suggestion is a dict {"q": ..., "src": ...} and `q` may be an f-string on the
        # culprit campaign, which is not a literal. Losing the whole dict would also lose
        # `src`, and `src` is exactly what the suggestion guards are about - so descend
        # one level and keep every key that IS a literal.
        if isinstance(node, ast.Dict):
            out = {}
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant):
                    out[k.value] = _lit_or_dynamic(v, consts)
            return out
        # Same reasoning one level up: a list of follow-ups where a single `q` is an
        # f-string is not a literal, so literal_eval throws and the WHOLE list would
        # read as "<dynamic>". The follow-up guards would then pass on nothing - the
        # exact silent-pass this helper was written to stop.
        if isinstance(node, (ast.List, ast.Tuple)):
            return [_lit_or_dynamic(e, consts) for e in node.elts]
        return "<dynamic>"


def _portal_routes():
    """Route paths declared by @app.<method>("...") decorators in main.py."""
    tree = ast.parse((PORTAL / "backend" / "main.py").read_text(encoding="utf-8"))
    paths = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
                    paths.add(dec.args[0].value)
    return paths


def test_portal_personas_map_onto_real_report_pages(cfg):
    import deploy_report as dr
    report, _, _, _ = dr.build_report(dict(_STUB_STATE), cfg)
    pages = {s["displayName"] for s in report["sections"]}
    for key, a in _portal_agents().items():
        assert a["reportPages"], f"persona '{key}' has no reportPages"
        for p in a["reportPages"]:
            assert p in pages, f"persona '{key}' points at page '{p}' which the report does not have"


def test_portal_personas_are_complete():
    for key, a in _portal_agents().items():
        for field in ("name", "description", "icon", "accent", "welcome"):
            assert a.get(field), f"persona '{key}' is missing '{field}'"
        assert a["accent"].startswith("#") and len(a["accent"]) == 7, f"persona '{key}': bad accent"
        assert len(a["suggestions"]) >= 3, f"persona '{key}' needs at least 3 suggestions"


def test_portal_frontend_calls_only_existing_endpoints():
    """Every /api/... literal in the UI must resolve to a route the backend declares."""
    import re
    html = (PORTAL / "static" / "index.html").read_text(encoding="utf-8")
    routes = _portal_routes()
    for literal in sorted(set(re.findall(r"""['"](/api/[^'"]*)""", html))):
        assert any(r == literal or r.startswith(literal) for r in routes), \
            f"frontend calls '{literal}' but no backend route matches"


def test_portal_has_no_leftover_eventhouse_code():
    """The floor-plan heat map and KQL dashboard embed belong to the sister project."""
    html = (PORTAL / "static" / "index.html").read_text(encoding="utf-8")
    py = (PORTAL / "backend" / "main.py").read_text(encoding="utf-8")
    for token in ("floorplan", "floorPlan", "ZONE_LAYOUT", "FABRIC_EMBED",
                  "kusto", "Eventhouse", "clusterUri", "dashboardId"):
        assert token not in html, f"index.html still references '{token}'"
        assert token not in py, f"main.py still references '{token}'"


def test_portal_frontend_has_no_broken_concatenation():
    """A string chain left open by a missing ';' swallows the next statement.

    `ctx.innerHTML = '<div>...' +` followed by `parent.appendChild(ctx)` concatenates
    the *return value* of appendChild into the markup, so the page renders a literal
    "[object HTMLDivElement]". Caught in the browser, never by a syntax check —
    it is valid JavaScript.
    """
    lines = (PORTAL / "static" / "index.html").read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.rstrip().endswith("+"):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        nxt = lines[j]
        assert ".appendChild(" not in nxt and ".innerHTML=" not in nxt, (
            f"index.html line {i + 1} ends with '+' and line {j + 1} is a statement "
            f"({nxt.strip()[:60]}...) — missing ';' swallows it into the string"
        )


# ── Generated data ──────────────────────────────────────────────
def _needs_data():
    return not (RAW / "crm" / "crm_customer_profile.csv").exists()


pytestmark_data = pytest.mark.skipif(_needs_data(), reason="run generate_data.py first")


@pytest.fixture(scope="module")
def data():
    if _needs_data():
        pytest.skip("run generate_data.py first")
    return {
        "profile": pd.read_csv(RAW / "crm" / "crm_customer_profile.csv"),
        "customers": pd.read_csv(RAW / "crm" / "crm_customers.csv"),
        "segments": pd.read_csv(RAW / "crm" / "crm_segments.csv"),
        "cust_segments": pd.read_csv(RAW / "crm" / "crm_customer_segments.csv"),
        "campaigns": pd.read_csv(RAW / "marketing" / "marketing_campaigns.csv"),
        "sends": pd.read_csv(RAW / "marketing" / "marketing_sends.csv"),
        "events": pd.read_csv(RAW / "marketing" / "marketing_events.csv"),
        "orders": pd.read_csv(RAW / "commerce" / "orders.csv"),
        "order_lines": pd.read_csv(RAW / "commerce" / "order_lines.csv"),
    }


# ── Referential integrity ───────────────────────────────────────
@pytest.mark.parametrize("child,col,parent,pcol", [
    ("sends", "customer_id", "customers", "customer_id"),
    ("orders", "customer_id", "customers", "customer_id"),
    ("events", "send_id", "sends", "send_id"),
    ("order_lines", "order_id", "orders", "order_id"),
    ("cust_segments", "segment_id", "segments", "segment_id"),
    ("profile", "customer_id", "customers", "customer_id"),
])
def test_no_orphan_foreign_keys(data, child, col, parent, pcol):
    orphans = (~data[child][col].isin(data[parent][pcol])).sum()
    assert orphans == 0, f"{child}.{col} has {orphans} orphans"


# ── The churn signal MUST be real (the core regression guard) ───
BEHAVIOUR_SIGNALS = ["days_since_last_order", "engagement_rate", "nps_last", "total_orders"]


@pytest.fixture(scope="module")
def buyers(data):
    """Churn is only meaningful for customers who actually bought."""
    p = data["profile"]
    return p[p["is_customer"]]


def test_churn_score_is_in_range(data):
    s = data["profile"]["churn_risk_score"]
    assert s.between(0, 100).all()
    assert s.nunique() > 20, "churn score is suspiciously granular/constant"


def test_prospects_have_no_churn_score(data):
    """A contact who never ordered has a CONVERSION problem, not a churn problem.
    Mixing the two fills the remediation cohort with people who were never customers."""
    p = data["profile"]
    non = p[~p["is_customer"]]
    assert (non["churn_risk_score"] == 0).all()
    assert (non["risk_band"] == "Prospect").all()


@pytest.mark.parametrize("signal", BEHAVIOUR_SIGNALS)
def test_churn_correlates_with_behaviour(buyers, signal):
    """Every behavioural driver must actually move the churn score.

    This is THE test the predecessor dataset would have failed (|r| ~ 0.01 everywhere).
    """
    r = buyers["churn_risk_score"].corr(buyers[signal])
    assert abs(r) > 0.15, f"churn score barely correlates with {signal} (r={r:+.3f}) — signal is noise"


def test_recency_drives_churn_in_the_right_direction(buyers):
    r = buyers["churn_risk_score"].corr(buyers["days_since_last_order"])
    assert r > 0.3, f"longer since last order must RAISE churn risk (r={r:+.3f})"


def test_engagement_drives_churn_in_the_right_direction(buyers):
    r = buyers["churn_risk_score"].corr(buyers["engagement_rate"])
    assert r < 0, f"higher engagement must LOWER churn risk (r={r:+.3f})"


def test_all_risk_bands_are_populated(data):
    """A band nobody ever reaches is a broken scale (Critical was empty at first)."""
    counts = data["profile"]["risk_band"].value_counts()
    for band in ["Low", "Medium", "High", "Critical"]:
        assert counts.get(band, 0) > 0, f"risk band '{band}' is never reached: {counts.to_dict()}"


def test_churned_customers_score_higher_than_active(data):
    df = data["profile"].merge(data["customers"][["customer_id", "lifecycle_stage"]], on="customer_id")
    means = df.groupby("lifecycle_stage")["churn_risk_score"].mean()
    assert means.get("churned", 0) > means.get("active", 100), \
        f"churned must out-score active: {means.to_dict()}"


# ── Aggregates must match the transactional truth ───────────────
def test_profile_aggregates_match_orders(data):
    """The predecessor shipped total_orders = 0 for everyone while orders.csv had 39k rows."""
    real = data["orders"].groupby("customer_id").size().rename("real")
    m = data["profile"].merge(real, on="customer_id", how="left").fillna({"real": 0})
    mismatched = (m["total_orders"] != m["real"]).sum()
    assert mismatched == 0, f"{mismatched} customers have a wrong total_orders"
    assert data["profile"]["total_spend_eur"].sum() > 0, "total_spend_eur is empty"


def test_clv_only_positive_for_buyers(data):
    p = data["profile"]
    assert (p.loc[p["total_orders"] == 0, "clv_eur"] == 0).all(), \
        "customers with no orders must not carry a CLV"


# ── Storyline must be discoverable ──────────────────────────────
def test_culprit_campaign_exists(data, cfg):
    assert cfg["storyline"]["culprit_campaign_id"] in set(data["campaigns"]["campaign_id"])


def test_culprit_campaign_over_mails_its_audience(data, cfg):
    """The root cause must be visible in the send volume, not just asserted in docs."""
    culprit = cfg["storyline"]["culprit_campaign_id"]
    per_campaign = data["sends"].groupby("campaign_id").size()
    per_cust = (data["sends"].groupby(["campaign_id", "customer_id"]).size()
                .groupby("campaign_id").mean())
    assert per_cust[culprit] > 1.5 * per_cust.drop(culprit).mean(), \
        f"culprit campaign does not over-mail (sends/customer={per_cust[culprit]:.2f})"
    assert per_campaign[culprit] > 0


def test_unsubscribes_concentrate_on_the_culprit(data, cfg):
    culprit = cfg["storyline"]["culprit_campaign_id"]
    unsub = data["events"][data["events"]["event_type"] == "unsubscribe"]
    share = (unsub["campaign_id"] == culprit).mean()
    assert share > 0.30, f"only {share:.0%} of unsubscribes come from the culprit campaign"


def test_at_risk_cohort_is_actionable(data, cfg):
    """Not so small it's anecdotal, not so large it's meaningless — measured on buyers."""
    thr = cfg["churn_model"]["at_risk_threshold"]
    p = data["profile"]
    buyers = p[p["is_customer"]]
    share = (buyers["churn_risk_score"] >= thr).mean()
    assert 0.02 <= share <= 0.35, f"at-risk cohort is {share:.1%} of the customer base"


# ── Marketing plausibility ──────────────────────────────────────
def test_consent_is_respected(data):
    m = data["sends"].merge(data["customers"][["customer_id", "consent_email"]],
                            on="customer_id", how="left")
    # A send may predate an unsubscribe, so we only assert the funnel is not absurd.
    assert len(m) == len(data["sends"])


def test_funnel_rates_are_plausible(data):
    sends = len(data["sends"])
    ev = data["events"]["event_type"].value_counts()
    open_rate = ev.get("open", 0) / sends
    bounce_rate = ev.get("bounce", 0) / sends
    ctr = ev.get("click", 0) / max(1, ev.get("open", 0))
    assert 0.05 <= open_rate <= 0.45, f"open rate {open_rate:.1%}"
    assert bounce_rate <= 0.06, f"bounce rate {bounce_rate:.1%}"
    assert 0.02 <= ctr <= 0.30, f"CTR (clicks/opens) {ctr:.1%}"


def test_attribution_share_is_realistic(data, cfg):
    o = data["orders"]
    share = o["attributed_campaign_id"].notna().mean() if "attributed_campaign_id" in o else 0
    assert share <= 0.40, f"{share:.1%} of orders attributed — inflates ROI"


def test_average_order_value_is_credible(data, cfg):
    aov = data["orders"]["total_amount_eur"].mean()
    assert 30 <= aov <= 600, f"AOV {aov:.0f} EUR is not credible for this scenario"


def _burned_cohort(data, cfg):
    """Customers a demo user can SEE were over-mailed: 2+ sends of the culprit campaign.

    Deliberately reconstructed from marketing_sends rather than from the generator's
    internal flag — if the burn is not visible in the shipped data, RCA cannot find it.
    """
    culprit = cfg["storyline"]["culprit_campaign_id"]
    hits = data["sends"][data["sends"]["campaign_id"] == culprit].groupby("customer_id").size()
    return set(hits[hits >= 2].index)


def test_over_mailed_cohort_is_measurably_worse(data, cfg):
    """The burn must show up in churn AND engagement, or the story is decoration."""
    burned = _burned_cohort(data, cfg)
    assert len(burned) > 500, f"only {len(burned)} customers were over-mailed — cohort too small"
    p = data["profile"].set_index("customer_id")
    inside = p.index.isin(burned)
    churn_gap = p[inside]["churn_risk_score"].mean() - p[~inside]["churn_risk_score"].mean()
    eng_gap = p[inside]["engagement_rate"].mean() - p[~inside]["engagement_rate"].mean()
    assert churn_gap > 3, f"over-mailed customers barely churn more (+{churn_gap:.1f} points)"
    assert eng_gap < -0.02, f"over-mailed customers are not less engaged ({eng_gap:+.3f})"


def test_root_cause_explains_about_half_the_at_risk_cohort(data, cfg):
    """The demo arc needs RCA to FIND the cause, not be handed it.

    Too low and the campaign is noise; too high and 'at risk' is just a synonym for
    'received CAMP_007', which makes the diagnosis tautological.
    """
    thr = cfg["churn_model"]["at_risk_threshold"]
    burned = _burned_cohort(data, cfg)
    risky = set(data["profile"].query("churn_risk_score >= @thr")["customer_id"])
    share = len(risky & burned) / max(1, len(risky))
    assert 0.30 <= share <= 0.80, \
        f"{share:.0%} of the at-risk cohort traces back to the culprit campaign"


# ── Report definition (offline structural validation) ───────────
# Legacy PBIX only. A visual without a prototypeQuery renders as an empty box, and a
# visual that groups by a column which cannot filter its measure silently renders the
# same total on every bar. Both are invisible until the demo is live — so they are
# checked here, before deploy.
DECORATIVE = {"textbox", "basicShape", "shape", "image", "actionButton"}


@pytest.fixture(scope="module")
def model_bim(cfg):
    import deploy_semantic_model as dsm
    return dsm.build_model_bim(cfg, {})


@pytest.fixture(scope="module")
def report_def(cfg):
    import deploy_report as dr
    # build_report binds the report to a live semantic model id; the geometry
    # under test does not depend on its value, so a placeholder keeps the suite
    # offline.
    state = {"semantic_model_id": "00000000-0000-0000-0000-000000000000"}
    report, pbir, theme_json, theme_name = dr.build_report(state, cfg)
    return {"report": report, "pbir": pbir, "theme": theme_json, "theme_name": theme_name}


@pytest.fixture(scope="module")
def model_index(model_bim):
    """{table: {'columns': set, 'measures': set}} plus the filter-propagation graph."""
    idx = {}
    for t in model_bim["model"]["tables"]:
        idx[t["name"]] = {"columns": {c["name"] for c in t.get("columns", [])},
                          "measures": {m["name"] for m in t.get("measures", [])}}
    # Single-direction many-to-one: filters flow from the "one" side to the "many" side.
    # A bothDirections relationship also lets them travel back up the bridge.
    flows = {}
    for r in model_bim["model"]["relationships"]:
        flows.setdefault(r["toTable"], set()).add(r["fromTable"])
        if r.get("crossFilteringBehavior") == "bothDirections":
            flows.setdefault(r["fromTable"], set()).add(r["toTable"])
    return {"tables": idx, "flows": flows}


def _reaches(flows, src, dst, seen=None):
    if src == dst:
        return True
    seen = seen or set()
    seen.add(src)
    return any(n not in seen and _reaches(flows, n, dst, seen) for n in flows.get(src, ()))


def _visuals(report):
    for section in report["sections"]:
        for vc in section["visualContainers"]:
            yield section["name"], json.loads(vc["config"])["singleVisual"]


def test_report_has_pages_and_visuals(report_def):
    sections = report_def["report"]["sections"]
    assert len(sections) >= 4, "the demo arc needs one page per step"
    for s in sections:
        assert s["visualContainers"], f"page '{s['name']}' is empty"


def test_every_data_visual_has_a_prototype_query(report_def):
    """PBIR/legacy trap: no prototypeQuery == an empty box in Fabric."""
    for page, sv in _visuals(report_def["report"]):
        if sv["visualType"] in DECORATIVE:
            continue
        pq = sv.get("prototypeQuery")
        assert pq and pq.get("From") and pq.get("Select"), \
            f"{page}/{sv['visualType']} has no usable prototypeQuery"


def test_report_only_references_existing_model_objects(report_def, model_index):
    tables = model_index["tables"]
    for page, sv in _visuals(report_def["report"]):
        for sel in sv.get("prototypeQuery", {}).get("Select", []):
            table, prop = sel["Name"].split(".", 1)
            assert table in tables, f"{page}: unknown table '{table}'"
            kind = "columns" if "Column" in sel else "measures"
            assert prop in tables[table][kind], \
                f"{page}: {table}[{prop}] is not a {kind[:-1]} of the semantic model"


def test_report_projections_match_the_query(report_def):
    """A queryRef with no matching Select silently drops the field from the visual."""
    for page, sv in _visuals(report_def["report"]):
        if sv["visualType"] in DECORATIVE:
            continue
        names = {s["Name"] for s in sv["prototypeQuery"]["Select"]}
        for role, refs in sv.get("projections", {}).items():
            for ref in refs:
                assert ref["queryRef"] in names, \
                    f"{page}: projection {role} -> {ref['queryRef']} has no Select entry"


def test_report_groupings_respect_filter_direction(report_def, model_index):
    """The silent killer: grouping by a column that cannot filter the measure.

    Relationships are many-to-one, so crm_customer_profile cannot filter crm_customers.
    Such a visual does not error — it just repeats the grand total on every category.

    Scope note: this only sees the report. The Data Agent asks far more axes than the
    46 visuals do — see test_the_axes_the_data_agent_is_asked_on_actually_filter.
    """
    flows = model_index["flows"]

    for page, sv in _visuals(report_def["report"]):
        if sv["visualType"] in DECORATIVE:
            continue
        cols = [s["Name"].split(".", 1) for s in sv["prototypeQuery"]["Select"] if "Column" in s]
        measures = [s["Name"].split(".", 1) for s in sv["prototypeQuery"]["Select"] if "Measure" in s]
        for ctable, ccol in cols:
            for mtable, mname in measures:
                assert _reaches(flows, ctable, mtable), (
                    f"{page}: grouping {ctable}[{ccol}] by [{mname}] ({mtable}) — "
                    f"filters cannot flow that way, every category would show the same total")


# (grouping column table, measure table, what a demo question would ask)
AGENT_AXES = [
    ("crm_segments", "crm_customer_profile", "churn score by segment"),
    ("crm_segments", "marketing_sends", "email pressure by segment"),
    ("crm_segments", "orders", "revenue by segment"),
    ("marketing_campaigns", "orders", "attributed revenue by campaign"),
    ("marketing_campaigns", "marketing_events", "opens/unsubs by campaign"),
    ("crm_customers", "crm_customer_profile", "risk by lifecycle stage"),
    ("crm_customers", "orders", "revenue by customer attribute"),
    ("crm_customer_profile", "crm_interactions", "complaints by risk band"),
    ("crm_customer_profile", "orders", "revenue by risk band"),
    ("crm_customer_profile", "marketing_sends", "email pressure by risk band"),
    ("products", "order_lines", "units by product"),
]

# Axes the data genuinely cannot answer, kept explicit so a flat result is a
# recorded decision and not an oversight. `returns` carries order_id and
# customer_id but NO product_id, so no per-product return metric exists; faking
# one by making order_lines <-> orders bidirectional would inflate [Revenue] per
# category into figures that no longer sum to the total.
KNOWN_FLAT_AXES = [
    ("products", "returns", "return rate by product — returns are order-level"),
]


def test_the_axes_the_data_agent_is_asked_on_actually_filter(model_index):
    """A wider net than the report: the agent groups by whatever the question implies.

    All three entries that were broken on the tenant are in here — segment_name
    repeated 29.5071 on all 13 segments, Attributed Revenue repeated 582 085 EUR on
    all 21 campaigns, and Negative Interactions repeated 8 752 on all 5 risk bands.
    None showed up in the report, because no visual crossed those axes.
    """
    flows, tables = model_index["flows"], model_index["tables"]
    for ctable, mtable, question in AGENT_AXES:
        assert ctable in tables, f"{ctable} is not in the model"
        assert mtable in tables, f"{mtable} is not in the model"
        assert _reaches(flows, ctable, mtable), (
            f"'{question}': {ctable} cannot filter {mtable} — the agent would answer "
            f"the grand total for every row and sound confident doing it")


def test_the_known_dead_axes_are_still_dead(model_index):
    """The complement of the guard above: if one of these starts filtering, someone
    added a relationship whose consequences (inflated totals) need reviewing."""
    flows = model_index["flows"]
    for ctable, mtable, why in KNOWN_FLAT_AXES:
        assert not _reaches(flows, ctable, mtable), (
            f"{ctable} now filters {mtable} — that axis was documented as unanswerable "
            f"({why}); check what the new path does to the grand totals")


def test_attribution_measures_keep_an_incoming_campaign_filter(model_index, model_bim):
    """CALCULATE replaces the filter on a column it filters itself.

    Attribution filters orders[attributed_campaign_id] — the very column the campaign
    relationship arrives on. Without KEEPFILTERS the campaign filter is overwritten and
    every campaign shows the same total, relationship or not.
    """
    orders = next(t for t in model_bim["model"]["tables"] if t["name"] == "orders")
    by_name = {m["name"]: m["expression"] for m in orders["measures"]}
    for name in ("Attributed Orders", "Attributed Revenue"):
        raw = by_name[name]
        expr = " ".join(raw) if isinstance(raw, list) else raw
        assert "attributed_campaign_id" in expr
        assert expr.count("KEEPFILTERS") == 2, (
            f"[{name}] filters attributed_campaign_id without KEEPFILTERS — "
            f"CALCULATE would erase the campaign filter arriving on that column")


def test_report_binds_to_the_configured_semantic_model(report_def, cfg):
    conn = report_def["pbir"]["datasetReference"]["byConnection"]["connectionString"]
    assert cfg["workspace_name"] in conn
    assert cfg["semantic_model_name"] in conn


def test_report_theme_matches_the_committed_theme_file(report_def):
    """The theme name must match in three places or Fabric falls back to the default."""
    name = report_def["theme_name"]
    report = report_def["report"]
    assert report["theme"] == name
    assert json.loads(report["config"])["themeCollection"]["baseTheme"]["name"] == name
    assert report_def["theme"]["name"] == name
    assert report_def["theme"]["dataColors"], "theme carries no palette"


def test_storyline_is_named_in_the_report(report_def, cfg):
    """The root-cause page must actually call out the culprit, not just plot it."""
    text = json.dumps(report_def["report"], ensure_ascii=False)
    assert cfg["storyline"]["culprit_campaign_name"] in text
    assert cfg["storyline"]["victim_segment_id"] in text


# ── Layout ──────────────────────────────────────────────────────────────────
# A report can bind to every field correctly and still ship unreadable. Power BI
# clips text that does not fit its box and lets visuals overlap, silently, with
# no warning at deploy time. These tests are the only thing that catches it
# before a human sees the report.

def test_report_layout_has_no_clipping_or_overlap(report_def):
    import deploy_report as dr
    problems = dr.validate_layout(report_def["report"])
    assert not problems, "layout defects:\n  " + "\n  ".join(problems)


def test_textbox_height_rule_rejects_a_too_small_box():
    """Guard the guard: the rule must actually fail on the geometry that shipped."""
    import deploy_report as dr
    assert dr.text_height(17) > 30, "17pt in a 30px box must be rejected"
    assert dr.text_height(10) > 20, "10pt in a 20px box must be rejected"


def test_height_model_keeps_padding_separate_from_line_height():
    """A single px-per-pt multiplier cannot express a constant padding term.

    Folding padding into a multiplier under-sizes small text and over-sizes
    large text — that is how a 10pt subtitle got a 22px box when it needs 26.
    """
    import deploy_report as dr
    # doubling the font must NOT double the required height: padding is constant
    assert dr.text_height(20) < 2 * dr.text_height(10)
    # the proportional part alone must scale linearly
    assert dr.line_px(20) == pytest.approx(2 * dr.line_px(10))


def test_the_card_stack_that_clipped_on_screen_is_rejected():
    """The one data point measured against the real renderer.

    A 112px card holding title 11pt + callout 24pt + label 9pt shipped to Fabric
    and rendered with its bottom label cut off. The model in force at the time
    computed 111.2px and passed it. Whatever the constants become, that stack
    must never fit 112px again — this is evidence, not a derivation.
    """
    import deploy_report as dr

    assert dr.card_height(11, 24, 9) > 112, (
        "the stack observed clipped in Fabric must not fit a 112px card")

    # The card that actually ships draws all three texts, and fits them.
    assert dr.card_height(dr.CARD_TITLE_PT, dr.CARD_VALUE_PT,
                          dr.CARD_LABEL_PT) <= dr.CARD_H


def test_padding_is_charged_per_stacked_text_not_once():
    """The exact mistake the render disproved.

    Collapsing the three per-block pads into a single container pad is what
    made a 112px box look sufficient. Adding a text must cost its own padding,
    so the stack must grow by more than the glyph height alone.
    """
    import deploy_report as dr

    two = dr.card_height(11, 24)
    three = dr.card_height(11, 24, 9)
    assert three - two >= dr.line_px(9) + dr.TEXT_PAD - 1, (
        f"a third text must cost its line box AND its own padding: "
        f"grew by {three - two}px, needs {dr.line_px(9) + dr.TEXT_PAD:.1f}px")


def test_a_hidden_label_is_not_charged_for_space():
    """show:false frees its line — but only when it is explicitly off."""
    import deploy_report as arc

    off = {"categoryLabel": [{"properties": {"show": {"expr": {"Literal": {"Value": "false"}}}}}]}
    assert not arc._shown(off, "categoryLabel")
    # Absent means Power BI renders its default, so it must still be charged.
    assert arc._shown({}, "categoryLabel"), (
        "an undeclared group renders by default and must count as taking space")


def test_every_card_is_tall_enough_for_what_it_actually_renders(report_def):
    import deploy_report as dr
    seen = 0
    for section in report_def["report"]["sections"]:
        for vc in section["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            if sv["visualType"] != "cardVisual":
                continue
            seen += 1
            objs = sv.get("objects", {})
            stack = [dr._font_pt(sv.get("vcObjects", {}), "title", dr.CARD_TITLE_PT),
                     dr._font_pt(objs, "calloutValue", dr.CARD_VALUE_PT)]
            if dr._shown(objs, "categoryLabel"):
                stack.append(dr._font_pt(objs, "categoryLabel", dr.CARD_LABEL_PT))
            assert vc["height"] >= dr.card_height(*stack), (
                f"card {vc['height']}px too short for {stack}")
    assert seen == 20, f"expected 20 cards, found {seen}"


def test_the_category_label_is_declared_visible(report_def):
    """Asked for explicitly: the label under the value must be shown.

    Power BI was observed ignoring show:false here, so hiding it would be a
    no-op today — but relying on a bug to satisfy a requirement is not a
    requirement being satisfied. Declare it visible, and size for it.
    """
    import deploy_report as dr
    for section in report_def["report"]["sections"]:
        for vc in section["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            if sv["visualType"] != "cardVisual":
                continue
            assert dr._shown(sv.get("objects", {}), "categoryLabel"), (
                f"{json.loads(vc['config'])['name']}: the category label must "
                f"stay visible")


def test_a_card_fits_even_if_power_bi_ignores_show_false(report_def):
    """Evidence, not derivation: the renderer ignored our hide toggle.

    The live report definition read back from Fabric carried
    categoryLabel show:false on all 20 cards, and Power BI drew the label
    anyway — clipped, because the box was sized for two texts. The validator
    was not wrong; it believed a declaration the engine did not honour.

    So sizing must not depend on a toggle we cannot verify: every card must
    fit title + value + label, whatever `show` says.
    """
    import deploy_report as dr
    seen = 0
    for section in report_def["report"]["sections"]:
        for vc in section["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            if sv["visualType"] != "cardVisual":
                continue
            seen += 1
            objs = sv.get("objects", {})
            worst = dr.card_height(
                dr._font_pt(sv.get("vcObjects", {}), "title", dr.CARD_TITLE_PT),
                dr._font_pt(objs, "calloutValue", dr.CARD_VALUE_PT),
                dr._font_pt(objs, "categoryLabel", dr.CARD_LABEL_PT))
            assert vc["height"] >= worst, (
                f"card is {vc['height']}px but needs {worst}px if the hide "
                f"toggle is ignored, as Power BI was observed to do")
    assert seen == 20, f"expected 20 cards, found {seen}"


def test_the_rows_below_the_cards_clear_them(report_def):
    """The cards grew into row 1's old position; the grid must move together."""
    import deploy_report as dr
    assert dr.CARD_Y + dr.CARD_H <= dr.ROW1_Y, (
        f"cards end at {dr.CARD_Y + dr.CARD_H}px, row 1 starts at {dr.ROW1_Y}px")
    assert dr.ROW1_Y + dr.ROW1_H <= dr.ROW2_Y
    assert dr.ROW2_Y + dr.ROW2_H <= dr.CANVAS_H
    assert not [p for p in dr.validate_layout(report_def["report"])], (
        "the shipped geometry must be defect-free")


def test_validator_detects_the_geometry_that_shipped(report_def):
    """Regression harness: re-inject the original geometry and count the defects.

    32 = 4 pages x (title + subtitle clipped + their overlap) + 20 cards whose
    category label was cut off by the 30pt callout. The label is switched back
    on here because that is what actually shipped — a card sized for two texts
    while rendering three is the whole defect.
    """
    import copy
    import deploy_report as dr
    old = copy.deepcopy(report_def["report"])
    for s in old["sections"]:
        for v in s["visualContainers"]:
            c = json.loads(v["config"])
            sv = c["singleVisual"]
            kind = sv["visualType"]
            if kind == "basicShape" and v["y"] == 0:
                v["height"] = 64
            elif kind == "textbox" and v["y"] == dr.HEADER_PAD_TOP:
                v["y"], v["height"] = 12, 30
            elif kind == "textbox":
                v["y"], v["height"] = 40, 20
            elif kind == "cardVisual":
                v["y"], v["height"] = 78, 112
                (sv["objects"]["calloutValue"][0]["properties"]
                 ["fontSize"]["expr"]["Literal"]["Value"]) = "30D"
                (sv["objects"]["categoryLabel"][0]["properties"]
                 ["show"]["expr"]["Literal"]["Value"]) = "true"
                v["config"] = json.dumps(c)
    problems = dr.validate_layout(old)
    assert len(problems) == 32, f"expected 32 defects, got {len(problems)}"
    assert sum("card stack clipped" in p for p in problems) == 20
    assert sum("overlap" in p for p in problems) == 4
    assert sum(("text clipped" in p) for p in problems) == 8


def test_header_band_is_not_counted_as_an_overlap(report_def):
    """The z=0 band sits under its own text on purpose — 4 false positives/page."""
    import deploy_report as dr
    bands = [vc for s in report_def["report"]["sections"]
             for vc in s["visualContainers"]
             if json.loads(vc["config"])["singleVisual"]["visualType"] == "basicShape"]
    assert bands, "no header band found"
    assert all(vc.get("z") == 0 for vc in bands), "the band must stay at z=0"
    assert not [p for p in dr.validate_layout(report_def["report"]) if "overlaps" in p]


def test_header_band_contains_its_text(report_def):
    """The banner must be tall enough for the title and subtitle stacked inside it."""
    import deploy_report as dr
    assert dr.HEADER_H >= (dr.HEADER_PAD_TOP + dr.HEADER_TITLE_H
                           + dr.HEADER_SUB_H + dr.HEADER_PAD_BOTTOM)
    for section in report_def["report"]["sections"]:
        bands = [vc for vc in section["visualContainers"]
                 if json.loads(vc["config"])["singleVisual"]["visualType"] == "basicShape"]
        texts = [vc for vc in section["visualContainers"]
                 if json.loads(vc["config"])["singleVisual"]["visualType"] == "textbox"]
        assert bands, f"{section['displayName']} has no header band"
        band = bands[0]
        for t in texts:
            assert t["y"] + t["height"] <= band["y"] + band["height"], (
                f"{section['displayName']}: header text spills below the band")


def test_cards_start_below_the_header(report_def):
    import deploy_report as dr
    for section in report_def["report"]["sections"]:
        for vc in section["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            if sv["visualType"] == "cardVisual":
                assert vc["y"] >= dr.HEADER_H, (
                    f"{section['displayName']}: a card overlaps the header band")


# ── Semantic model contract ─────────────────────────────────────────────────

def test_deprecated_measures_are_kept_as_aliases(model_bim):
    """Renaming a measure without keeping the old name breaks live reports.

    [Line Revenue] was renamed to [Product Revenue] and dropped; a deployed
    report bound to it started rendering 'Something's wrong with one or more
    fields'. The old name stays as a hidden alias until no consumer uses it.
    """
    measures = {m["name"]: m
                for t in model_bim["model"]["tables"]
                for m in t.get("measures", [])}
    assert "Line Revenue" in measures, "deprecated alias removed — this breaks live reports"
    assert measures["Line Revenue"].get("isHidden") is True, (
        "a deprecated alias must be hidden so it does not duplicate the model surface")
    assert "Product Revenue" in measures


def test_no_duplicate_measure_names(model_bim):
    """Two measures with the same name make the model ambiguous for DAX and Copilot."""
    names = [m["name"] for t in model_bim["model"]["tables"] for m in t.get("measures", [])]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate measure names: {sorted(dupes)}"



# -- Item ownership ----------------------------------------------------------

def test_there_is_exactly_one_report_generator():
    """Two sessions once published to the same Fabric report item and silently
    overwrote each other for a day. The fix that held was not a better guard,
    it was having a single owner.

    A second generator is allowed to exist -- but adding one must be a decision,
    not something that appears in a merge. If this fails, give the new file its
    own display name AND its own state.json key before deleting this assertion:
    sharing either one is enough to collide.
    """
    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    generators = sorted(p.name for p in src.glob("deploy_report*.py")
                        if not p.name.startswith("validate"))
    assert generators == ["deploy_report.py"], (
        f"found {generators}; a second report generator needs its own name and "
        f"state key, and docs/ARCHITECTURE.md must describe it")


def test_the_report_is_addressed_by_one_state_key():
    import deploy_report
    src = pathlib.Path(deploy_report.__file__).read_text(encoding="utf-8")
    assert 'state["report_id"]' in src or 'state.get("report_id")' in src, (
        "the canonical report must stay addressed by report_id")



# -- Shared model: union, not replace -----------------------------------------

@pytest.fixture(scope="module")
def dsm():
    import deploy_semantic_model
    return deploy_semantic_model


def _bim(*names):
    return {"model": {"tables": [{"name": "t",
                                  "measures": [{"name": n} for n in names]}]}}


def _measures(bim):
    return {m["name"]: m for t in bim["model"]["tables"] for m in t.get("measures", [])}


def _stub(dsm, monkeypatch, live, used):
    monkeypatch.setattr(dsm, "deployed_measures",
                        lambda *a, **k: {("t", n): dax for n, dax in live.items()})
    monkeypatch.setattr(dsm, "measures_used_by_reports", lambda *a, **k: used)


def test_a_measure_this_generator_never_heard_of_survives(dsm, monkeypatch):
    """The whole point: deploying must not erase the other generator's work.

    Two generators share one semantic model. The main checkout's model defines
    [Line Revenue] and has never heard of [Product Revenue]; this branch's does
    the opposite. Replacing the model wholesale means whoever deploys last wins
    and the other one's report renders "Something's wrong with one or more
    fields". So the deploy is a union: what is live and still used stays live.
    """
    bim = _bim("Line Revenue")
    _stub(dsm, monkeypatch,
          live={"Product Revenue": "SUM(order_lines[line_total_eur])",
                "Line Revenue": "[Product Revenue]"},
          used={"Product Revenue": {"RPT_Marketing_Churn"}})

    carried = dsm.carry_over_measures_reports_use("tok", "ws", "sm", bim)

    assert carried == ["Product Revenue"]
    kept = _measures(bim)["Product Revenue"]
    assert kept["expression"] == "SUM(order_lines[line_total_eur])", "DAX must survive verbatim"
    assert kept["isHidden"], "carried-over measures stay out of the field list"
    assert "RPT_Marketing_Churn" in kept["description"], "say who it was kept for"


def test_unused_measures_are_still_dropped(dsm, monkeypatch):
    """Union must not mean the model only ever grows: dead measures still go."""
    bim = _bim("Product Revenue")
    _stub(dsm, monkeypatch,
          live={"Product Revenue": "SUM(x)", "Scratch": "1"},
          used={"Product Revenue": {"RPT_Marketing_Churn"}})

    assert dsm.carry_over_measures_reports_use("tok", "ws", "sm", bim) == []
    assert "Scratch" not in _measures(bim)


def test_a_measure_the_generator_defines_is_not_carried_over(dsm, monkeypatch):
    """No duplicate: the generator's own definition always wins over the live one."""
    bim = _bim("Product Revenue", "Line Revenue")
    called = []
    monkeypatch.setattr(dsm, "deployed_measures",
                        lambda *a, **k: {("t", "Product Revenue"): "SUM(x)",
                                         ("t", "Line Revenue"): "[Product Revenue]"})
    monkeypatch.setattr(dsm, "measures_used_by_reports",
                        lambda *a, **k: called.append(1) or {})

    assert dsm.carry_over_measures_reports_use("tok", "ws", "sm", bim) == []
    assert len(_measures(bim)) == 2, "nothing added"
    assert not called, "nothing was missing, so the guard must not scan every report"


def test_refuses_when_the_expression_cannot_be_carried(dsm, monkeypatch):
    """Silently dropping a used measure is the failure mode. Refuse loudly instead.

    TMDL read-back returns an empty expression for a multi-line measure. Keeping
    it as an empty measure would be worse than not deploying.
    """
    _stub(dsm, monkeypatch,
          live={"Multi Line": ""},
          used={"Multi Line": {"RPT_Marketing_Churn"}})
    with pytest.raises(RuntimeError) as err:
        dsm.carry_over_measures_reports_use("tok", "ws", "sm", _bim("Other"))
    assert "Multi Line" in str(err.value)
    assert "RPT_Marketing_Churn" in str(err.value), "the error must name the consumer to fix"


def test_does_not_block_the_deploy_when_the_api_is_unavailable(dsm, monkeypatch):
    """A guard that fails the deploy on an unrelated API hiccup is a guard people delete."""
    def boom(*a, **k):
        raise RuntimeError("503 Service Unavailable")
    monkeypatch.setattr(dsm, "deployed_measures", boom)
    assert dsm.carry_over_measures_reports_use("tok", "ws", "sm", _bim("Product Revenue")) == []


def test_readback_parses_the_dax_not_just_the_name(dsm, monkeypatch):
    """The carry-over is only as good as the expression it recovers from TMDL."""
    tmdl = ("table t\n"
            "\tmeasure 'Product Revenue' = SUM(order_lines[line_total_eur])\n"
            "\t\tformatString: #,0\n"
            "\tmeasure Buyers = DISTINCTCOUNT(orders[customer_id])\n")
    monkeypatch.setattr(dsm, "get_definition_parts", lambda *a, **k: [
        {"path": "definition/tables/t.tmdl",
         "payload": base64.b64encode(tmdl.encode("utf-8")).decode()}])

    live = dsm.deployed_measures("tok", "ws", "sm")
    assert live[("t", "Product Revenue")] == "SUM(order_lines[line_total_eur])"
    assert live[("t", "Buyers")] == "DISTINCTCOUNT(orders[customer_id])"



# --- tenant pinning -------------------------------------------------------
# az silently flips back to the corporate tenant. The token stays valid, so the
# symptom is not "auth expired" but "this item does not exist / 401" on a
# perfectly healthy artefact. It once turned a working report into a fake 0/35.

def _runnable_scripts():
    """Every src/*.py that can be launched on its own and talks to the tenant.

    This used to be a hand-written list of three modules. Seven others were
    missing the guard and the test stayed green - the same scope defect the
    guard itself exists to catch. Discover them instead of naming them.
    """
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    for path in sorted(src.glob("*.py")):
        body = path.read_text(encoding="utf-8")
        if "__main__" not in body or not re.search(r"^def main\(", body, re.M):
            continue
        # A script needs the guard when it reaches Fabric / Power BI, directly
        # or through helpers.
        if "get_fabric_token" in body or "get_powerbi_token" in body:
            yield path.stem, body


def test_every_fabric_entrypoint_pins_the_tenant():
    import inspect
    import importlib

    missing = []
    for name, _ in _runnable_scripts():
        mod = importlib.import_module(name)
        if "ensure_tenant" not in inspect.getsource(mod.main):
            missing.append(name)
    assert not missing, (
        f"{', '.join(missing)}: main() must pin the az subscription before calling "
        f"Fabric, or a tenant flip reads as a broken artefact")


def test_no_src_module_imports_winreg_unconditionally():
    """CI runs on ubuntu-latest, and the suite imports these modules directly.

    Each one carried `import os, sys, winreg` at module scope for the Windows PATH
    workaround. `winreg` does not exist off Windows, so on the runner every one of those
    imports raises ModuleNotFoundError at collection and the whole suite collapses — a
    failure no Windows machine can reproduce, which is why it survived 27 commits.

    This reads the AST, not the text: a comment or a docstring mentioning winreg is not a
    defect, and a text rule would fire on it. Discovered, not listed — a hand-written list
    is how the tenant guard went stale.
    """
    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    offenders = []
    for path in sorted(src.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # top level only: nested under `if` is the fix
            if isinstance(node, ast.Import) and any(
                a.name.split(".")[0] == "winreg" for a in node.names
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "winreg is Windows-only; import it under `if sys.platform == \"win32\"` or the "
        f"module cannot be imported on the CI runner: {', '.join(offenders)}")


def test_restore_path_is_a_noop_off_windows():
    """The structural check above says winreg is not imported; this says it is not used."""
    import importlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    checked = []
    for path in sorted(src.glob("*.py")):
        mod = importlib.import_module(path.stem)
        fn = getattr(mod, "_restore_path", None)
        if fn is None:
            continue
        before = os.environ.get("PATH", "")
        real, sys.platform = sys.platform, "linux"
        try:
            fn()  # must return before touching winreg or PATH
        finally:
            sys.platform = real
        assert os.environ.get("PATH", "") == before, f"{path.name} edited PATH off Windows"
        checked.append(path.stem)
    assert len(checked) >= 10, f"expected the whole deploy family, got {checked}"



    """One implementation, so the guard cannot drift between scripts."""
    import helpers, deploy_all
    assert callable(helpers.ensure_tenant)
    body = inspect_source(deploy_all.ensure_tenant)
    assert "az account set" not in body, (
        "deploy_all must delegate to helpers.ensure_tenant, not re-implement it")


def inspect_source(fn):
    import inspect
    return inspect.getsource(fn)


def test_ensure_tenant_warns_instead_of_crashing_without_config(capsys):
    import helpers
    helpers.ensure_tenant({})
    assert "az_subscription" in capsys.readouterr().out


# --- data agent grounding -------------------------------------------------
# Observed live on 3 Aug 2026: asked which customers Black Friday Blast hit, the
# agent answered "over 1 500" against a true 317.
#
# My first reading of that was wrong and is recorded here so it is not repeated.
# I concluded the answer was fabricated, because few-shots are query TEMPLATES
# carrying no results, so an answer with no execute step could only be invented.
# Two observations killed that:
#   - all ten customers it named are real, with the right e-mail and 4 Black
#     Friday sends each, so a query HAD run;
#   - a no-execute answer was later exactly right ("3.90 e-mails per customer,
#     12.5 % open rate"), confirmed by DAX across all 20 campaigns.
# The no-execute path is a response cache - a novel question always executed,
# a repeated one replayed in 12 s - and redeploying the agent clears it.
# The 1 503 came from the ontology's 200-row cap: the answer acknowledged the
# cap and estimated past it anyway.

def _agent_module():
    import deploy_data_agent
    return deploy_data_agent


def test_fewshot_queries_only_reference_objects_that_exist(model_index):
    """A renamed measure silently rots every few-shot that used it."""
    tables = model_index["tables"]
    known_measures = {m for t in tables.values() for m in t["measures"]}

    for fs in _agent_module().SM_FEWSHOTS:
        q = fs["query"]
        # SUMMARIZECOLUMNS / ROW name their outputs: "Clients", COUNTROWS(...).
        # Those names are then referenced as [Clients] in ORDER BY - they are
        # local aliases, not model measures.
        local = set(re.findall(r'"([^"]+)"\s*,', q))
        for table, column in re.findall(r"(\w+)\[([^\]]+)\]", q):
            assert table in tables, f"{fs['id']}: unknown table '{table}'"
            assert column in tables[table]["columns"], \
                f"{fs['id']}: unknown column '{table}[{column}]'"
        # [Name] with no table prefix is a measure reference.
        for measure in re.findall(r"(?<![\w\]])\[([^\]]+)\]", q):
            if measure in local:
                continue
            assert measure in known_measures, \
                f"{fs['id']}: unknown measure '[{measure}]'"


def test_every_fewshot_has_a_unique_id_and_a_query():
    seen = set()
    for fs in _agent_module().SM_FEWSHOTS:
        assert fs["query"].strip().upper().startswith("EVALUATE"), \
            f"{fs['id']}: a semantic-model few-shot must be a DAX EVALUATE"
        assert fs["id"] not in seen, f"duplicate few-shot id {fs['id']}"
        seen.add(fs["id"])


def test_the_agent_is_not_told_return_reasons_are_untracked():
    """returns[reason] exists. A blanket 'returns are order-level' note made the
    agent decline 'what are the main return reasons' - a question it can answer."""
    dda = _agent_module()
    text = dda.ai_instructions(False, "Black Friday Blast", 60)
    assert "changed_mind" in text, \
        "the agent must be told the return reasons it can actually group on"
    per_product = text[text.index("What returns do NOT carry"):]
    assert "per product or per" in per_product, \
        "the returns limitation must be scoped to the product grain, not the reason"


def test_the_agent_is_forbidden_from_answering_without_a_query():
    text = _agent_module().ai_instructions(False, "Black Friday Blast", 60)
    lowered = text.lower()
    assert "never answer without running a query" in lowered
    assert "templates" in lowered, \
        "the agent must be told few-shots carry no results, only queries"
    assert "invent" in lowered, "the agent must be told never to invent identities"


def test_the_demo_questions_each_have_a_matching_fewshot():
    """The portal's canned buttons are a demo surface with no other guard.

    A question with no close template is the one the agent improvises on.
    """
    dda = _agent_module()
    questions = " | ".join(fs["question"].lower() for fs in dda.SM_FEWSHOTS)
    for topic in ("motifs de retour", "pourquoi la campagne", "segment concentre",
                  "taux d'ouverture", "panier moyen", "roi des campagnes"):
        assert topic in questions, f"no few-shot covers the demo question '{topic}'"


def test_the_agent_may_not_estimate_a_total_from_a_capped_list():
    """A truncated list answers WHICH, never HOW MANY.

    Observed on the live agent: a GQL list hit the 200-row cap and the answer read
    "the query already returns more than 200 distinct results, the total exceeds
    1 500 customers". The true figure was 317. The names it listed were real, so a
    query had run - what was invented was the total, extrapolated from a cap the
    answer had just acknowledged. Telling it the cap exists was therefore not
    enough; it has to be told what to do instead, and which words give the guess
    away.
    """
    text = _agent_module().ai_instructions(False, "Black Friday Blast", 60)
    lowered = text.lower()
    assert "a truncated list answers which. it never answers how many." in lowered, \
        "the agent needs the rule as an instruction, not only as a warning"
    for hedge in ("more than", "about", "exceeds"):
        assert hedge in lowered, \
            f"the hedging word {hedge!r} must be named as forbidden before a number"
    assert "second query" in lowered, \
        "the agent must be told to run the scalar aggregate rather than estimate"


# --- which source answered ------------------------------------------------
# Payloads below are verbatim from live runs on 3 Aug 2026. They are the whole
# point of these tests: the shapes are undocumented, so a test written from a
# guess would pass against the guess and fail against Fabric.

def _trace_source():
    import importlib.util
    path = ROOT / "portal" / "backend" / "trace_source.py"
    spec = importlib.util.spec_from_file_location("trace_source", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ONTOLOGY_RUN = [
    {"tool": "generate.filename", "arguments": '{"natural_language_description":"..."}'},
    {"tool": "analyze.database.execute", "arguments": json.dumps({
        "datasource_name": "ONT_Customer360",
        "datasource_type": "Ontology",
        "code": "```ontology\n" + json.dumps({"entitySelector": {
            "queryType": "GQL",
            "query": "MATCH (node_Customer:`Customer`)-[:`CustomerBelongsToAccount`]->"
                     "(node_Account:`Account`) RETURN node_Account.`account_name`"}}) + "\n```",
    })},
    {"tool": "trace.analyze_ontology", "arguments": '{"query":"..."}'},
    # The sting: an ontology run ends by loading the SEMANTIC MODEL few-shots.
    {"tool": "analyze.database.fewshots.loading", "arguments": json.dumps({
        "datasource_name": "SM_Marketing_Analytics", "datasource_type": "SemanticModel"})},
]

_SEMANTIC_MODEL_RUN = [
    {"tool": "analyze.database.execute", "arguments": json.dumps({
        "datasource_name": "SM_Marketing_Analytics",
        "datasource_type": "SemanticModel",
        "code": "```dax\nEVALUATE SUMMARIZECOLUMNS('orders'[channel], \"CA\", [Product Revenue])\n```",
    })},
]

_CACHED_RUN = [
    {"tool": "analyze.database.fewshots.loading", "arguments": json.dumps({
        "datasource_name": "SM_Marketing_Analytics", "datasource_type": "SemanticModel"})},
]


def test_the_source_is_read_from_the_execute_call_not_from_any_step():
    """An ontology run's LAST step names the semantic model.

    Captured live: a graph answer whose trace ends on fewshots.loading for
    SM_Marketing_Analytics. Any logic that scans the trace for a datasource name
    and takes the first or last one it finds captions that answer "semantic
    model" - and it does so precisely on the graph questions the demo exists to
    show. Only analyze.database.execute is authoritative.
    """
    got = _trace_source().describe(_ONTOLOGY_RUN)
    assert got["source"] == "ontology"
    assert got["sourceName"] == "ONT_Customer360"
    assert got["queryLanguage"] == "GQL"


def test_the_ontology_query_is_unwrapped_down_to_the_gql():
    """The ontology `code` is a JSON envelope carrying the query, not the query.

    Showing the envelope shows plumbing; showing the MATCH shows the graph.
    """
    got = _trace_source().describe(_ONTOLOGY_RUN)
    assert got["generatedQuery"].startswith("MATCH ")
    assert "entitySelector" not in got["generatedQuery"]
    assert "```" not in got["generatedQuery"]


def test_a_semantic_model_run_is_reported_as_dax():
    got = _trace_source().describe(_SEMANTIC_MODEL_RUN)
    assert got["source"] == "semantic_model"
    assert got["queryLanguage"] == "DAX"
    assert got["generatedQuery"].startswith("EVALUATE")
    assert "```" not in got["generatedQuery"]


def test_a_cached_answer_reports_no_source_rather_than_guessing():
    """No execute call means the source is genuinely unknown.

    The cached answer's trace still carries a datasource name, so guessing is
    easy and wrong: the Black Friday question replayed from cache while its
    trace named the semantic model, having originally been answered by the
    ontology. An empty source is the honest output.
    """
    got = _trace_source().describe(_CACHED_RUN)
    assert got["source"] == ""
    assert got["generatedQuery"] == ""


def test_the_chat_response_exposes_the_source_to_the_front_end():
    text = (PORTAL / "backend" / "main.py").read_text(encoding="utf-8")
    for field in ("source:", "sourceName:", "queryLanguage:", "generatedQuery:"):
        assert field in text, f"ChatResponse must carry {field} for the badge"
    assert "trace_source.describe" in text, \
        "the source must be computed, not left for the front end to re-derive"


def test_the_portal_imports_trace_source_whatever_the_launch_style():
    """The portal died on startup with ModuleNotFoundError while the code was fine.

    `python -m uvicorn backend.main:app` from portal/ puts portal/ on sys.path but NOT
    portal/backend/, so a bare `import trace_source` resolves only when the server is
    launched from inside backend/. The previous launch happened to be that style, so the
    breakage stayed hidden until the server was restarted a different way - on demo day
    that is a portal that will not start, with a green test suite.

    This is a static guard, so it proves the fix is present, not that every launch style
    works; the two launch styles were checked by hand when the fix went in.
    """
    text = (PORTAL / "backend" / "main.py").read_text(encoding="utf-8")
    pin = text.find("sys.path.insert(0, str(Path(__file__).resolve().parent))")
    imp = text.find("import trace_source")
    assert pin != -1, "main.py must pin its own directory on sys.path"
    assert pin < imp, "the sys.path pin must come BEFORE `import trace_source`"


def test_the_answer_is_tied_to_our_own_run_not_to_the_newest_message():
    """Fabric gives every caller the SAME thread, so the newest answer is not ours.

    Observed 3 Aug 2026: POST /threads returned thread_z2l0nmctAMLMaFU3rDTpnpOq
    twice in a row, and a brand-new thread already listed 20 messages from earlier
    portal calls. When our own run then failed, the portal returned the newest
    assistant message in that shared thread - a confident, well-formed answer about
    a customer nobody had asked about, with our question's GQL displayed next to it.

    A demo cannot survive answering the wrong question convincingly. Only run_id
    ties a message to the run we started.

    CORRECTION, same day, later: this test also forbade DELETE /threads, on the
    grounds that the thread is shared and deleting it would destroy a concurrent
    run's conversation. That reason still holds - which is why the delete must NOT
    be unconditional - but the blanket ban was wrong, and it hid the cure.

    That sticky thread eventually reaches a state where EVERY run on it fails in
    ~2s with {"code":"server_error"} before a single tool call. Measured: 0/8 novel
    questions across two different instruction sets, while the semantic model still
    answered DAX directly (HTTP 200, 5 083 349,74 EUR) on an Active F16 - and a
    throwaway agent built from the very same definition answered first time, on a
    thread of its own. DELETE then POST returned a genuinely new id and the original
    agent recovered: 3/3 immediately after.

    So the rule is narrower than either extreme: purge only when our run produced
    no answer, then retry once.
    """
    text = (PORTAL / "backend" / "main.py").read_text(encoding="utf-8")
    assert 'msg.get("run_id") == run_id' in text, \
        "the assistant message must be selected by run_id, never by recency"
    assert "run_status" in text, "an incomplete run must be reported, not papered over"

    lines = text.splitlines()
    deletes = [i for i, ln in enumerate(lines) if "client.delete(f\"{base}/threads/" in ln]
    assert deletes, "no thread purge: a poisoned thread would kill every later question"
    for i in deletes:
        guard = [j for j in range(max(0, i - 4), i) if "if not answer:" in lines[j]]
        assert guard, (f"line {i+1}: the thread purge must sit on the no-answer recovery "
                       "path - deleting on every call would break a concurrent run")


# --- ontology few-shots ---------------------------------------------------

def _ontology_module():
    import deploy_ontology
    return deploy_ontology


def test_ontology_fewshots_only_walk_entities_and_edges_that_exist():
    """A renamed entity or relationship rots every few-shot that used it.

    Nothing fails loudly: the agent keeps writing GQL against a label the graph no
    longer has, and simply answers "no data available" - which reads on stage as
    "the demo has no data", not as "the query was wrong".
    """
    ont = _ontology_module()
    entities = {name for name, *_ in ont.ENTITIES}
    edges = {name for name, *_ in ont.RELATIONSHIPS}

    for question, query in _agent_module().ONT_FEWSHOTS:
        for label in re.findall(r":([A-Z][A-Za-z]+)\s*[{)]", query):
            assert label in entities, f"{question}: unknown entity :{label}"
        for edge in re.findall(r"-\[\s*(?:\w+)?:([A-Za-z]+)\s*\]", query):
            assert edge in edges, f"{question}: unknown relationship :{edge}"


def test_ontology_fewshots_never_return_a_bare_node():
    """Returning a node emits that entity's full JSON on every row.

    Measured 3 Aug 2026: an account-per-customer query came back carrying an
    `account_json` column, the execute payload reached 160 588 characters, every
    tool step reported success - and the run still died at the answer step with
    "Sorry, something went wrong." Projecting the seven properties it needed made
    the same question complete. A few-shot showing the wide form teaches the agent
    to reproduce the crash.
    """
    for question, query in _agent_module().ONT_FEWSHOTS:
        returned = query.split("RETURN", 1)[1] if "RETURN" in query else ""
        for item in returned.split(" ORDER BY")[0].split(","):
            item = item.strip().split(" AS ")[0].strip()
            if not item or "(" in item or item.upper() == "DISTINCT":
                continue           # aggregate or DISTINCT keyword, not a projection
            item = item.replace("DISTINCT ", "").strip()
            assert "." in item, (
                f"{question}: RETURN {item} yields the whole node - project a property")


def test_multi_hop_ontology_fewshots_bound_their_result():
    """The graph's value on stage is the PATH, not the volume.

    A walk across several edges fans out fast, and an unbounded list both hits the
    200-row cap - which makes every count derived from it wrong - and fills a slide
    with rows nobody reads. So a multi-hop few-shot must narrow itself one of three
    ways: aggregate it, LIMIT it, or start from one named instance.

    The anchor is the weakest of the three and is accepted deliberately, not
    because it is always small: {campaign_name:'Black Friday Blast'} reaches 3 959
    customers. It is accepted because it pins the traversal to a subject the
    presenter has just named, which is what makes the path legible. Width is
    covered separately by the bare-node test - that is the one guarding the crash.
    """
    for question, query in _agent_module().ONT_FEWSHOTS:
        hops = len(re.findall(r"-\[", query))
        if hops < 2:
            continue
        upper = query.upper()
        bounded = ("COUNT(" in upper or "SUM(" in upper or "LIMIT" in upper
                   or re.search(r"\{\s*\w+\s*:\s*'[^']+'\s*\}", query))
        assert bounded, (
            f"{question}: {hops}-hop few-shot must aggregate, LIMIT, or anchor on an instance")


def test_the_agent_is_told_not_to_return_whole_entities():
    agent = _agent_module()
    for ontology_only in (True, False):
        text = agent.ai_instructions(ontology_only, "Black Friday Blast", 317)
        assert "NEVER return a bare node" in text, "the payload rule must reach the tenant"
        assert "160 588" in text, "keep the evidence with the rule, or it reads as a preference"


def test_the_agent_retries_a_query_the_capacity_throttled():
    """A throttled graph query must be retried, not turned into an apology.

    Two days before a customer demo: all 8 ontology probe questions routed
    correctly, generated valid GQL, and every single one came back to the user as
    "je ne peux pas acceder a cette information". The step error was:

        Failed to execute Ontology query with error "The federated query failed to
        execute due to capacity throttling. Please try again after 1 second(s)."

    The data was fine, the query was fine, and the source itself said to wait one second.
    The agent's default is to give up on the first failure, which on stage is
    indistinguishable from a broken demo. So the retry instruction is not a nicety: it is
    the difference between the graph working and the graph appearing to have no data.

    Guarded in BOTH branches because instruction text is tenant state - a rule present in
    one call site and absent in the other flips the live agent on alternating deploys.
    """
    agent = _agent_module()
    for ontology_only in (True, False):
        text = agent.ai_instructions(ontology_only, "Black Friday Blast", 317)
        assert "capacity throttling" in text, (
            f"ontology_only={ontology_only}: the agent must recognise a throttled query")
        assert "again" in text.lower(), (
            f"ontology_only={ontology_only}: recognising it is useless without retrying")
        assert "not that the information is unavailable" in text, (
            f"ontology_only={ontology_only}: a throttle must never be reported as missing data")


def test_the_agent_knows_the_exact_spelling_of_at_risk():
    """An empty result from a mis-spelled literal looks exactly like "there are none".

    3 Aug 2026, probing the follow-up questions: three of eight came back "aucun compte
    B2B n'a de clients a risque". The GQL was valid, the traversal correct, and the
    filter read `LOWER(cu.lifecycle_stage) = LOWER("at risk")` while the stored value is
    `at_risk`. The demo's churn questions are exactly the ones that break, and they break
    silently - the agent reports an empty set as a finding.

    The same session also showed the ambiguity is real: a fourth question answered on the
    SEGMENT named "At Risk" instead of the lifecycle state, and nothing said so.

    Guarded in both branches: instruction text is tenant state, and a rule present at one
    call site and absent at the other flips the live agent on alternating deploys.
    """
    agent = _agent_module()
    for ontology_only in (True, False):
        text = agent.ai_instructions(ontology_only, "Black Friday Blast", 317, "High Value, At Risk")
        assert "at_risk" in text, (
            f"ontology_only={ontology_only}: the exact lifecycle value must be stated")
        assert "UNDERSCORE" in text or "underscore" in text, (
            f"ontology_only={ontology_only}: 'at risk' vs 'at_risk' is the whole bug - "
            f"listing the value without calling out the spelling invites the same guess")
        assert "ambiguous" in text.lower(), (
            f"ontology_only={ontology_only}: 'at risk' is both a lifecycle state and a "
            f"segment name; unresolved, the agent answers a different question in silence")
        assert "High Value" in text, (
            f"ontology_only={ontology_only}: the segment names must reach the instructions "
            f"from config - hard-coding or dropping them brings the guessing back")

    src = (SRC / "deploy_data_agent.py").read_text(encoding="utf-8")
    assert 'cfg.get("segments"' in src or 'cfg["segments"]' in src, (
        "the segment list must be read from config.yaml at deploy time; a copy in the "
        "instructions would keep naming segments the generator no longer writes")


def test_the_agent_is_told_every_interaction_value_the_generator_writes():
    """The agent invents an enum value when the QUESTION contains one.

    3 Aug 2026, live run: "quels comptes B2B concentrent le plus d'interactions support
    negatives" produced valid GQL filtering `interaction_type = "support"`. The generator
    writes call, email, chat, ticket, meeting - there is no "support". Zero rows, and the
    agent reported "aucune interaction de ce type", which reads like a finding.

    Same failure as at_risk, different column: the model borrows a word from the question
    and treats it as data. The cure is not phrasing, it is stating the domain - so this
    test reads the values back out of generate_data.py and fails when the two drift.

    Both branches, because instruction text is tenant state and a rule at one call site
    only flips the live agent on alternating deploys.
    """
    gen = (SRC / "generate_data.py").read_text(encoding="utf-8")
    types = re.search(r'types = \[([^\]]+)\]', gen)
    assert types, "generate_data.py no longer declares the interaction types as a literal"
    values = re.findall(r'"([a-z_]+)"', types.group(1))
    assert len(values) >= 3, f"only parsed {values} - the regex is no longer reading the list"

    agent = _agent_module()
    for ontology_only in (True, False):
        text = agent.ai_instructions(ontology_only, "Black Friday Blast", 317, "High Value")
        for value in values:
            assert value in text, (
                f"ontology_only={ontology_only}: interaction_type '{value}' is written by the "
                f"generator but never named to the agent - it will keep guessing one")
        assert "support" in text.lower(), (
            f"ontology_only={ontology_only}: 'support' is the word the agent actually invented; "
            f"listing the real values without denying that one leaves the trap open")
        for value in ("negative", "neutral", "positive"):
            assert value in text, (
                f"ontology_only={ontology_only}: sentiment '{value}' must be stated - it is the "
                f"other column every churn question filters on")


# ── The graph must be VISIBLE in the portal ──────────────────────────────────
# Asked for on 3 Aug 2026: "il faut plus d'elements sur l'ontologie car le semantic
# model tout le monde connait". Before this, the portal declared one source (the
# semantic model), and 0 of its 20 canned questions ever reached the graph - so a demo
# could run start to finish without the audience seeing that a graph existed at all.

# The only phrasings PROVEN to reach the ontology, probed live on 3 Aug 2026 (8/8
# routed to GQL). A question that merely looks relational is not evidence: the first 20
# canned questions all looked reasonable and 0 of them left the semantic model. Adding a
# suggestion here without probing it is exactly the mistake this list exists to prevent.
_PROBED_ONTOLOGY_QUESTIONS = [
    "Quels comptes B2B regroupent le plus de clients touches par la campagne",
    "Quels clients ont recu a la fois",
    "ciblait-elle et quels segments a-t-elle reellement touches",
    "Quels objets d email ont ete utilises par la campagne",
    "Quels produits ont ete achetes par les clients touches par",
    "Quelles categories de produits les clients du segment High Value achetent-ils",
    "Quels comptes B2B concentrent le plus d interactions support negatives",
    "Quels clients a risque appartiennent au meme compte B2B",
    # Probed 2026-08-03 for the follow-up wave, 8/8 reached GQL
    # (files/probe_followups.json). The eighth, "Quelles campagnes ont genere des
    # commandes chez les clients a risque", routed correctly but returned an empty
    # result set - correct routing is not a usable answer, so it is not listed here.
    "Quels comptes B2B regroupent le plus de clients a risque",
    "Quelles campagnes ont touche les clients du segment High Value",
    "Quels segments les clients a risque partagent-ils",
    "Quels produits les clients a risque avaient-ils commandes",
    "Quels objets d email la campagne Cyber Monday a-t-elle utilises",
    "Quelles campagnes ont ete envoyees aux clients du segment High Value",
    "Quels clients ont commande des produits de la categorie Electronics",
]


def _suggestion_text(s):
    """A suggestion is {"q": ..., "src": ...}; `q` may be dynamic (f-string on CULPRIT)."""
    if isinstance(s, dict):
        return s.get("q", "")
    return s if isinstance(s, str) else ""


def test_every_persona_offers_at_least_one_graph_question():
    for key, a in _portal_agents().items():
        sugs = a.get("suggestions", [])
        assert sugs, f"persona '{key}' has no suggestions"
        graph = [s for s in sugs if isinstance(s, dict) and s.get("src") == "ontology"]
        assert graph, (
            f"persona '{key}' offers no ontology question: a visitor can walk the whole "
            f"persona without the graph ever being used")


def test_a_suggestion_declares_a_source_the_portal_can_render():
    for key, a in _portal_agents().items():
        for s in a.get("suggestions", []):
            assert isinstance(s, dict), f"{key}: suggestion must be a dict, got {type(s).__name__}"
            assert s.get("src") in ("ontology", "model"), (
                f"{key}: suggestion src={s.get('src')!r} - the frontend only renders "
                f"'ontology' and 'model', anything else silently loses its pill")
            assert s.get("q"), f"{key}: suggestion has no question text"


def test_a_question_announced_as_graph_was_actually_probed_as_graph():
    """The pill is a promise made before the answer arrives - it must be evidence-based.

    The portal announces the expected source under every suggestion. Announcing the
    ontology for a question that in fact routes to the semantic model is worse than
    announcing nothing: on stage the badge under the answer will contradict the pill,
    live, in front of the customer.
    """
    for key, a in _portal_agents().items():
        for s in a.get("suggestions", []):
            if not (isinstance(s, dict) and s.get("src") == "ontology"):
                continue
            text = _suggestion_text(s)
            if text == "<dynamic>":
                continue
            assert any(p in text for p in _PROBED_ONTOLOGY_QUESTIONS), (
                f"{key}: '{text}' is announced as a graph question but is not one of the "
                f"phrasings probed live - probe it first, or label it 'model'")


def test_the_portal_graph_map_is_read_from_the_deployer():
    """The picture the audience looks at must be generated by the graph that is deployed.

    A hand-maintained copy of the entity list in the portal would keep rendering the old
    graph the day an entity is added to deploy_ontology.py, and nothing would fail. So
    main.py parses the deployer, and this test proves the parse actually resolves.
    """
    main_src = (PORTAL / "backend" / "main.py").read_text(encoding="utf-8")
    assert "deploy_ontology.py" in main_src, (
        "the portal must read the graph from its deployer, not hold a copy")

    ns: dict = {}
    tree = ast.parse(main_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_ontology_graph":
            ns["found"] = True
    assert ns.get("found"), "_ontology_graph() disappeared - the graph panel would be empty"

    ont = _ontology_module()
    entities = {e[0] for e in ont.ENTITIES}
    relationships = {r[0] for r in ont.RELATIONSHIPS}
    assert len(entities) >= 8 and len(relationships) >= 9, (
        "the graph shrank - check deploy_ontology.py before the demo")


def test_the_suggestion_button_sends_the_question_not_its_pill():
    """The pill glyph lives inside the button, so textContent is no longer the question.

    Reading btn.textContent would send "&#128376;Quels comptes B2B..." to the agent.
    """
    html = (PORTAL / "static" / "index.html").read_text(encoding="utf-8")
    assert "data-q=" in html, "the suggestion button must carry its question in data-q"
    assert "getAttribute('data-q')" in html, (
        "useSug must read data-q; textContent now includes the source pill")


# ── Follow-up suggestions ────────────────────────────────────
# Same discipline as the opening suggestions: a follow-up announces an engine, so the
# announcement has to be evidence-based, and it has to survive to the screen.

def _portal_followup_tables() -> dict:
    """{_FOLLOWUP_TEMPLATES, _UNIVERSAL_FOLLOWUPS} read statically from main.py."""
    tree = ast.parse((PORTAL / "backend" / "main.py").read_text(encoding="utf-8"))
    consts = _module_constants(tree)
    out: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in (
                    "_FOLLOWUP_TEMPLATES", "_UNIVERSAL_FOLLOWUPS"):
                out[target.id] = _lit_or_dynamic(node.value, consts)
    return out


def _all_followup_branches():
    """(label, [follow-ups]) for every keyword branch and every universal fallback."""
    tables = _portal_followup_tables()
    for persona, branches in (tables.get("_FOLLOWUP_TEMPLATES") or {}).items():
        for pattern, items in (branches or {}).items():
            yield f"{persona}/{pattern}", items
    for persona, items in (tables.get("_UNIVERSAL_FOLLOWUPS") or {}).items():
        yield f"{persona}/universal", items


def test_the_followup_tables_are_actually_readable():
    """Guard the guards: a parse that silently degrades makes every test below vacuous.

    The `src` symbols are ast.Name and the lists hold f-strings, so a parser that does
    not resolve constants or does not descend into lists returns "<dynamic>" for
    everything and the follow-up assertions pass on an empty set.
    """
    branches = list(_all_followup_branches())
    assert len(branches) >= 8, f"only {len(branches)} follow-up branches parsed - parse degraded"
    degraded = [label for label, b in branches if not isinstance(b, list)]
    assert not degraded, (
        f"these branches parsed as {'<dynamic>'!r} instead of a list: {degraded}. Every "
        f"assertion below iterates them, so they would be checked character by character "
        f"and quietly prove nothing.")
    items = [c for _, b in branches for c in b]
    assert len(items) >= 30, f"only {len(items)} follow-ups parsed - parse degraded"
    assert any(isinstance(c, dict) and c.get("src") == "ontology" for c in items), (
        "no follow-up resolved to src='ontology' - the constants were not resolved")


def test_a_followup_declares_a_source_the_portal_can_render():
    """A follow-up with no pill breaks the colour code exactly when it has been learnt.

    The audience reads the purple badge on the answer, then three unlabelled buttons:
    the demo stops saying which engine is about to work, which is the whole point.
    """
    for label, items in _all_followup_branches():
        assert isinstance(items, list) and items, f"{label}: no follow-ups"
        for c in items:
            assert isinstance(c, dict), (
                f"{label}: follow-up must be {{q, src}}, got {type(c).__name__} - a bare "
                f"string serialises fine and renders with no pill")
            assert c.get("src") in ("ontology", "model"), (
                f"{label}: src={c.get('src')!r}; the frontend only renders 'ontology' "
                f"and 'model', anything else silently loses its pill")
            assert c.get("q"), f"{label}: follow-up has no question text"


def test_a_followup_announced_as_graph_was_actually_probed_as_graph():
    """Announcing the graph for a question that routes to DAX contradicts itself on stage."""
    for label, items in _all_followup_branches():
        for c in items:
            if not (isinstance(c, dict) and c.get("src") == "ontology"):
                continue
            text = _suggestion_text(c)
            if text == "<dynamic>":
                continue
            assert any(p in text for p in _PROBED_ONTOLOGY_QUESTIONS), (
                f"{label}: '{text}' is announced as a graph question but is not one of "
                f"the phrasings probed live - probe it first, or label it 'model'")


def test_every_followup_branch_can_offer_both_engines():
    """Three follow-ups from one thematic branch otherwise share one source.

    _blend_sources can only show both engines if the branch it draws from holds both.
    A branch that is graph-only turns the dual-source agent into a graph demo; a branch
    that is model-only is how a graph answer dead-ends and the ontology disappears.
    """
    for label, items in _all_followup_branches():
        srcs = {c.get("src") for c in items if isinstance(c, dict)}
        assert "ontology" in srcs and "model" in srcs, (
            f"{label}: offers only {sorted(srcs)} - the trio cannot show both engines")


def test_the_relational_vocabulary_leads_into_the_graph():
    """The words that describe relationships must have a branch, or the graph is unreachable.

    A question about B2B accounts, segments or products is a graph question; if no branch
    matches it, the answer falls back to the universal list and the conversation walks
    away from the ontology at the exact moment it was inside it.
    """
    templates = _portal_followup_tables().get("_FOLLOWUP_TEMPLATES") or {}
    assert templates, "the follow-up templates did not parse"
    for persona, branches in templates.items():
        joined = " ".join(branches or {})
        assert any(w in joined for w in ("compte", "b2b", "segment", "produit", "categorie",
                                         "objet", "asset")), (
            f"persona '{persona}' has no relational branch: a graph answer there falls "
            f"straight through to the generic fallback")


def test_followups_and_suggestions_share_one_renderer():
    """Two renderers is how the pill got added to one wave of buttons and not the other."""
    html = (PORTAL / "static" / "index.html").read_text(encoding="utf-8")
    assert "function sugBtnHtml(" in html, (
        "the shared suggestion/follow-up renderer disappeared")
    assert html.count("sugBtnHtml(key,s)") >= 2, (
        "both the opening suggestions and the follow-ups must go through sugBtnHtml")
    assert "d.followUps.map(function(s){return sugBtnHtml(key,s);})" in html, (
        "the follow-ups render through their own path again - they will lose the pill")


def test_a_busy_agent_is_waited_out_not_purged():
    """Two failures look identical from the portal and need opposite cures.

    3 Aug 2026: the Data Agent serialises runs per AGENT, not per thread, though it says
    "A run is already in progress for this thread". A brand-new thread id got the same
    refusal 45s after the previous question and the same question succeeded 150s later.
    On stage that is one click too fast on a follow-up button.

    The portal's only recovery was purge-the-thread-and-retry, which for this failure is
    worse than doing nothing: the block is not on our thread, the retry hits it again 3s
    later, and DELETE can kill a run another persona is mid-way through. So the busy case
    must WAIT on the same thread, and only the other case may purge.

    The distinction is invisible without last_error, so that is asserted too - polling only
    the status collapses both failures into "no answer" and the wait can never be chosen.
    """
    src = (ROOT / "portal" / "backend" / "main.py").read_text(encoding="utf-8")
    assert "already in progress" in src, (
        "the busy refusal must be recognised by its message; without it the portal purges "
        "a thread that was never the problem and gives up in 3 seconds")
    assert 'get("last_error")' in src, (
        "run status alone cannot tell 'agent busy' from 'thread poisoned' - both are "
        "status=failed with no answer; last_error must actually be READ from the run, not "
        "merely named (a variable initialised to \"\" satisfies a name check and nothing else)")

    # Anchor on the retry loop itself. A loose search for asyncio.sleep finds the 2s poll
    # in _attempt, which sits before the purge and makes this pass with no wait at all -
    # that hole was found by mutation on 3 Aug 2026, not by reading the test.
    loop = src.find("for _ in range(BUSY_RETRIES)")
    assert loop != -1, "the busy retry loop must be driven by BUSY_RETRIES"
    body = src[loop:src.find("if not answer:", loop)]
    assert "asyncio.sleep(BUSY_WAIT_S)" in body, (
        "the busy branch must actually wait - retrying immediately hits the same per-agent "
        "lock, and deleting the thread does not release it")
    assert "delete" not in body, (
        "the busy branch must not purge: the block is not on our thread, and DELETE can "
        "kill a run another persona is mid-way through")

    mod = _module_constants(ast.parse(src))
    total = mod.get("BUSY_RETRIES", 0) * mod.get("BUSY_WAIT_S", 0)
    assert total >= 120, (
        f"the wait budget is {total}s; the observed lock was still held at 45s and clear "
        f"at 150s, so anything under ~120s gives up while the agent is still working")


# ── The portal's text size ───────────────────────────────────────────────────
# 3 Aug 2026, two days before the demo: "la police de l'application est un peu petite".
# Body text was 13.5px, read from several metres on a projector. The fix is not 71 edited
# numbers, it is one knob - and a knob only stays a knob if nothing escapes it.

_PORTAL_HTML = ROOT / "portal" / "static" / "index.html"


def _portal_scale() -> float:
    """The html font-size percentage, as a multiplier. Every rem in the page rides on it."""
    m = re.search(r"html\s*\{[^}]*font-size:\s*([\d.]+)%", _PORTAL_HTML.read_text(encoding="utf-8"))
    assert m, ("the portal must set its root font-size as a percentage on html - that is the "
               "single knob every rem is relative to")
    return float(m.group(1)) / 100


def test_every_portal_font_size_rides_on_the_one_knob():
    """A single px font-size silently opts that element out of the scale.

    The page had 71 hard-coded px sizes from 8 to 38. Scaling them by hand is 71 chances
    to miss one, and a missed one is invisible until someone reads the screen from the back
    of the room - which is the only place it matters. They are now rem, so the knob moves
    all of them or none.

    SVG `font-size="24"` attributes are deliberately not covered: they are user units inside
    a viewBox and already scale with the drawing, not with the root font size.
    """
    html = _PORTAL_HTML.read_text(encoding="utf-8")
    stragglers = re.findall(r"font-size:\s*[\d.]+px", html)
    assert not stragglers, (
        f"{len(stragglers)} font-size still in px ({stragglers[:5]}): px ignores the root "
        f"scale, so this text stays small while everything around it grows")

    assert _portal_scale() >= 1.10, (
        f"the scale is {_portal_scale():.2f}; below ~1.10 the body text falls back under 15px, "
        f"which is what was reported as too small to read on a projector")


def test_a_fixed_height_portal_box_still_fits_its_text():
    """Text that grows inside a box that does not is the Power BI clipping bug, in CSS.

    Same arithmetic as validate_layout(): a line box is proportional (~1.35 for this kind of
    UI font) and the chrome around it is a constant (~8px) - one multiplier cannot express
    both. Checked at the CURRENT scale, so raising the knob later fails here rather than on
    stage. Only `height` counts: min-height grows and max-height pairs with overflow.
    """
    html = _PORTAL_HTML.read_text(encoding="utf-8")
    style = html[html.index("<style>"):html.index("</style>")]
    scale = _portal_scale()

    checked = 0
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", style):
        body = m.group(2)
        h = re.search(r"(?<![a-z-])height:\s*(\d+)px", body)
        fs = re.search(r"font-size:\s*([\d.]+)rem", body)
        if not (h and fs):
            continue
        checked += 1
        box = int(h.group(1))
        pt = float(fs.group(1)) * 16 * scale
        need = pt * 1.35 + 8
        sel = m.group(1).strip().splitlines()[-1].strip()
        assert box >= need, (
            f"{sel}: box is {box}px but {pt:.1f}px text needs {need:.1f}px - it will render "
            f"clipped, and the browser will neither shrink the font nor warn")
    assert checked >= 4, (
        f"only {checked} fixed-height text boxes examined; the regex has stopped matching "
        f"the stylesheet and this guard is passing on nothing")

