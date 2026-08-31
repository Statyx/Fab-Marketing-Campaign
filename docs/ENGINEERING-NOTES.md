# Engineering notes

Working notes for the people who deploy and extend this project. The
[README](../README.md) is the tour; this file is the logbook ? public-repo hygiene, the
local portal, the Foundry supervision plane, the workspace task flow, and the deployment
traps that cost real time to find.

For the design itself, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Public-repo hygiene

This repo is public and the demo data is synthetic. Nothing tracked here may name a real
customer, a real workspace owner, or a real Fabric item.

```powershell
python .github\scripts\check_client_leak.py     # exit 0 = clean
```

The same script runs in CI on every branch and every PR, and `tests/test_leak_guard.py`
gates the guard itself — every rule has a detection test **and** a silence test.

**It names nobody.** Two sister repos guard against customer names by listing those names
in the guard: one in a Python deny-list, one in a shell pattern with a letter parenthesised
so that it fools a full-text search and no human reader. A public file that enumerates a
client portfolio is a worse disclosure than the isolated mention it was written to catch.
So every rule here matches a **shape**:

| Rule | Shape |
|---|---|
| GUID | anything that is not one of two known placeholder forms |
| Fabric SQL endpoint | a `[a-z0-9]{20,}` opaque token in front of the service domain |
| Personal path | `C:\Users\…`, `/home/…`, `/Users/…` — placeholders exempt |
| Personal workspace prefix | 2–3 initials + ` - ` + Titlecase, **anchored at the start of a name** |
| Renamed repository | `…-Live-Event` with any prefix other than `Fab-` |

The GUID rule is an allow-list on purpose. The sister rule only looks at GUIDs preceded by
an identity label (`tenant_id`, `client_id`), which would have missed the real report id
this repo carried in a prose sentence for 27 commits — and an allow-list never has to
write the real identifier down in order to catch it.

An allow-list has a cost the deny-list does not: it also flags identifiers that are public
by construction. `github.com/user-attachments/assets/<id>` is one — it addresses an image
GitHub already serves from this public README, and reveals nothing about the tenant. That
exemption is not cosmetic: before it existed the rule fired on the screenshots the README
carried under its title, and acting on the finding deleted three of them. The guard degraded
the repository it protects. It is scoped to the URL span, not to the line, so a real id cannot
ride along next to an image tag — `test_the_attachment_exemption_covers_the_url_and_nothing_else`
pins that.

The workspace-prefix rule is the delicate one: initials followed by a dash and a name is
also the shape of a subtraction chain. Anchoring it at the start of a name and capping the
initials at three letters is what keeps `Revenue - COGS - Operating Expenses` and
`FAB_W = SLIDE_W - FAB_L - GAP - M` silent. Both are pinned as tests.

An optional name-based rule reads `CLIENT_DENYLIST` (one entry per line), wired to a GitHub
Actions secret in CI or to a gitignored `.clientdeny` locally. **Absent ⇒ the rule is
skipped with a warning, never a failure.**

`src/config.yaml`, `src/state.json` and `data/raw/` are gitignored because they hold real
identifiers or a regenerable dataset; CI fails if any of them ever becomes tracked. The
test suite is offline by construction — CI materialises `config.yaml` / `state.json` from
the committed `*.example` files, regenerates the dataset from its seed, and needs no
secret, no Azure credential and no capacity. **All 586 tests run; a skip fails the job**,
because a skipped test is a test that did not run, and the dataset tests are the ones
guarding behaviour-before-labels.

---

## The portal

A FastAPI app that puts one **persona** in front of each report page: the page is embedded on
the right, a chat with the Data Agent on the left, both sharing the same accent colour.

```powershell
az login                  # the backend uses AzureCliCredential — no service principal
.\portal\start.ps1        # http://localhost:8000
```

`start.ps1` refuses to launch if `src/state.json` has no `workspace_id` / `report_id` /
`data_agent_id`, so a missing deploy surfaces as an error instead of an empty embed panel.

| Persona | Page | Question it answers |
|---|---|---|
| 🎯 Direction | Direction | how much value is exposed |
| 🛟 Retention | Retention | who is leaving, and which customers to call |
| 📣 Marketing | Marketing | which campaign caused it |
| 🛒 Commerce | Commerce | what it costs in revenue |

