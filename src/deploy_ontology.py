#!/usr/bin/env python3
"""
Deploy the Customer 360 Ontology (Fabric IQ) — 8 entity types + 9 relationships.

DELIBERATELY NO TimeSeries BINDINGS.
    Two sister projects proved that the Fabric IQ TimeSeries query path returns EMPTY for a
    Data Agent: the GQL entitySelector resolves, the timeSeriesSelector comes back with 0 rows,
    whether bound to a KustoTable or a Lakehouse table, and even after RefreshGraph.
    So this ontology models RELATIONSHIPS only. Every NUMBER is answered by the Direct Lake
    semantic model (DAX) — see deploy_data_agent.py for the dual-source routing.

What the graph is for here:
    * RCA        : an at-risk customer -> which campaigns targeted them -> which over-mailed
    * Impact     : a campaign -> the segments it targets -> the customers -> their orders
    * Blast radius of a marketing decision, in one traversal

Run deploy_setup_notebook.py first (the Delta tables must exist).
Then run deploy_graph.py (build + push the graph definition + RefreshGraph).
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

import base64
import hashlib
import json
import uuid

import requests
from helpers import (get_fabric_token, fabric_headers, load_config, load_state,
                     save_state, poll_operation, find_item, ensure_tenant)

VT = {"string": "String", "int64": "BigInt", "double": "Double",
      "datetime": "DateTime", "bool": "Boolean"}

# (name, lakehouse_table, key_cols[], cols=[(col, type)])
ENTITIES = [
    ("Customer", "crm_customers", ["customer_id"], [
        ("customer_id", "string"), ("account_id", "string"), ("first_name", "string"),
        ("last_name", "string"), ("email", "string"), ("city", "string"),
        ("customer_type", "string"), ("lifecycle_stage", "string"),
        ("consent_email", "bool"), ("status", "string")]),
    ("Account", "crm_accounts", ["account_id"], [
        ("account_id", "string"), ("account_name", "string"), ("industry", "string"),
        ("city", "string"), ("employees", "int64")]),
    ("Segment", "crm_segments", ["segment_id"], [
        ("segment_id", "string"), ("segment_name", "string"), ("definition", "string"),
        ("is_premium", "bool")]),
    ("Campaign", "marketing_campaigns", ["campaign_id"], [
        ("campaign_id", "string"), ("campaign_name", "string"), ("objective", "string"),
        ("channel", "string"), ("budget_eur", "int64")]),
    ("Asset", "marketing_assets", ["asset_id"], [
        ("asset_id", "string"), ("campaign_id", "string"), ("variant", "string"),
        ("subject_line", "string"), ("cta", "string")]),
    ("Product", "products", ["product_id"], [
        ("product_id", "string"), ("product_name", "string"), ("category", "string"),
        ("unit_price_eur", "double"), ("margin_pct", "double")]),
    ("Order", "orders", ["order_id"], [
        ("order_id", "string"), ("customer_id", "string"), ("total_amount_eur", "double"),
        ("channel", "string"), ("attributed_campaign_id", "string")]),
    ("Interaction", "crm_interactions", ["interaction_id"], [
        ("interaction_id", "string"), ("customer_id", "string"), ("interaction_type", "string"),
        ("channel", "string"), ("sentiment", "string"), ("is_resolved", "bool")]),
]

# (name, source_entity, target_entity, fk_table, source_key_cols[], target_fk_cols[])
RELATIONSHIPS = [
    ("CustomerInSegment",        "Customer",    "Segment",  "crm_customer_segments", ["customer_id"],    ["segment_id"]),
    ("CustomerBelongsToAccount", "Customer",    "Account",  "crm_customers",         ["customer_id"],    ["account_id"]),
    ("CampaignTargetsSegment",   "Campaign",    "Segment",  "marketing_audiences",   ["campaign_id"],    ["segment_id"]),
    ("CampaignHasAsset",         "Campaign",    "Asset",    "marketing_assets",      ["campaign_id"],    ["asset_id"]),
    # THE demo edge: which campaigns actually reached which customers (pressure / fatigue).
    ("CampaignSentToCustomer",   "Campaign",    "Customer", "marketing_sends",       ["campaign_id"],    ["customer_id"]),
    ("OrderPlacedByCustomer",    "Order",       "Customer", "orders",                ["order_id"],       ["customer_id"]),
    ("OrderAttributedToCampaign", "Order",      "Campaign", "orders",                ["order_id"],       ["attributed_campaign_id"]),
    ("OrderContainsProduct",     "Order",       "Product",  "order_lines",           ["order_id"],       ["product_id"]),
    ("InteractionWithCustomer",  "Interaction", "Customer", "crm_interactions",      ["interaction_id"], ["customer_id"]),
]


def det_guid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.md5(seed.encode("utf-8")).digest()))


def b64(obj) -> str:
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


def print_step(n, total, msg):
    print(f"\n[{n}/{total}] {msg}\n" + "-" * 60)


def build_parts(workspace_id, lakehouse_id, ontology_name):
    et_id, prop_id, key_prop = {}, {}, {}
    for i, (name, table, keys, cols) in enumerate(ENTITIES):
        eid = str(1001 + i); et_id[name] = eid
        base = 10000 + i * 100
        for j, (col, _t) in enumerate(cols):
            prop_id[(name, col)] = str(base + 1 + j)
        key_prop[name] = [prop_id[(name, k)] for k in keys]

    parts = []
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Ontology", "displayName": ontology_name,
                     "description": "Customer 360 knowledge graph "
                                    f"({len(ENTITIES)} entities, {len(RELATIONSHIPS)} relationships). "
                                    "Relationships only — numbers come from the semantic model."},
        "config": {"version": "2.0", "logicalId": det_guid("ONT-FMC-logicalId")},
    }
    parts.append({"path": ".platform", "payload": b64(platform), "payloadType": "InlineBase64"})
    parts.append({"path": "definition.json", "payload": b64({}), "payloadType": "InlineBase64"})

    for name, table, keys, cols in ENTITIES:
        eid = et_id[name]
        non_key_str = [c for c, t in cols if t == "string" and c not in keys]
        disp_col = non_key_str[0] if non_key_str else keys[0]
        properties = [{"id": prop_id[(name, c)], "name": c, "redefines": None,
                       "baseTypeNamespaceType": None, "valueType": VT[t]} for c, t in cols]
        entity_def = {
            "id": eid, "namespace": "usertypes", "baseEntityTypeId": None, "name": name,
            "entityIdParts": key_prop[name], "displayNamePropertyId": prop_id[(name, disp_col)],
            "namespaceType": "Custom", "visibility": "Visible",
            "properties": properties, "timeseriesProperties": [],
        }
        parts.append({"path": f"EntityTypes/{eid}/definition.json",
                      "payload": b64(entity_def), "payloadType": "InlineBase64"})
        bind_guid = det_guid(f"NonTimeSeries-{eid}")
        binding = {"id": bind_guid, "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "propertyBindings": [{"sourceColumnName": c, "targetPropertyId": prop_id[(name, c)]}
                                 for c, _t in cols],
            "sourceTableProperties": {"sourceType": "LakehouseTable", "workspaceId": workspace_id,
                                      "itemId": lakehouse_id, "sourceTableName": table,
                                      "sourceSchema": "dbo"}}}
        parts.append({"path": f"EntityTypes/{eid}/DataBindings/{bind_guid}.json",
                      "payload": b64(binding), "payloadType": "InlineBase64"})

    for k, (rname, src, tgt, fk_table, src_keys, tgt_fks) in enumerate(RELATIONSHIPS):
        rid = str(3001 + k)
        rel_def = {"namespace": "usertypes", "id": rid, "name": rname, "namespaceType": "Custom",
                   "source": {"entityTypeId": et_id[src]}, "target": {"entityTypeId": et_id[tgt]}}
        parts.append({"path": f"RelationshipTypes/{rid}/definition.json",
                      "payload": b64(rel_def), "payloadType": "InlineBase64"})
        ctx_guid = det_guid(f"Ctx-{rid}")
        src_refs = [{"sourceColumnName": col, "targetPropertyId": key_prop[src][i]}
                    for i, col in enumerate(src_keys)]
        tgt_refs = [{"sourceColumnName": col, "targetPropertyId": key_prop[tgt][i]}
                    for i, col in enumerate(tgt_fks)]
        ctx = {"id": ctx_guid,
               "dataBindingTable": {"workspaceId": workspace_id, "itemId": lakehouse_id,
                                    "sourceTableName": fk_table, "sourceSchema": "dbo",
                                    "sourceType": "LakehouseTable"},
               "sourceKeyRefBindings": src_refs, "targetKeyRefBindings": tgt_refs}
        parts.append({"path": f"RelationshipTypes/{rid}/Contextualizations/{ctx_guid}.json",
                      "payload": b64(ctx), "payloadType": "InlineBase64"})

    return parts


def main():
    cfg = load_config(); state = load_state()
    ensure_tenant(cfg)
    api = cfg["fabric_api_base"]; ws = state["workspace_id"]; lh = state["lakehouse_id"]
    name = cfg["ontology_name"]
    token = get_fabric_token(); headers = fabric_headers(token)

    print(f"Deploying Ontology '{name}' — {len(ENTITIES)} entities, {len(RELATIONSHIPS)} relationships")

    print_step(1, 4, "Build definition parts")
    parts = build_parts(ws, lh, name)
    print(f"   {len(parts)} parts")

    print_step(2, 4, "Create or find Ontology item")
    ont_id = state.get("ontology_id")
    if ont_id:
        try:
            find_item(token, api, ws, name, "Ontology")
        except RuntimeError:
            ont_id = None
    if not ont_id:
        try:
            ont_id = find_item(token, api, ws, name, "Ontology")["id"]
        except RuntimeError:
            r = requests.post(f"{api}/workspaces/{ws}/items", headers=headers,
                              json={"displayName": name, "type": "Ontology",
                                    "description": "Customer 360 knowledge graph (Fabric IQ)"})
            if r.status_code in (200, 201):
                ont_id = r.json()["id"]
            elif r.status_code == 202:
                op = r.headers.get("x-ms-operation-id")
                if op:
                    poll_operation(token, api, op)
                ont_id = find_item(token, api, ws, name, "Ontology")["id"]
            else:
                raise RuntimeError(f"Create Ontology failed ({r.status_code}): {r.text[:400]}")
        print(f"   id: {ont_id}")
    else:
        print(f"   reusing: {ont_id}")

    print_step(3, 4, "Push full definition (updateDefinition)")
    resp = requests.post(f"{api}/workspaces/{ws}/items/{ont_id}/updateDefinition",
                         headers=headers, json={"definition": {"parts": parts}}, timeout=180)
    if resp.status_code in (200, 201):
        print("   accepted")
    elif resp.status_code == 202:
        op = resp.headers.get("x-ms-operation-id")
        if op:
            poll_operation(token, api, op)
        print("   accepted (async)")
    else:
        raise RuntimeError(f"updateDefinition failed ({resp.status_code}): {resp.text[:600]}")

    print_step(4, 4, "Persist state")
    state["ontology_id"] = ont_id
    save_state(state)
    print(f"   ontology_id = {ont_id}")
    print("\nOK. Next: deploy_graph.py (build + push graph definition + RefreshGraph).")


if __name__ == "__main__":
    main()
