# Copilot Instructions — Fab-Marketing-Campaign

## Mandatory Testing Gate
Before running ANY `deploy_*.py`, generator, or artifact script:
```bash
python -m pytest tests/ -v --tb=short
```
If ANY test fails → **STOP. Fix the code first. Do not proceed.**

## Project Context
- Python 3.12, Windows. Fabric API deploy scripts in `src/` (idempotent via `state.json`).
- **Customer 360 + churn** demo: CRM + Marketing + Commerce on Fabric.
  Lakehouse → Semantic Model (Direct Lake) → Ontology/Graph → dual-source Data Agent → Power BI.
- Workspace name + capacity_id + tenant_id come from `src/config.yaml` (copy `src/config.example.yaml`).
- Design source of truth: `docs/ARCHITECTURE.md`.

## The non-negotiable design rule
**Behaviour first, labels last.**
The generator simulates what each customer actually did (sends, opens, clicks, unsubscribes,
orders, support interactions) and only THEN derives `churn_risk_score`, `clv_eur` and
`lifecycle_stage` from that behaviour.

This exists because the predecessor project drew `churn_risk_score` from a distribution: it
correlated with **nothing** (|r| < 0.02 against every behavioural signal), the mean was flat across
lifecycle stages, and 5% of customers were simultaneously "critical risk" and NPS-9 promoters.
Every churn question was therefore unanswerable and no ML story was possible.

`tests/test_smoke.py` enforces this — correlation floors, direction of effect, band coverage,
aggregates matching the transactional truth. **Never weaken those tests to make data pass.**

## The storyline (must stay discoverable in the data)
Campaign **CAMP_007 "Black Friday Blast"** over-mails the **SEG_HIGH_VALUE** segment (4× sends)
→ unsubscribe spike → engagement halves → orders stop → churn.
Roughly half the at-risk cohort traces back to it, so RCA genuinely finds the cause instead of
being told the answer.

- Churn is scoped to **buyers only**. A contact who never ordered has a *conversion* problem, not a
  churn problem — they get `risk_band = "Prospect"` and score 0.
- Weights live in `config.yaml → churn_model.weights` and must sum to 1.0 (tested).

## Data split
- **Lakehouse** (Delta, 15 tables): CRM (accounts, customers, segments, customer_segments,
  interactions, customer_profile), Marketing (campaigns, assets, audiences, sends, events),
  Commerce (products, orders, order_lines, returns).
- **Text corpus**: customer_knowledge_notes + email_bodies (for AI transformations / RAG).
- The **Semantic Model** answers the numbers; the **Ontology/Graph** answers the relationships.

## Deploy order (strict)
workspace → lakehouse → setup notebook (CSV→Delta) → semantic model → ontology → graph →
report → data agent.
One command: `python deploy_all.py` (idempotent, tenant-guarded).

## Foundry plane (separate from the Fabric pipeline)
`src/deploy_foundry_agent.py` binds a Foundry agent to Fabric — **`fabric-iq` by default**
(`FabricIQPreviewTool`, an MCP surface over Fabric IQ), or `fabric-data-agent`
(`MicrosoftFabricPreviewTool`, delegating to the published Fabric data agent).
`src/foundry_supervision.py` replays a golden set and writes a run log. Deliberately
**not** a `deploy_all.py` step — a Foundry agent is not a Fabric item and every task on the
workspace task flow must be backed by one.
- **One binding per agent.** Attaching both is legal, silent, and almost always wrong: two
  Fabric answers that can disagree, with nothing in the response saying which you read.
  `build_tool()` returns exactly one tool (tested).
- **`fabric-iq` is a ROUTER, not a data source — the prompt follows the target.** Pointed at
  the published Fabric data agent (`fabric_iq_target: data_agent`) it *inherits* that agent's
  semantics — the churn threshold, "buyers only", the 49 DAX measures — and returns real
  aggregates, so the pass-through contract applies. Pointed at an ontology or a semantic
  model it is raw retrieval with no inherited semantics, and the containment contract is the
  only thing between matched rows and a company-level figure nobody computed. Handing
  containment to a data-agent-backed agent makes it decline totals it was correctly given
  (tested). `instructions_for(binding, target)` owns this choice.
- **`server_url` selects WHICH Fabric item is queried — the connection only carries the
  identity.** Three MCP routes: ontology
  (`/v1/mcp/dataPlane/workspaces/{ws}/items/{id}/ontologyEndpoint`), data agent
  (`/v1/mcp/workspaces/{ws}/dataagents/{id}/agent`) and Power BI semantic models
  (`/v1/mcp/fabricaihub/integrations/m365`, a fixed hub route with **no item id**).
  But **whether to send it depends on how the connection was made** — `fabric_iq_endpoint`:
  - `connection` — the portal's OneLake Catalog picker already bound one item into the
    connection. Sending `server_url` on top is a second, possibly contradictory, statement of
    the target and nothing reports which won. Omit it.
  - `explicit` — the connection came from `azd ai connection create` against a bare MCP
    target, so the item must be named in the tool.
  Build the URL from `state.json` (`fabric_iq_server_url()`); never paste one into
  `config.yaml` — it would survive a redeploy into a new workspace and point at the old one
  (tested). Only the **data agent** route supports background mode.
- **Delegated (OBO) auth only — application-only is not supported**, for Fabric IQ *and* for
  the Fabric data agent tool. An unattended replay under a service principal cannot work.
  Any "nightly supervision job" design must account for a human token. (From the docs, not
  observed here.)
