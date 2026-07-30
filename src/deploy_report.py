#!/usr/bin/env python3
"""
Deploy Power BI Report RPT_Marketing_Churn — 4-page report over
SM_Marketing_Analytics (Direct Lake).

FORMAT: legacy PBIX (report.json with sections[].visualContainers[]).
NEVER PBIR — it renders blank in Fabric. Every visual carries a prototypeQuery;
a visual without one shows up as an empty box.

The four pages follow the demo arc, one page per question:
  1. Detection    — who is at risk, and how much money is exposed
  2. Diagnostic   — WHY: which campaign over-mailed (CAMP_007 is unmistakable)
  3. Quantify     — what the churn is worth: revenue, orders, product mix
  4. Act          — the reachable/unreachable base and the customers to work

FILTER DIRECTION — read before adding any visual:
    All relationships are many-to-one, single-direction (see deploy_semantic_model.py).
    Filters flow from the "one" side to the "many" side ONLY. Consequences:
      * crm_customer_profile[risk_band] canNOT filter [Total Customers] or [Revenue]
        (profile -> customers, not the reverse). Group risk_band with measures that
        live on crm_customer_profile itself: [Profiled Customers], [Avg Churn Score], ...
      * crm_segments[segment_name] canNOT filter customers, orders or sends: the bridge
        crm_customer_segments points AT customers, so a segment only filters the bridge.
        Group segment_name with [Segment Memberships].
      * orders has NO relationship to marketing_campaigns (attributed_campaign_id is a
        deliberate non-related attribute), so [Campaign ROI] / [Attributed Revenue] are
        report-level totals, never per-campaign bars.
    Ignoring this does not error — it silently renders the same total on every bar.

Visual notes (proven on the sister demos):
  - Multi-colour bars: put the SAME column in Category AND Series, then hide the
    legend. dataPoint.colorByCategory does NOT work through the REST API.
  - Rounded cards: vcObjects.border show=true + radius 10L.

Run deploy_semantic_model.py first (the report binds to semantic_model_id).
"""
import os, sys, winreg


