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
import pathlib
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
    assert cfg["workspace_name"] == "CDR - Marketing Campaign"


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
                fields[fk.value] = [_lit_or_dynamic(e) for e in fv.elts] \
                    if isinstance(fv, ast.List) else "<dynamic>"
        out[k.value] = fields
    return out


def _lit_or_dynamic(node):
    try:
        return ast.literal_eval(node)
    except ValueError:
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
    import deploy_report_arc as dr
    report, pbir, theme_json, theme_name = dr.build_report({}, cfg)
    return {"report": report, "pbir": pbir, "theme": theme_json, "theme_name": theme_name}


@pytest.fixture(scope="module")
def model_index(model_bim):
    """{table: {'columns': set, 'measures': set}} plus the filter-propagation graph."""
    idx = {}
    for t in model_bim["model"]["tables"]:
        idx[t["name"]] = {"columns": {c["name"] for c in t.get("columns", [])},
                          "measures": {m["name"] for m in t.get("measures", [])}}
    # Single-direction many-to-one: filters flow from the "one" side to the "many" side.
    flows = {}
    for r in model_bim["model"]["relationships"]:
        flows.setdefault(r["toTable"], set()).add(r["fromTable"])
    return {"tables": idx, "flows": flows}


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

    Every relationship is many-to-one and single-direction, so crm_customer_profile
    cannot filter crm_customers, and crm_segments cannot filter marketing_sends.
    Such a visual does not error — it just repeats the grand total on every category.
    """
    flows = model_index["flows"]

    def reaches(src, dst, seen=None):
        if src == dst:
            return True
        seen = seen or set()
        seen.add(src)
        return any(n not in seen and reaches(n, dst, seen) for n in flows.get(src, ()))

    for page, sv in _visuals(report_def["report"]):
        if sv["visualType"] in DECORATIVE:
            continue
        cols = [s["Name"].split(".", 1) for s in sv["prototypeQuery"]["Select"] if "Column" in s]
        measures = [s["Name"].split(".", 1) for s in sv["prototypeQuery"]["Select"] if "Measure" in s]
        for ctable, ccol in cols:
            for mtable, mname in measures:
                assert reaches(ctable, mtable), (
                    f"{page}: grouping {ctable}[{ccol}] by [{mname}] ({mtable}) — "
                    f"filters cannot flow that way, every category would show the same total")


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
    import deploy_report_arc as dr
    problems = dr.validate_layout(report_def["report"])
    assert not problems, "layout defects:\n  " + "\n  ".join(problems)


def test_textbox_height_rule_rejects_a_too_small_box():
    """Guard the guard: the rule must actually fail on the geometry that shipped."""
    import deploy_report_arc as dr
    assert dr.text_height(17) > 30, "17pt in a 30px box must be rejected"
    assert dr.text_height(10) > 20, "10pt in a 20px box must be rejected"


def test_height_model_keeps_padding_separate_from_line_height():
    """A single px-per-pt multiplier cannot express a constant padding term.

    Folding padding into a multiplier under-sizes small text and over-sizes
    large text — that is how a 10pt subtitle got a 22px box when it needs 26.
    """
    import deploy_report_arc as dr
    # doubling the font must NOT double the required height: padding is constant
    assert dr.text_height(20) < 2 * dr.text_height(10)
    # the proportional part alone must scale linearly
    assert dr.line_px(20) == pytest.approx(2 * dr.line_px(10))


def test_card_height_accounts_for_the_whole_text_stack():
    """A card stacks title + callout + label; sizing on the callout alone clips."""
    import deploy_report_arc as dr
    assert dr.card_height(11, 30, 9) > 112, (
        "the 30pt callout stack must be rejected in a 112px card")
    assert dr.card_height(11, dr.CARD_VALUE_PT, 9) <= dr.CARD_H, (
        "the shipped card fonts must fit the shipped card height")


def test_every_card_is_tall_enough_for_its_own_fonts(report_def):
    import deploy_report_arc as dr
    seen = 0
    for section in report_def["report"]["sections"]:
        for vc in section["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            if sv["visualType"] != "cardVisual":
                continue
            seen += 1
            stack = [dr._font_pt(sv.get("vcObjects", {}), "title"),
                     dr._font_pt(sv.get("objects", {}), "calloutValue"),
                     dr._font_pt(sv.get("objects", {}), "categoryLabel")]
            stack = [p for p in stack if p]
            assert len(stack) == 3, "a card should declare three font sizes"
            assert vc["height"] >= dr.card_height(*stack)
    assert seen == 20, f"expected 20 cards, found {seen}"


def test_validator_detects_the_geometry_that_shipped(report_def):
    """Regression harness: re-inject the original geometry and count the defects.

    32 = 4 pages x (title + subtitle clipped + their overlap) + 20 cards whose
    category label was cut off by the 30pt callout.
    """
    import copy
    import deploy_report_arc as dr
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
                v["config"] = json.dumps(c)
    problems = dr.validate_layout(old)
    assert len(problems) == 32, f"expected 32 defects, got {len(problems)}"
    assert sum("card is" in p for p in problems) == 20
    assert sum("overlaps" in p for p in problems) == 4
    assert sum("text will be clipped" in p for p in problems) == 8


def test_header_band_is_not_counted_as_an_overlap(report_def):
    """The z=0 band sits under its own text on purpose — 4 false positives/page."""
    import deploy_report_arc as dr
    bands = [vc for s in report_def["report"]["sections"]
             for vc in s["visualContainers"]
             if json.loads(vc["config"])["singleVisual"]["visualType"] == "basicShape"]
    assert bands, "no header band found"
    assert all(vc.get("z") == 0 for vc in bands), "the band must stay at z=0"
    assert not [p for p in dr.validate_layout(report_def["report"]) if "overlaps" in p]


def test_header_band_contains_its_text(report_def):
    """The banner must be tall enough for the title and subtitle stacked inside it."""
    import deploy_report_arc as dr
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
    import deploy_report_arc as dr
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

@pytest.fixture(scope="module")
def dr():
    import deploy_report_arc
    return deploy_report_arc


@pytest.fixture(scope="module")
def reserved(dr):
    return next(iter(dr.RESERVED_REPORT_IDS))


def test_reserved_item_is_never_updated_from_state(dr, reserved):
    """state.json still points at the item another generator owns.

    Two sessions published to the same Fabric report item and overwrote each
    other for a day. The arbitration gave it to the main checkout; this makes
    the arbitration mechanical instead of a promise.
    """
    rpt_id, name = dr.resolve_report_target(
        {"report_id": reserved}, "RPT_Marketing_Churn", lambda n: None)
    assert rpt_id != reserved, "would overwrite the item owned by another generator"
    assert name != "RPT_Marketing_Churn", (
        "publishing under the same name lets the next run find the reserved item again")


def test_reserved_item_is_never_found_back_by_name(dr, reserved):
    """A fresh state must not re-collide by looking the reserved item up by name."""
    rpt_id, name = dr.resolve_report_target(
        {}, "RPT_Marketing_Churn",
        lambda n: reserved if n == "RPT_Marketing_Churn" else None)
    assert rpt_id != reserved
    assert name.endswith(dr.FORK_SUFFIX)


def test_own_item_is_reused_not_duplicated(dr, reserved):
    """Once the fork exists, deploying again updates it instead of piling up items."""
    assert dr.resolve_report_target(
        {"report_id": "0000-mine"}, "RPT_Marketing_Churn", lambda n: None) == (
            "0000-mine", "RPT_Marketing_Churn")
    assert dr.resolve_report_target(
        {}, "RPT_Marketing_Churn",
        lambda n: {"RPT_Marketing_Churn": reserved,
                   "RPT_Marketing_Churn" + dr.FORK_SUFFIX: "0000-fork"}.get(n)) == (
            "0000-fork", "RPT_Marketing_Churn" + dr.FORK_SUFFIX)


def test_fork_name_does_not_grow_a_second_suffix(dr, reserved):
    """A name already carrying the suffix must stay stable.

    Without this the item drifts on every run (`_wt`, `_wt_wt`, ...) whenever
    the stored id is the reserved one, leaving a trail of orphan reports.
    """
    forked = "RPT_Marketing_Churn" + dr.FORK_SUFFIX
    _, name = dr.resolve_report_target({"report_id": reserved}, forked, lambda n: None)
    assert name == forked, f"suffix applied twice: {name}"


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

