#!/usr/bin/env python3
"""
Validate RPT_Marketing_Churn against the live semantic model.

Rather than hand-written checks, every query is DERIVED from the report itself:
each visual's prototypeQuery is replayed as DAX through the Power BI
executeQueries REST API. If a visual would render blank in Fabric, it fails here.

Three failure modes are detected:
  FAIL  the measure or column does not resolve            -> DAX error
  FAIL  the query returns no row / only nulls             -> blank visual
  WARN  every category returns the SAME value             -> the filter does not
        propagate (relationship direction), so the chart is meaningless

USAGE
  python validate_report.py
"""
import os, sys, subprocess, json
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

import requests

sys.path.insert(0, str(Path(__file__).parent))
from helpers import load_config, load_state, print_step, ensure_tenant
from deploy_report import build_report

PBI = "https://api.powerbi.com/v1.0/myorg"


def pbi_token() -> str:
    out = subprocess.check_output(
        ["az", "account", "get-access-token", "--resource",
         "https://analysis.windows.net/powerbi/api", "--query", "accessToken", "-o", "tsv"],
        shell=True)
    return out.decode().strip()


def run_dax(ws, sm_id, tok, dax):
    r = requests.post(f"{PBI}/groups/{ws}/datasets/{sm_id}/executeQueries",
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                      json={"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}},
                      timeout=120)
    if r.status_code != 200:
        return None, f"{r.status_code}: {r.text[:220]}"
    return r.json()["results"][0]["tables"][0]["rows"], None


def extract_visuals(report):
    """[(page, visual_type, dim(table,col) | None, measure(table,name)), ...]"""
    out = []
    for section in report["sections"]:
        for vc in section["visualContainers"]:
            sv = json.loads(vc["config"])["singleVisual"]
            proto = sv.get("prototypeQuery")
            if not proto:
                continue
            entity_of = {f["Name"]: f["Entity"] for f in proto["From"]}
            dim = None
            measures = []
            for sel in proto["Select"]:
                kind = "Column" if "Column" in sel else "Measure"
                node = sel[kind]
                entity = entity_of[node["Expression"]["SourceRef"]["Source"]]
                if kind == "Column":
                    if dim is None:
                        dim = (entity, node["Property"])
                else:
                    measures.append((entity, node["Property"]))
            for m in measures:
                out.append((section["name"], sv.get("visualType"), dim, m))
    return out


def build_dax(dim, measure):
    """Replay the visual: a bare measure for cards, a grouped scan otherwise.
    SUMMARIZECOLUMNS is exactly what a Power BI chart issues, so a broken
    relationship path reproduces here instead of on stage."""
    _, mname = measure
    if dim is None:
        return f'EVALUATE ROW("val", [{mname}])'
    dtable, dcol = dim
    return (f'EVALUATE TOPN(5, SUMMARIZECOLUMNS(\'{dtable}\'[{dcol}], "val", [{mname}]), '
            f'[val], DESC)')


def main():
    config = load_config()
    ensure_tenant(config, quiet=True)   # a wrong tenant reads as 401 on every visual
    state = load_state()
    ws, sm_id = state.get("workspace_id"), state.get("semantic_model_id")
    if not ws or not sm_id:
        print("Deploy the workspace + semantic model first.")
        sys.exit(1)

    report, _, _, _ = build_report(state, config)
    visuals = extract_visuals(report)
    # Same (dim, measure) pair can appear on several pages — query it once.
    seen, checks = set(), []
    for page, vtype, dim, measure in visuals:
        key = (dim, measure)
        if key in seen:
            continue
        seen.add(key)
        checks.append((page, vtype, dim, measure))

    tok = pbi_token()
    print_step(1, 1, f"Validating {config['report_name']} — {len(checks)} distinct queries "
                     f"from {len(visuals)} data visuals")

    failures, warnings = [], []
    for page, vtype, dim, measure in checks:
        label = f"{page:<10} {vtype:<20} {measure[1]}" + (f" by {dim[1]}" if dim else "")
        rows, err = run_dax(ws, sm_id, tok, build_dax(dim, measure))
        if err:
            print(f"   [FAIL] {label}\n          {err}")
            failures.append(label)
            continue

        values = [r.get("[val]") for r in rows] if rows else []
        non_null = [v for v in values if v is not None]
        if not non_null:
            print(f"   [FAIL] {label} -> no data (visual renders blank)")
            failures.append(label)
            continue

        preview = ", ".join(f"{v:,.2f}" if isinstance(v, float) else str(v) for v in non_null[:3])
        if dim and len(non_null) > 1 and len(set(non_null)) == 1:
            print(f"   [WARN] {label} -> same value on every category ({preview}) "
                  f"— filter does not propagate")
            warnings.append(label)
        else:
            print(f"   [OK]   {label} -> {preview}")

    total = len(checks)
    ok = total - len(failures) - len(warnings)
    print(f"\n{ok}/{total} clean, {len(warnings)} warning(s), {len(failures)} failure(s).")
    if failures:
        print("   A failure means that visual renders BLANK in Fabric — fix before demoing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