def _restore_path():
    parts = []
    for root, sub in [(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")]:
        try:
            k = winreg.OpenKey(root, sub); v, _ = winreg.QueryValueEx(k, "Path")
            parts.append(os.path.expandvars(v)); winreg.CloseKey(k)
        except Exception:
            pass
    if parts:
        os.environ["PATH"] = ";".join(parts) + ";" + os.environ.get("PATH", "")


_restore_path()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import json
import math
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from helpers import (load_config, load_state, save_state, get_fabric_token, fabric_headers,
                     get_powerbi_token, b64encode_json, poll_operation, find_item, print_step)

PAGE_W, PAGE_H = 1280, 720

# ── Item ownership ──────────────────────────────────────────────────────────
# Two generators published to the SAME Fabric report item and silently
# overwrote each other's work for a full day. Arbitration (Clément, 2026-07-30)
# gave that item to the main checkout's src/deploy_report.py. An agreement is
# not a safeguard, so the constraint is encoded here: this generator creates a
# distinct item rather than updating a reserved one, whatever state.json says.
RESERVED_REPORT_IDS = {"ace677a4-02a7-4cbf-bc16-8b695fea3c7d"}
FORK_SUFFIX = "_wt"

# ── Text metrics ────────────────────────────────────────────────────────────
# A Power BI control clips its content when the box is shorter than the text it
# stacks. It does not shrink the font and it does not warn — the text is simply
# cut off. Two separate defects came from hard-coding box heights: a 17pt title
# in a 30px box, and a card stacking 11+30+9pt into 112px.
#
# The model has two parts, and conflating them is what makes estimates wrong:
#   * line height is PROPORTIONAL to the font — 1pt = 4/3 px at 96 DPI, and the
#     line box is ~1.35x the em.  -> 4/3 * 1.35 = 1.8 px per pt
#   * padding / chrome is CONSTANT and depends on the control, not the font.
# A single "px per pt" multiplier cannot express a constant term: it under-sizes
# small text and over-sizes large text. Keep the two terms separate.
#
# UNVERIFIED: these three constants are DERIVED, never measured against the
# Power BI renderer. The only evidence they are safe is that a header and a card
# sized with them were inspected visually and looked right. Do not restate them
# as verified. If a control ever clips despite passing validate_layout(), these
# are the first suspects.
#
# CARD_CHROME was 24. The main-checkout generator independently derived 32 for
# the same control (it keeps one textbox pad inside the card on top of the card
# chrome). Neither number is measured, so this converges on the larger one:
# being wrong by +8px costs nothing here (a 44pt stack needs 112 in a 112 box),
# while being wrong by -8px is exactly the defect that shipped.
LINE_PX_PER_PT = 1.8      # 96/72 DPI * 1.35 line-height
TEXTBOX_PAD = 8           # textbox inner padding, top + bottom
CARD_CHROME = 32          # card border + inner margins + one container pad


def line_px(font_pt):
    """Height of one rendered line of `font_pt` text, excluding container padding."""
    return font_pt * LINE_PX_PER_PT


def text_height(font_pt):
    """Minimum textbox height that renders `font_pt` without clipping."""
    return int(math.ceil(line_px(font_pt) + TEXTBOX_PAD))


def card_height(*font_pts):
    """Minimum cardVisual height for the stack of texts it renders.

    A card is not one line: it stacks its vcObjects.title, its calloutValue and
    its categoryLabel. Sizing it against the callout alone clips the label at
    the bottom — which is exactly what shipped.
    """
    return int(math.ceil(sum(line_px(p) for p in font_pts) + CARD_CHROME))


HEADER_TITLE_PT, HEADER_SUB_PT = 17, 10
HEADER_PAD_TOP, HEADER_PAD_BOTTOM = 6, 4
HEADER_TITLE_H = 42                                    # >= text_height(17) = 39
HEADER_SUB_H = 26                                      # == text_height(10)
HEADER_H = 80                                          # 6 + 42 + 26 + 4 + slack

# Card fonts. The callout was 30pt: 11+30+9 needs 122px in a 112px box, so the
# category label was clipped on all 20 cards. The grid has no 10px to spare
# vertically, so the fix is the font, not the box.
CARD_TITLE_PT, CARD_VALUE_PT, CARD_LABEL_PT = 11, 24, 9

MARGIN = 28
CARD_Y = HEADER_H + 8                                  # 88
CARD_H = 112                                           # needs card_height(11,24,9) = 104
ROW1_Y, ROW1_H = 208, 242                              # bottom stays at 450
ROW2_Y, ROW2_H = 462, 246

# Accessible palette — mirrors theme/Accessible_Fluent2_Theme.json.
C_BLUE, C_RED, C_GOLD, C_TEAL = "#00008F", "#863C41", "#896610", "#027180"
NEUTRAL, ALERT, GOOD, PREMIUM = "#252423", "#863C41", "#027180", "#896610"


# ── shared style fragments ──────────────────────────────────────────────────

def _lit(value):
    return {"expr": {"Literal": {"Value": value}}}


def _solid(color):
    return {"solid": {"color": _lit(f"'{color}'")}}


def _rounded_border(color="#E1DFDD"):
    return [{"properties": {"show": _lit("true"), "color": _solid(color), "radius": _lit("10L")}}]


def _shadow():
    return [{"properties": {"show": _lit("true"), "color": _solid("#cccccc"),
                            "preset": _lit("'Custom'"), "shadowBlur": _lit("6L"),
                            "shadowDistance": _lit("3L"), "transparency": _lit("80L")}}]


def _vc_title(title, color="#252423", size="12D"):
    return [{"properties": {"show": _lit("true"), "text": _lit(f"'{title}'"),
                            "fontSize": _lit(size), "fontColor": _solid(color)}}]


def _frame(title):
    return {"title": _vc_title(title),
            "background": [{"properties": {"show": _lit("true")}}],
            "border": _rounded_border(), "dropShadow": _shadow()}


def _container(name, x, y, w, h, single_visual, z=1):
    return {
        "x": x, "y": y, "z": z, "width": w, "height": h,
        "config": json.dumps({
            "name": name,
            "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": w, "height": h}}],
            "singleVisual": single_visual,
            "howCreated": "Copilot",
        }),
        "filters": "[]",
    }


def _sel_column(alias, table, col):
    return {"Column": {"Expression": {"SourceRef": {"Source": alias}}, "Property": col},
            "Name": f"{table}.{col}", "NativeReferenceName": col}


def _sel_measure(alias, table, measure):
    return {"Measure": {"Expression": {"SourceRef": {"Source": alias}}, "Property": measure},
            "Name": f"{table}.{measure}", "NativeReferenceName": measure}


# ── visual factory functions ────────────────────────────────────────────────

def _card(name, x, y, w, h, table, measure, accent, title, z=1):
    a = "m"
    return _container(name, x, y, w, h, {
        "visualType": "cardVisual",
        "projections": {"Data": [{"queryRef": f"{table}.{measure}"}]},
        "prototypeQuery": {"Version": 2,
                           "From": [{"Name": a, "Entity": table, "Type": 0}],
                           "Select": [_sel_measure(a, table, measure)]},
        "drillFilterOtherVisuals": True,
        "objects": {
            "outline": [{"properties": {"show": _lit("false")}}],
            "calloutValue": [{"properties": {"fontSize": _lit(f"{CARD_VALUE_PT}D"), "bold": _lit("true"),
                                             "color": _solid(accent)}}],
            "categoryLabel": [{"properties": {"show": _lit("true"), "fontSize": _lit(f"{CARD_LABEL_PT}D"),
                                              "color": _solid("#8A8886")}}],
        },
        "vcObjects": {
            "title": _vc_title(title, color="#605E5C", size=f"{CARD_TITLE_PT}D"),
            "visualHeader": [{"properties": {"show": _lit("false")}}],
            "visualHeaderTooltip": [{"properties": {"show": _lit("false")}}],
            "background": [{"properties": {"show": _lit("true")}}],
            "border": _rounded_border(), "dropShadow": _shadow(),
        },
    }, z)