Everything is config-driven: personas live in the `AGENTS` dict in `portal/backend/main.py`,
the frontend discovers them from `/api/agents`, and all IDs are read from `src/state.json` —
there is not a single hardcoded GUID. Adding a persona is one dict entry.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | liveness + token expiry — hit this first on a 502 |
| `GET /api/agents` | persona registry + tenant/workspace context |
| `POST /api/agents/{key}/chat` | question → Data Agent, returns answer, tool trace, follow-ups |
| `GET /api/embed-token` | report embed URL + user token (user-owns-data) |
| `POST /api/admin/refresh-tokens` | force a token refresh without restarting |


---

## The Foundry plane â€” supervision

A second consumption surface on the same Fabric workspace. It ships **two bindings**, and they
are not two ways to do the same thing â€” they are two different contracts. `foundry.binding`
in `config.yaml` picks one; `--binding` overrides it for a run.

```
binding: fabric-iq   (default)
  Foundry agent â”€â”€FabricIQPreviewTool (MCP)â”€â”€â–º Fabric IQ  â”€â”€ data + ONT_Customer360
    Application Insights â—„â”€tracesâ”€â”˜            Foundry reasons over what comes back.
                                               THE SEMANTICS ARE YOURS.

binding: fabric-data-agent
  Foundry agent â”€â”€MicrosoftFabricPreviewToolâ”€â”€â–º Marketing_Churn_Agent (published)
    (wrapper)                                     â”œâ”€ ONT_Customer360       GQL  relationships
    Application Insights â—„â”€tracesâ”€â”˜               â””â”€ SM_Marketing_Analytics DAX every number
                                               Fabric answers. You inherit its semantics.
```

| | `fabric-iq` | `fabric-data-agent` |
|---|---|---|
| What comes back | retrieved content | an answer |
| Who defines "at risk" | **your prompt** | the data agent's instructions |
| Inherits the 49 tested DAX measures | âŒ | âœ… |
| Inherits *churn = buyers only*, threshold 65 | âŒ | âœ… |
| Tool approval | `require_approval` field, set in config | interactive, once, in the playground |
| Main failure mode | a partial retrieval presented as a **total** | you cannot see how the answer was produced |

**Attaching both to one agent is legal, silent, and almost always wrong** â€” two Fabric answers
that can disagree, with nothing in the response saying which you are reading. `build_tool()`
returns exactly one tool and `test_only_one_fabric_tool_is_ever_attached` keeps it that way.

**Why supervision is worth doing here.** Nothing in a reply says how it was produced. Under
`fabric-data-agent` that means you cannot tell GQL from DAX. Under `fabric-iq` it means you
cannot tell an aggregate from a set of rows the model counted â€” and on this dataset those two
can land on the same number. A trace is the only place either distinction exists. Same for the
harder failure: an agent that sounds grounded because it answered from its own prompt shows an
**absent tool-call span** and nothing else.

```powershell
pip install "azure-ai-projects>=2.4.0"     # docs floor is 2.2.0

# ONE-TIME. Two ways to create the Fabric IQ connection, and the auth type decides
# whether a Global Administrator is involved at all.
#
# --- portal, OneLake Catalog picker (fastest, and it BINDS THE ITEM) ---------
#   Agent > Tools > Add > Fabric IQ (OneLake Catalog) > pick the Fabric item
#   The picked item is baked into the connection, so the tool must NOT also send a
#   server_url -> foundry.fabric_iq_endpoint: "connection"
#
# --- code, no Entra app / no admin consent / no redirect URI -----------------
#   This exact ARM call was run against a live project and returned the connection:
#     az rest --method put --url ".../projects/<PROJ>/connections/<NAME>?api-version=2025-06-01" \
#       --body '{"properties":{"category":"RemoteTool","group":"GenericProtocol",
#                "authType":"UserEntraToken","audience":"https://api.fabric.microsoft.com",
#                "target":"<mcp url>","metadata":{"type":"fabric_iq_preview"}}}'
#   Two things no document states, both read off a working connection:
#     * metadata.type MUST be "fabric_iq_preview" or FabricIQPreviewTool refuses it
#     * the audience is the FABRIC one, not the Power BI one the azd docs show
#   `azd ai connection create --kind remote-tool --auth-type user-entra-token` is the
#   documented equivalent (needs `azd extension install microsoft.foundry`) -- NOT run here.
#
# --- the expensive third way: BYO Entra app ---------------------------------
#   Only if you specifically want a dedicated app identity. Costs an app registration
#   with Power BI delegated perms (Item.Execute.All / Item.Read.All), TENANT-WIDE
#   admin consent from a Global Administrator, and a redirect URI Foundry only emits
#   AFTER the connection exists. The docs detail this path at length and mention the
#   passthrough alternative in one line -- which is how it gets picked by mistake.
#
# --- fabric-data-agent binding: Custom Keys ---------------------------------
#   two secret keys off the published agent's URL .../groups/<WS>/aiskills/<ARTIFACT>?
#     workspace-id : between `groups/` and `/aiskills`
#     artifact-id  : between `aiskills/` and `?`   (no trailing ?)
#
# NOT code-able either way: a Fabric admin must PUBLISH the item (unpublished = 404),
# and you need the `Foundry Project Manager` role to create the connection plus
# `Foundry User` to run it. Note the role names: there is no "Azure AI User" role.

python src\deploy_foundry_agent.py --check     # preflight, creates nothing
python src\deploy_foundry_agent.py             # bind with foundry.binding from config.yaml
python src\deploy_foundry_agent.py --binding fabric-data-agent   # the other contract
python src\foundry_supervision.py --dry-run    # golden set + local truth, calls nothing
python src\foundry_supervision.py              # replay through Foundry, write a run log
python src\foundry_supervision.py --repeat 4   # ask each question 4x -- does the figure hold?
```

