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

import base64
import json
import requests

from helpers import (load_config, load_state, save_state, get_fabric_token, print_step,
                     ensure_tenant, raw_dir, profile_dir, find_item, fabric_headers,
                     poll_operation, ItemNotFoundError)
from notebook_utils import recreate_notebook, run_notebook, create_notebook, push_notebook

NOTEBOOK_NAME = "NB_Setup_Customer360"
RAW = raw_dir()
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


def notebook_binding(source):
    lines, started = [], False
    for line in source.splitlines():
        if line.startswith("# META "):
            lines.append(line.removeprefix("# META "))
            started = True
        elif started:
            break
    if not lines:
        raise RuntimeError("Cannot verify existing notebook Lakehouse metadata")
    metadata = json.loads("\n".join(lines))
    return metadata.get("dependencies", {}).get("lakehouse", {})


def verify_notebook_binding(cfg, state, notebook_id, token):
    api, ws = cfg["fabric_api_base"], state["workspace_id"]
    headers = fabric_headers(token)
    response = requests.post(f"{api}/workspaces/{ws}/notebooks/{notebook_id}/getDefinition",
                             headers=headers, timeout=120)
    if response.status_code == 202:
        operation = response.headers.get("x-ms-operation-id")
        if not operation:
            raise RuntimeError("Notebook definition operation has no operation ID")
        poll_operation(token, api, operation)
        response = requests.get(f"{api}/operations/{operation}/result", headers=headers, timeout=120)
    response.raise_for_status()
    parts = response.json().get("definition", {}).get("parts", [])
    source = next((part for part in parts if part["path"] in
                   ("notebook-content.py", "notebook-content.ipynb")), None)
    if source is None:
        raise RuntimeError("Existing notebook has no inspectable Fabric source; refusing to update it")
    decoded = base64.b64decode(source["payload"]).decode("utf-8")
    if source["path"].endswith(".py"):
        binding = notebook_binding(decoded)
    else:
        metadata = json.loads(decoded).get("metadata", {})
        bindings = [metadata.get(key, {}).get("lakehouse", {})
                    for key in ("dependencies", "trident")]
        bindings = [binding for binding in bindings if binding]
        if not bindings or any(binding != bindings[0] for binding in bindings[1:]):
            raise RuntimeError("Notebook Lakehouse metadata is missing or contradictory")
        binding = bindings[0]
    expected = {"default_lakehouse": state["lakehouse_id"],
                "default_lakehouse_name": cfg["lakehouse_name"],
                "default_lakehouse_workspace_id": ws}
    if any(binding.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Existing notebook is bound to another Lakehouse or workspace")


def ensure_setup_notebook(cfg, state, source, token):
    ws = state["workspace_id"]
    if profile_dir() is None:
        return recreate_notebook(ws, NOTEBOOK_NAME, source, token)
    try:
        existing = find_item(token, cfg["fabric_api_base"], ws, NOTEBOOK_NAME, "Notebook")
    except ItemNotFoundError as exc:
        if state.get("notebook_setup_id"):
            raise RuntimeError("Recorded setup notebook is missing; refusing to replace its identity") from exc
        notebook_id = create_notebook(ws, NOTEBOOK_NAME, source, token)
        state["notebook_setup_id"] = notebook_id
        save_state(state)
    else:
        notebook_id = existing["id"]
        if state.get("notebook_setup_id") != notebook_id:
            raise RuntimeError("Existing setup notebook is not owned by this profile")
        verify_notebook_binding(cfg, state, notebook_id, token)
        push_notebook(ws, notebook_id, source, token)
    verify_notebook_binding(cfg, state, notebook_id, token)
    return notebook_id


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
    n = spark.table(t).count()
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
    nb_id = ensure_setup_notebook(cfg, state, py, token)
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