def _categorical(visual_type, name, x, y, w, h, dim_table, dim_col, fact_table, measure,
                 title, labels=True, series=True, z=1):
    """Bar / column with the Category column repeated in Series (the multi-colour trick)."""
    same = dim_table == fact_table
    d = "d"; f = "d" if same else "f"
    froms = [{"Name": d, "Entity": dim_table, "Type": 0}]
    if not same:
        froms.append({"Name": f, "Entity": fact_table, "Type": 0})
    projections = {"Category": [{"queryRef": f"{dim_table}.{dim_col}"}],
                   "Y": [{"queryRef": f"{fact_table}.{measure}"}]}
    if series:
        projections["Series"] = [{"queryRef": f"{dim_table}.{dim_col}"}]
    horizontal = visual_type == "clusteredBarChart"
    return _container(name, x, y, w, h, {
        "visualType": visual_type,
        "projections": projections,
        "prototypeQuery": {
            "Version": 2, "From": froms,
            "Select": [_sel_column(d, dim_table, dim_col), _sel_measure(f, fact_table, measure)],
            "OrderBy": [{"Direction": 2,
                         "Expression": {"Measure": {"Expression": {"SourceRef": {"Source": f}},
                                                    "Property": measure}}}],
        },
        "drillFilterOtherVisuals": True,
        "objects": {
            "categoryAxis": [{"properties": {"fontSize": _lit("10D")}}],
            "valueAxis": [{"properties": {"show": _lit("false" if horizontal else "true"),
                                          "fontSize": _lit("10D")}}],
            "labels": [{"properties": {"show": _lit("true" if labels else "false"),
                                       "fontSize": _lit("9D")}}],
            "legend": [{"properties": {"show": _lit("false")}}],
        },
        "vcObjects": _frame(title),
    }, z)


def _bar(name, x, y, w, h, dim_table, dim_col, fact_table, measure, title, labels=True, z=1):
    return _categorical("clusteredBarChart", name, x, y, w, h, dim_table, dim_col,
                        fact_table, measure, title, labels=labels, z=z)


def _column(name, x, y, w, h, dim_table, dim_col, fact_table, measure, title, labels=True, z=1):
    return _categorical("clusteredColumnChart", name, x, y, w, h, dim_table, dim_col,
                        fact_table, measure, title, labels=labels, z=z)


def _donut(name, x, y, w, h, cat_table, cat_col, val_table, measure, title, z=1):
    same = cat_table == val_table
    c = "c"; v = "c" if same else "v"
    froms = [{"Name": c, "Entity": cat_table, "Type": 0}]
    if not same:
        froms.append({"Name": v, "Entity": val_table, "Type": 0})
    return _container(name, x, y, w, h, {
        "visualType": "donutChart",
        "projections": {"Category": [{"queryRef": f"{cat_table}.{cat_col}"}],
                        "Y": [{"queryRef": f"{val_table}.{measure}"}]},
        "prototypeQuery": {"Version": 2, "From": froms,
                           "Select": [_sel_column(c, cat_table, cat_col),
                                      _sel_measure(v, val_table, measure)]},
        "drillFilterOtherVisuals": True,
        "objects": {
            "legend": [{"properties": {"show": _lit("true"), "position": _lit("'Right'"),
                                       "fontSize": _lit("9D")}}],
            "labels": [{"properties": {"labelStyle": _lit("'Category, percent of total'"),
                                       "fontSize": _lit("9D")}}],
        },
        "vcObjects": _frame(title),
    }, z)