- **The connection IS code-able, and two of its required fields are undocumented.**
  Read off a working connection and reproduced by hand against a live project (both
  accepted): `category: RemoteTool`, `group: GenericProtocol`, `authType: UserEntraToken`,
  and crucially `metadata: {"type": "fabric_iq_preview"}` — **without that marker the
  connection resolves but `FabricIQPreviewTool` refuses it**. The audience is
  `https://api.fabric.microsoft.com`, **not** the `https://analysis.windows.net/powerbi/api`
  the azd docs show for Fabric connections. `arm_connection_body()` holds the exact payload.
  `user-entra-token` forwards the signed-in user's own token: no app registration, no client
  secret, no tenant-wide admin consent, no redirect URI. `client.connections` has no
  `create`, so the data-plane SDK is not the route; ARM (or `azd`) is.
- **The generic Fabric host works.** `https://api.fabric.microsoft.com/v1/mcp/...` and the
  per-workspace `https://<ws-no-hyphens>.z04.w.api.fabric.microsoft.com/v1/mcp/...` both
  returned 200 on MCP `initialize`. Build the generic one — the `z04` cluster segment is
  derivable from nothing we hold. The portal writes the per-workspace form; do not copy it.
- **Role names are `Foundry User` and `Foundry Project Manager`.** There is no "Azure AI
  User" role — `az role assignment create` fails with "Role doesn't exist". Subscription
  **Owner does not grant the data plane**; assign these explicitly on the account.
- **`foundry.agent_name` may not contain underscores.** `create_version` rejects it with
  "Must start and end with alphanumeric characters..." naming **no field**, so it reads as a
  connection fault. `check_agent_name()` catches it in preflight.
- **Still not code-able:** a Fabric admin must PUBLISH the item (unpublished = unreachable at
  the MCP endpoint = **404**, the most likely first error).
- **The Fabric IQ failure mode is a retrieval presented as a total.** Retrieval returns the
  records it matched and never says whether that was all of them. The prompt must forbid
  counting/summing/extrapolating them into a company-level figure and must say "the total
  was not returned" instead — a visible gap beats a plausible number (tested).
- **`require_approval` must be set explicitly.** Unset, the SDK omits the field from the
  payload (confirmed by serialising it) and the service defaults to `always`: every call
  waits for a human and an unattended supervision replay hangs.
- Connection is resolved **by name**; a GUID in code breaks environment promotion (tested).
- `allow_preview=True` is mandatory, and `azure-ai-projects` must be **>= 2.4.0** (the 1.x
  line has no `PromptAgentDefinition` and neither Fabric tool).
- Neither prompt carries **a single digit** — a grounded agent with hardcoded facts is worse
  than an ungrounded one, because it looks sourced.
- For `fabric-data-agent`, approve the tool by running the agent **alone** first; that tool
  has no approval field and a workflow preview cannot show the consent prompt, so it errors.
- Connect Application Insights **before** the runs you want to cite — telemetry is not
  retroactive. The trace is the only place "how was this produced" exists: GQL vs DAX under
  `fabric-data-agent`, aggregate vs counted rows under `fabric-iq`.
- Under `fabric-iq`, a figure matching the local truth is **not** proof a measure was
  evaluated — the model may have summed what it received and landed on the same number.
- No OpenTelemetry code: span/attribute names are unknown here. Do not invent them.

## Supervisor plane (Foundry orchestrating two subordinates)

    Marketing-Supervisor --A2A--> Marketing-Churn-Front-Door --MCP--> Fabric data agent --DAX--> Lakehouse
                         --A2A--> Voice-Of-Customer          --file_search--> VoC corpus

The rule the user set, and it is not negotiable: **the data world stays on the data side**.
Fabric owns semantics, measures, DAX/GQL routing and the ontology. Foundry orchestrates,
retrieves documents, synthesises. Foundry recomputes nothing. The latency (40-60 s per
supervised answer) is the price and it has been accepted — do not propose moving routing
into Foundry to save seconds.

- **`a2a_preview` and `file_search` DO NOT COEXIST on one agent.** The obvious build (A2A for
  the numbers + `FileSearchTool` for the corpus) deploys fine and never routes. Isolated three
  times, same instructions, same question, only the tool list changing:
  `a2a_preview` alone -> fires, correct answer; `a2a_preview` + `file_search` -> A2A never
  fires, 11-16 `file_search` calls instead; adding `tool_choice="required"` -> still
  `file_search`. `file_search` describes itself to the model; an A2A tool surfaces only under
  its **connection name**, which says nothing about what it fronts. Naming the tool in the
  prompt was not enough (two attempts). The fix is architectural: expose the corpus over A2A
  too, so both tools are the same nature and are told apart **by name only**.
- **An A2A target MUST carry an agent card.** `protocols: [a2a]` with no `agent_card` is
  reachable and unusable: the caller fails at invoke with `Failed to fetch agent card: 400`,
  which reads as a permission fault and is not one. `ensure_incoming_a2a()` writes the card
  and **never overwrites an existing one** (it may have been hand-tuned).
- **Read and write the endpoint as raw JSON, never through the SDK.** `AgentEndpointConfig`
  has no `protocols` field, so `agent.as_dict()` returns `None` for a block the REST API
  returns in full, and writing through the SDK model would silently drop `protocols` — i.e.
  disable `responses` and break the front door. Merge-patch also **replaces arrays**: re-list
  every protocol or you turn one off.
- **`api-version` is the literal string `"v1"`** for the Agents API (a date-shaped value
  returns 400 and reads as a broken route). ARM connections use `2025-06-01`.
- The A2A target URL is `{project_endpoint}/agents/{name}/endpoint/protocols/a2a`, audience
  `https://ai.azure.com`, `metadata.type: "a2a_preview"`. **Never set `agent_card_path`** —
  Foundry resolves the card and negotiates the protocol version; setting it is actively
  harmful. A tool needs a **project connection**, not a bare `base_url`: a URL alone has no
  identity to fetch the card with and returns 401 PermissionDenied.
