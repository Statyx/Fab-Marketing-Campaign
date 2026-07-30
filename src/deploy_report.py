#!/usr/bin/env python3
"""
Deploy Power BI Report RPT_Marketing_Churn — 4-page persona report over
SM_Marketing_Analytics (Direct Lake). Legacy PBIX format (report.json with
sections[].visualContainers[]) — never PBIR (renders blank in Fabric).

Pages follow the demo arc — detect -> diagnose -> quantify -> act:
  1. Direction  — pilotage global (valeur du portefeuille, exposition au churn)
  2. Retention  — DETECT : la cohorte a risque, ses signaux comportementaux
  3. Marketing  — DIAGNOSE : Sends per Customer par campagne = la cause racine
  4. Commerce   — QUANTIFY : chiffre d'affaires, panier, attribution, retours

Design notes (legacy visual catalog):
  - Multi-color bars: put the SAME category column in both Category AND Series,
    then hide the legend. Without Series every bar is the same Fluent-2 blue.
  - dataPoint.colorByCategory does NOT work via the REST API — use the Series trick.
  - Rounded cards: vcObjects.border show=true + radius.

MODEL CONSTRAINT — relationships are many->one, crossFilteringBehavior=oneDirection.
A filter only travels from the "one" side to the "many" side, so:
  - crm_customers      -> filters crm_customer_profile / orders / marketing_sends /
                          crm_interactions / crm_customer_segments      OK
  - crm_customer_profile -> does NOT filter orders  => risk slicing must use the
                          profile's own measures ([Revenue at Risk], [CLV at Risk])
  - crm_segments       -> only filters crm_customer_segments
  - products           -> filters order_lines but NOT orders  => use [Line Revenue]
  - marketing_campaigns -> filters marketing_sends and (via sends) marketing_events
Every visual below respects that graph; breaking it yields blank visuals.
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
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from helpers import (load_config, load_state, save_state,
                     get_fabric_token, fabric_headers,
                     b64encode_json, poll_operation, find_item, print_step)

CANVAS_W, CANVAS_H = 1280, 720


# ── shared style fragments ──────────────────────────────────────────────────

def _lit(value):
    return {"expr": {"Literal": {"Value": value}}}


def _solid(color):
    return {"solid": {"color": _lit(f"'{color}'")}}


def _rounded_border(color="#E1DFDD"):
    return [{"properties": {"show": _lit("true"), "color": _solid(color),
                            "radius": _lit("10L")}}]


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
            "border": _rounded_border(),
            "dropShadow": _shadow()}


def _container(name, x, y, w, h, z, single_visual):
    return {"x": x, "y": y, "z": z, "width": w, "height": h,
            "config": json.dumps({
                "name": name,
                "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": w, "height": h}}],
                "singleVisual": single_visual,
                "howCreated": "Copilot",
            }), "filters": "[]"}


def _sel_column(alias, table, col):
    return {"Column": {"Expression": {"SourceRef": {"Source": alias}}, "Property": col},
            "Name": f"{table}.{col}", "NativeReferenceName": col}


def _sel_measure(alias, table, measure):
    return {"Measure": {"Expression": {"SourceRef": {"Source": alias}}, "Property": measure},
            "Name": f"{table}.{measure}", "NativeReferenceName": measure}


# ── visual factory functions ────────────────────────────────────────────────

def _card(name, x, y, w, h, table, measure, accent, title, z=1):
    alias = "m"
    return _container(name, x, y, w, h, z, {
        "visualType": "cardVisual",
        "projections": {"Data": [{"queryRef": f"{table}.{measure}"}]},
        "prototypeQuery": {
            "Version": 2,
            "From": [{"Name": alias, "Entity": table, "Type": 0}],
            "Select": [_sel_measure(alias, table, measure)],
        },
        "drillFilterOtherVisuals": True,
        "objects": {
            "outline": [{"properties": {"show": _lit("false")}}],
            "calloutValue": [{"properties": {"fontSize": _lit("30D"), "bold": _lit("true"),
                                             "color": _solid(accent)}}],
            "categoryLabel": [{"properties": {"show": _lit("true"), "fontSize": _lit("9D"),
                                              "color": _solid("#8A8886")}}],
        },
        "vcObjects": {
            "title": _vc_title(title, color="#605E5C", size="11D"),
            "visualHeader": [{"properties": {"show": _lit("false")}}],
            "visualHeaderTooltip": [{"properties": {"show": _lit("false")}}],
            "background": [{"properties": {"show": _lit("true")}}],
            "border": _rounded_border(),
            "dropShadow": _shadow(),
        },
    })


def _categorical(visual_type, name, x, y, w, h, dim_table, dim_col,
                 fact_table, measure, title, labels=True, z=1):
    """Multi-colored bar / column: the same column drives Category AND Series."""
    same = dim_table == fact_table
    d = "d"
    f = "d" if same else "f"
    froms = [{"Name": d, "Entity": dim_table, "Type": 0}]
    if not same:
        froms.append({"Name": f, "Entity": fact_table, "Type": 0})

    proto = {
        "Version": 2, "From": froms,
        "Select": [_sel_column(d, dim_table, dim_col), _sel_measure(f, fact_table, measure)],
        "OrderBy": [{"Direction": 2,
                     "Expression": {"Measure": {"Expression": {"SourceRef": {"Source": f}},
                                                "Property": measure}}}],
    }

    horizontal = visual_type == "clusteredBarChart"
    return _container(name, x, y, w, h, z, {
        "visualType": visual_type,
        "projections": {"Category": [{"queryRef": f"{dim_table}.{dim_col}"}],
                        "Y": [{"queryRef": f"{fact_table}.{measure}"}],
                        "Series": [{"queryRef": f"{dim_table}.{dim_col}"}]},
        "prototypeQuery": proto,
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
    })


def _bar(name, x, y, w, h, dim_table, dim_col, fact_table, measure, title,
         labels=True, z=1):
    return _categorical("clusteredBarChart", name, x, y, w, h, dim_table, dim_col,
                        fact_table, measure, title, labels, z)


def _column(name, x, y, w, h, dim_table, dim_col, fact_table, measure, title,
            labels=True, z=1):
    return _categorical("clusteredColumnChart", name, x, y, w, h, dim_table, dim_col,
                        fact_table, measure, title, labels, z)


def _donut(name, x, y, w, h, cat_table, cat_col, val_table, measure, title, z=1):
    same = cat_table == val_table
    ca = "c"
    va = "c" if same else "v"
    froms = [{"Name": ca, "Entity": cat_table, "Type": 0}]
    if not same:
        froms.append({"Name": va, "Entity": val_table, "Type": 0})
    return _container(name, x, y, w, h, z, {
        "visualType": "donutChart",
        "projections": {"Category": [{"queryRef": f"{cat_table}.{cat_col}"}],
                        "Y": [{"queryRef": f"{val_table}.{measure}"}]},
        "prototypeQuery": {"Version": 2, "From": froms,
                           "Select": [_sel_column(ca, cat_table, cat_col),
                                      _sel_measure(va, val_table, measure)]},
        "drillFilterOtherVisuals": True,
        "objects": {
            "legend": [{"properties": {"show": _lit("true"), "position": _lit("'Right'"),
                                       "fontSize": _lit("9D")}}],
            "labels": [{"properties": {"labelStyle": _lit("'Category, percent of total'"),
                                       "fontSize": _lit("9D")}}],
        },
        "vcObjects": _frame(title),
    })


def _line(name, x, y, w, h, axis_table, axis_col, val_table, measure, title, color=None, z=1):
    same = axis_table == val_table
    aa = "a"
    va = "a" if same else "v"
    froms = [{"Name": aa, "Entity": axis_table, "Type": 0}]
    if not same:
        froms.append({"Name": va, "Entity": val_table, "Type": 0})
    obj = {
        "categoryAxis": [{"properties": {"fontSize": _lit("9D"),
                                         "concatenateLabels": _lit("false")}}],
        "valueAxis": [{"properties": {"fontSize": _lit("9D")}}],
        "legend": [{"properties": {"show": _lit("false")}}],
    }
    if color:
        obj["dataPoint"] = [{"properties": {"fill": _solid(color)}}]
    return _container(name, x, y, w, h, z, {
        "visualType": "lineChart",
        "projections": {"Category": [{"queryRef": f"{axis_table}.{axis_col}"}],
                        "Y": [{"queryRef": f"{val_table}.{measure}"}]},
        "prototypeQuery": {
            "Version": 2, "From": froms,
            "Select": [_sel_column(aa, axis_table, axis_col), _sel_measure(va, val_table, measure)],
            "OrderBy": [{"Direction": 1,
                         "Expression": {"Column": {"Expression": {"SourceRef": {"Source": aa}},
                                                   "Property": axis_col}}}],
        },
        "drillFilterOtherVisuals": True,
        "objects": obj,
        "vcObjects": _frame(title),
    })


def _table(name, x, y, w, h, items, title, order_by=None, z=1):
    """Table visual. items = [(table, prop, 'Column'|'Measure'), ...]
    order_by = (table, prop, 'Column'|'Measure', descending: bool)"""
    tables = list(dict.fromkeys(t for t, _, _ in items))
    aliases = {t: chr(ord("a") + i) for i, t in enumerate(tables)}
    froms = [{"Name": aliases[t], "Entity": t, "Type": 0} for t in tables]
    selects = [_sel_column(aliases[t], t, p) if tp == "Column" else _sel_measure(aliases[t], t, p)
               for t, p, tp in items]
    proto = {"Version": 2, "From": froms, "Select": selects}
    if order_by:
        ot, op, otp, desc = order_by
        expr_key = "Column" if otp == "Column" else "Measure"
        proto["OrderBy"] = [{"Direction": 2 if desc else 1,
                             "Expression": {expr_key: {"Expression": {"SourceRef": {"Source": aliases[ot]}},
                                                       "Property": op}}}]

    return _container(name, x, y, w, h, z, {
        "visualType": "tableEx",
        "projections": {"Values": [{"queryRef": f"{t}.{p}"} for t, p, _ in items]},
        "prototypeQuery": proto,
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
    })


def _textbox(name, x, y, w, h, text, font_size="16pt", color="#252423"):
    return {"x": x, "y": y, "z": 1, "width": w, "height": h,
            "config": json.dumps({
                "name": name,
                "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 1, "width": w, "height": h}}],
                "singleVisual": {
                    "visualType": "textbox",
                    "objects": {"general": [{"properties": {"paragraphs": [{
                        "textRuns": [{"value": text, "textStyle": {
                            "fontFamily": "Segoe UI Semibold", "fontWeight": "bold",
                            "fontSize": font_size, "color": color}}],
                        "horizontalTextAlignment": "left"}]}}]},
                    "vcObjects": {
                        "background": [{"properties": {"show": _lit("false")}}],
                        "border": [{"properties": {"show": _lit("false")}}],
                    },
                },
            }), "filters": "[]"}


def _band(name, x, y, w, h, color):
    """Solid colored header band (page identity strip)."""
    return {"x": x, "y": y, "z": 0, "width": w, "height": h,
            "config": json.dumps({
                "name": name,
                "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}],
                "singleVisual": {"visualType": "basicShape", "objects": {
                    "line": [{"properties": {"show": _lit("false")}}],
                    "fill": [{"properties": {"fillColor": _solid(color),
                                             "transparency": _lit("0L")}}],
                }},
            }), "filters": "[]"}


# ── Report definition ───────────────────────────────────────────────────────

def build_report(state, config):
    ws_name = config["workspace_name"]
    sm_name = config["semantic_model_name"]
    sm_id = state["semantic_model_id"]
    theme = "CY26SU02"

    story = config.get("storyline", {})
    culprit = story.get("culprit_campaign_name", "Black Friday Blast")
    victim = story.get("victim_segment_id", "SEG_HIGH_VALUE")
    at_risk = config.get("churn_model", {}).get("at_risk_threshold", 65)

    # Accessible custom-theme palette: 4 dark brand colors with AA/AAA white-text
    # contrast -> one per persona header band.
    C_BLUE, C_TEAL, C_GOLD, C_RED = "#00008F", "#027180", "#896610", "#863C41"
    NEUTRAL, ALERT, GOOD, PREMIUM = "#252423", "#863C41", "#027180", "#896610"

    def header(prefix, accent, title, subtitle):
        return [
            _band(f"{prefix}_band", 0, 0, CANVAS_W, 64, accent),
            _textbox(f"{prefix}_t", 28, 12, 1100, 30, title, "17pt", "#FFFFFF"),
            _textbox(f"{prefix}_s", 28, 40, 1100, 20, subtitle, "10pt", "#F3F2F1"),
        ]

    # ── Page 1 — Direction : la valeur du portefeuille et son exposition ──
    p1 = header("d", C_BLUE, "Customer 360 — Pilotage Direction",
                "Valeur du portefeuille, exposition a l'attrition et sante de la relation client") + [
        _card("c1", 28, 78, 238, 112, "orders", "Revenue", NEUTRAL, "Chiffre d'Affaires"),
        _card("c2", 278, 78, 238, 112, "crm_customers", "Total Customers", NEUTRAL, "Clients"),
        _card("c3", 528, 78, 238, 112, "crm_customer_profile", "At Risk %", ALERT, "Part a Risque"),
        _card("c4", 778, 78, 238, 112, "crm_customer_profile", "CLV at Risk", ALERT, "Valeur Vie Exposee"),
        _card("c5", 1028, 78, 224, 112, "crm_customer_profile", "Avg NPS", GOOD, "NPS Moyen"),
        _line("l1", 28, 202, 760, 248, "orders", "order_at", "orders", "Revenue",
              "Chiffre d'Affaires dans le Temps (le decrochage post-campagne)", color=C_BLUE),
        _donut("dn1", 800, 202, 452, 248, "crm_customer_profile", "risk_band",
               "crm_customer_profile", "Profiled Customers",
               "Repartition du Portefeuille par Bande de Risque"),
        _bar("b1", 28, 462, 1224, 246, "crm_customers", "lifecycle_stage",
             "crm_customer_profile", "Avg Churn Score",
             "Score d'Attrition Moyen par Etape du Cycle de Vie"),
    ]

    # ── Page 2 — Retention : DETECT ──
    p2 = header("r", C_TEAL, "Retention — Detection de la Cohorte a Risque",
                f"Clients acheteurs scorant >= {at_risk}/100 : recence, engagement, desabonnement, friction") + [
        _card("c6", 28, 78, 238, 112, "crm_customer_profile", "Customers at Risk", ALERT, "Clients a Risque"),
        _card("c7", 278, 78, 238, 112, "crm_customer_profile", "Avg Churn Score", ALERT, "Score Moyen"),
        _card("c8", 528, 78, 238, 112, "crm_customer_profile", "Avg Recency (days)", NEUTRAL, "Recence Moyenne (j)"),
        _card("c9", 778, 78, 238, 112, "crm_customer_profile", "Unsubscribed Customers", ALERT, "Desabonnes"),
        _card("c10", 1028, 78, 224, 112, "crm_customers", "Churned Customers", ALERT, "Clients Perdus"),
        _bar("b2", 28, 202, 760, 248, "crm_customer_profile", "risk_band",
             "crm_customer_profile", "Revenue at Risk",
             "Chiffre d'Affaires Historique Expose par Bande de Risque"),
        _donut("dn2", 800, 202, 452, 248, "crm_interactions", "sentiment",
               "crm_interactions", "Total Interactions",
               "Interactions Support par Sentiment"),
        _table("tbl1", 28, 462, 1224, 246, [
            ("crm_customers", "last_name", "Column"),
            ("crm_customers", "city", "Column"),
            ("crm_customers", "lifecycle_stage", "Column"),
            ("crm_customer_profile", "Avg Churn Score", "Measure"),
            ("crm_customer_profile", "Avg Recency (days)", "Measure"),
            ("crm_customer_profile", "CLV at Risk", "Measure"),
        ], "Clients a Rappeler en Priorite — Score, Recence et Valeur Vie",
            order_by=("crm_customer_profile", "Avg Churn Score", "Measure", True)),
    ]

    # ── Page 3 — Marketing : DIAGNOSE (la cause racine) ──
    p3 = header("m", C_GOLD, "Marketing — Diagnostic de la Pression Commerciale",
                f"Pression email par campagne : « {culprit} » sur-sollicite le segment {victim}") + [
        _card("c11", 28, 78, 238, 112, "marketing_sends", "Sends per Customer", ALERT, "Emails / Client"),
        _card("c12", 278, 78, 238, 112, "marketing_events", "Unsubscribe Rate", ALERT, "Taux de Desabo."),
        _card("c13", 528, 78, 238, 112, "marketing_events", "Open Rate", GOOD, "Taux d'Ouverture"),
        _card("c14", 778, 78, 238, 112, "marketing_events", "Click Through Rate", GOOD, "Taux de Clic"),
        _card("c15", 1028, 78, 224, 112, "marketing_sends", "Total Sends", NEUTRAL, "Emails Envoyes"),
        _bar("b3", 28, 202, 760, 248, "marketing_campaigns", "campaign_name",
             "marketing_sends", "Sends per Customer",
             f"Emails par Client et par Campagne — « {culprit} » decroche"),
        _donut("dn3", 800, 202, 452, 248, "marketing_campaigns", "objective",
               "marketing_campaigns", "Total Budget",
               "Budget par Objectif de Campagne"),
        _column("col1", 28, 462, 605, 246, "marketing_campaigns", "campaign_name",
                "marketing_events", "Unsubscribes",
                "Desabonnements par Campagne (la consequence)", labels=False),
        _column("col2", 647, 462, 605, 246, "marketing_campaigns", "campaign_name",
                "marketing_events", "Open Rate",
                "Taux d'Ouverture par Campagne (l'engagement qui s'effondre)", labels=False),
    ]

    # ── Page 4 — Commerce : QUANTIFY ──
    p4 = header("k", C_RED, "Commerce — Impact Business & Attribution",
                "Chiffre d'affaires, panier moyen, contribution des campagnes et retours produits") + [
        _card("c16", 28, 78, 238, 112, "orders", "Revenue", NEUTRAL, "Chiffre d'Affaires"),
        _card("c17", 278, 78, 238, 112, "orders", "Total Orders", NEUTRAL, "Commandes"),
        _card("c18", 528, 78, 238, 112, "orders", "Average Order Value", GOOD, "Panier Moyen"),
        _card("c19", 778, 78, 238, 112, "orders", "Attributed Revenue", PREMIUM, "CA Attribue"),
        _card("c20", 1028, 78, 224, 112, "returns", "Return Rate", ALERT, "Taux de Retour"),
        _bar("b4", 28, 202, 760, 248, "products", "category", "order_lines", "Line Revenue",
             "Chiffre d'Affaires par Categorie de Produit"),
        _donut("dn4", 800, 202, 452, 248, "orders", "channel", "orders", "Revenue",
               "Repartition du CA par Canal de Vente"),
        _bar("b5", 28, 462, 605, 246, "returns", "reason", "returns", "Total Returns",
             "Retours par Motif"),
        _bar("b6", 647, 462, 605, 246, "crm_customers", "customer_type", "orders", "Revenue",
             "Chiffre d'Affaires par Type de Client"),
    ]

    report_config = {
        "version": "5.70",
        "themeCollection": {"baseTheme": {"name": theme,
                                          "version": {"visual": "2.6.0", "report": "3.1.0", "page": "2.3.0"},
                                          "type": 2}},
        "activeSectionIndex": 0, "defaultDrillFilterOtherVisuals": True,
        "settings": {"useNewFilterPaneExperience": True, "allowChangeFilterTypes": True,
                     "useStylableVisualContainerHeader": True, "exportDataMode": 1},
    }

    def _page_cfg(name):
        # Light-gray canvas + outspace so white visuals "pop" (Fluent-2 look).
        grey = "'#F5F4F2'"
        obj = {"background": [{"properties": {"color": {"solid": {"color": _lit(grey)}},
                                              "transparency": _lit("0D")}}],
               "outspace": [{"properties": {"color": {"solid": {"color": _lit(grey)}},
                                            "transparency": _lit("0D")}}]}
        return json.dumps({"name": name, "objects": obj})

    def _section(name, display, visuals):
        return {"name": name, "displayName": display, "displayOption": 1,
                "width": CANVAS_W, "height": CANVAS_H,
                "config": _page_cfg(name), "filters": "[]", "visualContainers": visuals}

    report = {
        "config": json.dumps(report_config), "layoutOptimization": 0,
        "resourcePackages": [{"resourcePackage": {
            "name": "SharedResources", "type": 2,
            "items": [{"type": 202, "path": f"BaseThemes/{theme}.json", "name": theme}],
            "disabled": False}}],
        "sections": [
            _section("Direction", "Direction", p1),
            _section("Retention", "Retention", p2),
            _section("Marketing", "Marketing", p3),
            _section("Commerce", "Commerce", p4),
        ],
        "theme": theme,
    }

    conn_str = (f'Data Source="powerbi://api.powerbi.com/v1.0/myorg/{ws_name}";'
                f"initial catalog={sm_name};integrated security=ClaimsToken;semanticmodelid={sm_id}")
    pbir = {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                       "definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byConnection": {"connectionString": conn_str}}}

    # Reusable accessible theme — single source of truth is theme/Accessible_Fluent2_Theme.json
    # (WCAG-checked, colorblind-distinguishable palette). Inline fallback if absent.
    theme_file = Path(__file__).parent.parent / "theme" / "Accessible_Fluent2_Theme.json"
    if theme_file.exists():
        base_theme = json.loads(theme_file.read_text(encoding="utf-8"))
    else:
        base_theme = {
            "dataColors": ["#863C41", "#F6B6C8", "#896610", "#E6E689",
                           "#027180", "#86E2EE", "#00008F", "#B2CEEC"],
            "foreground": "#252423", "background": "#FFFFFF", "tableAccent": "#027180",
            "good": "#58C645", "neutral": "#FDCC39", "bad": "#FF4E56",
        }
    base_theme.pop("$schema", None)
    base_theme["name"] = theme          # must match resourcePackages + themeCollection
    base_theme.setdefault("version", "5.70")
    return report, pbir, base_theme, theme


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

    parts = [
        {"path": "report.json", "payload": b64encode_json(report), "payloadType": "InlineBase64"},
        {"path": "definition.pbir", "payload": b64encode_json(pbir), "payloadType": "InlineBase64"},
        {"path": f"StaticResources/SharedResources/BaseThemes/{theme}.json",
         "payload": b64encode_json(base_theme), "payloadType": "InlineBase64"},
    ]

    rpt_id = state.get("report_id")
    if not rpt_id:
        try:
            rpt_id = find_item(token, api, ws_id, rpt_name, "Report")["id"]
        except RuntimeError:
            pass

    if rpt_id:
        resp = requests.post(f"{api}/workspaces/{ws_id}/reports/{rpt_id}/updateDefinition",
                             headers=headers, json={"definition": {"parts": parts}}, timeout=180)
    else:
        resp = requests.post(f"{api}/workspaces/{ws_id}/items", headers=headers,
                             json={"displayName": rpt_name, "type": "Report",
                                   "description": "Customer 360 — rapport 4 personas (churn)",
                                   "definition": {"parts": parts}}, timeout=180)

    if resp.status_code in (200, 201):
        rpt_id = resp.json().get("id", rpt_id) if resp.text else rpt_id
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
    print(f"\nReport deployed: {rpt_id}")
    print(f"   Pages: {pages} | Visuals: {visuals}")


if __name__ == "__main__":
    main()