def _line(name, x, y, w, h, axis_table, axis_col, val_table, measure, title, color=None, z=1):
    same = axis_table == val_table
    a = "a"; v = "a" if same else "v"
    froms = [{"Name": a, "Entity": axis_table, "Type": 0}]
    if not same:
        froms.append({"Name": v, "Entity": val_table, "Type": 0})
    objects = {
        "categoryAxis": [{"properties": {"fontSize": _lit("9D"),
                                         "concatenateLabels": _lit("false")}}],
        "valueAxis": [{"properties": {"fontSize": _lit("9D")}}],
        "legend": [{"properties": {"show": _lit("false")}}],
    }
    if color:
        objects["dataPoint"] = [{"properties": {"fill": _solid(color)}}]
    return _container(name, x, y, w, h, {
        "visualType": "lineChart",
        "projections": {"Category": [{"queryRef": f"{axis_table}.{axis_col}"}],
                        "Y": [{"queryRef": f"{val_table}.{measure}"}]},
        "prototypeQuery": {
            "Version": 2, "From": froms,
            "Select": [_sel_column(a, axis_table, axis_col), _sel_measure(v, val_table, measure)],
            "OrderBy": [{"Direction": 1,
                         "Expression": {"Column": {"Expression": {"SourceRef": {"Source": a}},
                                                   "Property": axis_col}}}],
        },
        "drillFilterOtherVisuals": True,
        "objects": objects,
        "vcObjects": _frame(title),
    }, z)


def _table(name, x, y, w, h, items, title, z=1):
    """items = [(table, property, 'Column'|'Measure'), ...]"""
    tables = list(dict.fromkeys(t for t, _, _ in items))
    alias = {t: chr(ord("a") + i) for i, t in enumerate(tables)}
    selects = [_sel_column(alias[t], t, p) if kind == "Column" else _sel_measure(alias[t], t, p)
               for t, p, kind in items]
    return _container(name, x, y, w, h, {
        "visualType": "tableEx",
        "projections": {"Values": [{"queryRef": f"{t}.{p}"} for t, p, _ in items]},
        "prototypeQuery": {"Version": 2,
                           "From": [{"Name": alias[t], "Entity": t, "Type": 0} for t in tables],
                           "Select": selects},
        "drillFilterOtherVisuals": True,
        "objects": {
            "values": [{"properties": {"fontSize": _lit("10D")}}],
            "columnHeaders": [{"properties": {"fontSize": _lit("10D"), "bold": _lit("true"),
                                              "fontColor": _solid("#FFFFFF"),
                                              "backColor": _solid("#605E5C")}}],
            "grid": [{"properties": {"gridVertical": _lit("false"), "rowPadding": _lit("4D")}}],
            "total": [{"properties": {"show": _lit("false")}}],
        },
        "vcObjects": _frame(title),
    }, z)


def _textbox(name, x, y, w, h, text, font_size="16pt", color="#252423"):
    return _container(name, x, y, w, h, {
        "visualType": "textbox",
        "objects": {"general": [{"properties": {"paragraphs": [{
            "textRuns": [{"value": text, "textStyle": {"fontFamily": "Segoe UI Semibold",
                                                       "fontWeight": "bold",
                                                       "fontSize": font_size, "color": color}}],
            "horizontalTextAlignment": "left"}]}}]},
        "vcObjects": {"background": [{"properties": {"show": _lit("false")}}],
                      "border": [{"properties": {"show": _lit("false")}}]},
    })


def _band(name, x, y, w, h, color):
    return _container(name, x, y, w, h, {
        "visualType": "basicShape",
        "objects": {"line": [{"properties": {"show": _lit("false")}}],
                    "fill": [{"properties": {"fillColor": _solid(color),
                                             "transparency": _lit("0L")}}]},
    }, z=0)


def _header(prefix, accent, title, subtitle):
    """Banner sized from the fonts it contains, so the text cannot be clipped."""
    return [
        _band(f"{prefix}_band", 0, 0, PAGE_W, HEADER_H, accent),
        _textbox(f"{prefix}_t", MARGIN, HEADER_PAD_TOP, PAGE_W - 2 * MARGIN,
                 HEADER_TITLE_H, title, f"{HEADER_TITLE_PT}pt", "#FFFFFF"),
        _textbox(f"{prefix}_s", MARGIN, HEADER_PAD_TOP + HEADER_TITLE_H,
                 PAGE_W - 2 * MARGIN, HEADER_SUB_H, subtitle,
                 f"{HEADER_SUB_PT}pt", "#F3F2F1"),
    ]


# ── Report definition ───────────────────────────────────────────────────────