`foundry.agent_name` may not contain underscores. The service rejects it with a message
that names no field, so it reads like a connection fault; `check_agent_name()` catches it
in preflight instead.

**The prompt follows the target, not the binding.** Fabric IQ is a *router*, and what it
routes to decides which contract is correct:

| `fabric_iq_target` | what answers | prompt |
|---|---|---|
| `data_agent` | the published Fabric data agent — **inherits** the churn threshold, "buyers only" and the 49 DAX measures, returns real aggregates | pass-through |
| `ontology` | NL2Ontology over entities and relationships — raw retrieval, no inherited semantics | containment |
| `semantic_model` | measures and hierarchies via a fixed hub route | containment |

Handing the containment prompt to a data-agent-backed agent is not a safe default: it makes
the agent decline totals it was correctly given. `test_fabric_iq_over_the_data_agent_uses_the_pass_through_contract`
fails if that regresses.

> **Model choice is not cosmetic here.** Two independent sources say a mini model fails at
> this job: the Fabric IQ docs recommend `gpt-5.4` / `opus 4.7` for semantic-model measure
> reasoning, and the Contoso Customer360 workshop warns that mini models are unreliable at
> chaining several tools. `foundry.model_deployment` defaults to `gpt-4o`; raise it before
> concluding that a binding "does not work".

> **Delegated auth only â€” this caps the supervision loop.** Fabric IQ and the Fabric data
> agent both run **On-Behalf-Of the signed-in user**; application-only auth is *not*
> supported. So the replay cannot run unattended under a service principal â€” no CI cron, no
> nightly job, unless a human token is present. That is a property of the tool, not of this
> code, and it was found in the docs, never observed here.

**Which Fabric item answers is `server_url`, not the connection.** The connection carries the
identity; the endpoint carries the target â€” and this workspace holds an ontology *and* a
semantic model *and* a data agent. Omit `server_url` and the target is simply unstated. So it
is built from the ids `deploy_all.py` already wrote to `state.json`, never pasted:

| `foundry.fabric_iq_target` | endpoint | note |
|---|---|---|
| `ontology` | `â€¦/v1/mcp/dataPlane/workspaces/{ws}/items/{ontology_id}/ontologyEndpoint` | relationships, RCA |
| `data_agent` | `â€¦/v1/mcp/workspaces/{ws}/dataagents/{data_agent_id}/agent` | the only target supporting background mode |
| `semantic_model` | `â€¦/v1/mcp/fabricaihub/integrations/m365` | fixed hub route, **no item id** â€” you cannot say *which* model answers |

A URL in `config.yaml` would survive a redeploy into a new workspace and point at the old one;
`test_no_endpoint_is_hardcoded_in_the_config_example` fails on any `mcp` string in the block.