- **Verify routing by connection NAME, not by item type.** Both tools emit
  `a2a_preview_call`, so the type no longer identifies which subordinate ran — only `name`
  does. A check on the type alone passes while the supervisor asks the corpus for a number.
  Assert the expected tool fired **and** the other did not.
- **Connections are not validated at creation.** A connection pointing at a `.invalid` host
  is accepted with HTTP 200; it fails only at invoke.
- **The supervisor must relay figures VERBATIM, with their scope.** Left free it rewrote
  "825 customers (risk_band High + Critical)" into "800 customers." — reintroducing one storey
  up the ambiguity that was just fixed. It must never recompute, and must surface an empty
  retrieval instead of retrying until documents appear: a retry loop manufactures confidence
  it does not have (the corpus agent returns nothing outright ~11% of the time).
- **A relay contract with no interpretation mandate produces an echo, and the fix is not the
  prompt.** The supervisor read as a bare data agent because the prompt only ever said what
  *not* to add — and, decisively, because **no suggested question in the app could reach the
  second subordinate** (`Source` was `'model' | 'ontology'`, both routing through the same
  front door). With one source there is nothing to juxtapose, so the prompt rewrite alone was
  cosmetic. Before blaming a synthesis prompt, check that two sources actually arrive.
- **Granting interpretation buys the opposite defect, and it is worse.** Asked for provenance,
  the model produced a bibliography: the same five facts as a lead sentence *and* as bullets,
  seventeen verbatims across seven themes each followed by a gloss, and a closing paragraph on
  what to ask next. Every sentence true and sourced; the whole unusable — the dominant grievance
  sat third of seven at the same weight as one voiced once, *a false picture assembled out of
  true quotations*. Ask for economy in the same breath as provenance, or you get neither.
- **A prose rule cannot beat a structural mandate — remove the habitat, not the behaviour.**
  "Give the provenance in one place and nowhere else" was in the prompt and was disobeyed,
  because the *shape* section mandated two places: a lead carrying the scope, and a "what the
  data measured" section. The model saw a sentence and a bullet list as different presentations,
  not a repeat. Deleting the second location cut the answer 3517 -> 1500 chars in one deploy.
  Same mechanism on volume: "a couple of quotations" was read **per theme**, so cap the
  *themes* ("the dominant one, at most one other"), not the quotations.
- **Ask the reading to say what stands out — never what to ask next.** "Name the question worth
  asking next, and the subordinate that would answer it" put process talk at the end of every
  answer, exactly where the insight was expected.
- **Prompt-level economy IS followed here, but only when it is structural.** Measured on the
  same question across three versions: mandate removed -> effect immediate and large. Adjectives
  ("sparingly", "concise") moved nothing on their own.
- **"At risk" has three legitimate readings** and a question that does not choose is
  under-specified, not unstable: `risk_band='High'` -> 800, `churn_risk_score >= 65` (High +
  Critical) -> 825, `lifecycle_stage='at_risk'` -> 593. All three are correct. The old
  825/593 oscillation was **born in Fabric**, reproduced by querying the data agent directly
  without Foundry in the loop. Disambiguate the question — naming the table AND the column —
  instead of retrying. The same trap bit `unsubscribe`: asked without naming the table the
  agent searched `crm_interactions` (where that event type does not exist) and answered 0;
  naming `marketing_events.event_type` returned the real figure.
- **A2A hops are observable live.** `responses.create(stream=True)` emits
  `response.output_item.added` for the A2A call at ~20% of the total latency (8.9 s of 43.4 s
  measured), then keepalives. Only the **supervisor's own** hops stream; what happens inside
  the front door (MCP -> data agent -> DAX) arrives only with the A2A output. Do not animate
  inner steps as if they were observed.
- **`data/raw` is no longer a source of truth.** The local draw and the Lakehouse carry
  different per-row draws (population identical: same customers, same segments, same campaign
  targeting; counts differ). Coherence is judged against **what Fabric answers**, not against
  the CSVs.
- Application Insights showed the full span tree once (`invoke_agent` -> `execute_tool` ->
  `invoke_agent` -> `execute_tool` -> `chat`) and now returns nothing at all. Do not rely on
  it as the proof that a tool ran; the raw response items name the connection.

### Answer economy — the four rules that cost thirteen versions
- **Never let the agent hand the question back.** The prompt licensed "ask the reader which
  reading they meant" as the cure for ambiguity; the agent then asked — on a question the user
  reached by **clicking the app's own suggestion**, one click into a demo, answering a prepared
  question with an intake form and zero sources. **Decide out loud instead**: name a criterion,
  pick the widest actionable cohort, declare the reading. The reading is still declared, so
  nothing was lost; only the refusal is gone. Removing an escape hatch means asserting its
  **absence** in a test, or the model satisfies the prompt through the easier branch.