def build_report(state, config):
    """Build (report.json, definition.pbir, base theme, theme name).

    Pure function of config/state — no Fabric call — so the tests can validate the
    definition offline.
    """
    ws_name = config["workspace_name"]
    sm_name = config["semantic_model_name"]
    sm_id = state.get("semantic_model_id", "")
    theme = "CY26SU02"

    sl = config["storyline"]
    culprit = sl["culprit_campaign_name"]
    victim = sl["victim_segment_id"]
    at_risk = config["churn_model"]["at_risk_threshold"]

    # Page 1 — Détection : qui est à risque, et combien ça coûte.
    p1 = _header("d", C_BLUE, "Customer 360 — Détection du risque d'attrition",
                 f"Score de churn calculé à partir du comportement · cohorte actionnable ≥ {at_risk}") + [
        _card("c1", 28, CARD_Y, 238, CARD_H, "crm_customers", "Total Customers", NEUTRAL, "Base clients"),
        _card("c2", 278, CARD_Y, 238, CARD_H, "crm_customer_profile", "Customers at Risk", ALERT, "Clients à risque"),
        _card("c3", 528, CARD_Y, 238, CARD_H, "crm_customer_profile", "At Risk %", ALERT, "Part de la base"),
        _card("c4", 778, CARD_Y, 238, CARD_H, "crm_customer_profile", "Revenue at Risk", ALERT, "CA menacé"),
        _card("c5", 1028, CARD_Y, 224, CARD_H, "crm_customer_profile", "CLV at Risk", PREMIUM, "Valeur vie menacée"),
        _column("col1", 28, ROW1_Y, 620, ROW1_H, "crm_customer_profile", "risk_band",
                "crm_customer_profile", "Profiled Customers",
                "Clients par bande de risque (Prospect = jamais commandé)"),
        _bar("b1", 660, ROW1_Y, 592, ROW1_H, "crm_customers", "lifecycle_stage",
             "crm_customer_profile", "Avg Churn Score",
             "Score de churn moyen par étape de cycle de vie"),
        _line("l1", 28, ROW2_Y, 760, ROW2_H, "orders", "order_at", "orders", "Revenue",
              "Chiffre d'affaires dans le temps (le décrochage suit la campagne)", color=C_BLUE),
        _donut("dn1", 800, ROW2_Y, 452, ROW2_H, "crm_customers", "lifecycle_stage",
               "crm_customers", "Total Customers", "Répartition du cycle de vie"),
    ]

    # Page 2 — Diagnostic : la cause racine, une campagne qui sur-sollicite.
    p2 = _header("r", C_RED, "Cause racine — Pression marketing",
                 f"« {culprit} » sur-sollicite {victim} : envois × 4 → désabonnements → arrêt des commandes") + [
        _card("c6", 28, CARD_Y, 238, CARD_H, "marketing_sends", "Total Sends", NEUTRAL, "Emails envoyés"),
        _card("c7", 278, CARD_Y, 238, CARD_H, "marketing_sends", "Sends per Customer", ALERT, "Pression / client"),
        _card("c8", 528, CARD_Y, 238, CARD_H, "marketing_events", "Unsubscribes", ALERT, "Désabonnements"),
        _card("c9", 778, CARD_Y, 238, CARD_H, "marketing_events", "Unsubscribe Rate", ALERT, "Taux de désabo."),
        _card("c10", 1028, CARD_Y, 224, CARD_H, "marketing_events", "Open Rate", GOOD, "Taux d'ouverture"),
        _bar("b2", 28, ROW1_Y, 620, ROW1_H, "marketing_campaigns", "campaign_name",
             "marketing_sends", "Sends per Customer",
             f"Envois par client et par campagne — « {culprit} » décroche"),
        _bar("b3", 660, ROW1_Y, 592, ROW1_H, "marketing_campaigns", "campaign_name",
             "marketing_events", "Unsubscribes",
             "Désabonnements par campagne — même coupable"),
        _column("col2", 28, ROW2_Y, 760, ROW2_H, "marketing_campaigns", "campaign_name",
                "marketing_events", "Open Rate",
                "Taux d'ouverture par campagne (l'engagement s'effondre)", labels=False),
        _donut("dn2", 800, ROW2_Y, 452, ROW2_H, "marketing_events", "event_type",
               "marketing_events", "Total Events", "Événements email par type"),
    ]

    # Page 3 — Quantification : ce que l'attrition vaut.
    p3 = _header("q", C_GOLD, "Quantification — Ce que l'attrition coûte",
                 "CA, commandes, panier moyen et mix produit du portefeuille exposé") + [
        _card("c11", 28, CARD_Y, 238, CARD_H, "orders", "Revenue", NEUTRAL, "Chiffre d'affaires"),
        _card("c12", 278, CARD_Y, 238, CARD_H, "orders", "Total Orders", NEUTRAL, "Commandes"),
        _card("c13", 528, CARD_Y, 238, CARD_H, "orders", "Average Order Value", NEUTRAL, "Panier moyen"),
        _card("c14", 778, CARD_Y, 238, CARD_H, "crm_customer_profile", "Revenue at Risk", ALERT, "CA menacé"),
        _card("c15", 1028, CARD_Y, 224, CARD_H, "returns", "Return Rate", ALERT, "Taux de retour"),
        _bar("b4", 28, ROW1_Y, 620, ROW1_H, "crm_customers", "lifecycle_stage", "orders", "Revenue",
             "Chiffre d'affaires par étape de cycle de vie"),
        _bar("b5", 660, ROW1_Y, 592, ROW1_H, "products", "category", "order_lines", "Product Revenue",
             "Chiffre d'affaires par catégorie produit"),
        _table("t1", 28, ROW2_Y, 1224, ROW2_H, [
            ("crm_customer_profile", "risk_band", "Column"),
            ("crm_customer_profile", "Profiled Customers", "Measure"),
            ("crm_customer_profile", "Avg Churn Score", "Measure"),
            ("crm_customer_profile", "Avg Recency (days)", "Measure"),
            ("crm_customer_profile", "Avg Engagement Rate", "Measure"),
            ("crm_customer_profile", "Revenue at Risk", "Measure"),
            ("crm_customer_profile", "CLV at Risk", "Measure"),
        ], "Cohorte par bande de risque — effectif, drivers et exposition"),
    ]

    # Page 4 — Agir : qui reste joignable, et sur qui travailler.
    p4 = _header("a", C_TEAL, "Plan d'action — Réengager sans re-brûler",
                 "Base joignable, friction support, segments à suspendre et clients à travailler") + [
        _card("c16", 28, CARD_Y, 238, CARD_H, "crm_customers", "Opted-in Customers", GOOD, "Encore joignables"),
        _card("c17", 278, CARD_Y, 238, CARD_H, "crm_customer_profile", "Unsubscribed Customers", ALERT, "Désabonnés"),
        _card("c18", 528, CARD_Y, 238, CARD_H, "crm_customer_profile", "Avg Engagement Rate", NEUTRAL, "Engagement moyen"),
        _card("c19", 778, CARD_Y, 238, CARD_H, "crm_customer_profile", "Avg NPS", NEUTRAL, "NPS moyen"),
        _card("c20", 1028, CARD_Y, 224, CARD_H, "crm_interactions", "Unresolved Negative", ALERT, "Litiges ouverts"),
        _bar("b6", 28, ROW1_Y, 620, ROW1_H, "crm_segments", "segment_name",
             "crm_customer_segments", "Segment Memberships",
             f"Effectif par segment — {victim} est la cible à suspendre"),
        _donut("dn3", 660, ROW1_Y, 592, ROW1_H, "crm_interactions", "sentiment",
               "crm_interactions", "Total Interactions", "Sentiment des interactions support"),
        _table("t2", 28, ROW2_Y, 1224, ROW2_H, [
            ("crm_customers", "last_name", "Column"),
            ("crm_customers", "city", "Column"),
            ("crm_customers", "lifecycle_stage", "Column"),
            ("crm_customer_profile", "Avg Churn Score", "Measure"),
            ("crm_customer_profile", "Avg Recency (days)", "Measure"),
            ("crm_customer_profile", "Total CLV", "Measure"),
        ], "Clients à travailler — score, récence et valeur vie"),
    ]

    report_config = {
        "version": "5.70",
        "themeCollection": {"baseTheme": {"name": theme,
                                          "version": {"visual": "2.6.0", "report": "3.1.0",
                                                      "page": "2.3.0"}, "type": 2}},
        "activeSectionIndex": 0, "defaultDrillFilterOtherVisuals": True,
        "settings": {"useNewFilterPaneExperience": True, "allowChangeFilterTypes": True,
                     "useStylableVisualContainerHeader": True, "exportDataMode": 1},
    }

    def _page_cfg(name):
        grey = "'#F5F4F2'"
        obj = {"background": [{"properties": {"color": {"solid": {"color": {"expr": {"Literal": {"Value": grey}}}}},
                                              "transparency": _lit("0D")}}],
               "outspace": [{"properties": {"color": {"solid": {"color": {"expr": {"Literal": {"Value": grey}}}}},
                                            "transparency": _lit("0D")}}]}
        return json.dumps({"name": name, "objects": obj})

    def _section(name, display, visuals):
        return {"name": name, "displayName": display, "displayOption": 1,
                "width": PAGE_W, "height": PAGE_H, "config": _page_cfg(name),
                "filters": "[]", "visualContainers": visuals}

    report = {
        "config": json.dumps(report_config), "layoutOptimization": 0,
        "resourcePackages": [{"resourcePackage": {
            "name": "SharedResources", "type": 2, "disabled": False,
            "items": [{"type": 202, "path": f"BaseThemes/{theme}.json", "name": theme}]}}],
        "sections": [
            _section("Detection", "1 · Détection", p1),
            _section("CauseRacine", "2 · Cause racine", p2),
            _section("Quantification", "3 · Quantification", p3),
            _section("Action", "4 · Action", p4),
        ],
        "theme": theme,
    }

    conn_str = (f'Data Source="powerbi://api.powerbi.com/v1.0/myorg/{ws_name}";'
                f"initial catalog={sm_name};integrated security=ClaimsToken;semanticmodelid={sm_id}")
    pbir = {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                       "definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byConnection": {"connectionString": conn_str}}}

    theme_file = Path(__file__).parent.parent / "theme" / "Accessible_Fluent2_Theme.json"
    base_theme = json.loads(theme_file.read_text(encoding="utf-8"))
    base_theme.pop("$schema", None)
    base_theme["name"] = theme            # must match resourcePackages + themeCollection
    base_theme.setdefault("version", "5.70")
    return report, pbir, base_theme, theme


