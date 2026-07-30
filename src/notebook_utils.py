#!/usr/bin/env python3
"""
Fabric Notebook Utilities — Convert, Push, Run.

Encodes hard-won lessons from Fabric REST API edge cases:
- Always use .py format (notebook-content.py), never ipynb for API operations
- Never use "format": "ipynb" in definition — it causes silent job failures
- Replace %pip install with subprocess.check_call for scheduled/Jobs API runs
- Blank line required after each # CELL / # MARKDOWN / # METADATA header
"""

import base64
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from helpers import get_fabric_token, fabric_headers, find_item

API = "https://api.fabric.microsoft.com/v1"


# ─── ipynb → Fabric .py conversion ────────────────────────────────

def ipynb_to_fabric_py(ipynb_path: str) -> str:
    """Convert a Jupyter .ipynb file to Fabric's proprietary .py notebook format.

    Key format rules (each learned the hard way):
    1. Must start with '# Fabric notebook source' + two blank lines
    2. Sections: # METADATA / # MARKDOWN / # CELL followed by ' ********************'
    3. MUST have a blank line after each section header (Fabric ignores cells otherwise)
    4. Markdown lines prefixed with '# '
    5. METADATA section contains kernel_info + lakehouse binding as # META lines
    6. %pip install must be replaced with subprocess.check_call for Jobs API compatibility
    """
    with open(ipynb_path, "r", encoding="utf-8") as f:
        nb = json.load(f)

    lines: List[str] = ["# Fabric notebook source\n"]

    # ── METADATA section ──
    metadata = nb.get("metadata", {})
    trident = metadata.get("trident", {})
    kernel = metadata.get("kernelspec", {}).get("name", "synapse_pyspark")

    meta_obj: Dict = {"kernel_info": {"name": kernel}}
    lakehouse = trident.get("lakehouse", {})
    if lakehouse.get("default_lakehouse"):
        meta_obj["dependencies"] = {"lakehouse": lakehouse}

    lines.append("\n# METADATA ********************\n\n")
    for line in json.dumps(meta_obj, indent=2).split("\n"):
        lines.append(f"# META {line}\n")

    # ── Cells ──
    for cell in nb["cells"]:
        cell_type = cell["cell_type"]
        source = "".join(cell["source"])

        if cell_type == "markdown":
            lines.append("\n# MARKDOWN ********************\n\n")
            for src_line in source.split("\n"):
                lines.append("# " + src_line + "\n")

        elif cell_type == "code":
            # Fix %pip install → subprocess (crashes in scheduled mode)
            source = _fix_pip_magic(source)
            lines.append("\n# CELL ********************\n\n")
            lines.append(source + "\n")

    return "".join(lines)


def _fix_pip_magic(source: str) -> str:
    """Replace %pip install with subprocess.check_call.

    %pip magic works in interactive mode but CRASHES notebooks run
    via the Jobs API (scheduled mode) with 'Job instance failed
    without detail error' after ~38 seconds.
    """
    pattern = r"^%pip\s+install\s+(.+)$"
    match = re.match(pattern, source.strip())
    if match:
        args = match.group(1).strip()
        # Parse package names (strip --quiet, --no-deps, etc.)
        packages = [a for a in args.split() if not a.startswith("-")]
        flags = [a for a in args.split() if a.startswith("-")]
        cmd_parts = '["pip", "install"'
        for pkg in packages:
            cmd_parts += f', "{pkg}"'
        for flag in flags:
            cmd_parts += f', "{flag}"'
        cmd_parts += "]"
        return f"import subprocess as _sp\n_sp.check_call({cmd_parts})"
    return source


# ─── Push notebook to workspace ───────────────────────────────────

def push_notebook(workspace_id: str, notebook_id: str,
                  py_content: str, token: Optional[str] = None,
                  max_wait: int = 120) -> bool:
    """Push .py content to an existing notebook via updateDefinition API.

    Always uses notebook-content.py path (never ipynb).
    Never includes "format" key in definition.
    """
    if token is None:
        token = get_fabric_token()
    headers = fabric_headers(token)
    nb_b64 = base64.b64encode(py_content.encode("utf-8")).decode("ascii")

    body = {
        "definition": {
            "parts": [
                {
                    "path": "notebook-content.py",
                    "payload": nb_b64,
                    "payloadType": "InlineBase64",
                }
            ]
        }
    }

    url = f"{API}/workspaces/{workspace_id}/notebooks/{notebook_id}/updateDefinition"
    resp = requests.post(url, headers=headers, json=body, timeout=60)

    if resp.status_code == 200:
        return True
    elif resp.status_code == 202:
        return _poll_location(resp, headers, max_wait)
    else:
        raise RuntimeError(
            f"updateDefinition failed ({resp.status_code}): {resp.text[:500]}"
        )


def create_notebook(workspace_id: str, display_name: str,
                    py_content: str, token: Optional[str] = None,
                    max_wait: int = 120) -> str:
    """Create a new notebook in the workspace. Returns the notebook ID.

    Uses .py format only. Never includes "format" key.
    """
    if token is None:
        token = get_fabric_token()
    headers = fabric_headers(token)
    nb_b64 = base64.b64encode(py_content.encode("utf-8")).decode("ascii")

    body = {
        "displayName": display_name,
        "type": "Notebook",
        "definition": {
            "parts": [
                {
                    "path": "notebook-content.py",
                    "payload": nb_b64,
                    "payloadType": "InlineBase64",
                }
            ]
        },
    }

    resp = requests.post(
        f"{API}/workspaces/{workspace_id}/items",
        headers=headers, json=body, timeout=60,
    )

    if resp.status_code in (200, 201):
        return resp.json()["id"]
    elif resp.status_code == 202:
        _poll_location(resp, headers, max_wait)
        # Find the notebook by name
        item = find_item(token, API, workspace_id, display_name, "Notebook")
        return item["id"]
    else:
        raise RuntimeError(
            f"Create notebook failed ({resp.status_code}): {resp.text[:500]}"
        )


