#!/usr/bin/env python3
"""
Deploy the Marketing Churn Data Agent.

TWO MODES — this script exists to let us TEST the hypothesis, not just assert it:

  python deploy_data_agent.py --ontology-only   # source = ontology ONLY  (experiment)
  python deploy_data_agent.py                   # dual-source: ontology + semantic model

WHY THE EXPERIMENT MATTERS
    On two sister projects (Publicis-Live-Event, Network_Operations) an ontology-only Data Agent
    could not answer any NUMERIC question: the GQL entitySelector resolved, but the
    timeSeriesSelector returned 0 rows. In BOTH those projects the ontology's numeric data came
    from an EVENTHOUSE (Kusto) — either bound directly as KustoTable, or via a OneLake mirror.

    Here everything is plain Lakehouse Delta and there are NO TimeSeries bindings at all.
    So the failure mode may or may not reproduce. Run --ontology-only first, probe it, and
    let the trace decide. Do not write "verified" anywhere until a trace proves it.

KNOWN LIMIT OF THE ONTOLOGY IN THIS PROJECT
    crm_customer_profile (churn_risk_score, clv_eur, engagement_rate, ...) is deliberately NOT an
    entity type — it is an analytical aggregate, not a business object. So churn NUMBERS cannot
    come from the graph by construction; they come from the semantic model. The ontology answers
    "how are things connected / who is affected".
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

import argparse
import time
import uuid

import requests
from helpers import (load_config, load_state, save_state, get_fabric_token,
                     fabric_headers, poll_operation, b64encode_json, print_step)

SCH = "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition"

AGENT_DESC = ("Marketing churn agent: relationships / root cause / impact via the "
              "ONT_Customer360 ontology (GQL) + every number via the SM_Marketing_Analytics "
              "semantic model (DAX).")

# ── Ontology source ─────────────────────────────────────────────
ONT_INSTRUCTIONS = (
    "Use for TOPOLOGY, RELATIONSHIPS, ROOT-CAUSE and IMPACT (GQL). Node label = entity name, "
    "edge label = relationship name. To find who a campaign reached, traverse "
    "Campaign -[CampaignSentToCustomer]-> Customer. To find which campaigns hit a customer, "
    "traverse the same edge in reverse. Segment targeting: Campaign -[CampaignTargetsSegment]-> "
    "Segment <-[CustomerInSegment]- Customer. "
    "Do NOT use this source for aggregate NUMBERS (counts of at-risk customers, revenue at risk, "
    "churn scores, rates) - use the semantic model instead. The churn profile "
    "(churn_risk_score, clv_eur, engagement_rate) is NOT in this graph by design. "
    "CRITICAL - results are TRUNCATED at 200 rows. NEVER derive a count or a total by counting or "
    "summing the rows of a returned list: the list is partial and the number will be wrong. "
    "Always push the aggregate INTO the query - RETURN COUNT(DISTINCT n.id) / SUM(n.amount) - and "
    "NEVER add GROUP BY when the question asks for a single overall number. "
    "Also: an edge count is NOT a node count. Counting CampaignSentToCustomer edges gives sends, "
    "not customers; use COUNT(DISTINCT cu.customer_id) for customers."
)

ONT_FEWSHOTS = [
    ("Which customers did campaign CAMP_007 reach?",
     "MATCH (c:Campaign {campaign_id:'CAMP_007'})-[:CampaignSentToCustomer]->(cu:Customer) "
     "RETURN DISTINCT cu.customer_id, cu.first_name, cu.last_name, cu.lifecycle_stage"),
    ("Which segments does the Black Friday Blast campaign target?",
     "MATCH (c:Campaign {campaign_name:'Black Friday Blast'})-[:CampaignTargetsSegment]->(s:Segment) "
     "RETURN s.segment_id, s.segment_name, s.definition"),
    ("Which campaigns reached customer CUST_000042?",
     "MATCH (c:Campaign)-[:CampaignSentToCustomer]->(cu:Customer {customer_id:'CUST_000042'}) "
     "RETURN DISTINCT c.campaign_id, c.campaign_name, c.objective"),
    ("Which customers belong to the High Value segment?",
     "MATCH (cu:Customer)-[:CustomerInSegment]->(s:Segment {segment_id:'SEG_HIGH_VALUE'}) "
     "RETURN cu.customer_id, cu.lifecycle_stage, cu.city"),
    ("List the campaigns and their objectives.",
     "MATCH (c:Campaign) RETURN c.campaign_id, c.campaign_name, c.objective, c.budget_eur"),
    ("Which orders are attributed to campaign CAMP_007?",
     "MATCH (o:Order)-[:OrderAttributedToCampaign]->(c:Campaign {campaign_id:'CAMP_007'}) "
     "RETURN o.order_id, o.total_amount_eur, o.channel"),
    ("Which products did customer CUST_000042 buy?",
     "MATCH (o:Order)-[:OrderPlacedByCustomer]->(cu:Customer {customer_id:'CUST_000042'}), "
     "(o)-[:OrderContainsProduct]->(p:Product) "
     "RETURN DISTINCT p.product_id, p.product_name, p.category"),
    ("Which churned customers were reached by the Black Friday Blast?",
     "MATCH (c:Campaign {campaign_name:'Black Friday Blast'})-[:CampaignSentToCustomer]->"
     "(cu:Customer) WHERE cu.lifecycle_stage = 'churned' "
     "RETURN DISTINCT cu.customer_id, cu.city, cu.customer_type"),
    ("Which customers have unresolved negative interactions?",
     "MATCH (i:Interaction)-[:InteractionWithCustomer]->(cu:Customer) "
     "WHERE i.sentiment = 'negative' AND i.is_resolved = false "
     "RETURN DISTINCT cu.customer_id, cu.lifecycle_stage"),
    ("Which accounts do B2B customers belong to?",
     "MATCH (cu:Customer)-[:CustomerBelongsToAccount]->(a:Account) "
     "RETURN a.account_id, a.account_name, a.industry, cu.customer_id"),
    ("What creative assets does campaign CAMP_007 use?",
     "MATCH (c:Campaign {campaign_id:'CAMP_007'})-[:CampaignHasAsset]->(a:Asset) "
     "RETURN a.asset_id, a.variant, a.subject_line, a.cta"),
    ("Which customers opted out of email?",
     "MATCH (cu:Customer) WHERE cu.consent_email = false "
     "RETURN cu.customer_id, cu.lifecycle_stage, cu.city"),

    # --- Scalar aggregates: aggregate INSIDE the query, never GROUP BY, never count rows.
    # Results are truncated at 200 rows, so a per-entity projection silently under-counts.
    ("How many distinct customers did campaign CAMP_007 reach?",
     "MATCH (c:Campaign {campaign_id:'CAMP_007'})-[:CampaignSentToCustomer]->(cu:Customer) "
     "RETURN COUNT(DISTINCT cu.customer_id) AS customers_reached"),
    ("How many sends did campaign CAMP_007 generate?",
     "MATCH (c:Campaign {campaign_id:'CAMP_007'})-[e:CampaignSentToCustomer]->(cu:Customer) "
     "RETURN COUNT(e) AS total_sends"),
    ("How many orders are attributed to campaign CAMP_007 and what is the total revenue?",
     "MATCH (o:Order)-[:OrderAttributedToCampaign]->(c:Campaign {campaign_id:'CAMP_007'}) "
     "RETURN COUNT(o) AS order_count, SUM(o.total_amount_eur) AS total_revenue_eur"),
    ("How many customers are in the at_risk lifecycle stage?",
     "MATCH (cu:Customer) WHERE cu.lifecycle_stage = 'at_risk' "
     "RETURN COUNT(DISTINCT cu.customer_id) AS at_risk_customers"),
    ("What is the total revenue of customers in the at_risk lifecycle stage?",
     "MATCH (o:Order)-[:OrderPlacedByCustomer]->(cu:Customer) "
     "WHERE cu.lifecycle_stage = 'at_risk' "
     "RETURN SUM(o.total_amount_eur) AS revenue_at_risk_eur"),
]


# ── Semantic model source ───────────────────────────────────────
def build_sm_elements():
    def _col(name, desc):
        return {"id": None, "display_name": name, "type": "semantic_model.column",
                "is_selected": True, "description": desc, "children": []}

    def _meas(name, desc):
        return {"id": None, "display_name": name, "type": "semantic_model.measure",
                "is_selected": True, "description": desc, "children": []}

    def _table(name, desc, children):
        return {"id": None, "display_name": name, "type": "semantic_model.table",
                "is_selected": True, "description": desc, "children": children}

    return [
        _table("crm_customer_profile", "Behavioural churn profile (all columns computed)", [
            _col("risk_band", "Low / Medium / High / Critical / Prospect"),
            _col("churn_risk_score", "0-100 derived from behaviour"),
            _col("is_customer", "Has at least one order"),
            _meas("Avg Churn Score", "Average churn risk across buyers"),
            _meas("Customers at Risk", "Buyers scoring above the threshold"),
            _meas("At Risk %", "Share of the customer base at risk"),
            _meas("Revenue at Risk", "Historic spend of the at-risk cohort"),
            _meas("CLV at Risk", "Lifetime value exposed to churn"),
            _meas("Avg Recency (days)", "Average days since last order"),
            _meas("Avg Engagement Rate", "Average open rate"),
            _meas("Unsubscribed Customers", "Opted out of email"),
            _meas("Total CLV", "Lifetime value of the base"),
            _meas("Avg NPS", "Average NPS"),
        ]),
        _table("crm_customers", "Customer master", [
            _col("lifecycle_stage", "lead / prospect / active / at_risk / churned"),
            _col("customer_type", "B2B / B2C"), _col("city", "City"),
            _meas("Total Customers", "Number of contacts"),
            _meas("Churned Customers", "Customers churned"),
            _meas("Opted-in Customers", "Still reachable by email"),
        ]),
        _table("crm_segments", "Marketing segments", [
            _col("segment_name", "Segment name"), _col("is_premium", "Premium flag"),
            _meas("Total Segments", "Number of segments"),
        ]),
        _table("marketing_campaigns", "Email campaigns", [
            _col("campaign_name", "Campaign name"), _col("objective", "Campaign objective"),
            _meas("Total Campaigns", "Number of campaigns"),
            _meas("Total Budget", "Total campaign budget"),
        ]),
        _table("marketing_sends", "Email sends (marketing pressure)", [
            _meas("Total Sends", "Emails sent"),
            _meas("Sends per Customer", "Marketing pressure - the root-cause metric"),
            _meas("Customers Contacted", "Distinct customers emailed"),
        ]),
        _table("marketing_events", "Email events", [
            _col("event_type", "open / click / bounce / unsubscribe"),
            _meas("Opens", "Email opens"), _meas("Clicks", "Email clicks"),
            _meas("Unsubscribes", "Unsubscribes - the fatigue signal"),
            _meas("Open Rate", "Opens / sends"),
            _meas("Click Through Rate", "Clicks / opens"),
            _meas("Unsubscribe Rate", "Unsubscribes / sends"),
        ]),
        _table("orders", "Customer orders", [
            _col("channel", "web / app / store"),
            _meas("Total Orders", "Order count"), _meas("Revenue", "Total revenue"),
            _meas("Average Order Value", "Average basket"),
            _meas("Attributed Orders", "Orders with a last-touch campaign"),
            _meas("Attributed Revenue", "Revenue attributed to campaigns"),
            _meas("Attribution Rate", "Share of orders attributed"),
            _meas("Campaign ROI", "(attributed revenue - budget) / budget"),
        ]),
        _table("products", "Product catalogue", [
            _col("category", "Category"),
            _meas("Total Products", "Catalogue size"), _meas("Units Sold", "Units sold"),
        ]),
    ]


SM_FEWSHOTS = [
    {"id": "sm-001", "question": "Combien de clients sont a risque d'attrition ?",
     "query": 'EVALUATE ROW("Customers at Risk", [Customers at Risk], "At Risk %", [At Risk %])'},
    {"id": "sm-002", "question": "Quel chiffre d'affaires est menace par l'attrition ?",
     "query": 'EVALUATE ROW("Revenue at Risk", [Revenue at Risk], "CLV at Risk", [CLV at Risk])'},
    {"id": "sm-003", "question": "Repartition des clients par bande de risque",
     "query": 'EVALUATE SUMMARIZECOLUMNS(crm_customer_profile[risk_band], "Clients", COUNTROWS(crm_customer_profile)) ORDER BY [Clients] DESC'},
    {"id": "sm-004", "question": "Quelle campagne sur-sollicite le plus ses clients ?",
     "query": 'EVALUATE SUMMARIZECOLUMNS(marketing_campaigns[campaign_name], "Sends per Customer", [Sends per Customer], "Unsubscribes", [Unsubscribes]) ORDER BY [Sends per Customer] DESC'},
    {"id": "sm-005", "question": "Quelle campagne genere le plus de desabonnements ?",
     "query": 'EVALUATE SUMMARIZECOLUMNS(marketing_campaigns[campaign_name], "Unsubscribes", [Unsubscribes]) ORDER BY [Unsubscribes] DESC'},
    {"id": "sm-006", "question": "Quel est le score de churn moyen ?",
     "query": 'EVALUATE ROW("Avg Churn Score", [Avg Churn Score], "Avg Recency", [Avg Recency (days)])'},
    {"id": "sm-007", "question": "Score de churn moyen par etape du cycle de vie",
     "query": 'EVALUATE SUMMARIZECOLUMNS(crm_customers[lifecycle_stage], "Avg Churn", [Avg Churn Score], "Clients", [Total Customers])'},
    {"id": "sm-008", "question": "Quel est l'entonnoir email global ?",
     "query": 'EVALUATE ROW("Sends", [Total Sends], "Open Rate", [Open Rate], "CTR", [Click Through Rate], "Unsub Rate", [Unsubscribe Rate])'},
    {"id": "sm-009", "question": "Quel est le chiffre d'affaires et le panier moyen ?",
     "query": 'EVALUATE ROW("Revenue", [Revenue], "Orders", [Total Orders], "AOV", [Average Order Value])'},
    {"id": "sm-010", "question": "Quel est le ROI des campagnes ?",
     "query": 'EVALUATE ROW("Attributed Revenue", [Attributed Revenue], "Budget", [Total Budget], "ROI", [Campaign ROI])'},
    {"id": "sm-011", "question": "Chiffre d'affaires par canal",
     "query": 'EVALUATE SUMMARIZECOLUMNS(orders[channel], "Revenue", [Revenue], "Orders", [Total Orders]) ORDER BY [Revenue] DESC'},
    {"id": "sm-012", "question": "Combien de clients se sont desabonnes ?",
     "query": 'EVALUATE ROW("Unsubscribed", [Unsubscribed Customers], "Opted-in", [Opted-in Customers])'},
    {"id": "sm-013", "question": "Chiffre d'affaires par categorie de produit",
     "query": 'EVALUATE SUMMARIZECOLUMNS(products[category], "Units Sold", [Units Sold]) ORDER BY [Units Sold] DESC'},
    {"id": "sm-014", "question": "Quel est l'engagement email moyen et le NPS ?",
     "query": 'EVALUATE ROW("Avg Engagement", [Avg Engagement Rate], "Avg NPS", [Avg NPS])'},
    {"id": "sm-015", "question": "Donne-moi un resume de la sante client",
     "query": 'EVALUATE ROW("Customers", [Total Customers], "At Risk", [Customers at Risk], "Revenue at Risk", [Revenue at Risk], "Churned", [Churned Customers], "Avg Churn", [Avg Churn Score])'},
]


def ai_instructions(ontology_only: bool, culprit_name: str, at_risk: int) -> str:
    head = (
        "You are the Marketing Churn Agent for a retailer. You answer questions about customers, "
        "segments, campaigns, orders and CHURN RISK. ALWAYS answer by querying a source - NEVER "
        "from general knowledge. If a query returns nothing, say so explicitly rather than guessing.\n\n"
    )
    if ontology_only:
        return head + (
            "## Your only source: ONT_Customer360 (Ontology, GQL)\n"
            "Entities: Customer, Account, Segment, Campaign, Asset, Product, Order, Interaction.\n"
            "Relationships: CustomerInSegment, CustomerBelongsToAccount, CampaignTargetsSegment,\n"
            "CampaignHasAsset, CampaignSentToCustomer, OrderPlacedByCustomer,\n"
            "OrderAttributedToCampaign, OrderContainsProduct, InteractionWithCustomer.\n\n"
            "Node label = entity name, edge label = relationship name.\n"
            "Impact of a campaign: (c:Campaign)-[:CampaignSentToCustomer]->(cu:Customer).\n"
            "Root cause for a customer: traverse that edge in reverse.\n\n"
            "NOTE: the churn profile (churn_risk_score, clv_eur, engagement_rate) is NOT in this "
            "graph. If asked for it, say the data is not available in this source.\n\n"
            "## CRITICAL - results are truncated at 200 rows\n"
            "NEVER answer a 'how many' or 'how much' question by counting or summing the rows you "
            "received: the list is capped at 200 and the number would be wrong. Push the aggregate "
            "INTO the query - RETURN COUNT(DISTINCT cu.customer_id) / SUM(o.total_amount_eur) - and "
            "do NOT add GROUP BY when a single overall number is asked for.\n"
            "An edge count is NOT a node count: counting CampaignSentToCustomer edges gives sends, "
            "not customers.\n"
            "If the tool output says the result is incomplete, re-query with a scalar aggregate "
            "instead of reporting the partial figure.\n"
        )
    return head + (
        "## Two data sources - pick the right one for each question\n"
        "1. ONT_Customer360 (Ontology, GQL) - RELATIONSHIPS, ROOT CAUSE, IMPACT.\n"
        "   Which customers a campaign reached, which campaigns hit a customer, segment targeting,\n"
        "   what a customer bought, which account they belong to.\n"
        "   Entities: Customer, Account, Segment, Campaign, Asset, Product, Order, Interaction.\n"
        "   Relationships: CustomerInSegment, CustomerBelongsToAccount, CampaignTargetsSegment,\n"
        "   CampaignHasAsset, CampaignSentToCustomer, OrderPlacedByCustomer,\n"
        "   OrderAttributedToCampaign, OrderContainsProduct, InteractionWithCustomer.\n\n"
        "2. SM_Marketing_Analytics (Semantic Model, DAX) - EVERY NUMBER.\n"
        "   Churn: [Customers at Risk], [At Risk %], [Revenue at Risk], [CLV at Risk],\n"
        "   [Avg Churn Score], [Avg Recency (days)], [Churned Customers].\n"
        "   Pressure/funnel: [Total Sends], [Sends per Customer], [Opens], [Clicks],\n"
        "   [Unsubscribes], [Open Rate], [Click Through Rate], [Unsubscribe Rate].\n"
        "   Commerce: [Revenue], [Total Orders], [Average Order Value], [Units Sold].\n"
        "   Attribution: [Attributed Orders], [Attributed Revenue], [Attribution Rate], [Campaign ROI].\n"
        "   ALWAYS reuse these measures; never recompute from raw columns.\n\n"
        "## Routing rule\n"
        "A NUMBER / metric / ranking / aggregate -> Semantic Model (DAX).\n"
        "HOW things connect or WHO is affected -> Ontology (GQL).\n"
        "For 'detect then diagnose' questions: get the figure from the semantic model, then\n"
        "traverse the graph from the offending campaign for the impacted customers.\n\n"
        "## Ontology results are truncated at 200 rows\n"
        "NEVER derive a count or a total by counting/summing the rows a GQL query returned - the\n"
        "list is capped at 200 and the figure would be wrong. Numbers come from the semantic model.\n"
        "If you must aggregate in GQL, push COUNT(DISTINCT ...) / SUM(...) into the query and do\n"
        "NOT add GROUP BY. An edge count is NOT a node count.\n\n"
        "## Domain notes\n"
        f"- Churn applies to BUYERS only. A contact who never ordered is in the 'Prospect' band\n"
        f"  with no churn score - that is a conversion problem, not a churn problem.\n"
        f"- Actionable cohort = churn_risk_score >= {at_risk}.\n"
        f"- Known incident: the '{culprit_name}' campaign over-mailed its target segment, which\n"
        f"  triggered an unsubscribe spike, halved engagement and stopped orders. Marketing\n"
        f"  pressure ([Sends per Customer]) is the metric that exposes it.\n"
        "- Returns are recorded per ORDER, never per product line. There is therefore NO return\n"
        "  rate, refund total or return count per product or per category. If asked, say the data\n"
        "  is not tracked at that grain - do NOT report the overall rate as if it were per product.\n\n"
        "## Response format\n"
        "- Lead with a direct one-line answer, figures as digits.\n"
        "- Then a short bullet list of the values or entities found.\n"
        "- For impact questions, state the path you traversed.\n"
        "- Be concise and operational - your reader is a CRM / marketing lead."
    )


def b64(obj):
    return b64encode_json(obj)


def find_agent(api, ws, h, name):
    r = requests.get(f"{api}/workspaces/{ws}/items?type=DataAgent", headers=h, timeout=60)
    if r.status_code == 200:
        for it in r.json().get("value", []):
            if it.get("displayName") == name:
                return it["id"]
    return None


def build_parts(ws, ont_id, ont_name, sm_id, sm_name, agent_name, ontology_only, culprit, at_risk):
    ont_folder = f"ontology-{ont_name}"
    sm_folder = f"semantic-model-{sm_name}"
    data_agent = {"$schema": f"{SCH}/dataAgent/2.1.0/schema.json"}
    stage = {"$schema": f"{SCH}/stageConfiguration/1.0.0/schema.json",
             "aiInstructions": ai_instructions(ontology_only, culprit, at_risk)}

    ont_ds = {
        "$schema": f"{SCH}/dataSource/1.0.0/schema.json",
        "artifactId": ont_id, "workspaceId": ws, "displayName": ont_name, "type": "ontology",
        "userDescription": "Customer 360 knowledge graph: 8 entities and 9 relationships "
                           "(customers, segments, campaigns, orders, products, interactions).",
        "dataSourceInstructions": ONT_INSTRUCTIONS,
    }
    ont_fs = {"$schema": f"{SCH}/fewShots/1.0.0/schema.json",
              "fewShots": [{"id": str(uuid.uuid4()), "question": q, "query": g} for q, g in ONT_FEWSHOTS]}

    s = b64(stage)
    ont_ds_b, ont_fs_b = b64(ont_ds), b64(ont_fs)
    mode = "ontology-only" if ontology_only else "dual-source (ontology + semantic model)"
    pub = b64({"$schema": f"{SCH}/publishInfo/1.0.0/schema.json",
               "description": f"{agent_name} -- {mode} -- published {time.strftime('%Y-%m-%d')}"})

    def _p(path, payload):
        return {"path": path, "payload": payload, "payloadType": "InlineBase64"}

    parts = [
        _p("Files/Config/data_agent.json", b64(data_agent)),
        _p("Files/Config/draft/stage_config.json", s),
        _p(f"Files/Config/draft/{ont_folder}/datasource.json", ont_ds_b),
        _p(f"Files/Config/draft/{ont_folder}/fewshots.json", ont_fs_b),
        _p("Files/Config/publish_info.json", pub),
        _p("Files/Config/published/stage_config.json", s),
        _p(f"Files/Config/published/{ont_folder}/datasource.json", ont_ds_b),
        _p(f"Files/Config/published/{ont_folder}/fewshots.json", ont_fs_b),
    ]

    if not ontology_only:
        sm_ds = {
            "$schema": f"{SCH}/dataSource/1.0.0/schema.json",
            "artifactId": sm_id, "workspaceId": ws, "displayName": sm_name, "type": "semantic_model",
            "dataSourceInstructions": (
                "Use for ALL numbers and aggregates: churn cohort size, revenue at risk, churn "
                "scores, marketing pressure (sends per customer), funnel rates, unsubscribes, "
                "revenue, AOV, attribution and ROI. ALWAYS reuse the existing DAX measures; never "
                "recompute from raw columns. Group with marketing_campaigns[campaign_name], "
                "crm_customer_profile[risk_band], crm_customers[lifecycle_stage], orders[channel]."
            ),
            "elements": build_sm_elements(),
        }
        sm_fs = {"$schema": f"{SCH}/fewShots/1.0.0/schema.json", "fewShots": SM_FEWSHOTS}
        sm_ds_b, sm_fs_b = b64(sm_ds), b64(sm_fs)
        parts += [
            _p(f"Files/Config/draft/{sm_folder}/datasource.json", sm_ds_b),
            _p(f"Files/Config/draft/{sm_folder}/fewshots.json", sm_fs_b),
            _p(f"Files/Config/published/{sm_folder}/datasource.json", sm_ds_b),
            _p(f"Files/Config/published/{sm_folder}/fewshots.json", sm_fs_b),
        ]
    return parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ontology-only", action="store_true",
                    help="deploy with the ontology as the ONLY source (experiment)")
    ap.add_argument("--delete", action="store_true")
    args = ap.parse_args()

    cfg = load_config(); st = load_state()
    api = cfg["fabric_api_base"]; ws = st["workspace_id"]
    name = cfg["data_agent_name"]
    ont_id = st["ontology_id"]; ont_name = cfg["ontology_name"]
    sm_id = st.get("semantic_model_id"); sm_name = cfg["semantic_model_name"]
    culprit = cfg["storyline"]["culprit_campaign_name"]
    at_risk = cfg["churn_model"]["at_risk_threshold"]
    token = get_fabric_token(); h = fabric_headers(token)

    if args.delete:
        aid = st.get("data_agent_id") or find_agent(api, ws, h, name)
        if aid:
            requests.delete(f"{api}/workspaces/{ws}/items/{aid}", headers=h, timeout=60)
            print(f"deleted {aid}")
            st.pop("data_agent_id", None); save_state(st)
        else:
            print("no agent to delete")
        return

    if not args.ontology_only and not sm_id:
        print("Semantic model not deployed. Run deploy_semantic_model.py, or use --ontology-only.")
        sys.exit(1)

    mode = "ONTOLOGY-ONLY (experiment)" if args.ontology_only else "DUAL-SOURCE"
    print_step(1, 3, f"Create/Update Data Agent '{name}' — {mode}")
    parts = build_parts(ws, ont_id, ont_name, sm_id, sm_name, name,
                        args.ontology_only, culprit, at_risk)
    aid = st.get("data_agent_id") or find_agent(api, ws, h, name)
    if aid:
        print(f"   updating: {aid}  ({len(parts)} parts)")
        r = requests.post(f"{api}/workspaces/{ws}/items/{aid}/updateDefinition", headers=h,
                          json={"definition": {"parts": parts}}, timeout=180)
    else:
        print(f"   creating  ({len(parts)} parts)")
        r = requests.post(f"{api}/workspaces/{ws}/items", headers=h,
                          json={"displayName": name, "type": "DataAgent",
                                "description": AGENT_DESC,
                                "definition": {"parts": parts}}, timeout=180)
    if r.status_code in (200, 201):
        aid = r.json().get("id", aid)
        print("   ok")
    elif r.status_code == 202:
        op = r.headers.get("x-ms-operation-id")
        if op:
            poll_operation(token, api, op)
        if not aid:
            aid = find_agent(api, ws, h, name)
        print("   updated (202)")
    else:
        raise RuntimeError(f"Data Agent deploy failed ({r.status_code}): {r.text[:500]}")

    print_step(2, 3, "Persist state")
    st["data_agent_id"] = aid
    st["data_agent_mode"] = "ontology-only" if args.ontology_only else "dual-source"
    save_state(st)
    print(f"   data_agent_id = {aid}")

    print_step(3, 3, "Readback (confirm datasource types accepted)")
    d = requests.post(f"{api}/workspaces/{ws}/items/{aid}/getDefinition", headers=h, timeout=120)
    if d.status_code in (200, 202):
        if d.status_code == 202:
            op = d.headers.get("x-ms-operation-id")
            if op:
                poll_operation(token, api, op)
                d = requests.get(f"{api}/operations/{op}/result", headers=h, timeout=60)
        try:
            import base64 as _b64
            import json as _json
            for p in d.json().get("definition", {}).get("parts", []):
                if p["path"].endswith("datasource.json") and "/draft/" in p["path"]:
                    obj = _json.loads(_b64.b64decode(p["payload"]))
                    print(f"   datasource.type = {obj.get('type'):<16} artifactId = {obj.get('artifactId')}")
        except Exception as e:
            print(f"   (readback parse skipped: {e})")

    print(f"\nOK. Data Agent '{name}' deployed in {mode} mode.")
    if args.ontology_only:
        print("   Probe it with a NUMERIC question and read the trace — that is the experiment.")


if __name__ == "__main__":
    main()