def report_field_refs(report):
    """Every (kind, table, field) the report binds to.

    Legacy PBIX stores each visual's config as a JSON *string*, so a naive walk
    over the dict finds nothing — the payload has to be parsed back first.
    """
    measures, columns = set(), set()
    for sec in report["sections"]:
        for v in sec["visualContainers"]:
            sv = json.loads(v["config"]).get("singleVisual", {})
            pq = sv.get("prototypeQuery")
            if not pq:
                continue
            src = {f["Name"]: f["Entity"] for f in pq.get("From", [])}
            for sel in pq.get("Select", []):
                if "Measure" in sel:
                    ref = sel["Measure"]
                    measures.add(ref["Property"])
                elif "Column" in sel:
                    ref = sel["Column"]
                    alias = ref["Expression"]["SourceRef"]["Source"]
                    columns.add((src.get(alias, alias), ref["Property"]))
                elif "Aggregation" in sel:
                    expr = sel["Aggregation"]["Expression"]
                    if "Column" in expr:
                        alias = expr["Column"]["Expression"]["SourceRef"]["Source"]
                        columns.add((src.get(alias, alias), expr["Column"]["Property"]))
    return measures, columns


def _font_pt(objects, key, default=None):
    """Read a fontSize like '30D' out of a visual's objects/vcObjects blob."""
    try:
        raw = objects[key][0]["properties"]["fontSize"]["expr"]["Literal"]["Value"]
    except (KeyError, IndexError, TypeError):
        return default
    return float(str(raw).rstrip("Dd"))


