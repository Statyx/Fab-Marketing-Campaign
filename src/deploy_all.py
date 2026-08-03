#!/usr/bin/env python3
"""
One-shot idempotent orchestrator for the Fab-Marketing-Campaign demo.

Runs every deploy step in the correct, dependency-safe order. Each step is
idempotent (reuses items via state.json), so this is safe to re-run; it resumes
rather than duplicating. Finishes with a warm-up so the first live demo query
is paid for off-stage.

USAGE
  python deploy_all.py                       # full deploy, then warm-up
  python deploy_all.py --from semantic_model # resume from a given step to the end
  python deploy_all.py workspace lakehouse   # run only these steps (canonical order)
  python deploy_all.py --skip report
  python deploy_all.py --warmup              # warm-up only (no deploy)
  python deploy_all.py --no-warmup           # deploy only

TENANT: az silently flips to another tenant. Set `az_subscription` in config.yaml
and this script runs `az account set` first. Without it you get 404 EntityNotFound.

GATE: run `python -m pytest tests/ -v` BEFORE this. The orchestrator refuses to
start if the generated dataset is missing.
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

import argparse
import importlib
import subprocess
import time
from pathlib import Path

import requests
from helpers import (load_config, load_state, get_fabric_token, fabric_headers, print_step,
                     ensure_tenant as _ensure_tenant)

RAW = Path(__file__).parent.parent / "data" / "raw"

# Canonical deploy order (name -> module). Each module exposes main().
STEPS = [
    ("generate_data",   "generate_data"),
    ("workspace",       "deploy_workspace"),
    ("lakehouse",       "deploy_lakehouse"),
    ("setup_notebook",  "deploy_setup_notebook"),
    ("semantic_model",  "deploy_semantic_model"),
    ("ontology",        "deploy_ontology"),
    ("graph",           "deploy_graph"),
    ("report",          "deploy_report"),
    ("data_agent",      "deploy_data_agent"),
]
STEP_NAMES = [name for name, _ in STEPS]


def ensure_tenant(cfg):
    """Pin az to the right subscription/tenant — see helpers.ensure_tenant.

    Kept as a thin alias so the orchestrator's step list stays readable; the
    implementation is shared so standalone scripts get the same guard.
    """
    _ensure_tenant(cfg)


def select_steps(args):
    if args.steps:
        unknown = [s for s in args.steps if s not in STEP_NAMES]
        if unknown:
            raise SystemExit(f"Unknown step(s): {unknown}. Valid: {STEP_NAMES}")
        chosen = [s for s in STEP_NAMES if s in args.steps]  # keep canonical order
    elif args.from_step:
        if args.from_step not in STEP_NAMES:
            raise SystemExit(f"Unknown --from step '{args.from_step}'. Valid: {STEP_NAMES}")
        chosen = STEP_NAMES[STEP_NAMES.index(args.from_step):]
    else:
        chosen = list(STEP_NAMES)
    skip = set(s.strip() for s in (args.skip or "").split(",") if s.strip())
    return [s for s in chosen if s not in skip]


def run_steps(names):
    mod_of = dict(STEPS)
    total = len(names)
    for idx, name in enumerate(names, 1):
        print_step(idx, total, f"STEP: {name}  (module {mod_of[name]})")
        try:
            mod = importlib.import_module(mod_of[name])
        except ModuleNotFoundError:
            print(f"   (module {mod_of[name]} not implemented yet — skipping)")
            continue
        mod.main()
    print(f"\nOK  {total} step(s) processed.")


def warm_up(cfg, state):
    """Pay the first-query latency off-stage."""
    print_step(1, 1, "Warm-up: Fabric workspace + token")
    try:
        api = cfg["fabric_api_base"]; ws = state["workspace_id"]
        h = fabric_headers(get_fabric_token())
        t0 = time.time()
        items = requests.get(f"{api}/workspaces/{ws}/items", headers=h, timeout=60).json().get("value", [])
        print(f"   Fabric OK — {len(items)} items in workspace ({time.time() - t0:.1f}s).")
        for i in sorted(items, key=lambda x: x.get("type", "")):
            print(f"      {i.get('type'):<18} {i.get('displayName')}")
    except Exception as e:
        print(f"   (warm-up skipped: {e})")


def main():
    p = argparse.ArgumentParser(description="Fab-Marketing-Campaign deploy orchestrator")
    p.add_argument("steps", nargs="*", help=f"run only these steps (order fixed). Valid: {STEP_NAMES}")
    p.add_argument("--from", dest="from_step", help="resume from this step to the end")
    p.add_argument("--skip", help="comma-separated steps to skip")
    p.add_argument("--warmup", action="store_true", help="run warm-up only (no deploy)")
    p.add_argument("--no-warmup", dest="no_warmup", action="store_true", help="deploy without warm-up")
    args = p.parse_args()

    cfg = load_config()
    ensure_tenant(cfg)

    if args.warmup:
        warm_up(cfg, load_state())
        return

    names = select_steps(args)
    if "generate_data" not in names and not (RAW / "crm" / "crm_customer_profile.csv").exists():
        raise SystemExit("No generated dataset. Run generate_data.py (or include the step) first.")

    print(f"Plan: {names}")
    run_steps(names)

    if not args.no_warmup:
        warm_up(cfg, load_state())

    print("\nReady. Demo arc: detect (churn cohort) -> diagnose (CAMP_007 over-mailing) -> "
          "quantify (revenue at risk, VIPs) -> act.")


if __name__ == "__main__":
    main()
