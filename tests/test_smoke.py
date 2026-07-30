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