- **Cap the wrong unit and the verbosity just moves.** Learned three times: capping *themes*
  left quotations-per-theme free; capping *layout* ("one line per record") was obeyed with seven
  attributes on the line; capping *facts per line* was obeyed with a fifty-row list — 8 591
  characters, more than twice the answer it replaced; capping that produced sub-bullets under
  each record, the same form in a third orientation. **Name a countable unit** ("more than three
  things in total") and **leave no neighbouring unit free**. The only cap that no re-orientation
  routes around is the **total**: one screen, about thirty lines. Measured effect: spread went
  from 2 070–8 591 characters to **2 004–3 079** across six runs.
- **Duplication migrates rather than dies.** Killed at the provenance level it came back as
  identifiers printed in the lead *and* again in the list below.
- **A prose rule cannot stop a model ending its turn early — move the constraint into the
  definition.** About one turn in four returned in ~6 s with **zero tool calls**, replying with
  its plan ("I must first question the two sources separately" — an echo of the routing rule read
  as a procedure), which the app renders as a one-line answer under an "unsourced" banner. Adding
  a prose rule against it did not hold. `PromptAgentDefinition(tool_choice="required")` makes it
  unrepresentable: **6/6 runs answered, 6/6 dual-source**. `tool_choice` is not in
  `_attribute_map` but is accepted through `**kwargs` and serialises. Safe **only because both
  tools are subordinates** — with a `file_search` in the list, `required` is satisfied by the
  wrong tool (that is the earlier failed experiment, a different problem with a different cure).
- **Judge a prompt change on a batch, never on one run.** Three runs of one version gave 2 070 /
  2 748 / 6 024 characters with every rule identically in place. A single probe would have
  "proved" whichever conclusion it drew.
- **`_ask()` already returns extracted items.** Calling `_items()` on its result gives `[]`,
  which reads exactly like "no subordinate fired" — the same signal the app's unsourced banner
  uses. A tooling bug and a real regression are indistinguishable here; confirm against the
  answer text before believing either.

## Inherited lessons — do not relearn these
- **One Fabric item has ONE owning generator.** Two sessions published to the same report
  id (`<REPORT_ID_REDACTED>` — real identifiers never belong in this file) and silently
  overwrote each other for a day. The fix that
  held was not a better guard, it was **a single owner**: `src/deploy_report.py`, and the second
  generator was deleted. `test_there_is_exactly_one_report_generator` fails if a `deploy_report*.py`
  reappears — adding one must be a decision, not something that shows up in a merge. If you do add
  one, give it a **distinct display name** *and* a **distinct `state.json` key**: sharing either is
  enough to collide, and a guard that only checks the *id* leaves a hole on a first run.
  **Deleting a generator can silently delete its tests.** The layout suite was written against the
  arc module, so removing the file would have taken the entire clipping protection with it while
  the gate stayed green. Port the tests onto the survivor *first*, and prove the refactor is
  output-neutral by hashing the built report before and after.
- **Data Agent must be DUAL-SOURCE**: ontology (GQL) for relationships/RCA/impact, semantic model
  (DAX) for every number. The Fabric IQ ontology TimeSeries/measure path returns empty for value
  questions — proven twice on sister projects. Route explicitly in `aiInstructions`.
- **Legacy PBIX only** for reports (`report.json` with `sections[].visualContainers[]`), never PBIR.
  Every visual needs a `prototypeQuery`. Multi-colour bars need the same column in Category AND Series.
- **Power BI clips text — it never shrinks the font and never warns.** A box too short for its
  font renders truncated glyphs on stage with a perfectly successful deploy. Never eyeball
  geometry — `validate_layout()` computes the fit and `sys.exit(1)`s before publishing.
  Two terms, kept separate: line height is **proportional**, padding/chrome is a **constant**.
  A single "px per pt" multiplier cannot express a constant and under-sizes small text.
  `min_height = pt * (96/72) * 1.35 + 8`  (1pt = 4/3 px @96 DPI, Segoe UI line box ~1.35, 8px pad).
  A `cardVisual` stacks **three** texts — title + callout value + category label — and **each keeps
  its own padding**: `min_height = sum(pt * 1.8 + 8) + 24`. The earlier model collapsed the three
  pads into one (`sum(pt) * 1.8 + 32`) and was **disproved by a render**: a 112px card holding
  11 + 24 + 9 pt computed 111.2, passed the validator, and shipped with its bottom label clipped on
  screen. That single observation now outranks the derivation, and it is locked by
  `test_the_card_stack_that_clipped_on_screen_is_rejected`.
  **The one constant with evidence behind it is that stack; 1.35, 8 and 24 are still calculated.**
  Re-check visually when a font size changes, and treat a passing validator as necessary, not
  sufficient.
  **`show: false` is a request, not a guarantee — Power BI ignored ours.** All 20 cards shipped
  with `objects.categoryLabel.show = false`; the definition read back from Fabric still carried it,
  and the renderer drew the label anyway, clipped, because the box had been sized for two texts.
  The validator was not wrong — it believed a declaration the engine did not honour. So **never let
  a hide toggle be what makes a box fit**: size for every text the visual declares, and treat the
  saved space as a bonus if the toggle happens to work. `test_a_card_fits_even_if_power_bi_ignores_show_false`
  computes the stack ignoring `show` entirely; `test_the_category_label_is_declared_visible` keeps
  the label on, because satisfying a requirement by relying on a renderer bug is not satisfying it.
  A validator must model what is **rendered**, not what is declared: read `show`, and treat an
  absent group as **visible** (that is Power BI's default). Inferring "hidden" from a missing
  `fontSize` under-reserves.
  Growing the card is then a **grid** change, not a card change: 112 → 128 pushed the cards from
  y=88..200 to y=88..216, so content row 1 had to move 208 → 224 and shrink 242 → 226 to keep its
  bottom at 450. Bind the call sites to `CARD_Y/CARD_H/ROW1_Y/ROW1_H/ROW2_Y/ROW2_H` rather than
  repeating literals 34 times — the constants existed while the call sites still hard-coded 88/112,
  which is exactly how a grid drifts from the model that validates it.
- Header bands are `z=0` decoration with the title/subtitle textboxes at `z=1` on top; the overlap
  check therefore only considers `z >= 1`. Two textboxes that overlap by 2px is a real defect.
- **Renaming a measure in a shared semantic model is a breaking change.** Fabric does not warn,
  the report keeps the old reference and fails only at render with "Something's wrong with one or
  more fields". Keep the old name as a hidden alias measure (`isHidden: true`) pointing at the new
  one, and check consumers before deploying.
- **Validate columns, not just measures.** `EVALUATE ROW("v",[M])` only exercises measures and lets
  broken column references through; test a column with `EVALUATE TOPN(1, VALUES('t'[c]))`.
  A broken column kills a visual just as hard.
- **Semantic model**: avoid ambiguous relationship paths (two routes between the same two tables) —
  the model import fails outright.
- **`COUNTROWS(FILTER(...))` returns BLANK, not 0** → wrap in `COALESCE(..., 0)`.
- **One shared semantic model, several generators → the push must be a UNION.** Each script
  defines the model in full, so pushing it replaces everything and whoever deploys last erases the
  other's measures. `carry_over_measures_reports_use()` in `deploy_semantic_model.py` reads the live
  model back and copies over any measure it doesn't define that a report still uses (hidden, DAX
  verbatim). Unused ones are still dropped.
- **`updateDefinition` can return 202/Succeeded while applying nothing** → always read the
  definition back and compare before declaring success.
- **A wrong `az` tenant does not look like an auth problem — it looks like a broken artefact.**
  `az` silently flips back to the corporate tenant. The token stays valid and lists 172 workspaces
  — of the *other* directory — so Fabric answers 404 EntityNotFound and the Power BI REST API
  answers **401 with an empty body**. That 401 reads as "expired token" and sends you diagnosing
  auth while the report is healthy: it once turned a passing report into a fake 0/35 and nearly
  got its content rewritten. Before believing any Fabric failure, check
  `az account show --query tenantId` against `config.yaml → tenant_id`.
  The guard is `helpers.ensure_tenant(cfg)`, called from **every** entrypoint's `main()`
  (`deploy_all`, `deploy_report`, `validate_report`) — there is a test that
  fails if a new one forgets. One shared implementation, so it cannot drift between scripts.
- Capacity pauses when idle → resume before deploy/demo.
- Never use `az rest` from a Python subprocess (hangs). Use `requests` + `az account get-access-token`.
- PowerShell `Set-Content -Encoding utf8` writes a **BOM** and breaks JSON parsing → use
  `[System.IO.File]::WriteAllText(path, text, (New-Object System.Text.UTF8Encoding $false))`.
- PowerShell `Measure-Object -Line` counts **non-empty lines only** — an empty string is 0 lines.
  It under-reports any file with blank lines, whatever the source (`Get-Content`, a pipeline, a
  hand-made array). Proof: `@("a","","b") | Measure-Object -Line` → 2. For a real count use
  Python (`blob.count(b"\n")`).
- **A clean merge is not a correct merge.** Git guarantees no textual collision, not the absence of
  semantic redundancy. Merging two branches that independently learned the same lesson left
  `copilot-instructions.md` stating four rules twice, with **no conflict** — different words, same
  rule. After any merge that touches shared prose or tests, check for *duplicated meaning*, not just
  markers. For test files that means scanning every module-scope `def`, not just `def test_` — a
  duplicated fixture is worse than a duplicated test: pytest stays green and the surviving function
  runs under the expected name with the wrong body. (Fixtures are module-scoped, so the same name in
  two files is fine — unless a `conftest.py` exists, and then a local fixture silently shadows it.)
- **Any literal that lands in the tenant is shared state.** `description`, `displayFolder`,
  `aiInstructions` — if two generators word them differently, each alternating deploy flips them in
  the live artefact. Invisible in code review, visible only in Fabric, impossible to attribute after
  the fact. `aiInstructions` is the worst: it routes the Data Agent, so the agent's behaviour changes
  between deploys with no code change. Align the wording across generators, don't just merge it.
- Terminal PATH can be wiped by venv activation — restore from Machine+User env.

## Public repo — the guard must not become the leak
This repo is public. `.github/scripts/check_client_leak.py` runs in CI on every branch.

**Detect by shape, never by name.** Two sister repos guard against customer names by
listing those names in the guard itself — one plainly, one with a letter parenthesised so
a full-text search misses it and a human does not. A public file enumerating a client
portfolio is a worse disclosure than the single mention it was written to catch. Every
rule here matches a form: an unrecognised GUID, an opaque token in front of a Fabric
service domain, a personal filesystem path, initials-plus-name anchored at the start of a
display name, a renamed-repo shape. If a name-based check is genuinely needed, read it
from `CLIENT_DENYLIST` (Actions secret) or a gitignored `.clientdeny`, and **degrade to a
warning when absent — never a failure**.

**The GUID rule is an allow-list, and that is the point.** The sister rule only flags a
GUID preceded by an identity label (`tenant_id`, `client_id`). The real report id this
repo published sat in the middle of a prose sentence, so that rule would have walked past
it for 27 commits. An allow-list also never has to write the real identifier down in order
to catch it.

**Scan `git ls-files`, never the working tree** — `__pycache__/*.pyc` and `node_modules`
embed absolute paths and produce guaranteed false positives.

**Exempt lines, not files.** The guard and its tests must spell out fake leaks to prove
the rules fire. Exempting the whole file (what the sister repos do) blinds the scanner to
everything else it contains. The marker here is per-line **and inert outside the guard's
own two files** (`PROBE_FILES`), so documenting it cannot silence a line and spraying it
elsewhere does nothing.

**Text rules fire on prose, including your own.** The workspace-prefix rule caught a
sentence in the README that was *describing* the rule. Reword; do not exempt. A shape rule
that cannot fire on documentation is a shape rule with a hole in it.

**A finding is a hypothesis, not a verdict — and acting on a wrong one costs content.** The
allow-list flagged three `github.com/user-attachments/assets/<guid>` URLs in the README and I
deleted them, which silently removed three screenshots. Those ids address images GitHub was
already serving from this public repo: public by construction, unrelated to the tenant. A
deny-list would never have seen them; an allow-list sees *every* identifier, so the burden is
on the reader to classify each one before removing anything. **Ask what the identifier
addresses** — a Power BI report in a private tenant is a leak, a CDN asset behind a public
README is not. Symptom to watch for: a "fix" that deletes rendered content rather than
replacing a value. Exempt at the **span** of the URL, never at the line, or one image tag
blinds everything sitting next to it. Both directions are pinned by
`test_a_github_attachment_url_stays_silent` and
`test_the_attachment_exemption_covers_the_url_and_nothing_else`.

**Concatenated string literals split a line, and line-scoped rules do not span them.** The
first version of those tests wrapped the URL across two source lines; the guard then saw a
bare GUID with no URL around it and failed on the test file itself. Keep any literal a
line-based rule must match on **one physical line**.

**A guard must constrain the surface it protects — pin the file that ships.** The neutral
workspace name was pinned by asserting on `src/config.yaml`, which is **gitignored** and
therefore cannot leak anything. It could not protect the repo, and it did constrain the
operator's own Fabric workspace: the gate went red on a machine whose live workspace is
legitimately named something else, and the only ways out were renaming a customer-facing
workspace two days before a demo or deleting the check. The pin belongs on
`src/config.example.yaml` (`test_the_published_workspace_name_is_pinned_neutral`), and what
makes that sufficient — the private file never shipping — is itself asserted by
`test_the_private_config_can_never_be_published`, which runs `git check-ignore`. Before
writing an assertion about publication, ask **whether the file it reads is published**;
if it is not, the assertion is aimed at the operator, not at the repo.

## CI runs on Linux; this repo was written on Windows
Every `src/*.py` carried `import os, sys, winreg` at module scope for the PATH workaround.
`winreg` is Windows-only, and the test suite imports those modules directly — so on
`ubuntu-latest` the whole suite died at collection. **A Windows machine cannot reproduce
it**, and a fresh-clone rehearsal on Windows proves nothing about the runner. Import it
under `if sys.platform == "win32"` and make `_restore_path()` return early elsewhere;
`test_no_src_module_imports_winreg_unconditionally` reads the **AST**, not the text, so a
comment mentioning winreg is not a false positive.

Spoofing `sys.platform` to fake a Linux run does not work: third-party packages branch on
it and then call the real OS's stdlib. `"linux"` makes numpy call `os.uname()`, `"darwin"`
makes urllib import `_scproxy` — both absent on Windows, both failures about the
simulation rather than about this repo. Check the structure and the behaviour separately,
and say plainly that the Linux run itself is unverified until CI runs it.

**A skip is a test that did not run.** 28 tests were skipped whenever `data/raw/` was
missing — including the entire behaviour-before-labels gate. CI regenerates the dataset
from its seed and fails the job if the summary reports any skip.

## Never claim "verified"
Do not write that something works in docs, instructions or agent prompts unless a test output or a
trace proves it. A false "verified" makes everything downstream retry a path that cannot work.

**Scope your claim to the command you ran.** Four false statements in one thread shared one
mechanism: a true command, then a sentence broader than what it covered. `git ls-tree main` says
what main has — it says nothing about the other branch. Compare both sides in the same command
(`Compare-Object (git ls-tree -r A --name-only) (git ls-tree -r B --name-only)`), and name the ref:
`origin/main` and a local `main` can differ materially.

## Rayfin app plane — the V2 static SPA

V2 of the demo UI is a **static SPA hosted by Rayfin on a Fabric capacity**, calling Fabric and
Foundry **directly from the browser** with MSAL tokens. V1 was Python/FastAPI on `localhost:8000`
holding operator `az` tokens and reading `state.json` from disk. The data platform did **not** move:
the app sits on one capacity, the Lakehouse, semantic model, ontology and agents stay on another,
and the cross-region read works — a Sweden-hosted app enumerated the items of a West US 3 workspace
with a granular Fabric token. Hosting the app elsewhere is not a reason to touch the data plane.

### The Rayfin CLI reports success it did not achieve — twice

- **`rayfin init` is silently broken by a non-ASCII character anywhere in the target path.** It
  prints the whole ceremony (`Copying template files`, `Project created successfully`) and **exits
  0**, but writes only the Rayfin scaffolding (`rayfin.yml`, `AGENTS.md`, `.mcp.json`, the skill):
  no `package.json`, no `index.html`, no `src/`. Nothing in the output can be grepped for. Isolated
  one variable at a time: a plain temp path OK; a temp path with spaces and dashes OK — so it is not
  the name shape; a repo path containing `Démo` **absent**; `%TEMP%\rf-Démo` **absent**. The `é` is
  the cause. `npm install`, `tsc -b`, `vite build` and the entire `rayfin up` pipeline are
  unaffected by the same path. Workaround: scaffold under `%TEMP%`, then `robocopy /E /XD
  node_modules` into the repo.
- **`npm run` and `rayfin up` exit 1 even on success** — npm audit advisories fold into the exit
  code. **Verify the artefact, never the exit code**: fetch the hosting URL, confirm the new asset
  hash in `index.html`, then grep the served bundle for a string only the newest build can contain.

### Renaming a Rayfin app is the "one item, one owner" rule wearing a different hat

`rayfin.yml → id:` drives the AppBackend displayName (**not** `name:`), and the CLI calls
`getOrCreateRayfinItem(workspaceId, displayName)`, which **creates an item when the name is not
found**. `rayfin/.deployments.json` is gitignored and pins `fabricItemId` under the sanitized
workspace name, and the deploy short-circuits to it. So editing `id:` on the machine that deployed
is a **silent no-op**, while the identical edit on a fresh clone produces a **second AppBackend**
and the two then drift apart with nobody watching. Rename in this order: `PATCH
/v1/workspaces/{ws}/items/{id}` on the live item, read it back to verify, then align `id:` and
`name:` to that exact string. Hosting URLs are stable per item and are auto-appended to
`allowedRedirectUris` after the first deploy, so a second item also means a second URL.

### `data.enabled: true` provisions a SQL Database nobody asked for

The CLI logs *"No entity classes found — skipping database configuration"*, which only skips
**applying the DAB schema**. The `SQLDatabase` and `SQLEndpoint` items are created regardless, and
setting `data.enabled: false` on a later deploy does **not** remove them —
`DELETE /v1/workspaces/{ws}/items/{id}` does, and deleting the database takes its endpoint with it.
An app that reads a Lakehouse through a semantic model has no use for either.

### `.env.local` is generated, and its failure mode is invisible

`rayfin env` rewrites `.env.local` on every `predev`/`prebuild`; it carries a do-not-edit header.
App variables belong in `.env.production.local` / `.env.development.local` — gitignored, loaded by
Vite at higher precedence, never touched by Rayfin. **Confirm the `VITE_*` values are actually
inlined in the served bundle.** A missing `VITE_ENTRA_CLIENT_ID` does not raise: `msalConfigured`
evaluates to `false` and the app ships with authentication *silently disabled*, which looks like a
working app until someone asks it for data.

### Fabric static hosting has no server surface, and the bundle is public

A Fabric Data App has exactly three child services — SQL Database, Authentication, Static Content —
exposing only `/api/graphql`, `/auth`, `/storage`. `@microsoft/rayfin-functions` installs, but its
own README calls it experimental, it contributes zero entries to the docs corpus and it has no
child-service row in the portal: **do not build a demo on it**. SPA history-mode fallback works (a
deep link to a route with no physical file returns the app HTML with 200); whether the host rewrites
unknown `.html` paths to `index.html` is **unverified**, which is why the redirect landing page
carries its own guard (below). **The hosting URL returns 200 with no credentials at all** — every
value the bundle inlines at build time is published. Treat that as a design input, not a footnote.

### Rayfin session tokens cannot call Fabric — the app runs its own MSAL

Rayfin Fabric Auth exchanges the portal handoff for **opaque** Rayfin session tokens; the docs say
to gate UI on `isAuthenticated` and nothing further. They do not authorize
`api.fabric.microsoft.com`. So `bootstrapAuth()` returns the MSAL service whenever
`VITE_ENTRA_CLIENT_ID` is set, and **the whole Rayfin embedded-auth path is dead code in
production**. Any future reasoning about `initEmbeddedAuth` must start from MSAL, not from the
Rayfin SDK — reading the SDK path first cost a full debugging cycle on code that never runs.

### The popup came back and nothing happened — MSAL v5 stopped polling

**Symptom:** the popup opens, the user picks an account (so Entra accepted the request), and then
the popup just sits there showing *the app's own sign-in card*; `loginPopup()` never resolves and
eventually throws `BrowserAuthError: timed_out`, subcode `redirect_bridge_timeout`. It reads as a
broken redirect URI or a consent problem. It is neither.

**First diagnosis, wrong:** that the popup was booting the full SPA and `AuthGuard`'s
`<Navigate to="/auth" replace />` was destroying the response hash. That is a real failure mode and
it is worth guarding, but it was not what was happening.

**Actual cause, read from the installed source:** MSAL v5 **no longer polls the popup's URL**.
`waitForBridgeResponse()` opens `new BroadcastChannel(libraryState.id)` and waits; the landing page
must publish to it by calling `broadcastResponseToMainFrame()` from
`@azure/msal-browser/redirect-bridge`, which sets `document.title`, posts the payload and closes the
window. **A genuinely blank page is a dead end** — it broadcasts nothing, so the opener times out.
The prebuilt UMD bundle does not self-execute either; it only assigns the export.

**The diagnostic tell:** the popup's title stays the app's own title. If the bridge had run it would
have renamed the document. Title unchanged ⇒ the bridge never executed. Check that before
suspecting auth.

Three structural consequences:
- **`redirectUri` must not be the app root.** Point it at a dedicated landing page, or the popup
  boots the router and the router discards the fragment.
- **That landing page must be a Vite build entry, not a `public/` file.** `public/` is copied to
  `dist/` *after* the build and silently overwrites what `rollupOptions.input` just produced.
- **Guard `main.tsx` anyway:** before `createRoot(...).render(...)`, if `window.location.hash`
  matches `/(^|[#&?])(code|error|state|id_token)=/`, import the bridge, broadcast, and do **not**
  mount the router. This costs four lines and covers the unverified `.html` rewrite above.

Also: MSAL 5.19 removed `auth.navigateToLoginRequestUrl` and `cache.storeAuthStateInCookie` — both
now fail the build with `TS2353`.

### Inside the Fabric portal the app is in a cross-origin iframe

The portal opens the app with `?fabricEmbedded=true`. **`ssoSilent` cannot succeed when framed** — a
nested iframe to Entra is third-party/partitioned storage — and it will simply never settle. Detect
it by attempting `window.top.location.href`: the `SecurityError` *is* the proof, so return `true`
on throw, and skip `ssoSilent` in that case.

That non-settling promise is what produced the permanent "Loading…" hang, because the startup chain
awaited it inside a `.finally()` and `loading` never cleared. **The exact promise was never
identified** — the portal host cannot be simulated in dev — so the fix is deliberately defensive
rather than specific: wrap every startup await in `withTimeout(promise, step, ms)` with an 8-second
budget, and on timeout surface a visible warning plus an "open in a new tab" escape hatch. A hang
that reports itself is recoverable; a spinner is not. **Whether `loginPopup()` succeeds in-frame is
still unproven — the standalone URL is the only proven path.**

### Two audiences, one service principal — the wrong one returns 401

Both resources resolve to the **Power BI Service** principal, but they must be requested as separate
audiences in separate `acquireToken` calls:
- `https://api.fabric.microsoft.com` — Fabric REST (`GET /v1/workspaces/{ws}/items`, MCP routes)
- `https://analysis.windows.net/powerbi/api` — **`executeQueries`**, and nothing else works there
- `https://ai.azure.com` (Azure ML Services, `user_impersonation`) — the Foundry agent endpoint

`az account get-access-token --resource https://api.fabric.microsoft.com` returns
`scp: user_impersonation`, but only because the Azure CLI is a **pre-authorized first-party client**.
A custom SPA cannot follow that precedent and must request the granular delegated scopes
(`Item.Execute.All`, `Item.Read.All`, `Workspace.Read.All`, `Dataset.Read.All`) with admin consent;
the feared `AADSTS65002` did not materialise once they were consented — the token came back carrying
all four in `scp`. Register the app as **SPA** (no secret), `signInAudience: AzureADMyOrg`, and when
adding redirect URIs **PATCH the full array** — `spa.redirectUris` replaces, it does not merge.

**A successful CORS preflight proves only that the endpoint accepts cross-origin requests.** It says
nothing about whether an authenticated POST will succeed or whether the registration can obtain the
token. Every endpoint used here preflights clean, including `executeQueries` (which omits
`Access-Control-Allow-Methods` — not a blocker, POST is safelisted).

### DAX from the browser — the fast path, and its sharp edges

`POST https://api.powerbi.com/v1.0/myorg/datasets/{datasetId}/executeQueries` with the
`analysis.windows.net` audience, body `{"queries":[{"query":"EVALUATE …"}],"serializerSettings":
{"includeNulls":true}}`. **One query per request — it is not a batch endpoint.** Measured **~1.3 s**
end to end, against **43–47 s** for a supervised Foundry answer: the supervisor is for
natural-language questions and root-cause narration, and must never be on a page-load path. When it
is used, show a live elapsed-second counter, because a 45-second silent spinner reads as a crash.

- Result row keys are `table[column]` for dimensions and `[Alias]` for measures.
- **`TOPN` keeps ties** — `TOPN(12, …)` came back with **21 rows**. Order and slice client-side when
  you need exactly N.
- A dimension query can return a **blank row** as a join artefact; filter it in the mapper.

**Never recompute an aggregate in TypeScript.** Dividing 825 at-risk by 12 000 customers gives 6.9 %
while the Power BI report says 7.847 %, because the report correctly divides by *buyers*. Both
numbers are defensible and they contradict each other on screen, which is the "second source of
truth" trap the ontology and measures exist to prevent. Bind to the model's own measures — and note
that this is the same ambiguity as the three readings of "at risk" documented above, one layer up.

### Dark mode over hardcoded Tailwind — an unlayered block, and where it misses

When `bg-white` / `text-slate-900` / `border-slate-300` are spread across a dozen files, the
least-disruptive retrofit is an **unlayered CSS block at the end of `main.css`** that remaps those
utilities: Tailwind emits its own inside `@layer utilities`, and an unlayered rule beats a layered
one whatever the specificity. Tailwind v4's `@custom-variant dark` must be pointed at
`[data-theme='dark']`, not the default class selector.

What that retrofit does **not** catch, each of which shipped a real defect:
- **`gray-*` and `slate-*` are different scales** — cover both families explicitly.
- **`bg-white/10` is a distinct class name** from `bg-white` and is not remapped. That is exactly
  what keeps glass headers working in both themes, so it is a feature — but it means alpha variants
  must be reasoned about separately.
- **Tinted alert islands** (`bg-amber-50`, `bg-red-50`) left near-white on a dark page read as a
  rendering fault. Flip them to dark tints; the alert still separates by hue.
- **A gradient background is not a `background-color`** and is never matched. Lifting the text of
  such a page to a light colour without theming its background produces white on white — on the
  first screen a user sees. Theme those pages with `var(--…)` tokens instead, which also makes them
  immune to the next retrofit change.

Measure, do not eyeball: on the dark card `#1e293b`, `#f1f5f9` is 13.35:1, `#cbd5e1` 9.85:1,
`#94a3b8` 5.71:1 — and `#64748b` is **3.07:1**, below the AA floor. That last one was the
"pas très lisible" the user reported. `html { font-size: 115% }` is the single sizing dial: never
set a font size in px, only `rem` or Tailwind `text-*`.

### Windows tooling, additions specific to this app

- **`npm.ps1` is blocked by ExecutionPolicy** → `& "$env:ProgramFiles\nodejs\npm.cmd"`, or the
  `.cmd` shims under `node_modules\.bin\`. `node` itself is fine.
- **`$array -match 'pattern'` returns the matching elements, not a boolean.** Used on a bundle it
  dumped **794 KB** into the transcript. Use ``($arr -join "`n").Contains('…')``.
- **`Invoke-WebRequest` mis-decodes UTF-8 and can hang on a credential prompt** when a request
  returns an unexpected status. Probe served bundles with **ASCII-only** fragments, and use
  `curl.exe -s -o NUL -D -` for headers (5.1 has no `-SkipHttpErrorCheck`).
- **`[System.IO.File]::ReadAllText("relative.css")` resolves against .NET's CWD, not the shell's.**
  It will happily read a different file. Pass absolute paths.
- **Rayfin's stderr surfaces as `System.Management.Automation.RemoteException` lines** — noise, not
  an error; filter them out before reading the log.
- **`git status` takes >90 s in this OneDrive-synced repo after an `npm install`.** For a single
  file, `git check-ignore <path>` answers instantly.