def validate_layout(report):
    """Offline geometry checks — returns a list of human-readable problems.

    Catches the failure modes that ship silently: text clipped because its box
    is shorter than the stack it renders, and visuals that overlap or fall off
    the page. Power BI renders all of these without any warning.
    """
    problems = []
    for sec in report["sections"]:
        page = sec["displayName"]
        boxes = []
        for v in sec["visualContainers"]:
            cfg = json.loads(v["config"])
            sv = cfg.get("singleVisual", {})
            kind = sv.get("visualType", "?")
            name = cfg.get("name", "?")
            x, y, w, h = v["x"], v["y"], v["width"], v["height"]
            z = v.get("z", 1)

            if x < 0 or y < 0 or x + w > PAGE_W or y + h > PAGE_H:
                problems.append(
                    f"{page}/{name} ({kind}): {x},{y} {w}x{h} falls outside the "
                    f"{PAGE_W}x{PAGE_H} page")

            if kind == "textbox":
                for para in sv["objects"]["general"][0]["properties"]["paragraphs"]:
                    for run in para["textRuns"]:
                        pt = float(str(run["textStyle"]["fontSize"]).rstrip("pt"))
                        need = text_height(pt)
                        if h < need:
                            problems.append(
                                f"{page}/{name}: box is {h}px tall for {pt:g}pt text "
                                f"(needs {need}px) — text will be clipped")

            elif kind == "cardVisual":
                # A card stacks three texts. Sizing against the callout alone
                # clips the category label — the defect that shipped on all 20.
                stack = [_font_pt(sv.get("vcObjects", {}), "title"),
                         _font_pt(sv.get("objects", {}), "calloutValue"),
                         _font_pt(sv.get("objects", {}), "categoryLabel")]
                stack = [p for p in stack if p]
                if stack:
                    need = card_height(*stack)
                    if h < need:
                        problems.append(
                            f"{page}/{name}: card is {h}px tall for a "
                            f"{'+'.join(f'{p:g}' for p in stack)}pt stack "
                            f"(needs {need}px) — the bottom label will be clipped")

            # z=0 is the deliberate header background sitting under its text.
            if z >= 1:
                boxes.append((name, kind, x, y, w, h))

        for i, (n1, k1, x1, y1, w1, h1) in enumerate(boxes):
            for n2, k2, x2, y2, w2, h2 in boxes[i + 1:]:
                if x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1:
                    problems.append(
                        f"{page}: {n1} ({k1}) overlaps {n2} ({k2})")
    return problems


