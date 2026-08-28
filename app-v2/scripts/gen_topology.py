"""Emit the architecture topology the V2 app draws — derived, never typed.

V1 served this from FastAPI: `/api/workflow` re-read `state.json` on every call, so opening
the modal right after a deploy showed the deploy. V2 is a static SPA with no backend, so that
door is closed. Reimplementing `build_workflow()` in TypeScript was the obvious move and the
wrong one: 34 tests in `test_workflow_view.py` guard that topology (edge crossings, the
character budgets, the honesty rule), and a parallel TS copy would leave every one of them
guarding a file the app no longer uses. So the tested Python stays the single owner of the
shape and this script only serialises it; the frontend owns pixels and nothing else.

WHAT IS DELIBERATELY NOT IN THE OUTPUT
--------------------------------------
`build_workflow()` is called with **state={}** — an empty receipt. That is not a shortcut, it
is the contract: `state.json` is gitignored (it is the operator's tenant receipt) and this
file is committed and shipped inside a bundle that is downloadable without authentication.
Passing the real state would bake item GUIDs into a public asset and, worse, would freeze a
claim — "déployé" as of build time — that nobody re-checks afterwards. `workflow.py`'s own
docstring says a picture that can disagree with what is deployed is worse than no picture.

So every node ships `deployed: false`, and the browser decides otherwise by asking the tenant
(`services/topology.ts`). That is strictly stronger than V1, which believed a JSON file.

`config.yaml` IS read, because names, connection names and the model deployment are what make
the drawing legible, and they are not identifiers. `_strip_guids()` is the structural backstop:
it rejects anything GUID-shaped whatever its source, in the same spirit as the repo's leak
guard — match a shape, never a list of names.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal" / "backend"))

import workflow  # noqa: E402  (needs the sys.path line above)

OUT = ROOT / "app-v2" / "src" / "data" / "topology.generated.json"

# Same allow-list shape the repo's leak guard uses: any 8-4-4-4-12 hex run is an identifier
# until proven otherwise, wherever it came from.
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _load_config() -> dict:
    """Read config.yaml for the *names*, tolerating its absence.

    A fresh clone has no `src/config.yaml` (gitignored). Returning {} then is correct rather
    than fatal: `build_workflow()` falls back to the constants written in its own source, so
    CI still produces a complete, honest diagram — the same one, minus the operator's chosen
    connection names.
    """
    path = ROOT / "src" / "config.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _ontology_graph() -> dict:
    """The ontology as the deploy script declares it — read from source, not from the tenant.

    Straight port of `portal/backend/main.py::_ontology_graph`, minus the ontology GUID. AST
    over import: importing `deploy_ontology` would execute a deploy script's module scope.
    """
    try:
        tree = ast.parse((ROOT / "src" / "deploy_ontology.py").read_text(encoding="utf-8"))
    except Exception:
        return {"entities": [], "relationships": []}
    found: dict[str, list] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            if getattr(node.targets[0], "id", "") in ("ENTITIES", "RELATIONSHIPS"):
                try:
                    found[node.targets[0].id] = ast.literal_eval(node.value)
                except Exception:
                    pass
    return {
        "entities": [{"name": e[0], "table": e[1], "properties": len(e[3])}
                     for e in found.get("ENTITIES", [])],
        "relationships": [{"name": r[0], "from": r[1], "to": r[2]}
                          for r in found.get("RELATIONSHIPS", [])],
    }


def _strip_guids(value: Any) -> Any:
    """Blank any GUID-shaped string anywhere in the tree.

    Belt and braces: `state={}` should already mean no `ref` carries an id, but this file is
    published and a future edit that starts passing a real state must not silently leak. The
    emitted value becomes empty rather than the identifier, so the test that asserts the
    output is GUID-free keeps passing and the guard cannot rot into decoration.
    """
    if isinstance(value, str):
        return GUID.sub("", value)
    if isinstance(value, list):
        return [_strip_guids(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_guids(v) for k, v in value.items()}
    return value


def build() -> dict:
    wf = workflow.build_workflow({}, _load_config())
    # Warnings computed here would all read "non déployé" — an artefact of the empty state,
    # not a fact about the tenant. The browser recomputes them once it knows what is live.
    wf.pop("warnings", None)
    return _strip_guids({"workflow": wf, "ontology": _ontology_graph()})


def main() -> int:
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    wf = data["workflow"]
    print(f"{OUT.relative_to(ROOT)}: {len(wf['nodes'])} nodes, {len(wf['edges'])} edges, "
          f"{wf['crossings']} crossings, {len(data['ontology']['entities'])} entities, "
          f"{len(data['ontology']['relationships'])} relationships")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
