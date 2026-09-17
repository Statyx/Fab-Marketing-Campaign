#!/usr/bin/env python3
"""
Shared helpers for Fab-Marketing-Campaign deployment scripts.
Authentication, async polling, config/state, Fabric items, Kusto (Eventhouse).
Reused from the proven sister-project pattern.
"""

import base64
import binascii
import json
import os
import subprocess
import sys
import time
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

import requests

SCRIPT_DIR = Path(__file__).parent
ROOT = SCRIPT_DIR.parent
CONFIG_FILE = SCRIPT_DIR / "config.yaml"
STATE_FILE = SCRIPT_DIR / "state.json"
ACTIVE_PROFILE_FILE = ROOT / "deployments" / "active-profile.json"
PROFILE_ENV = "FAB_MARKETING_PROFILE_DIR"


class ItemNotFoundError(RuntimeError):
    pass


def profile_dir() -> Optional[Path]:
    selected = os.environ.get(PROFILE_ENV)
    if selected is not None:
        if not selected.strip():
            raise RuntimeError(f"{PROFILE_ENV} is empty")
        path = Path(selected)
        if not path.is_absolute():
            path = ROOT / path
    elif ACTIVE_PROFILE_FILE.exists():
        pointer = json.loads(ACTIVE_PROFILE_FILE.read_text(encoding="utf-8"))
        name = pointer.get("profile") if isinstance(pointer, dict) else None
        if (not isinstance(name, str) or not name.strip()
                or name in (".", "..") or "/" in name or "\\" in name):
            raise RuntimeError("active-profile.json must select one named deployment profile")
        path = ACTIVE_PROFILE_FILE.parent / name
    else:
        return None
    path = path.resolve()
    if path == SCRIPT_DIR.resolve() or not (path / "config.yaml").is_file():
        raise RuntimeError(f"Selected deployment profile has no separate config.yaml: {path}")
    return path


def config_path() -> Path:
    profile = profile_dir()
    return profile / "config.yaml" if profile else CONFIG_FILE


def state_path() -> Path:
    profile = profile_dir()
    return profile / "state.json" if profile else STATE_FILE


def raw_dir() -> Path:
    profile = profile_dir()
    return profile / "raw" if profile else ROOT / "data" / "raw"


