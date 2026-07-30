#!/usr/bin/env python3
"""
Populate the Customer 360 ontology's Graph Model definition directly (the API workaround).

Deploying the ontology via REST does NOT generate the child Graph Model — this builds the
graph definition (graphType + dataSources + graphDefinition) from the SAME entity/relationship
model as deploy_ontology.py, pushes it via updateDefinition, then runs RefreshGraph to ingest.

dataSource paths use the REAL OneLake locations from GET /lakehouses/{lh}/tables.
"""
import os, sys, winreg, json, base64, hashlib, uuid, time
def _restore_path():
    parts = []
    for root, sub in [(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")]:
        try:
            k = winreg.OpenKey(root, sub); v, _ = winreg.QueryValueEx(k, "Path")
            parts.append(os.path.expandvars(v)); winreg.CloseKey(k)
        except Exception: pass
    if parts: os.environ["PATH"] = ";".join(parts) + ";" + os.environ.get("PATH", "")
_restore_path()
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")

import requests
from helpers import get_fabric_token, fabric_headers, load_config, load_state
from deploy_ontology import ENTITIES, RELATIONSHIPS

GT = {"string": "STRING", "int64": "INT", "double": "FLOAT", "datetime": "ZONED DATETIME", "bool": "BOOLEAN"}


def b64(obj):
    return base64.b64encode(json.dumps(obj, indent=1).encode("utf-8")).decode("ascii")


def alias(seed):
    return str(int(hashlib.md5(seed.encode()).hexdigest()[:15], 16))


def guid(seed):
    return str(uuid.UUID(bytes=hashlib.md5(seed.encode()).digest()))


def find_graph_model(api, ws, headers, substr):
    items = requests.get(f"{api}/workspaces/{ws}/items", headers=headers, timeout=60).json().get("value", [])
    for it in items:
        if it.get("type") == "GraphModel" and substr in (it.get("displayName") or ""):
            return it["id"], it["displayName"]
    raise RuntimeError("Graph Model item not found (deploy the ontology first)")


def table_locations(api, ws, lh, headers):
    r = requests.get(f"{api}/workspaces/{ws}/lakehouses/{lh}/tables", headers=headers, timeout=60)
    rows = r.json().get("data") or r.json().get("value") or []
    return {row["name"]: row["location"] for row in rows}


def build_definition(lh, locations):
    tbl_cols = {table: {c: t for c, t in cols} for _, table, _, cols in ENTITIES}
    node_alias, node_types, node_tables = {}, [], []
    data_sources = {}

    def ds_name(table): return f"{lh}_{table}"

    def add_ds(table):
        name = ds_name(table)
        if name not in data_sources:
            path = locations.get(table)
            if not path:
                raise RuntimeError(f"Table '{table}' not in lakehouse OneLake tables")
            data_sources[name] = {"name": name, "type": "DeltaTable", "properties": {"path": path}}
        return name

    for name, table, keys, cols in ENTITIES:
        a = alias(f"node-{name}"); node_alias[name] = a
        node_types.append({"primaryKeyProperties": list(keys), "alias": a, "labels": [name],
                           "properties": [{"name": c, "type": GT[t]} for c, t in cols]})
        add_ds(table)
        node_tables.append({"nodeTypeAlias": a, "id": guid(f"nodetable-{name}"), "dataSourceName": ds_name(table),
                            "propertyMappings": [{"propertyName": c, "sourceColumn": c} for c, _t in cols]})

    edge_types, edge_tables = [], []
    for rname, src, tgt, fk_table, src_keys, tgt_fks in RELATIONSHIPS:
        a = alias(f"edge-{rname}")
        props, seen = [], set()
        for c in list(src_keys) + list(tgt_fks):
            if c not in seen:
                seen.add(c); t = tbl_cols.get(fk_table, {}).get(c, "string")
                props.append({"name": c, "type": GT[t]})
        edge_types.append({"sourceNodeType": {"alias": node_alias[src]}, "alias": a,
                           "destinationNodeType": {"alias": node_alias[tgt]}, "labels": [rname], "properties": props})
        add_ds(fk_table)
        edge_tables.append({"edgeTypeAlias": a, "id": guid(f"edgetable-{rname}"), "edgeIdMapping": None,
                            "dataSourceName": ds_name(fk_table), "sourceNodeKeyColumns": list(src_keys),
                            "propertyMappings": [{"propertyName": p["name"], "sourceColumn": p["name"]} for p in props],
                            "destinationNodeKeyColumns": list(tgt_fks)})

    base = "https://developer.microsoft.com/json-schemas/fabric/item/graphInstance/definition"
    gt = {"$schema": f"{base}/graphType/1.0.0/schema.json", "nodeTypes": node_types, "edgeTypes": edge_types}
    ds = {"$schema": f"{base}/dataSources/1.0.0/schema.json", "dataSources": list(data_sources.values())}
    gd = {"$schema": f"{base}/graphDefinition/1.0.0/schema.json", "nodeTables": node_tables, "edgeTables": edge_tables}
    st = {"$schema": f"{base}/stylingConfiguration/1.0.0/schema.json",
          "modelLayout": {"positions": {}, "styles": {}, "pan": {"x": 0.0, "y": 0.0}, "zoomLevel": 1.0},
          "visualFormat": None, "scenario": "Ontology"}
    return gt, ds, gd, st


def main():
    cfg = load_config(); state = load_state()
    api = cfg["fabric_api_base"]; ws = state["workspace_id"]; lh = state["lakehouse_id"]
    substr = cfg["ontology_name"] + "_graph"
    token = get_fabric_token(); headers = fabric_headers(token)

    print("Locating Graph Model item...")
    gid, gname = find_graph_model(api, ws, headers, substr)
    print(f"   {gname} ({gid})")

    print("Fetching real OneLake table locations...")
    locations = table_locations(api, ws, lh, headers)
    print(f"   {len(locations)} tables")

    gt, ds, gd, st = build_definition(lh, locations)
    print(f"   {len(gt['nodeTypes'])} node types, {len(gt['edgeTypes'])} edge types, {len(ds['dataSources'])} data sources")

    platform = {"$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                "metadata": {"type": "GraphModel", "displayName": gname},
                "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"}}
    parts = [{"path": "graphType.json", "payload": b64(gt), "payloadType": "InlineBase64"},
             {"path": "dataSources.json", "payload": b64(ds), "payloadType": "InlineBase64"},
             {"path": "graphDefinition.json", "payload": b64(gd), "payloadType": "InlineBase64"},
             {"path": "stylingConfiguration.json", "payload": b64(st), "payloadType": "InlineBase64"},
             {"path": ".platform", "payload": b64(platform), "payloadType": "InlineBase64"}]

    print("Pushing graph definition (updateDefinition)...")
    r = requests.post(f"{api}/workspaces/{ws}/items/{gid}/updateDefinition",
                      headers=headers, json={"definition": {"parts": parts}}, timeout=120)
    print(f"   HTTP {r.status_code}")
    if r.status_code == 202:
        loc = r.headers.get("Location") or r.headers.get("Operation-Location")
        if loc:
            for _ in range(40):
                time.sleep(3); stt = requests.get(loc, headers=headers).json().get("status")
                if stt in ("Succeeded", "Completed", "Failed"):
                    print(f"   op: {stt}")
                    if stt == "Failed":
                        print("   ", requests.get(loc, headers=headers).text[:400])
                    break
    elif r.status_code not in (200, 201):
        raise RuntimeError(f"updateDefinition failed: {r.status_code} {r.text[:500]}")

    print("Triggering RefreshGraph...")
    jr = requests.post(f"{api}/workspaces/{ws}/items/{gid}/jobs/instances?jobType=RefreshGraph",
                       headers=headers, json={}, timeout=60)
    print(f"   HTTP {jr.status_code}")
    loc = jr.headers.get("Location")
    if jr.status_code == 202 and loc:
        for _ in range(80):
            time.sleep(5); j = requests.get(loc, headers=headers).json(); stt = j.get("status")
            print(f"   refresh: {stt}")
            if stt in ("Completed", "Failed", "Cancelled", "Deduped"):
                if stt == "Failed": print("   failure:", j.get("failureReason"))
                break
    state["graph_model_id"] = gid
    from helpers import save_state
    save_state(state)
    print("\nDone.")


if __name__ == "__main__":
    main()
