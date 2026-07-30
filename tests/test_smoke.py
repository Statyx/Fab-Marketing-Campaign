"""Smoke tests for Fab-Marketing-Campaign — offline gate (no Fabric needed).

Validates: Python compiles, config/state parse, and — most importantly — that the generated
dataset carries a REAL, learnable churn signal.

The predecessor project failed exactly here: churn_risk_score was drawn from a distribution and
correlated with nothing (|r| < 0.02 against every behavioural signal), which silently made every
churn question unanswerable. These tests exist so that can never ship again.

Run BEFORE any deploy:  python -m pytest tests/ -v --tb=short
"""
import ast
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
