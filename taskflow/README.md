# Workspace Task Flow — `Customer 360 Marketing`

The visual canvas at the top of the workspace. It turns a flat list of items into a readable
story, and lets you click a task to filter the item list down to that stage.

```
Ingest ──► Lakehouse ─┬─► Ontology ────────┐
(notebook)            │   (+ graph)        ├─► Data Agent
                      └─► Semantic Model ──┘
                              │
                              └────────────► Report
```

The two arrows landing on the Data Agent are deliberate: they make the **dual-source** rule
visible on the canvas — the semantic model answers every *number* (DAX), the ontology's graph
answers every *relationship* (GQL).

Two things deliberately do **not** get a task of their own:

- **The graph model** is underlying to the ontology, not a stage. It lives on the Ontology task.
- **The CSV→Delta notebook** *is* the ingest, so it sits on the Get data task rather than
  duplicating it as a separate "prepare" step.

---

## Why this is a file and not a deploy step

**Fabric exposes no public REST API for task flows.** The Fabric REST reference has no
`taskflow` endpoint — only the UI articles under `fabric/fundamentals/task-flow-*`. The one
automatable path is the workspace's **Import task flow**, which consumes a `.json`.

So `marketing_taskflow.json` is generated from `src/config.yaml` and imported by hand. It is
deliberately **not** in `deploy_all.py`, because nothing in it can be deployed by API.

### What the file does and does not carry

| Carried by the `.json` | Not carried |
|---|---|
| task flow name + description | **item assignments** |
| tasks (type, id, name, description) | canvas positions |
| connectors between tasks | |

Fabric drops item assignments on export, so re-creating them after import is manual. The
mapping below is what you replay.

---

## Import (once per workspace)

1. Open the workspace, click a blank area of the task flow canvas → the **task flow details**
   pane opens on the right.
2. Select the **Import and export task flow** icon → **Import**.
3. Pick `taskflow/marketing_taskflow.json`.
4. If a task flow already exists, choose **overwrite**.
5. Replay the item assignments from the table below: on each task, click the **clip** icon
   (*Assign items*), tick the item, **Select**.

> An item can be assigned to **one task only**. Assigning it elsewhere moves it.

You need **Admin**, **Member** or **Contributor** on the workspace.

---

## Item → task mapping

| Task | Type | Items to assign | Count |
|---|---|---|---|
| Ingest CRM / Marketing / Commerce | `get data` | `NB_Setup_Customer360` (Notebook) | 1 |
| Lakehouse - `LH_Customer360` | `store data` | `LH_Customer360` (Lakehouse) + its SQL analytics endpoint | 2 |
| Ontology - `ONT_Customer360` | `analyze and train data` | `ONT_Customer360` (Ontology), `ONT_Customer360_graph_<ontologyId>` (Graph model), `ONT_Customer360_lh_<ontologyId>` (Lakehouse) + its SQL endpoint | 4 |
| Semantic Model - `SM_Marketing_Analytics` | `prepare data` | `SM_Marketing_Analytics` (Semantic model) | 1 |
| Data Agent - `Marketing_Churn_Agent` | `analyze and train data` | `Marketing_Churn_Agent` (Data agent) | 1 |
| Report - `RPT_Marketing_Churn` | `visualize` | `RPT_Marketing_Churn` (Report) | 1 |

**Total: 10 items — the whole workspace.** Nothing is left unassigned.

> The graph and the ontology's backing Lakehouse carry a GUID suffix that changes every time the
> ontology is recreated (`ONT_Customer360_graph_33896b92…`). Match on the prefix when assigning.

The `portal/` app is **not** a Fabric item — it runs locally on `http://localhost:8000` — so it
has no task.

---

## Regenerating

Item names live in `src/config.yaml`, never in the JSON by hand.

```powershell
python src\build_taskflow.py           # rewrite taskflow\marketing_taskflow.json
python src\build_taskflow.py --check   # fail if it drifted from config.yaml
```

`tests/test_taskflow.py` gates the file: schema, valid task types, no cycles, no floating
tasks, item names in sync with config, and the dual-source contract. Rename an item in
`config.yaml` without regenerating and the test suite fails.

```powershell
python -m pytest tests\ -v --tb=short
```

---

## Notes on the schema

Confirmed against a **real Fabric export**, not inferred:

```json
{
  "name": "...", "description": "...",
  "tasks": [{ "type": "get data", "id": "<guid>", "name": "...", "description": "..." }],
  "edges": [{ "source": "<guid>", "target": "<guid>" }]
}
```

- The `type` values are the lowercase forms the product writes. Note it emits `visualize`,
  while the UI and the docs table label the type *"Visualize data"*.
- Task ids are stable, readable, v4-shaped GUIDs (`c3600000-…`) so re-imports stay diff-free.
  The numbering has gaps (3 and 6): those ids belonged to the removed *CSV→Delta prepare* and
  *Knowledge Graph* tasks and are deliberately not reused.
- Types confirmed in a genuine export: `get data`, `store data`, `track data`,
  `analyze and train data`, `visualize`. **`prepare data` is confirmed too** — it renders as
  *"Prepare data"* on the imported canvas for the Semantic Model task.