def output_path(default_path: Path, *profile_parts: str) -> Path:
    profile = profile_dir()
    if profile is None:
        return Path(default_path)
    relative = Path(*profile_parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Profile output must stay inside its artifacts directory")
    return profile / "artifacts" / relative


def _profile_context(cfg: Dict[str, Any]) -> Dict[str, str]:
    deployment = cfg.get("deployment", {})
    context = {
        "tenant_id": cfg.get("tenant_id"),
        "subscription_id": cfg.get("az_subscription"),
        "account": deployment.get("expected_account"),
        "workspace_name": cfg.get("workspace_name"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in context.values()):
        raise RuntimeError("Profile requires tenant_id, az_subscription, workspace_name "
                           "and deployment.expected_account")
    return context


def load_config() -> Dict[str, Any]:
    with open(config_path(), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise RuntimeError("Deployment configuration must be a YAML object")
    return cfg


def load_state() -> Dict[str, Any]:
    """Load deployment state (IDs created so far)."""
    path = state_path()
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        raise RuntimeError("Deployment state must be a JSON object")
    if profile_dir() and state:
        if state.get("_deployment_context") != _profile_context(load_config()):
            raise RuntimeError("State does not belong to the selected deployment profile; "
                               "do not copy old deployment IDs")
    return state


def save_state(state: Dict[str, Any]):
    """Persist deployment state."""
    path = state_path()
    if profile_dir():
        context = _profile_context(load_config())
        if state.get("_deployment_context", context) != context:
            raise RuntimeError("Refusing to save state from a different deployment profile")
        state = dict(state, _deployment_context=context)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(path)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _configure_profile_cli(cfg: Dict[str, Any]) -> Dict[str, str]:
    context = _profile_context(cfg)
    configured = cfg.get("deployment", {}).get("azure_config_dir")
    if not isinstance(configured, str) or not configured.strip():
        raise RuntimeError("Profile requires deployment.azure_config_dir for its isolated CLI cache")
    cache = Path(os.path.expandvars(configured)).expanduser()
    if not cache.is_absolute() or not cache.is_dir():
        raise RuntimeError("The selected profile's isolated Azure CLI cache must already exist")
    cache = cache.resolve()
    if cache == (Path.home() / ".azure").resolve():
        raise RuntimeError("A deployment profile cannot use the normal Azure CLI cache")
    active = os.environ.get("AZURE_CONFIG_DIR")
    if active and Path(active).expanduser().resolve() != cache:
        raise RuntimeError("AZURE_CONFIG_DIR differs from the selected profile; "
                           "use its dedicated shell, not another tenant's cache")
    os.environ["AZURE_CONFIG_DIR"] = str(cache)
    if cfg.get("fabric_api_base") != "https://api.fabric.microsoft.com/v1":
        raise RuntimeError("Selected profile must use the public Fabric API endpoint")
    return context


def validate_token_identity(token: str, cfg: Optional[Dict[str, Any]] = None):
    if profile_dir() is None:
        return
    context = _profile_context(cfg if cfg is not None else load_config())
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise RuntimeError("Cannot verify the selected profile's token identity") from exc
    if not isinstance(claims, dict):
        raise RuntimeError("Token claims must be an object")
    account = claims.get("upn") or claims.get("preferred_username") or claims.get("unique_name")
    if (str(claims.get("tid", "")).casefold() != context["tenant_id"].casefold()
            or str(account or "").casefold() != context["account"].casefold()):
        raise RuntimeError("Access token does not belong to the selected tenant and user")


def _validate_fabric_ownership(token: str):
    if profile_dir() is None:
        return
    cfg, state = load_config(), load_state()
    ws = state.get("workspace_id")
    if not ws:
        return
    base = f"{cfg['fabric_api_base']}/workspaces/{ws}"
    headers = fabric_headers(token)
    response = requests.get(base, headers=headers, timeout=60)
    response.raise_for_status()
    if response.json().get("displayName") != cfg["workspace_name"]:
        raise RuntimeError("Recorded workspace does not match the selected profile name")
    expected = {
        "lakehouse_id": ("Lakehouse", cfg["lakehouse_name"]),
        "semantic_model_id": ("SemanticModel", cfg["semantic_model_name"]),
        "report_id": ("Report", cfg["report_name"]),
        "ontology_id": ("Ontology", cfg.get("ontology_name")),
        "data_agent_id": ("DataAgent", cfg.get("data_agent_name")),
        "notebook_setup_id": ("Notebook", "NB_Setup_Customer360"),
        "graph_model_id": ("GraphModel", None),
    }
    for key, (kind, name) in expected.items():
        if not state.get(key):
            continue
        item = requests.get(f"{base}/items/{state[key]}", headers=headers, timeout=60)
        item.raise_for_status()
        body = item.json()
        if body.get("type") != kind or (name and body.get("displayName") != name):
            raise RuntimeError(f"Recorded {key} does not match the selected profile's item")
        if key == "graph_model_id":
            suffix = state["ontology_id"]
            expected_names = {f"{cfg['ontology_name']}_graph_{suffix}",
                              f"{cfg['ontology_name']}_graph_{suffix.replace('-', '')}"}
            if body.get("displayName") not in expected_names:
                raise RuntimeError("Recorded graph is not the selected ontology's managed graph")


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
    if profile_dir() is not None:
        context = _configure_profile_cli(cfg)
        result = subprocess.run(
            ["az", "account", "show", "--output", "json"],
            shell=sys.platform == "win32", check=True, capture_output=True, text=True,
            timeout=90,
        )
        account = json.loads(result.stdout)
        if (str(account.get("tenantId", "")).casefold() != context["tenant_id"].casefold()
                or str(account.get("id", "")).casefold() != context["subscription_id"].casefold()
                or str(account.get("user", {}).get("name", "")).casefold()
                != context["account"].casefold()):
            raise RuntimeError("Isolated Azure CLI identity does not match the selected profile; "
                               "no account was switched")
        if not quiet:
            print(f"OK  isolated tenant/account match profile '{profile_dir().name}'")
        return
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


def get_resource_token(resource: str) -> str:
    if profile_dir():
        ensure_tenant(quiet=True)
    result = subprocess.check_output(
        ["az", "account", "get-access-token",
         "--resource", resource,
         "--query", "accessToken", "-o", "tsv"],
        shell=sys.platform == "win32", timeout=90,
    )
    token = result.decode().strip()
    validate_token_identity(token)
    return token


def get_fabric_token() -> str:
    """Get a checked Fabric token and reject stale cross-profile item IDs."""
    token = get_resource_token("https://api.fabric.microsoft.com")
    _validate_fabric_ownership(token)
    return token


def get_powerbi_token() -> str:
    """Get a Power BI API token (dataset refresh, executeQueries).

    The Fabric token does not work against api.powerbi.com — the refresh and
    DAX endpoints need the analysis.windows.net audience.
    """
    if profile_dir():
        get_fabric_token()
    return get_resource_token("https://analysis.windows.net/powerbi/api")


class _CheckedProfileCredential:
    def __init__(self, credential):
        self._credential = credential

    def get_token(self, *scopes, **kwargs):
        ensure_tenant(quiet=True)
        token = self._credential.get_token(*scopes, **kwargs)
        validate_token_identity(token.token)
        return token

    def close(self):
        self._credential.close()


def get_sdk_credential(*, process_timeout: int = 90):
    if profile_dir() is None:
        from azure.identity import DefaultAzureCredential
        return DefaultAzureCredential(process_timeout=process_timeout)
    from azure.identity import AzureCliCredential
    cfg = load_config()
    _configure_profile_cli(cfg)
    return _CheckedProfileCredential(AzureCliCredential(
        tenant_id=cfg["tenant_id"],
        process_timeout=process_timeout,
    ))


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
    raise ItemNotFoundError(f"{item_type} '{display_name}' not found")


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
