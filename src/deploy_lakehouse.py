#!/usr/bin/env python3
"""
Deploy the Customer 360 Lakehouse: create the item + upload every generated CSV to
OneLake Files/raw/<domain>/. Delta tables are created afterwards by deploy_setup_notebook.py.

OneLake upload uses a single reusable http.client.HTTPSConnection (3-step DFS:
PUT create -> PATCH append -> PATCH flush) — requests/urllib3 hang on OneLake DFS.
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

import http.client
import subprocess
from pathlib import Path

import requests
from helpers import (load_config, load_state, save_state, get_fabric_token,
                     fabric_headers, find_item, poll_operation, print_step,
                     ensure_tenant)

SRC = Path(__file__).parent
RAW = SRC.parent / "data" / "raw"
ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"
DOMAINS = ["crm", "marketing", "commerce"]


def storage_token() -> str:
    out = subprocess.check_output(
        ["az", "account", "get-access-token", "--resource", "https://storage.azure.com",
         "--query", "accessToken", "-o", "tsv"], shell=True)
    return out.decode().strip()


def _put(conn, hdr, path, data):
    """3-step DFS write: create -> append -> flush."""
    conn.request("PUT", path + "?resource=file", headers=hdr)
    conn.getresponse().read()
    h2 = dict(hdr); h2["Content-Type"] = "application/octet-stream"
    conn.request("PATCH", path + "?action=append&position=0", body=data, headers=h2)
    conn.getresponse().read()
    conn.request("PATCH", path + f"?action=flush&position={len(data)}", headers=hdr)
    r = conn.getresponse(); r.read()
    return r.status


def upload_csvs(ws_id, lh_id, token):
    conn = http.client.HTTPSConnection(ONELAKE_HOST, timeout=180)
    hdr = {"Authorization": f"Bearer {token}"}
    n = 0
    try:
        for domain in DOMAINS:
            d = RAW / domain
            if not d.exists():
                continue
            for csv in sorted(d.glob("*.csv")):
                data = csv.read_bytes()
                path = f"/{ws_id}/{lh_id}/Files/raw/{domain}/{csv.name}"
                status = _put(conn, hdr, path, data)
                n += 1
                print(f"   raw/{domain}/{csv.name:34} {len(data):>10,} bytes [{status}]")
    finally:
        conn.close()
    return n


def upload_text_corpus(ws_id, lh_id, token, limit_notes=400):
    """Upload the text corpus for AI transformations / RAG.

    Notes are capped by default: the demo only needs enough documents to show the pattern,
    and pushing thousands of tiny files over DFS one-by-one is slow for no added value.
    """
    conn = http.client.HTTPSConnection(ONELAKE_HOST, timeout=180)
    hdr = {"Authorization": f"Bearer {token}"}
    n = 0
    try:
        notes = sorted((RAW / "text" / "customer_knowledge_notes").glob("*.txt"))[:limit_notes]
        for f in notes:
            _put(conn, hdr, f"/{ws_id}/{lh_id}/Files/raw/text/customer_knowledge_notes/{f.name}",
                 f.read_bytes())
            n += 1
        mails = sorted((RAW / "text" / "email_bodies").glob("*.txt"))
        for f in mails:
            _put(conn, hdr, f"/{ws_id}/{lh_id}/Files/raw/text/email_bodies/{f.name}", f.read_bytes())
            n += 1
        print(f"   text corpus: {len(notes)} notes + {len(mails)} email bodies")
    finally:
        conn.close()
    return n


def main():
    cfg = load_config(); state = load_state()
    ensure_tenant(cfg)
    api = cfg["fabric_api_base"]; ws = state["workspace_id"]; name = cfg["lakehouse_name"]
    token = get_fabric_token(); h = fabric_headers(token)

    if not RAW.exists() or not any((RAW / d).glob("*.csv") for d in DOMAINS if (RAW / d).exists()):
        print("No generated data found. Run generate_data.py first.")
        sys.exit(1)

    print_step(1, 4, f"Create or find Lakehouse '{name}'")
    lh_id = None
    try:
        lh_id = find_item(token, api, ws, name, "Lakehouse")["id"]
        print(f"   reusing: {lh_id}")
    except RuntimeError:
        r = requests.post(f"{api}/workspaces/{ws}/items", headers=h,
                          json={"displayName": name, "type": "Lakehouse",
                                "description": "Customer 360 — CRM + Marketing + Commerce"}, timeout=60)
        if r.status_code in (200, 201):
            lh_id = r.json()["id"]
        elif r.status_code == 202:
            op = r.headers.get("x-ms-operation-id")
            if op:
                poll_operation(token, api, op)
            lh_id = find_item(token, api, ws, name, "Lakehouse")["id"]
        else:
            raise RuntimeError(f"Create Lakehouse failed ({r.status_code}): {r.text[:300]}")
        print(f"   created: {lh_id}")

    stok = storage_token()

    print_step(2, 4, "Upload CSVs to OneLake Files/raw/<domain>/")
    n_csv = upload_csvs(ws, lh_id, stok)

    print_step(3, 4, "Upload text corpus")
    upload_text_corpus(ws, lh_id, stok)

    print_step(4, 4, "Persist state (+ SQL endpoint)")
    det = requests.get(f"{api}/workspaces/{ws}/lakehouses/{lh_id}", headers=h, timeout=60).json()
    sql = det.get("properties", {}).get("sqlEndpointProperties", {}).get("connectionString")
    state["lakehouse_id"] = lh_id
    if sql:
        state["lakehouse_sql_endpoint"] = sql
    save_state(state)
    print(f"   lakehouse_id = {lh_id}  ({n_csv} CSVs uploaded)")
    print("\nOK. Next: deploy_setup_notebook.py to create the Delta tables.")


if __name__ == "__main__":
    main()