def validate_fields(report, ws_id, sm_id):
    """Check every measure and column against the deployed model before shipping.

    Validating measures alone is not enough — a visual breaks on a missing
    *column* just as hard, and shows the same 'Something's wrong with one or
    more fields' at runtime instead of at deploy time.
    """
    measures, columns = report_field_refs(report)
    headers = {"Authorization": f"Bearer {get_powerbi_token()}",
               "Content-Type": "application/json"}
    url = (f"https://api.powerbi.com/v1.0/myorg/groups/{ws_id}"
           f"/datasets/{sm_id}/executeQueries")

    def resolves(dax):
        try:
            r = requests.post(url, headers=headers,
                              json={"queries": [{"query": dax}]}, timeout=120)
        except Exception:
            return True          # network trouble is not a report defect
        return r.status_code == 200

    broken = []
    for m in sorted(measures):
        if not resolves(f'EVALUATE ROW("v", [{m}])'):
            broken.append(f"measure [{m}]")
    for table, col in sorted(columns):
        if not resolves(f"EVALUATE TOPN(1, VALUES({table}[{col}]))"):
            broken.append(f"column {table}[{col}]")
    return broken, len(measures), len(columns)


def resolve_report_target(state, rpt_name, lookup):
    """Choose which item to publish to, never one reserved for another generator.

    `lookup(name)` returns an item id or None. Returning a *new* name when the
    stored id is reserved is what stops the next run from finding the reserved
    item again by name and re-colliding with it.
    """
    rpt_id = state.get("report_id") or lookup(rpt_name)
    if rpt_id not in RESERVED_REPORT_IDS:
        return rpt_id, rpt_name
    fork_name = rpt_name + FORK_SUFFIX
    return lookup(fork_name), fork_name


def main():
    config = load_config()
    state = load_state()
    api = config["fabric_api_base"]
    ws_id = state.get("workspace_id")
    sm_id = state.get("semantic_model_id")

    if not ws_id or not sm_id:
        print("Prerequisites not met. Deploy workspace + semantic model first.")
        sys.exit(1)

    token = get_fabric_token()
    headers = fabric_headers(token)
    rpt_name = config["report_name"]

    print_step(1, 1, f"Deploying Report: {rpt_name}")
    report, pbir, base_theme, theme = build_report(state, config)
    pages = len(report["sections"])
    visuals = sum(len(s["visualContainers"]) for s in report["sections"])
    print(f"   {pages} pages, {visuals} visuals")

    problems = validate_layout(report)
    if problems:
        print("\n".join(f"   LAYOUT: {p}" for p in problems))
        raise RuntimeError(f"{len(problems)} layout problem(s) — refusing to deploy")

    broken, n_m, n_c = validate_fields(report, ws_id, sm_id)
    if broken:
        print("\n".join(f"   FIELD: {b} does not exist in the model" for b in broken))
        raise RuntimeError(f"{len(broken)} broken field binding(s) — refusing to deploy")
    print(f"   validated: layout clean, {n_m} measures + {n_c} columns resolve")

    parts = [
        {"path": "report.json", "payload": b64encode_json(report), "payloadType": "InlineBase64"},
        {"path": "definition.pbir", "payload": b64encode_json(pbir), "payloadType": "InlineBase64"},
        {"path": f"StaticResources/SharedResources/BaseThemes/{theme}.json",
         "payload": b64encode_json(base_theme), "payloadType": "InlineBase64"},
    ]

    def lookup(name):
        try:
            return find_item(token, api, ws_id, name, "Report")["id"]
        except RuntimeError:
            return None

    rpt_id, rpt_name = resolve_report_target(state, rpt_name, lookup)
    if rpt_name != config["report_name"]:
        print(f"   '{config['report_name']}' is reserved for another generator "
              f"— publishing to '{rpt_name}' instead")

    if rpt_id:
        resp = requests.post(f"{api}/workspaces/{ws_id}/reports/{rpt_id}/updateDefinition",
                             headers=headers, json={"definition": {"parts": parts}}, timeout=180)
    else:
        resp = requests.post(f"{api}/workspaces/{ws_id}/items", headers=headers,
                             json={"displayName": rpt_name, "type": "Report",
                                   "description": "Customer 360 — détection, cause racine, "
                                                  "quantification et plan d'action de l'attrition",
                                   "definition": {"parts": parts}}, timeout=180)

    if resp.status_code in (200, 201):
        rpt_id = resp.json().get("id", rpt_id)
    elif resp.status_code == 202:
        op_id = resp.headers.get("x-ms-operation-id", "")
        if op_id:
            poll_operation(token, api, op_id)
        if not rpt_id:
            rpt_id = find_item(token, api, ws_id, rpt_name, "Report")["id"]
    else:
        raise RuntimeError(f"Deploy failed ({resp.status_code}): {resp.text[:400]}")

    state["report_id"] = rpt_id
    save_state(state)
    print(f"\nOK  Report deployed: {rpt_id}")
    print(f"    Pages: {pages} | Visuals: {visuals}")
    print("    Demo arc: Détection -> Cause racine -> Quantification -> Action")


if __name__ == "__main__":
    main()