| Piece | What it does |
|---|---|
| `src/deploy_foundry_agent.py` | resolves the project connection **by name**, creates an agent version with `FabricIQPreviewTool` or `MicrosoftFabricPreviewTool`, writes `foundry_*` keys (including `foundry_binding`) to `state.json` |
| `src/foundry_supervision.py` | replays 10 golden questions, scores them against truth recomputed from `data/raw`, writes `data/supervision/run_*.json` with the binding recorded. `--repeat N` adds a verdict that needs **no reference data**: same question N times, does the leading figure hold? |
| `tests/test_foundry.py` | offline gate â€” containment clauses, no business fact in either prompt, one tool only, binding resolution, golden set integrity, answer-parser correctness |

Neither prompt carries **a single digit**. The Microsoft lab this pattern comes from pins ten
product ids into the same prompt that says "the response must come only from the tool output" â€”
a grounded agent with hardcoded facts is worse than an ungrounded one, because it looks sourced.
`test_wrapper_contains_no_business_fact` and `test_fabric_iq_prompt_contains_no_business_fact`
fail on any digit; the storyline tests fail if `CAMP_007` ever appears, because an agent handed
the culprit does not discover it.

The Fabric IQ prompt carries one clause the wrapper does not need, and it is the important one:
*never present the number of records you received as a total*. Retrieval returns what it
matched and never states whether that was everything. No retrieval setting prevents a model
from counting those rows and calling it the business figure â€” only the prompt contract does,
and only by making it say the total was not returned. A visible gap beats a plausible number.

**Verdicts.** `PASS` Â· `DRIFT` (figure returned but off â€” usually the deployed Lakehouse holds
an earlier draw) Â· `MISS` Â· `EMPTY` Â· `ERROR`. Every row also prints what the run **cannot**
settle, as a per-question checklist for the trace â€” and under `fabric-iq` the report says
plainly that a matching figure is not proof a measure was evaluated.

### Three gates, and one thing that is not verified

- **Connect Application Insights before the runs you intend to cite.** Telemetry is emitted
  as the run happens; a conversation that ran before the connection existed is not
  retroactively traceable. `--check` reports the status via
  `telemetry.get_application_insights_connection_string()`.
- **Set `require_approval` explicitly (`fabric-iq`).** Left unset, the SDK omits the field
  entirely â€” confirmed by serialising the payload â€” and the service applies its documented
  default, `always`. Every call then waits for a human and an unattended replay simply hangs.
- **Approve the tool by running the agent alone first (`fabric-data-agent`).** That tool has
  no approval field: consent is interactive, and a workflow preview has nowhere to show the
  prompt, so it errors instead. Playground â†’ force a tool call â†’ *Always approve this tool*.

> The SDK surface used here (`allow_preview`, `FabricIQPreviewTool`,
> `MicrosoftFabricPreviewTool`, `connections.get(name)`, `get_openai_client(agent_name=â€¦)`,
> `telemetry.â€¦`) was read off `azure-ai-projects==2.4.0` by introspection, and the emitted
> payload â€” `{"type": "fabric_iq_preview", "project_connection_id": â€¦, "require_approval": â€¦}`
> â€” by serialising a constructed definition. Neither was written from memory. What has **not**
> been verified is any live call: in particular what the `model` field must carry on the
> per-agent endpoint. `_ask()` tries both candidates and records the one that worked under
> `invocation_shape` â€” read it after the first real run and pin it. No OpenTelemetry code is
> written here at all, because the span and attribute names are unknown and a wrong field name
> in a trace-reading guide sends the next reader hunting something that was never there.

Foundry is deliberately **not** a step in `deploy_all.py`: a Foundry agent is not a Fabric
item, and everything on the workspace task flow must be backed by one.

---

### The supervisor — two subordinates, one contract

```
Marketing-Supervisor ──A2A──► Marketing-Churn-Front-Door ──MCP──► Marketing_Churn_Agent ──DAX──► Lakehouse
                     ──A2A──► Voice-Of-Customer          ──file_search──► VoC verbatim corpus
```

Four agents, four hops. The split is the whole point: **Fabric carries the numbers, the corpus
carries the motive, the supervisor carries neither** — it routes, then relays verbatim.

```powershell
python src\deploy_voc_agent.py            # upload the corpus + create the verbatim agent
python src\deploy_supervisor_agent.py     # connections + cards + the supervisor
python src\deploy_supervisor_agent.py --check     # preflight only, no writes
python src\verify_supervisor.py                   # three live checks, exit 1 on failure
```

**Both subordinates are reached over A2A, and that is architectural, not a preference.**
`a2a_preview` and `file_search` do not coexist on one agent. The obvious build — A2A for the
figures, `FileSearchTool` for the corpus — deploys cleanly and never routes. Isolated three
times, same instructions, same question, only the tool list changing:

