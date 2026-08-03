#!/usr/bin/env python3
"""
Build + run the setup notebook that turns Files/raw/<domain>/*.csv into Delta tables.

Also materialises two curated views the whole demo leans on:
  * v_churn_cohort      — the actionable at-risk customers, with their drivers
  * v_campaign_pressure — sends per customer per campaign (exposes the CAMP_007 over-mailing)

Idempotent: the notebook is recreated and re-run; Delta writes use overwrite.
"""
import os, sys
# The venv activation on this project's Windows machines can wipe PATH, so the registry
# copy is read back. That fix is Windows-only and `winreg` does not exist elsewhere, so an
# unconditional import made this module unimportable on Linux - and the tests import it.
if sys.platform == "win32":
    import winreg



def _restore_path():
    if sys.platform != "win32":
        return
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

from pathlib import Path

from helpers import (load_config, load_state, save_state, get_fabric_token, print_step,
                     ensure_tenant)
from notebook_utils import recreate_notebook, run_notebook

NOTEBOOK_NAME = "NB_Setup_Customer360"
RAW = Path(__file__).parent.parent / "data" / "raw"
DOMAINS = ["crm", "marketing", "commerce"]


def discover_tables():
    """(domain, table_name) for every generated CSV."""
    out = []
    for d in DOMAINS:
        folder = RAW / d
        if folder.exists():
            for csv in sorted(folder.glob("*.csv")):
                out.append((d, csv.stem))
    return out


def build_notebook_py(ws_id, lh_id, lh_name, tables, at_risk_threshold, culprit):
    pairs = ", ".join(f'("{d}", "{t}")' for d, t in tables)
    return f'''# Fabric notebook source

# METADATA ********************

# META {{
# META   "kernel_info": {{
# META     "name": "synapse_pyspark"
# META   }},
# META   "dependencies": {{
# META     "lakehouse": {{
# META       "default_lakehouse": "{lh_id}",
# META       "default_lakehouse_name": "{lh_name}",
# META       "default_lakehouse_workspace_id": "{ws_id}"
# META     }}
# META   }}
# META }}

# MARKDOWN ********************

# # NB_Setup_Customer360 - CSV (Files/raw) -> Delta tables + curated churn views

# CELL ********************

pairs = [{pairs}]
created = []
for domain, t in pairs:
    df = spark.read.csv(f"Files/raw/{{domain}}/{{t}}.csv", header=True, inferSchema=True)
    df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(t)
    n = df.count()
    created.append((t, n))
    print(f"{{t}}: {{n}} rows")

print("TABLES DONE", created)

# CELL ********************

# Curated view 1 - the actionable churn cohort with its drivers.
# Everything here comes from behaviour; nothing is a random label.
spark.sql("""
CREATE OR REPLACE VIEW v_churn_cohort AS
SELECT
    p.customer_id,
    c.first_name,
    c.last_name,
    c.city,
    c.customer_type,
    c.lifecycle_stage,
    p.churn_risk_score,
    p.risk_band,
    p.clv_eur,
    p.nps_last,
    p.total_orders,
    p.total_spend_eur,
    p.days_since_last_order,
    p.orders_90d,
    p.orders_prev_90d,
    p.engagement_rate,
    p.unsubscribed,
    p.unresolved_interactions
FROM crm_customer_profile p
JOIN crm_customers c ON c.customer_id = p.customer_id
WHERE p.is_customer = true
  AND p.churn_risk_score >= {at_risk_threshold}
""")
print("v_churn_cohort:", spark.table("v_churn_cohort").count(), "customers at risk")

# CELL ********************

# Curated view 2 - marketing pressure per customer per campaign.
# This is what exposes the root cause: {culprit} sends far more than any other campaign.
spark.sql("""
CREATE OR REPLACE VIEW v_campaign_pressure AS
SELECT
    s.campaign_id,
    ca.campaign_name,
    ca.objective,
    COUNT(*)                              AS sends,
    COUNT(DISTINCT s.customer_id)         AS customers,
    ROUND(COUNT(*) / COUNT(DISTINCT s.customer_id), 2) AS sends_per_customer,
    SUM(CASE WHEN e.event_type = 'unsubscribe' THEN 1 ELSE 0 END) AS unsubscribes
FROM marketing_sends s
JOIN marketing_campaigns ca ON ca.campaign_id = s.campaign_id
LEFT JOIN marketing_events e ON e.send_id = s.send_id
GROUP BY s.campaign_id, ca.campaign_name, ca.objective
""")
display(spark.sql("SELECT * FROM v_campaign_pressure ORDER BY sends_per_customer DESC"))

# CELL ********************

print("DONE")
'''


def main():
    cfg = load_config(); state = load_state()
    ensure_tenant(cfg)
    ws = state["workspace_id"]; lh = state["lakehouse_id"]; lh_name = cfg["lakehouse_name"]
    token = get_fabric_token()

    tables = discover_tables()
    if not tables:
        print("No generated CSVs found. Run generate_data.py first.")
        sys.exit(1)

    print_step(1, 3, f"Build + (re)create notebook '{NOTEBOOK_NAME}' ({len(tables)} tables)")
    py = build_notebook_py(ws, lh, lh_name, tables,
                           cfg["churn_model"]["at_risk_threshold"],
                           cfg["storyline"]["culprit_campaign_id"])
    nb_id = recreate_notebook(ws, NOTEBOOK_NAME, py, token)
    print(f"   notebook_id = {nb_id}")

    print_step(2, 3, "Run notebook (Spark cold start ~60-90s)")
    run_notebook(ws, nb_id, token, max_wait=1200, poll_interval=20)
    print("   notebook completed")

    print_step(3, 3, "Persist state")
    state["notebook_setup_id"] = nb_id
    save_state(state)
    print("   saved notebook_setup_id")
    print("\nOK. Delta tables + curated churn views created.")


if __name__ == "__main__":
    main()