def delete_notebook(workspace_id: str, notebook_id: str,
                    token: Optional[str] = None,
                    wait_for_release: bool = True,
                    display_name: Optional[str] = None) -> bool:
    """Delete a notebook and optionally wait for the name to be released."""
    if token is None:
        token = get_fabric_token()
    headers = fabric_headers(token)

    resp = requests.delete(
        f"{API}/workspaces/{workspace_id}/notebooks/{notebook_id}",
        headers=headers, timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Delete failed ({resp.status_code}): {resp.text[:300]}"
        )

    if wait_for_release and display_name:
        for attempt in range(12):
            time.sleep(10)
            try:
                find_item(token, API, workspace_id, display_name, "Notebook")
            except RuntimeError:
                return True  # Name released
            print(f"  Waiting for name release ({attempt+1}/12)...")
            sys.stdout.flush()

    return True


def recreate_notebook(workspace_id: str, display_name: str,
                      py_content: str, token: Optional[str] = None) -> str:
    """Delete existing notebook (if any) and create fresh. Returns new ID.

    Useful when updateDefinition leaves the notebook in a corrupted state.
    """
    if token is None:
        token = get_fabric_token()

    # Delete if exists
    try:
        existing = find_item(token, API, workspace_id, display_name, "Notebook")
        print(f"  Deleting existing {display_name} ({existing['id']})...")
        delete_notebook(workspace_id, existing["id"], token,
                        wait_for_release=True, display_name=display_name)
    except RuntimeError:
        pass  # Doesn't exist

    # Create
    print(f"  Creating {display_name}...")
    return create_notebook(workspace_id, display_name, py_content, token)


# ─── Run notebook ─────────────────────────────────────────────────

def run_notebook(workspace_id: str, notebook_id: str,
                 token: Optional[str] = None,
                 parameters: Optional[Dict] = None,
                 max_wait: int = 600,
                 poll_interval: int = 15) -> Dict:
    """Run a notebook via the Jobs API and poll until completion.

    Uses /items/{id}/jobs/instances (NOT /notebooks/{id}/...).
    jobType must be RunNotebook (NOT SparkJob).

    Returns the final job status dict.
    """
    if token is None:
        token = get_fabric_token()
    headers = fabric_headers(token)

    url = f"{API}/workspaces/{workspace_id}/items/{notebook_id}/jobs/instances?jobType=RunNotebook"

    body = None
    if parameters:
        body = {
            "executionData": {
                "parameters": {
                    k: {"value": str(v), "type": "string"}
                    for k, v in parameters.items()
                }
            }
        }

    resp = requests.post(url, headers=headers, json=body, timeout=60)

    if resp.status_code not in (200, 202):
        raise RuntimeError(
            f"Run notebook failed ({resp.status_code}): {resp.text[:500]}"
        )

    if resp.status_code == 200:
        return {"status": "Completed"}

    location = resp.headers.get("Location")
    retry = min(int(resp.headers.get("Retry-After", str(poll_interval))), poll_interval)

    for i in range(max_wait // retry):
        time.sleep(retry)
        elapsed = (i + 1) * retry
        r = requests.get(location, headers=headers, timeout=30)
        if r.status_code == 200:
            data = r.json()
            status = data.get("status", "?")
            failure = data.get("failureReason", {})
            print(f"  [{elapsed}s] {status}")
            sys.stdout.flush()

            if status == "Completed":
                return data
            if status in ("Failed", "Cancelled"):
                msg = failure.get("message", "No error detail")
                code = failure.get("errorCode", "Unknown")
                raise RuntimeError(
                    f"Notebook run {status}: {code} — {msg}\n"
                    f"Check Fabric Monitoring Hub for Spark logs (stderr)."
                )

    raise TimeoutError(f"Notebook run did not complete in {max_wait}s")


# ─── Convenience: convert + push + run ────────────────────────────

def deploy_notebook_from_ipynb(workspace_id: str,
                               notebook_id: str,
                               ipynb_path: str,
                               run: bool = True,
                               token: Optional[str] = None,
                               max_run_wait: int = 600) -> Optional[Dict]:
    """One-shot: convert ipynb → .py, push to workspace, optionally run.

    This is the recommended way to deploy notebooks from local ipynb files.
    """
    if token is None:
        token = get_fabric_token()

    print(f"  Converting {Path(ipynb_path).name} → .py format...")
    py_content = ipynb_to_fabric_py(ipynb_path)
    print(f"  {len(py_content)} chars")

    print(f"  Pushing to {notebook_id}...")
    push_notebook(workspace_id, notebook_id, py_content, token)
    print(f"  ✅ Notebook updated")

    if run:
        print(f"  Running notebook...")
        return run_notebook(workspace_id, notebook_id, token,
                            max_wait=max_run_wait)

    return None


# ─── Internal helpers ─────────────────────────────────────────────

def _poll_location(resp, headers, max_wait=120) -> bool:
    """Poll a 202 response Location header until Succeeded."""
    loc = resp.headers.get("Location")
    if not loc:
        return True
    retry = min(int(resp.headers.get("Retry-After", "5")), 10)

    for i in range(max_wait // retry):
        time.sleep(retry)
        r = requests.get(loc, headers=headers, timeout=30)
        if r.status_code != 200:
            continue
        data = r.json()
        status = data.get("status", "?")
        if status == "Succeeded":
            return True
        if status in ("Failed", "Cancelled"):
            raise RuntimeError(f"Operation {status}: {data}")
    raise TimeoutError(f"Operation did not complete in {max_wait}s")
