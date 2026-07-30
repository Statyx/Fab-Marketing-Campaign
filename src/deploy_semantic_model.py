#!/usr/bin/env python3
"""
Deploy Semantic Model SM_Marketing_Analytics — Direct Lake over LH_Customer360.

Star schema: CRM / Marketing / Commerce dimensions + facts, with the churn table
(crm_customer_profile) as the analytical spine.

RELATIONSHIP DESIGN — read before adding any relationship:
    Power BI refuses a model with two routes between the same pair of tables
    ("ambiguous path") and the import fails outright. Several fact tables carry a
    denormalised customer_id that WOULD create a second route:
        marketing_events -> customer_id   (already reaches customers via sends)
        returns          -> customer_id   (already reaches customers via orders)
        order_lines      -> (reaches customers via orders)
    Those columns stay in the model as attributes but are deliberately NOT related.

Run deploy_setup_notebook.py first so the Delta tables exist.
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
import uuid
import base64
import re
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from helpers import (load_config, load_state, save_state, get_fabric_token, fabric_headers,
                     get_powerbi_token, b64encode_json, poll_operation, find_item, print_step)

API_BASE = None


def _tag():
    return str(uuid.uuid4())


def _col(name, data_type, desc="", fmt="", hidden=False, summarize_none=False):
    col = {"name": name, "dataType": data_type, "sourceColumn": name, "lineageTag": _tag()}
    if desc:
        col["description"] = desc
    if fmt:
        col["formatString"] = fmt
    if hidden:
        col["isHidden"] = True
    if summarize_none:
        col["summarizeBy"] = "none"
    return col


def _measure(name, expr, desc="", fmt="", folder="", hidden=False):
    m = {"name": name, "expression": expr.split("\n"), "lineageTag": _tag()}
    if desc:
        m["description"] = desc
    if fmt:
        m["formatString"] = fmt
    if folder:
        m["displayFolder"] = folder
    if hidden:
        m["isHidden"] = True
    return m


def _partition(table_name):
    return {"name": table_name, "mode": "directLake",
            "source": {"type": "entity", "entityName": table_name,
                       "expressionSource": "DatabaseQuery"}}


def build_model_bim(config, state):
    at_risk = config["churn_model"]["at_risk_threshold"]
    tables = []

    # ── crm_customers ────────────────────────────────────────────
    tables.append({
        "name": "crm_customers", "lineageTag": _tag(),
        "description": "Customer master (lifecycle recomputed from behaviour)",
        "columns": [
            _col("customer_id", "string", "Customer code", summarize_none=True),
            _col("account_id", "string", "B2B account FK", hidden=True, summarize_none=True),
            _col("first_name", "string", "First name"),
            _col("last_name", "string", "Last name"),
            _col("email", "string", "Email", summarize_none=True),
            _col("city", "string", "City"),
            _col("customer_type", "string", "B2B / B2C"),
            _col("lifecycle_stage", "string", "lead / prospect / active / at_risk / churned"),
            _col("consent_email", "boolean", "Email opt-in"),
            _col("first_seen_at", "dateTime", "First seen", summarize_none=True),
            _col("status", "string", "active / churned"),
        ],
        "measures": [
            _measure("Total Customers", "COUNTROWS(crm_customers)",
                     "Number of contacts in the base", fmt="#,0", folder="Counts"),
            _measure("Opted-in Customers",
                     "CALCULATE(COUNTROWS(crm_customers), crm_customers[consent_email] = TRUE())",
                     "Contacts still reachable by email", fmt="#,0", folder="Counts"),
            _measure("Churned Customers",
                     'CALCULATE(COUNTROWS(crm_customers), crm_customers[lifecycle_stage] = "churned")',
                     "Customers whose last order is older than a year", fmt="#,0", folder="Churn"),
        ],
        "partitions": [_partition("crm_customers")],
    })

    # ── crm_customer_profile (the churn spine) ───────────────────
    tables.append({
        "name": "crm_customer_profile", "lineageTag": _tag(),
        "description": "Behavioural profile — every column is COMPUTED from real activity",
        "columns": [
            _col("customer_id", "string", "Customer FK", summarize_none=True),
            _col("is_customer", "boolean", "Has at least one order"),
            _col("churn_risk_score", "int64", "0-100, derived from behaviour", fmt="#,0", summarize_none=True),
            _col("risk_band", "string", "Low / Medium / High / Critical / Prospect"),
            _col("clv_eur", "double", "Customer lifetime value", fmt="#,0.00", summarize_none=True),
            _col("nps_last", "int64", "Last NPS score 0-10", summarize_none=True),
            _col("total_orders", "int64", "Lifetime order count", fmt="#,0", summarize_none=True),
            _col("total_spend_eur", "double", "Lifetime spend", fmt="#,0.00", summarize_none=True),
            _col("avg_order_value_eur", "double", "Average basket", fmt="#,0.00", summarize_none=True),
            _col("days_since_last_order", "int64", "Recency in days", fmt="#,0", summarize_none=True),
            _col("orders_90d", "int64", "Orders in the last 90 days", fmt="#,0", summarize_none=True),
            _col("orders_prev_90d", "int64", "Orders in the previous 90 days", fmt="#,0", summarize_none=True),
            _col("sends", "int64", "Emails received", fmt="#,0", summarize_none=True),
            _col("opens", "int64", "Emails opened", fmt="#,0", summarize_none=True),
            _col("clicks", "int64", "Emails clicked", fmt="#,0", summarize_none=True),
            _col("engagement_rate", "double", "Opens / sends", fmt="0.00%", summarize_none=True),
            _col("click_rate", "double", "Clicks / opens", fmt="0.00%", summarize_none=True),
            _col("unsubscribed", "boolean", "Opted out of email"),
            _col("total_interactions", "int64", "Support interactions", fmt="#,0", summarize_none=True),
            _col("negative_interactions", "int64", "Negative interactions", fmt="#,0", summarize_none=True),
            _col("unresolved_interactions", "int64", "Unresolved negative interactions", fmt="#,0", summarize_none=True),
            _col("first_order_at", "dateTime", "First order", summarize_none=True),
            _col("last_order_at", "dateTime", "Last order", summarize_none=True),
        ],
        "measures": [
            # Counts live HERE, not on crm_customers: the relationship runs
            # profile -> customers (many-to-one, one-direction), so slicing by
            # risk_band cannot filter [Total Customers]. Any visual that groups by
            # risk_band needs a count that sits on the profile table itself.
            _measure("Profiled Customers", "COUNTROWS(crm_customer_profile)",
                     "Contacts with a behavioural profile — countable by risk band",
                     fmt="#,0", folder="Counts"),
            _measure("Buyers",
                     "COALESCE(CALCULATE(COUNTROWS(crm_customer_profile), "
                     "crm_customer_profile[is_customer] = TRUE()), 0)",
                     "Contacts with at least one order — the only population churn applies to",
                     fmt="#,0", folder="Counts"),
            _measure("Avg Churn Score",
                     "CALCULATE(AVERAGE(crm_customer_profile[churn_risk_score]), "
                     "crm_customer_profile[is_customer] = TRUE())",
                     "Average churn risk across actual buyers", fmt="#,0.0", folder="Churn"),
            _measure("Max Churn Score",
                     "MAX(crm_customer_profile[churn_risk_score])",
                     "Worst churn risk in context", fmt="#,0", folder="Churn"),
            _measure("Customers at Risk",
                     f"COALESCE(CALCULATE(COUNTROWS(crm_customer_profile), "
                     f"crm_customer_profile[churn_risk_score] >= {at_risk}, "
                     f"crm_customer_profile[is_customer] = TRUE()), 0)",
                     f"Buyers scoring >= {at_risk}", fmt="#,0", folder="Churn"),
            _measure("At Risk %",
                     "DIVIDE([Customers at Risk], "
                     "CALCULATE(COUNTROWS(crm_customer_profile), crm_customer_profile[is_customer] = TRUE()))",
                     "Share of the customer base at risk", fmt="0.0%", folder="Churn"),
            _measure("Revenue at Risk",
                     f"COALESCE(CALCULATE(SUM(crm_customer_profile[total_spend_eur]), "
                     f"crm_customer_profile[churn_risk_score] >= {at_risk}), 0)",
                     "Historic spend of the at-risk cohort", fmt="#,0", folder="Churn"),
            _measure("CLV at Risk",
                     f"COALESCE(CALCULATE(SUM(crm_customer_profile[clv_eur]), "
                     f"crm_customer_profile[churn_risk_score] >= {at_risk}), 0)",
                     "Lifetime value exposed to churn", fmt="#,0", folder="Churn"),
            _measure("Unsubscribed Customers",
                     "COALESCE(CALCULATE(COUNTROWS(crm_customer_profile), "
                     "crm_customer_profile[unsubscribed] = TRUE()), 0)",
                     "Opted out of email", fmt="#,0", folder="Engagement"),
            _measure("Avg Engagement Rate", "AVERAGE(crm_customer_profile[engagement_rate])",
                     "Average open rate per customer", fmt="0.0%", folder="Engagement"),
            _measure("Avg Recency (days)", "AVERAGE(crm_customer_profile[days_since_last_order])",
                     "Average days since last order", fmt="#,0", folder="Churn"),
            _measure("Total CLV", "SUM(crm_customer_profile[clv_eur])",
                     "Lifetime value of the base", fmt="#,0", folder="Value"),
            _measure("Avg NPS", "AVERAGE(crm_customer_profile[nps_last])",
                     "Average NPS", fmt="#,0.0", folder="Experience"),
        ],
        "partitions": [_partition("crm_customer_profile")],
    })

    # ── crm_segments + bridge ────────────────────────────────────
    tables.append({
        "name": "crm_segments", "lineageTag": _tag(),
        "description": "Marketing segments",
        "columns": [
            _col("segment_id", "string", "Segment code", summarize_none=True),
            _col("segment_name", "string", "Segment name"),
            _col("definition", "string", "Business rule"),
            _col("is_premium", "boolean", "Premium segment"),
        ],
        "measures": [
            _measure("Total Segments", "COUNTROWS(crm_segments)", "Number of segments",
                     fmt="#,0", folder="Counts"),
        ],
        "partitions": [_partition("crm_segments")],
    })

    tables.append({
        "name": "crm_customer_segments", "lineageTag": _tag(),
        "description": "Bridge: customer <-> segment membership",
        "columns": [
            _col("customer_id", "string", "Customer FK", summarize_none=True),
            _col("segment_id", "string", "Segment FK", summarize_none=True),
            _col("assigned_at", "dateTime", "Assignment date", summarize_none=True),
        ],
        "measures": [
            _measure("Segment Memberships", "COUNTROWS(crm_customer_segments)",
                     "Number of customer-segment links", fmt="#,0", folder="Counts"),
        ],
        "partitions": [_partition("crm_customer_segments")],
    })

    # ── crm_interactions ─────────────────────────────────────────
    tables.append({
        "name": "crm_interactions", "lineageTag": _tag(),
        "description": "Support interactions",
        "columns": [
            _col("interaction_id", "string", "Interaction code", summarize_none=True),
            _col("customer_id", "string", "Customer FK", summarize_none=True),
            _col("interaction_type", "string", "Type"),
            _col("channel", "string", "Channel"),
            _col("sentiment", "string", "positive / neutral / negative"),
            _col("is_resolved", "boolean", "Resolved"),
            _col("occurred_at", "dateTime", "Timestamp", summarize_none=True),
        ],
        "measures": [
            _measure("Total Interactions", "COUNTROWS(crm_interactions)",
                     "Support interactions", fmt="#,0", folder="Experience"),
            _measure("Negative Interactions",
                     'CALCULATE(COUNTROWS(crm_interactions), crm_interactions[sentiment] = "negative")',
                     "Negative interactions", fmt="#,0", folder="Experience"),
            _measure("Unresolved Negative",
                     'CALCULATE(COUNTROWS(crm_interactions), crm_interactions[sentiment] = "negative", '
                     'crm_interactions[is_resolved] = FALSE())',
                     "Negative and still open", fmt="#,0", folder="Experience"),
        ],
        "partitions": [_partition("crm_interactions")],
    })

    # ── marketing_campaigns ──────────────────────────────────────
    tables.append({
        "name": "marketing_campaigns", "lineageTag": _tag(),
        "description": "Email campaigns",
        "columns": [
            _col("campaign_id", "string", "Campaign code", summarize_none=True),
            _col("campaign_name", "string", "Campaign name"),
            _col("objective", "string", "acquisition / retention / upsell / winback / engagement"),
            _col("channel", "string", "Channel"),
            _col("start_date", "dateTime", "Start", summarize_none=True),
            _col("end_date", "dateTime", "End", summarize_none=True),
            _col("budget_eur", "int64", "Budget", fmt="#,0", summarize_none=True),
        ],
        "measures": [
            _measure("Total Campaigns", "COUNTROWS(marketing_campaigns)",
                     "Number of campaigns", fmt="#,0", folder="Counts"),
            _measure("Total Budget", "SUM(marketing_campaigns[budget_eur])",
                     "Total campaign budget", fmt="#,0", folder="Marketing"),
        ],
        "partitions": [_partition("marketing_campaigns")],
    })

    # ── marketing_sends ──────────────────────────────────────────
    tables.append({
        "name": "marketing_sends", "lineageTag": _tag(),
        "description": "Individual email sends",
        "columns": [
            _col("send_id", "string", "Send code", summarize_none=True),
            _col("campaign_id", "string", "Campaign FK", summarize_none=True),
            _col("customer_id", "string", "Customer FK", summarize_none=True),
            _col("asset_id", "string", "Creative variant", summarize_none=True),
            _col("sent_at", "dateTime", "Sent at", summarize_none=True),
        ],
        "measures": [
            _measure("Total Sends", "COUNTROWS(marketing_sends)",
                     "Emails sent", fmt="#,0", folder="Marketing"),
            _measure("Sends per Customer",
                     "DIVIDE([Total Sends], DISTINCTCOUNT(marketing_sends[customer_id]))",
                     "Marketing pressure — the root-cause metric", fmt="#,0.00", folder="Marketing"),
            _measure("Customers Contacted", "DISTINCTCOUNT(marketing_sends[customer_id])",
                     "Distinct customers emailed", fmt="#,0", folder="Marketing"),
        ],
        "partitions": [_partition("marketing_sends")],
    })

    # ── marketing_events ─────────────────────────────────────────
    # NOTE: customer_id / campaign_id are kept as attributes but NOT related —
    # events already reach both through marketing_sends (ambiguous path otherwise).
    tables.append({
        "name": "marketing_events", "lineageTag": _tag(),
        "description": "Email events (open / click / bounce / unsubscribe)",
        "columns": [
            _col("event_id", "string", "Event code", summarize_none=True),
            _col("send_id", "string", "Send FK", summarize_none=True),
            _col("customer_id", "string", "Customer (denormalised, not related)", summarize_none=True),
            _col("campaign_id", "string", "Campaign (denormalised, not related)", summarize_none=True),
            _col("event_type", "string", "open / click / bounce / unsubscribe"),
            _col("event_at", "dateTime", "Event timestamp", summarize_none=True),
        ],
        "measures": [
            _measure("Total Events", "COUNTROWS(marketing_events)",
                     "All email events — countable by event_type", fmt="#,0", folder="Funnel"),
            _measure("Opens", 'CALCULATE(COUNTROWS(marketing_events), marketing_events[event_type] = "open")',
                     "Email opens", fmt="#,0", folder="Funnel"),
            _measure("Clicks", 'CALCULATE(COUNTROWS(marketing_events), marketing_events[event_type] = "click")',
                     "Email clicks", fmt="#,0", folder="Funnel"),
            _measure("Bounces", 'CALCULATE(COUNTROWS(marketing_events), marketing_events[event_type] = "bounce")',
                     "Bounces", fmt="#,0", folder="Funnel"),
            _measure("Unsubscribes",
                     'CALCULATE(COUNTROWS(marketing_events), marketing_events[event_type] = "unsubscribe")',
                     "Unsubscribes — the fatigue signal", fmt="#,0", folder="Funnel"),
            _measure("Open Rate", "DIVIDE([Opens], [Total Sends])",
                     "Opens / sends", fmt="0.0%", folder="Funnel"),
            _measure("Click Through Rate", "DIVIDE([Clicks], [Opens])",
                     "Clicks / opens", fmt="0.0%", folder="Funnel"),
            _measure("Bounce Rate", "DIVIDE([Bounces], [Total Sends])",
                     "Bounces / sends", fmt="0.0%", folder="Funnel"),
            _measure("Unsubscribe Rate", "DIVIDE([Unsubscribes], [Total Sends])",
                     "Unsubscribes / sends", fmt="0.00%", folder="Funnel"),
        ],
        "partitions": [_partition("marketing_events")],
    })

    # ── products ─────────────────────────────────────────────────
    tables.append({
        "name": "products", "lineageTag": _tag(),
        "description": "Product catalogue",
        "columns": [
            _col("product_id", "string", "Product code", summarize_none=True),
            _col("product_name", "string", "Product name"),
            _col("category", "string", "Category"),
            _col("unit_price_eur", "double", "Unit price", fmt="#,0.00", summarize_none=True),
            _col("margin_pct", "double", "Margin", fmt="0.0%", summarize_none=True),
        ],
        "measures": [
            _measure("Total Products", "COUNTROWS(products)", "Catalogue size",
                     fmt="#,0", folder="Counts"),
        ],
        "partitions": [_partition("products")],
    })

    # ── orders ───────────────────────────────────────────────────
    tables.append({
        "name": "orders", "lineageTag": _tag(),
        "description": "Customer orders (last-touch attributed)",
        "columns": [
            _col("order_id", "string", "Order code", summarize_none=True),
            _col("customer_id", "string", "Customer FK", summarize_none=True),
            _col("order_at", "dateTime", "Order timestamp", summarize_none=True),
            _col("total_amount_eur", "double", "Order total", fmt="#,0.00", summarize_none=True),
            _col("channel", "string", "web / app / store"),
            _col("attributed_campaign_id", "string", "Last-touch campaign", summarize_none=True),
        ],
        "measures": [
            _measure("Total Orders", "COUNTROWS(orders)", "Order count", fmt="#,0", folder="Commerce"),
            _measure("Revenue", "SUM(orders[total_amount_eur])", "Total revenue",
                     fmt="#,0", folder="Commerce"),
            _measure("Average Order Value", "AVERAGE(orders[total_amount_eur])",
                     "Average basket", fmt="#,0.00", folder="Commerce"),
            _measure("Attributed Orders",
                     'COALESCE(CALCULATE(COUNTROWS(orders), NOT(ISBLANK(orders[attributed_campaign_id])), '
                     'orders[attributed_campaign_id] <> ""), 0)',
                     "Orders with a last-touch campaign", fmt="#,0", folder="Attribution"),
            _measure("Attributed Revenue",
                     'COALESCE(CALCULATE(SUM(orders[total_amount_eur]), '
                     'NOT(ISBLANK(orders[attributed_campaign_id])), orders[attributed_campaign_id] <> ""), 0)',
                     "Revenue attributed to campaigns", fmt="#,0", folder="Attribution"),
            _measure("Attribution Rate", "DIVIDE([Attributed Orders], [Total Orders])",
                     "Share of orders attributed", fmt="0.0%", folder="Attribution"),
            _measure("Campaign ROI",
                     "DIVIDE([Attributed Revenue] - [Total Budget], [Total Budget])",
                     "(attributed revenue - budget) / budget", fmt="0.0%", folder="Attribution"),
        ],
        "partitions": [_partition("orders")],
    })

    # ── order_lines ──────────────────────────────────────────────
    tables.append({
        "name": "order_lines", "lineageTag": _tag(),
        "description": "Order detail lines",
        "columns": [
            _col("order_line_id", "string", "Line code", summarize_none=True),
            _col("order_id", "string", "Order FK", summarize_none=True),
            _col("product_id", "string", "Product FK", summarize_none=True),
            _col("quantity", "int64", "Quantity", fmt="#,0", summarize_none=True),
            _col("unit_price_eur", "double", "Unit price", fmt="#,0.00", summarize_none=True),
            _col("line_total_eur", "double", "Line total", fmt="#,0.00", summarize_none=True),
        ],
        "measures": [
            _measure("Units Sold", "SUM(order_lines[quantity])", "Units sold",
                     fmt="#,0", folder="Commerce"),
            # [Revenue] lives on orders, which has no relationship to products —
            # revenue by product category has to be summed from the lines.
            _measure("Product Revenue", "SUM(order_lines[line_total_eur])",
                     "Revenue summed from order lines — sliceable by product category",
                     fmt="#,0", folder="Commerce"),
            # DEPRECATED alias of [Product Revenue]. This measure was renamed and
            # simply dropped, which broke a live report that was bound to it —
            # a semantic model is a contract, and removing a measure is a
            # breaking change for every downstream consumer. Kept hidden so it
            # still resolves for existing reports without offering a duplicate
            # to Copilot, the Data Agent or report authors. Remove only once no
            # report references it (deploy checks this and will refuse).
            _measure("Line Revenue", "[Product Revenue]",
                     "Deprecated — use [Product Revenue].",
                     fmt="#,0", folder="Commerce", hidden=True),
            _measure("Lines per Order", "DIVIDE(COUNTROWS(order_lines), [Total Orders])",
                     "Average lines per order", fmt="#,0.00", folder="Commerce"),
        ],
        "partitions": [_partition("order_lines")],
    })

    # ── returns ──────────────────────────────────────────────────
    # customer_id kept as attribute but NOT related (reaches customers via orders).
    tables.append({
        "name": "returns", "lineageTag": _tag(),
        "description": "Order returns",
        "columns": [
            _col("return_id", "string", "Return code", summarize_none=True),
            _col("order_id", "string", "Order FK", summarize_none=True),
            _col("customer_id", "string", "Customer (denormalised, not related)", summarize_none=True),
            _col("returned_at", "dateTime", "Return timestamp", summarize_none=True),
            _col("reason", "string", "Return reason"),
            _col("refund_amount_eur", "double", "Refund", fmt="#,0.00", summarize_none=True),
        ],
        "measures": [
            _measure("Total Returns", "COUNTROWS(returns)", "Returns", fmt="#,0", folder="Commerce"),
            _measure("Refund Amount", "SUM(returns[refund_amount_eur])", "Refunded",
                     fmt="#,0", folder="Commerce"),
            _measure("Return Rate", "DIVIDE(COUNTROWS(returns), [Total Orders])",
                     "Returns / orders", fmt="0.0%", folder="Commerce"),
        ],
        "partitions": [_partition("returns")],
    })

    # ── Relationships (many -> one). NO second route between any pair. ──
    relationships = [
        {"name": "rel_profile_customer", "fromTable": "crm_customer_profile", "fromColumn": "customer_id",
         "toTable": "crm_customers", "toColumn": "customer_id"},
        {"name": "rel_custseg_customer", "fromTable": "crm_customer_segments", "fromColumn": "customer_id",
         "toTable": "crm_customers", "toColumn": "customer_id"},
        {"name": "rel_custseg_segment", "fromTable": "crm_customer_segments", "fromColumn": "segment_id",
         "toTable": "crm_segments", "toColumn": "segment_id"},
        {"name": "rel_interaction_customer", "fromTable": "crm_interactions", "fromColumn": "customer_id",
         "toTable": "crm_customers", "toColumn": "customer_id"},
        {"name": "rel_send_customer", "fromTable": "marketing_sends", "fromColumn": "customer_id",
         "toTable": "crm_customers", "toColumn": "customer_id"},
        {"name": "rel_send_campaign", "fromTable": "marketing_sends", "fromColumn": "campaign_id",
         "toTable": "marketing_campaigns", "toColumn": "campaign_id"},
        {"name": "rel_event_send", "fromTable": "marketing_events", "fromColumn": "send_id",
         "toTable": "marketing_sends", "toColumn": "send_id"},
        {"name": "rel_order_customer", "fromTable": "orders", "fromColumn": "customer_id",
         "toTable": "crm_customers", "toColumn": "customer_id"},
        {"name": "rel_line_order", "fromTable": "order_lines", "fromColumn": "order_id",
         "toTable": "orders", "toColumn": "order_id"},
        {"name": "rel_line_product", "fromTable": "order_lines", "fromColumn": "product_id",
         "toTable": "products", "toColumn": "product_id"},
        {"name": "rel_return_order", "fromTable": "returns", "fromColumn": "order_id",
         "toTable": "orders", "toColumn": "order_id"},
    ]
    rels = [{"name": r["name"], "fromTable": r["fromTable"], "fromColumn": r["fromColumn"],
             "toTable": r["toTable"], "toColumn": r["toColumn"],
             "crossFilteringBehavior": "oneDirection"} for r in relationships]

    lh_name = config.get("lakehouse_name", "LH_Customer360")
    sql_endpoint = state.get("lakehouse_sql_endpoint", "")
    if not sql_endpoint and state.get("lakehouse_id"):
        h_tmp = fabric_headers(get_fabric_token())
        r_lh = requests.get(f"{API_BASE}/workspaces/{state['workspace_id']}/lakehouses/{state['lakehouse_id']}",
                            headers=h_tmp, timeout=60)
        if r_lh.status_code == 200:
            sql_endpoint = (r_lh.json().get("properties", {})
                            .get("sqlEndpointProperties", {}).get("connectionString", ""))

    expressions = [{
        "name": "DatabaseQuery", "kind": "m", "lineageTag": _tag(),
        "expression": ["let",
                       f'    database = Sql.Database("{sql_endpoint}", "{lh_name}")',
                       "in", "    database"],
    }]

    return {
        "compatibilityLevel": 1604,
        "model": {
            "defaultPowerBIDataSourceVersion": "PowerBI_V3",
            "defaultMode": "directLake",
            "discourageImplicitMeasures": True,
            "tables": tables,
            "relationships": rels,
            "expressions": expressions,
            "culture": "fr-FR",
            "annotations": [
                {"name": "__PBI_CopilotInstructions", "value": (
                    "Ce modele analyse la relation client d'un retailer : CRM, campagnes marketing, "
                    "commerce et surtout le RISQUE D'ATTRITION (churn). "
                    "Toujours utiliser les mesures existantes plutot que des agregations manuelles. "
                    "Churn : [Avg Churn Score], [Customers at Risk], [At Risk %], [Revenue at Risk], "
                    "[CLV at Risk], [Avg Recency (days)], [Churned Customers]. "
                    "Engagement : [Avg Engagement Rate], [Unsubscribed Customers]. "
                    "Entonnoir email : [Total Sends], [Sends per Customer], [Open Rate], "
                    "[Click Through Rate], [Bounce Rate], [Unsubscribe Rate], [Unsubscribes]. "
                    "Commerce : [Revenue], [Total Orders], [Average Order Value], [Units Sold], [Return Rate]. "
                    "Attribution : [Attributed Orders], [Attributed Revenue], [Attribution Rate], [Campaign ROI]. "
                    "Le score de churn est CALCULE a partir du comportement (recence, chute de frequence, "
                    "engagement, NPS, desabonnement, friction support) et ne s'applique qu'aux clients ayant "
                    "deja commande (crm_customer_profile[is_customer] = TRUE). Les contacts sans commande "
                    "sont en bande 'Prospect'. "
                    "Pour la cause racine de l'attrition, comparer [Sends per Customer] par campagne : "
                    "une campagne qui sur-sollicite genere des desabonnements puis de l'attrition. "
                    "Grouper avec marketing_campaigns[campaign_name], crm_segments[segment_name], "
                    "crm_customer_profile[risk_band], crm_customers[lifecycle_stage]."
                )},
                {"name": "__PBI_LinguisticSchema", "value": json.dumps({
                    "Version": "1.0.0", "Language": "fr-FR", "DynamicImprovement": "HighConfidence",
                    "Entities": {
                        "crm_customer_profile": {"Definition": {"Binding": {"ConceptualEntity": "crm_customer_profile"}},
                                                 "State": "Generated",
                                                 "Terms": [["churn"], ["attrition"], ["risque"], ["profil client"]]},
                        "crm_customers": {"Definition": {"Binding": {"ConceptualEntity": "crm_customers"}},
                                          "State": "Generated", "Terms": [["client"], ["contact"], ["base client"]]},
                        "crm_segments": {"Definition": {"Binding": {"ConceptualEntity": "crm_segments"}},
                                         "State": "Generated", "Terms": [["segment"], ["cible"]]},
                        "marketing_campaigns": {"Definition": {"Binding": {"ConceptualEntity": "marketing_campaigns"}},
                                                "State": "Generated", "Terms": [["campagne"], ["operation"]]},
                        "marketing_sends": {"Definition": {"Binding": {"ConceptualEntity": "marketing_sends"}},
                                            "State": "Generated", "Terms": [["envoi"], ["email"], ["pression"]]},
                        "marketing_events": {"Definition": {"Binding": {"ConceptualEntity": "marketing_events"}},
                                             "State": "Generated",
                                             "Terms": [["ouverture"], ["clic"], ["desabonnement"]]},
                        "orders": {"Definition": {"Binding": {"ConceptualEntity": "orders"}},
                                   "State": "Generated", "Terms": [["commande"], ["achat"], ["vente"]]},
                        "products": {"Definition": {"Binding": {"ConceptualEntity": "products"}},
                                     "State": "Generated", "Terms": [["produit"], ["article"]]},
                    },
                })},
                {"name": "PBI_ProTooling", "value": json.dumps(
                    ["DirectLakeOnOneLakeInWeb", "WebModelingEdit", "DaxQueryView_Desktop",
                     "CopilotTooling", "MCP-PBIModeling"])},
                {"name": "__PBI_TimeIntelligenceEnabled", "value": "1"},
                {"name": "PBI_QueryOrder", "value": json.dumps([f"DirectLake - {lh_name}"])},
                {"name": "__PBI_VerifiedAnswers", "value": json.dumps([
                    {"Question": "Combien de clients sont a risque d'attrition ?",
                     "Answer": {"Query": 'EVALUATE ROW("At Risk", [Customers at Risk], "Share", [At Risk %])',
                                "Description": "Cohorte a risque et sa part de la base"}},
                    {"Question": "Quel chiffre d'affaires est menace ?",
                     "Answer": {"Query": 'EVALUATE ROW("Revenue at Risk", [Revenue at Risk], "CLV at Risk", [CLV at Risk])',
                                "Description": "Revenu historique et valeur vie exposes"}},
                    {"Question": "Quelle campagne sur-sollicite le plus ses clients ?",
                     "Answer": {"Query": 'EVALUATE TOPN(1, SUMMARIZECOLUMNS(marketing_campaigns[campaign_name], '
                                         '"Pression", [Sends per Customer]), [Pression], DESC)',
                                "Description": "Cause racine : pression marketing par campagne"}},
                    {"Question": "Quelle campagne genere le plus de desabonnements ?",
                     "Answer": {"Query": 'EVALUATE TOPN(1, SUMMARIZECOLUMNS(marketing_campaigns[campaign_name], '
                                         '"Desabonnements", [Unsubscribes]), [Desabonnements], DESC)',
                                "Description": "Campagne la plus destructrice"}},
                    {"Question": "Repartition des clients par bande de risque",
                     "Answer": {"Query": 'EVALUATE SUMMARIZECOLUMNS(crm_customer_profile[risk_band], '
                                         '"Clients", COUNTROWS(crm_customer_profile))',
                                "Description": "Distribution du risque"}},
                    {"Question": "Quel est le ROI des campagnes ?",
                     "Answer": {"Query": 'EVALUATE ROW("Attributed Revenue", [Attributed Revenue], '
                                         '"Budget", [Total Budget], "ROI", [Campaign ROI])',
                                "Description": "Retour sur investissement marketing"}},
                ])},
            ],
        },
    }


def get_definition_parts(token, ws_id, item_kind, item_id):
    """Fetch an item's definition parts, following Fabric's async 202 pattern."""
    headers = fabric_headers(token)
    resp = requests.post(
        f"{API_BASE}/workspaces/{ws_id}/{item_kind}/{item_id}/getDefinition",
        headers=headers, timeout=180)
    if resp.status_code == 202:
        op_id = resp.headers.get("x-ms-operation-id", "")
        poll_operation(token, API_BASE, op_id)
        resp = requests.get(f"{API_BASE}/operations/{op_id}/result",
                            headers=headers, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"getDefinition failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json().get("definition", {}).get("parts", [])


def deployed_measure_names(token, ws_id, sm_id):
    """Read the model back from Fabric and return its {(table, measure)} inventory.

    Fabric stores the model as TMDL even when it is pushed as a .bim, so the
    read-back is parsed from the TMDL parts rather than compared as JSON.
    """
    found = set()
    for part in get_definition_parts(token, ws_id, "semanticModels", sm_id):
        if "/tables/" not in part["path"]:
            continue
        table = part["path"].split("/")[-1].removesuffix(".tmdl")
        tmdl = base64.b64decode(part["payload"]).decode("utf-8")
        for name in re.findall(r"^\s*measure\s+(.+?)\s*=", tmdl, re.M):
            found.add((table, name.strip().strip("'\"")))
    return found


def measures_used_by_reports(token, ws_id):
    """Map every measure name referenced by a report in the workspace to its report.

    A semantic model is a contract. Dropping a measure is a breaking change for
    every report bound to it, and Fabric gives no warning: the report keeps its
    reference and renders 'Something's wrong with one or more fields' at runtime.
    That is exactly how a live report was broken by renaming [Line Revenue].
    """
    used = {}
    try:
        items = requests.get(f"{API_BASE}/workspaces/{ws_id}/items",
                             headers=fabric_headers(token), timeout=120).json().get("value", [])
    except Exception as exc:
        print(f"   WARNING: could not list workspace items ({exc}); skipping consumer check")
        return used

    for item in [i for i in items if i.get("type") == "Report"]:
        try:
            parts = get_definition_parts(token, ws_id, "reports", item["id"])
        except Exception as exc:
            print(f"   WARNING: could not read report '{item['displayName']}' ({exc})")
            continue
        for part in parts:
            if not part["path"].endswith("report.json"):
                continue
            body = base64.b64decode(part["payload"]).decode("utf-8")
            # Legacy PBIX nests the visual config as a JSON *string*, so the
            # measure references are only visible in the raw text.
            for name in re.findall(r'\\?"Measure\\?":\s*\{[^{}]*?\\?"Property\\?":\s*\\?"(.*?)\\?"', body):
                used.setdefault(name, set()).add(item["displayName"])
    return used


def check_breaking_removals(token, ws_id, sm_id, model_bim):
    """Refuse to delete a measure that a report still uses."""
    try:
        current = {name for _, name in deployed_measure_names(token, ws_id, sm_id)}
    except Exception as exc:
        print(f"   WARNING: could not read current model ({exc}); skipping removal check")
        return
    new = {m["name"] for t in model_bim["model"]["tables"] for m in t.get("measures", [])}
    removed = current - new
    if not removed:
        return

    used = measures_used_by_reports(token, ws_id)
    breaking = {m: sorted(used[m]) for m in removed if m in used}
    if breaking:
        detail = "; ".join(f"[{m}] used by {', '.join(r)}" for m, r in breaking.items())
        raise RuntimeError(
            "Refusing to deploy: this would delete measures that reports still "
            f"reference, breaking them silently. {detail}. "
            "Keep them as hidden deprecated aliases, or migrate the reports first.")
    print(f"   removing {len(removed)} unused measure(s): {', '.join(sorted(removed))}")


def verify_deployment(token, ws_id, sm_id, model_bim, repush=None):
    """Fail loudly if Fabric did not store what we sent.

    updateDefinition can return 202, poll to 'Succeeded', and still leave the
    previous definition in place. That produced a model that was silently one
    revision behind: the script printed OK, and the report then bound to
    measures that did not exist. Trust the read-back, not the poll.
    """
    expected = {(t["name"], m["name"])
                for t in model_bim["model"]["tables"]
                for m in t.get("measures", [])}
    for attempt in range(1, 4):
        actual = deployed_measure_names(token, ws_id, sm_id)
        missing, extra = expected - actual, actual - expected
        if not missing and not extra:
            print(f"   verified: {len(actual)} measures match the definition")
            return
        print(f"   read-back mismatch (attempt {attempt}/3): "
              f"{len(missing)} missing, {len(extra)} stale")
        if attempt == 3:
            raise RuntimeError(
                "Semantic model did not apply after 3 attempts. "
                f"Missing: {sorted(missing)} | Stale: {sorted(extra)}")
        if repush:
            print("   re-pushing definition...")
            repush()
        time.sleep(15)


def reframe_direct_lake(ws_id, sm_id):
    """Force Direct Lake to pick up the current Delta files.

    Direct Lake does NOT reframe on its own when the setup notebook rewrites the
    Delta tables. Without this the model keeps serving the previous snapshot, so
    the report and the Data Agent answer with stale numbers while every
    deployment step reports success.
    """
    token = get_powerbi_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = f"https://api.powerbi.com/v1.0/myorg/groups/{ws_id}/datasets/{sm_id}"
    resp = requests.post(f"{base}/refreshes", headers=headers,
                         json={"type": "full"}, timeout=120)
    if resp.status_code not in (200, 202):
        print(f"   WARNING: reframe could not be queued ({resp.status_code}): {resp.text[:200]}")
        return
    print("   reframing Direct Lake...")
    for _ in range(60):
        time.sleep(5)
        hist = requests.get(f"{base}/refreshes?$top=1", headers=headers, timeout=60).json()
        entries = hist.get("value") or []
        if not entries:
            continue
        status = entries[0].get("status")
        if status == "Completed":
            print("   reframe completed")
            return
        if status in ("Failed", "Disabled"):
            raise RuntimeError(f"Direct Lake reframe {status}: "
                               f"{entries[0].get('serviceExceptionJson', '')[:300]}")
    print("   WARNING: reframe still running after 5 min — check the workspace")


def main():
    config = load_config(); state = load_state()
    global API_BASE
    API_BASE = config["fabric_api_base"]
    ws_id = state.get("workspace_id")
    if not ws_id:
        print("Workspace not created. Run deploy_workspace.py first.")
        sys.exit(1)

    token = get_fabric_token(); headers = fabric_headers(token)
    sm_name = config["semantic_model_name"]

    print_step(1, 1, f"Deploying Semantic Model: {sm_name}")
    model_bim = build_model_bim(config, state)
    tcount = len(model_bim["model"]["tables"])
    mcount = sum(len(t.get("measures", [])) for t in model_bim["model"]["tables"])
    rcount = len(model_bim["model"]["relationships"])
    print(f"   {tcount} tables, {mcount} measures, {rcount} relationships")

    definition = {"parts": [
        {"path": "definition.pbism", "payload": b64encode_json({"version": "1.0"}),
         "payloadType": "InlineBase64"},
        {"path": "model.bim", "payload": b64encode_json(model_bim), "payloadType": "InlineBase64"},
    ]}

    sm_id = state.get("semantic_model_id")
    if sm_id:
        check_breaking_removals(token, ws_id, sm_id, model_bim)

    def push(target_id):
        return requests.post(
            f"{API_BASE}/workspaces/{ws_id}/semanticModels/{target_id}/updateDefinition",
            headers=headers, json={"definition": definition})

    if sm_id:
        print(f"   updating existing model {sm_id}")
        resp = push(sm_id)
    else:
        try:
            sm_id = find_item(token, API_BASE, ws_id, sm_name, "SemanticModel")["id"]
            print(f"   updating found model {sm_id}")
            resp = push(sm_id)
        except RuntimeError:
            print("   creating new model...")
            resp = requests.post(f"{API_BASE}/workspaces/{ws_id}/items", headers=headers,
                                 json={"displayName": sm_name, "type": "SemanticModel",
                                       "description": "Customer 360 — churn, marketing funnel, commerce (Direct Lake)",
                                       "definition": definition})

    if resp.status_code in (200, 201):
        sm_id = resp.json().get("id", sm_id)
    elif resp.status_code == 202:
        op_id = resp.headers.get("x-ms-operation-id", "")
        if op_id:
            print(f"   polling operation {op_id}...")
            poll_operation(token, API_BASE, op_id)
        if not sm_id:
            sm_id = find_item(token, API_BASE, ws_id, sm_name, "SemanticModel")["id"]
    else:
        raise RuntimeError(f"Deploy failed ({resp.status_code}): {resp.text[:400]}")

    verify_deployment(token, ws_id, sm_id, model_bim, repush=lambda: push(sm_id))
    reframe_direct_lake(ws_id, sm_id)

    state["semantic_model_id"] = sm_id
    save_state(state)
    print(f"\nOK. Semantic model deployed: {sm_id}")


if __name__ == "__main__":
    main()