| tools attached | does the A2A tool fire? |
|---|---|
| `a2a_preview` alone | **yes** — correct answer, with its scope |
| `a2a_preview` + `file_search` | **no** — 11-16 `file_search` calls instead |
| the same, plus `tool_choice="required"` | **no** — still `file_search` |

`file_search` describes itself to the model; an A2A tool surfaces only under its *connection
name*, which says nothing about what it fronts. Naming the tool in the prompt did not fix it.
So the corpus is exposed over A2A too, and the two tools are told apart **by name only** —
which is also why verification matches the connection name and asserts the other tool stayed
out. Both tools emit `a2a_preview_call`; the item type no longer identifies anything.

**An A2A target must carry an agent card.** `protocols: [a2a]` with no card is reachable and
unusable — the caller fails at invoke with `Failed to fetch agent card: 400`, which reads as a
permission problem and is not one. The deploy script writes the card and never overwrites an
existing one.

**"At risk" has three legitimate readings**, and a question that does not choose is
under-specified rather than unstable:

| question as asked | answer |
|---|---|
| `risk_band = 'High'` | one figure |
| `churn_risk_score >= 65` (High + Critical) | a larger one |
| `lifecycle_stage = 'at_risk'` | a smaller one |

All three are correct. The oscillation this used to look like was reproduced by querying the
Fabric data agent **directly**, with no Foundry in the loop — it is born in the semantic model,
not in the orchestration. The supervisor no longer picks one silently: asked an ambiguous
question it declines to answer and hands the choice back. That is the third live check.

## Workspace task flow

The workspace canvas that turns a flat item list into the story:

```
Ingest ──► Lakehouse ─┬─► Ontology (+ graph) ─┐
(notebook)            │                       ├─► Data Agent
                      └─► Semantic Model ─────┘
                              │
                              └──────────────────► Report
```

Both the semantic model **and** the ontology's graph feed the Data Agent — the dual-source rule
made visible on the canvas. The graph gets no task of its own (it is underlying to the ontology),
and the CSV→Delta notebook sits on the ingest task rather than duplicating it.

Fabric has **no public REST API for task flows**, so this is a generated JSON you import once:

```powershell
python src\build_taskflow.py     # -> taskflow\marketing_taskflow.json
```

Then workspace → task flow details pane → **Import and export task flow** → *Import*, and
replay the item assignments (the file cannot carry them). Full steps and the item→task table:
[`taskflow/README.md`](taskflow/README.md).

---

---

### Two deployment traps this repo now guards against

Both were hit for real on this tenant and both were **silent** — every script printed OK.

1. **`updateDefinition` can succeed and change nothing.** The call returns `202`, the operation
   polls to `Succeeded`, and the previous definition stays in place. The model sat one revision
   behind, so two report visuals were bound to measures that did not exist.
   `deploy_semantic_model.py` now reads the definition back, diffs the measure inventory, and
   re-pushes (up to 3×) before failing loudly.

2. **Direct Lake does not reframe on its own.** The setup notebook rewrites the Delta tables but
   the model keeps serving the previous snapshot, so the report and the Data Agent answer with
   stale numbers. `deploy_semantic_model.py` now forces a full refresh and waits for it.

A third, unrelated race: after deleting a notebook Fabric frees the *display name* later than it
removes the item from the listing, so recreating it returns `409 ItemDisplayNameNotAvailableYet`.
`notebook_utils.create_notebook()` retries on it.

The storyline is visible in the deployed data without being told: `Black Friday Blast` runs at
**3.90 sends per customer against 1.00** for the 19 other campaigns, and carries 247 unsubscribes.

> ⚠️ The local `data/raw` CSVs are a **different draw** than what is in the Lakehouse
> (981 at risk / 4.96 M€ locally). Re-running `generate_data.py` + the setup notebook will
> move the report and the agent onto the local figures. Regenerate both together, never one alone.

### Curated views (created by the setup notebook)

| View | Purpose |
|---|---|
| `v_churn_cohort` | the actionable at-risk customers with their drivers |
| `v_campaign_pressure` | sends per customer per campaign — exposes the over-mailing |

Built on the same idempotent pattern as the sister demos (`Fab-Live-Event`,
`Fab-Network-Operations`): config-driven, `state.json`, resumable `deploy_all.py`, mandatory test gate.
