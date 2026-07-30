#!/usr/bin/env python3
"""
Shared helpers for Network Operations deployment scripts.
Authentication, async polling, config/state, Fabric items, Kusto (Eventhouse).
Reused from the proven Financial_Platform pattern.
"""

import base64
import json
import subprocess
import sys
import time
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

import requests

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.yaml"
STATE_FILE = SCRIPT_DIR / "state.json"


def load_config() -> Dict[str, Any]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> Dict[str, Any]:
    """Load deployment state (IDs created so far)."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: Dict[str, Any]):
    """Persist deployment state."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def ensure_tenant(cfg: Optional[Dict[str, Any]] = None, quiet: bool = False):
    """Pin az to the right subscription/tenant (az silently flips to corp).

    Every script that talks to Fabric or Power BI needs this, not just the
    orchestrator. On the wrong tenant the token is perfectly valid — it just
    belongs to another directory — so the symptom is an authorisation error on
    a resource the identity genuinely cannot see. The Fabric API answers 404
    EntityNotFound, the Power BI REST API answers **401 with an empty body**.
    That 401 reads exactly like an expired token and sends you diagnosing auth
    instead of identity; it once turned a healthy report into a fake 0/35.
    """
    cfg = cfg if cfg is not None else load_config()
    sub = cfg.get("az_subscription")
    if not sub:
        print("!  No 'az_subscription' in config.yaml — ensure az is on the correct tenant "
              "(404 EntityNotFound, or 401 on Power BI, = wrong tenant).")
        return
    try:
        subprocess.run(["az", "account", "set", "--subscription", sub],
                       shell=True, check=True, capture_output=True)
        if not quiet:
            print(f"OK  az subscription set to '{sub}'")
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode(errors="replace").strip()
        raise RuntimeError(f"Could not set az subscription '{sub}': {detail or e}")


def get_fabric_token() -> str:
    """Get Fabric API access token via Azure CLI."""
    result = subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", "https://api.fabric.microsoft.com",
         "--query", "accessToken", "-o", "tsv"],
        shell=True
    )
    return result.decode().strip()


def get_powerbi_token() -> str:
    """Get a Power BI API token (dataset refresh, executeQueries).

    The Fabric token does not work against api.powerbi.com — the refresh and
    DAX endpoints need the analysis.windows.net audience.
    """
    result = subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", "https://analysis.windows.net/powerbi/api",
         "--query", "accessToken", "-o", "tsv"],
        shell=True
    )
    return result.decode().strip()


def get_kusto_token(query_service_uri: str) -> str:
    """Get Kusto token, trying multiple scopes."""
    scopes = [
        query_service_uri,
        "https://kusto.kusto.windows.net",
        "https://help.kusto.windows.net",
        "https://api.fabric.microsoft.com",
    ]
    for scope in scopes:
        try:
            result = subprocess.check_output(
                ["az", "account", "get-access-token",
                 "--resource", scope,
                 "--query", "accessToken", "-o", "tsv"],
                shell=True
            )
            token = result.decode().strip()
            if token:
                return token
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError("Could not acquire Kusto token with any scope")


def fabric_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def poll_operation(token: str, api_base: str, operation_id: str,
                   max_wait: int = 120) -> Dict:
    """Poll an async Fabric operation until completion."""
    headers = fabric_headers(token)
    for _ in range(max_wait // 5):
        time.sleep(5)
        resp = requests.get(f"{api_base}/operations/{operation_id}",
                            headers=headers)
        resp.raise_for_status()
        op = resp.json()
        status = op.get("status", "")
        if status == "Succeeded":
            return op
        if status in ("Failed", "Cancelled"):
            raise RuntimeError(f"Operation {status}: {op.get('error', {})}")
    raise TimeoutError(f"Operation {operation_id} did not complete in {max_wait}s")


def create_fabric_item(token: str, api_base: str, workspace_id: str,
                       display_name: str, item_type: str,
                       description: str = "",
                       definition: Optional[Dict] = None) -> Dict:
    """Create a Fabric item and poll until complete."""
    headers = fabric_headers(token)
    body: Dict[str, Any] = {
        "displayName": display_name,
        "type": item_type,
    }
    if description:
        body["description"] = description
    if definition:
        body["definition"] = definition

    resp = requests.post(
        f"{api_base}/workspaces/{workspace_id}/items",
        headers=headers, json=body
    )

    if resp.status_code == 200:
        return resp.json()
    elif resp.status_code in (201, 202):
        op_id = resp.headers.get("x-ms-operation-id")
        if op_id:
            poll_operation(token, api_base, op_id)
            result = requests.get(
                f"{api_base}/operations/{op_id}/result",
                headers=headers
            )
            if result.status_code == 200:
                return result.json()
        return find_item(token, api_base, workspace_id, display_name, item_type)
    else:
        raise RuntimeError(f"Create {item_type} failed ({resp.status_code}): {resp.text}")


def find_item(token: str, api_base: str, workspace_id: str,
              display_name: str, item_type: str) -> Dict:
    """Find an item by name and type in a workspace.

    Lists all items (no ?type= filter — that endpoint can return 404 in some
    workspaces) and filters client-side by displayName + type.
    """
    headers = fabric_headers(token)
    resp = requests.get(
        f"{api_base}/workspaces/{workspace_id}/items",
        headers=headers
    )
    resp.raise_for_status()
    for item in resp.json().get("value", []):
        if item.get("displayName") == display_name and item.get("type") == item_type:
            return item
    raise RuntimeError(f"{item_type} '{display_name}' not found")


def b64encode_json(obj: Any) -> str:
    """Base64-encode a JSON object for Fabric definition parts."""
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


def kusto_mgmt(query_service_uri: str, kusto_token: str,
               db_name: str, command: str) -> Dict:
    """Execute a Kusto management command."""
    headers = {
        "Authorization": f"Bearer {kusto_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {"db": db_name, "csl": command}
    resp = requests.post(
        f"{query_service_uri}/v1/rest/mgmt",
        headers=headers, json=body, timeout=60
    )
    resp.raise_for_status()
    return resp.json()


def kusto_streaming_ingest(query_service_uri: str, kusto_token: str,
                           db_name: str, table_name: str,
                           csv_payload: str) -> None:
    """Ingest CSV data via the Kusto streaming ingestion REST API.

    Uses POST /v1/rest/ingest/{db}/{table}?streamFormat=Csv
    which is more reliable than .ingest inline for larger volumes.
    """
    headers = {
        "Authorization": f"Bearer {kusto_token}",
        "Content-Type": "text/csv; charset=utf-8",
    }
    url = (f"{query_service_uri}/v1/rest/ingest/"
           f"{db_name}/{table_name}?streamFormat=Csv")
    resp = requests.post(url, headers=headers, data=csv_payload.encode("utf-8"),
                         timeout=60)
    resp.raise_for_status()


def print_step(step: int, total: int, msg: str):
    print(f"\n[{step}/{total}] {msg}")
    print("-" * 60)
