#!/usr/bin/env python3
"""
Bind a Microsoft Foundry agent to Fabric -- by default to FABRIC IQ.

    python deploy_foundry_agent.py --check                       # preflight only, creates nothing
    python deploy_foundry_agent.py                               # binding from config.yaml
    python deploy_foundry_agent.py --binding fabric-data-agent   # the other contract

WHAT THIS IS
    A second plane on top of the Fabric demo. Foundry adds the agent surface and, with
    Application Insights, the only place where "which source actually answered" is visible
    at all -- a chat response never says it.

TWO BINDINGS, TWO CONTRACTS -- pick deliberately, they are not interchangeable
    fabric-iq  (default)
        `FabricIQPreviewTool` -- an MCP surface over Fabric IQ, bound by a project
        connection. Foundry reaches into the data and the ontology and reasons over what
        comes back. The SEMANTICS ARE YOURS: nothing in the Fabric data agent's instructions
        travels with it -- not the churn threshold, not "churn applies to buyers only", not
        the DAX measures. The prompt below carries the guard rails instead, and its hardest
        job is to stop the model turning a partial retrieval into a company-level total.

    fabric-data-agent
        `MicrosoftFabricPreviewTool` -- delegates the question to the PUBLISHED Fabric data
        agent, which answers with its own reasoning and its own metric definitions. You
        inherit the curated semantics; you give up seeing how the answer was produced.

    Attaching BOTH to one agent is legal, silent, and almost always wrong: two Fabric answers
    that can disagree, and nothing in the response telling you which path produced either.
    Hence one binding per agent, chosen here, recorded in the agent metadata.

WHY IT IS NOT IN deploy_all.py
    A Foundry agent is not a Fabric item. It does not belong on the workspace task flow and it
    uses a different endpoint and a different token scope. Keeping it out of the orchestrator
    keeps `deploy_all.py` honest: everything in there produces a Fabric item.

CREATING THE PROJECT CONNECTION -- THIS *IS* CODE-ABLE
    An earlier revision of this file claimed the connection was a portal step with no code
    path. That was wrong: `azd ai connection create` does it, and the auth type chosen there
    decides whether a Global Administrator has to be involved at all.

    fabric-iq, recommended -- no Entra app, no admin consent, no redirect URI:
        azd extension install microsoft.foundry
        azd ai project set <foundry.project_endpoint>
        azd ai connection create <foundry.fabric_iq_connection_name> \
            --kind remote-tool \
            --target <the server_url built by fabric_iq_server_url()> \
            --auth-type user-entra-token \
            --audience https://analysis.windows.net/powerbi/api
      `user-entra-token` forwards the SIGNED-IN USER'S OWN Entra token. Nothing to register,
      nothing to consent to, no secret to rotate.

    fabric-iq, the other way -- BYO Entra app. Only needed if you specifically want a
    dedicated app identity. Costs an app registration with Power BI delegated permissions
    (Item.Execute.All, Item.Read.All), TENANT-WIDE ADMIN CONSENT from a Global Administrator,
    and a redirect URI that Foundry only emits AFTER the connection exists. Do not walk this
    path by default -- the Microsoft docs detail it at length and mention the managed/
    passthrough alternative in one line, which is how it gets picked by mistake.

    fabric-data-agent -- Custom Keys, two secret values read off the published agent's URL:
        https://.../groups/<WORKSPACE-ID>/aiskills/<ARTIFACT-ID>?...
        workspace-id : between `groups/` and `/aiskills`
        artifact-id  : between `aiskills/` and `?`   (do NOT include the `?`)

    NOT code-able, either way:
      - a Fabric admin must PUBLISH the item (ontology / semantic model / data agent).
        Unpublished is unreachable at the MCP endpoint and returns 404.
      - Foundry Project Manager to create the connection, Foundry User to run it.
      - the calling identity needs access to the Fabric workspace (Manage access). Foundry
        reaching into Fabric is a cross-service identity hop and it is the first thing to
        check on a permission error, before anything else.

    NOTHING HERE HAS BEEN RUN. The commands above come from the docs, not from a trace.

    This script resolves the connection BY NAME. No GUID is ever written into code, so the
    same script promotes across environments unchanged -- only the connection differs.

THE GATE THAT WILL BREAK YOUR DEMO
    `FabricIQPreviewTool.require_approval` defaults to "always": every call pauses the run
    waiting for a human. That is correct for a first manual run and fatal for an unattended
    supervision replay, which simply hangs. `foundry.require_approval` in config.yaml sets
    it explicitly, so the posture is a recorded decision rather than a default nobody read.
    The Fabric data agent tool has no such field -- there you approve once, interactively,
    in the playground ("Always approve this tool"), and a workflow preview has nowhere to
    show that prompt, so an unapproved tool surfaces as a run error.

REQUIREMENTS
    pip install "azure-ai-projects>=2.4.0"
    The 1.x line is the previous generation and carries neither Fabric tool.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from helpers import load_config, load_state, save_state, print_step

SCRIPT_DIR = Path(__file__).parent
PLACEHOLDERS = ("<", "0000")

FABRIC_IQ = "fabric-iq"
FABRIC_DATA_AGENT = "fabric-data-agent"
BINDINGS = (FABRIC_IQ, FABRIC_DATA_AGENT)

# Fabric IQ exposes a DIFFERENT MCP endpoint per Fabric item type, and the tool targets
# exactly one of them. The connection carries the OAuth identity; `server_url` carries the
# target. Omit it and you have not said what to query -- so it is built here, from the ids
# deploy_all.py already wrote to state.json, and never pasted by hand.
#   docs: learn.microsoft.com/azure/foundry/agents/how-to/tools/fabric-iq#find-your-fabric-iq-server-details
IQ_ONTOLOGY = "ontology"
IQ_SEMANTIC_MODEL = "semantic_model"
IQ_DATA_AGENT = "data_agent"
FABRIC_IQ_TARGETS = (IQ_ONTOLOGY, IQ_SEMANTIC_MODEL, IQ_DATA_AGENT)

# WHERE the endpoint comes from is a separate question from WHAT the target is.
#   "connection" -> the portal's OneLake Catalog picker already bound one Fabric item into
#                   the connection. Sending a server_url on top would be a second, possibly
#                   contradictory, statement of the target. Omit it.
#   "explicit"   -> the connection was made with `azd ai connection create` against a bare
#                   MCP target, so the item must be named in the tool.
IQ_FROM_CONNECTION = "connection"
IQ_EXPLICIT = "explicit"
IQ_ENDPOINT_SOURCES = (IQ_FROM_CONNECTION, IQ_EXPLICIT)

# state.json key each target needs. The semantic model endpoint is a fixed hub route and
# takes no item id at all -- which also means it cannot be pointed at one specific model.
IQ_TARGET_STATE_KEY = {
    IQ_ONTOLOGY: "ontology_id",
    IQ_DATA_AGENT: "data_agent_id",
    IQ_SEMANTIC_MODEL: None,
}

# Config keys each binding needs on top of the common ones.
BINDING_CONNECTION_KEY = {
    FABRIC_IQ: "fabric_iq_connection_name",
    FABRIC_DATA_AGENT: "fabric_connection_name",
}

# Keys written back to state.json so the supervision runner needs no arguments.
STATE_KEYS = ("foundry_agent_name", "foundry_agent_version", "foundry_project_endpoint",
              "foundry_binding")


# -- the Fabric IQ prompt (default binding) -----------------------------------
# Fabric IQ returns RETRIEVED CONTENT, not a computed answer. Nothing from the Fabric data
# agent's instructions comes with it, so every guard rail the curated agent provided has to
# exist here instead -- and the one that matters is not "don't hallucinate", it is
# "don't turn the rows you received into a total". Retrieval over a table of customers hands
# back the records it matched; a model asked "how many are at risk" will count them and
# present that count as the business figure, confidently, with no way for the reader to tell.
#
# That failure cannot be prevented by retrieval config. It is a prompt contract, and making
# the agent say "the total was not returned" is the whole point: a visible gap beats a
# plausible wrong number. The three containment clauses come from the observed lab; the
# counting section is this project's own exposure.
#
# Deliberately contains NO business figure, no campaign id, no threshold -- a grounded agent
# with hardcoded facts looks sourced and keeps answering after the data moves.
# tests/test_foundry.py fails if a digit appears below.
FABRIC_IQ_INSTRUCTIONS = """You are the Marketing Churn analyst for this Fabric workspace.

Your only source of truth is the Fabric IQ tool. It reaches into this business's customers,
segments, campaigns, orders and interactions, and into the ontology that links them. You have
no other knowledge of this company and no memory of it.

What to do
- Query the Fabric IQ tool before answering anything. Every turn is grounded, including
  follow-ups and clarifications.
- Ground every statement in what the tool returned: the figures, the entity names, the
  relationships.

Content handling
- Generate responses only from the retrieved content and tool output. Do not assume or
  invent any value.
- Do not summarise away, filter, or drop records the tool returned. The reader may need them.
- If the required information is not in the tool output, say clearly that it could not be
  found. An empty result is a valid answer; an invented one is not.
- Never answer from general knowledge, and never restate a figure the tool did not return.

Counting and totals -- the rule that matters most here
- Retrieval returns the records it matched. That is not the same thing as every record that
  exists, and nothing in the result tells you whether it was complete.
- Never present the number of records you received as a total for the business. If you were
  not handed an explicit aggregate, say that the total was not returned, then describe what
  you actually saw and over what scope.
- Never sum, average, or extrapolate returned records into a company-level figure unless the
  tool itself returned that figure as an aggregate.
- Whenever you quote a figure, state the entity or scope it applies to.

Style
- Lead with a direct one-line answer, figures as digits.
- Then a short bullet list of the values or entities the tool returned.
- Your reader is a CRM or marketing lead: concise and operational."""


# -- the wrapper prompt (fabric-data-agent binding) ---------------------------
# Pattern B: exactly one tool, a rigid pass-through contract, no ambition beyond it.
#
# Deliberately contains NO business figure, no campaign id, no threshold. A wrapper that
# carries facts is worse than one that carries none, because it looks sourced: it keeps
# answering confidently after the data moves, and the chat gives you no way to tell.
# tests/test_foundry.py fails if a digit appears below.
WRAPPER_INSTRUCTIONS = """You are the Marketing Churn front door.

Your only capability is the Fabric Data Agent tool. It fronts a curated Fabric data agent
that already knows this business -- its customers, segments, campaigns, orders and its churn
model -- and that has its own rules for which underlying source answers which kind of
question. You do not second-guess those rules and you do not reimplement them.

What to do
- Send the user's question to the Fabric Data Agent tool, essentially unchanged.
- Return what the tool returns: the figures, the entity names, and the path it says it took.
- Send follow-up questions to the tool as well. Every turn goes through the tool.

Content handling
- Do not generate summaries or remove any data from the response.
- The response must come only from the Fabric Data Agent tool output.
- Never answer from general knowledge, and never restate a figure the tool did not return.
- If the tool returns nothing, or returns an error, say so plainly. An empty result is a
  valid answer; an invented one is not.
- Do not claim which underlying source produced the answer unless the tool says so itself.

Style
- Lead with a direct one-line answer, figures as digits.
- Then a short bullet list of the values or entities the tool returned.
- Your reader is a CRM or marketing lead: concise and operational."""


def resolve_binding(cfg_binding, cli_binding) -> str:
    """CLI overrides config; config overrides the default. Unknown values fail loudly."""
    chosen = (cli_binding or cfg_binding or FABRIC_IQ).strip().lower()
    if chosen not in BINDINGS:
        raise SystemExit(f"Unknown binding '{chosen}'. Pick one of: {', '.join(BINDINGS)}")
    return chosen


def foundry_config(cfg=None, binding: str | None = None) -> dict:
    """Read and validate the `foundry:` block of config.yaml for one binding.

    Only the connection the chosen binding actually uses is required. Demanding both would
    force anyone running Fabric IQ to invent a data-agent connection name they never create.
    """
    cfg = cfg or load_config()
    fnd = cfg.get("foundry")
    if not fnd:
        raise SystemExit(
            "No `foundry:` block in src/config.yaml. Copy it from src/config.example.yaml "
            "and fill in project_endpoint / model_deployment / the connection name."
        )
    binding = resolve_binding(fnd.get("binding"), binding)
    required = ("project_endpoint", "model_deployment", "agent_name",
                BINDING_CONNECTION_KEY[binding])
    missing = [k for k in required if not str(fnd.get(k, "")).strip()]
    if missing:
        raise SystemExit(f"config.yaml -> foundry: missing {missing} (binding={binding})")
    unset = [k for k in required if any(p in str(fnd[k]) for p in PLACEHOLDERS)]
    if unset:
        raise SystemExit(f"config.yaml -> foundry: still on example placeholders: {unset}")
    return {**fnd, "binding": binding}


def instructions_for(binding: str, target: str | None = None) -> str:
    """Pick the prompt from what actually answers -- the TARGET, not the binding.

    Fabric IQ is a router, not a data source. Pointed at the published Fabric data agent it
    inherits that agent's semantics (the churn threshold, 'buyers only', the DAX measures)
    and returns real aggregates -- so the pass-through contract applies and the containment
    clause would be actively harmful: it would make the agent refuse to state totals it was
    correctly given. Pointed at an ontology or a semantic model it is raw retrieval, and the
    containment contract is the only thing standing between a set of matched rows and a
    company-level figure that nobody computed.
    """
    if binding == FABRIC_DATA_AGENT or (binding == FABRIC_IQ and target == IQ_DATA_AGENT):
        return WRAPPER_INSTRUCTIONS
    return FABRIC_IQ_INSTRUCTIONS


def connection_name(fnd: dict) -> str:
    return str(fnd[BINDING_CONNECTION_KEY[fnd["binding"]]])


def fabric_iq_server_url(cfg: dict, state: dict, target: str) -> str:
    """Build the Fabric IQ MCP endpoint for one Fabric item.

    Ids come from state.json, which deploy_all.py already filled -- so the URL cannot drift
    from the workspace that was actually deployed, and no GUID is typed twice.
    """
    if target not in FABRIC_IQ_TARGETS:
        raise SystemExit(f"Unknown fabric_iq_target '{target}'. "
                         f"Pick one of: {', '.join(FABRIC_IQ_TARGETS)}")
    # config carries ".../v1"; the MCP routes hang off it.
    base = str(cfg.get("fabric_api_base", "https://api.fabric.microsoft.com/v1")).rstrip("/")

    if target == IQ_SEMANTIC_MODEL:
        return f"{base}/mcp/fabricaihub/integrations/m365"

    ws = state.get("workspace_id")
    item = state.get(IQ_TARGET_STATE_KEY[target])
    missing = [n for n, v in (("workspace_id", ws), (IQ_TARGET_STATE_KEY[target], item)) if not v]
    if missing:
        raise SystemExit(
            f"state.json is missing {missing} -- cannot build the Fabric IQ '{target}' "
            "endpoint. Run deploy_all.py first; the ids are written there."
        )
    if target == IQ_ONTOLOGY:
        return f"{base}/mcp/dataPlane/workspaces/{ws}/items/{item}/ontologyEndpoint"
    return f"{base}/mcp/workspaces/{ws}/dataagents/{item}/agent"


def project_client(fnd: dict, credential=None):
    """Build an AIProjectClient with preview surfaces enabled.

    `allow_preview=True` is not optional: the Fabric tool and the per-agent OpenAI endpoint
    are preview surfaces, and without it they are simply absent -- with an error that does
    not mention preview anywhere.

    `credential` is an escape hatch for callers that need a different token timeout. The
    default AzureCliCredential gives `az` 10 seconds, which is enough for one call and not
    enough when several threads ask at once -- see deploy_voc_agent's corpus upload.
    """
    try:
        from azure.ai.projects import AIProjectClient  # noqa: PLC0415
    except ImportError as e:
        raise SystemExit(
            f"azure-ai-projects is not installed ({e}). "
            'Run: pip install "azure-ai-projects>=2.4.0"'
        )
    try:
        from azure.ai.projects.models import PromptAgentDefinition  # noqa: F401,PLC0415
    except ImportError:
        raise SystemExit(
            "The installed azure-ai-projects is the previous generation (1.x): it has no "
            'PromptAgentDefinition and no Fabric tool. Run: pip install --upgrade "azure-ai-projects>=2.4.0"'
        )
    from azure.identity import DefaultAzureCredential  # noqa: PLC0415

    return AIProjectClient(
        endpoint=fnd["project_endpoint"],
        credential=credential or DefaultAzureCredential(),
        allow_preview=True,
    )


FABRIC_AUDIENCE = "https://api.fabric.microsoft.com"
# The connection must carry this marker or FabricIQPreviewTool will not recognise it.
# Read off a working portal-made connection, then reproduced by hand -- both accepted.
FABRIC_IQ_CONNECTION_METADATA = {"type": "fabric_iq_preview"}


def arm_connection_body(server_url: str) -> dict:
    """The exact ARM payload that creates a working Fabric IQ connection.

    Every field here was read back off a connection the portal had made, not guessed:
    category/group/authType/audience/metadata. `metadata.type` is the one that matters
    and the one no document mentions -- without it the connection exists but the tool
    does not accept it.

    Note the audience: `https://api.fabric.microsoft.com`. The Foundry azd docs show
    `https://analysis.windows.net/powerbi/api` for Fabric connections; the working
    connection uses the Fabric one. Prefer what the service produced over what the doc says.
    """
    return {"properties": {
        "category": "RemoteTool",
        "group": "GenericProtocol",
        "authType": "UserEntraToken",
        "audience": FABRIC_AUDIENCE,
        "target": server_url,
        "isSharedToAll": False,
        "useWorkspaceManagedIdentity": False,
        "metadata": dict(FABRIC_IQ_CONNECTION_METADATA),
    }}


AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def check_agent_name(name: str) -> str:
    """Reject an agent name the service will reject, before the network call.

    Underscores are the trap: they read as legal in every other name in this repo (config
    keys, table names, state keys) and the service rejects them with a message that names
    no field -- "Must start and end with alphanumeric characters..." -- so you cannot tell
    whether it means the agent, the server_label or the connection. Observed on a real
    create_version call with agent_name "Marketing_Churn_Front_Door".
    """
    if AGENT_NAME_RE.match(name or ""):
        return name
    raise SystemExit(
        f"config.yaml -> foundry.agent_name '{name}' will be rejected by the service.\n"
        "   Alphanumeric start and end, hyphens allowed in the middle, 63 chars max.\n"
        "   Underscores are NOT allowed -- the service error names no field, so this "
        "would look like a connection problem.\n"
        f"   Try: {re.sub(r'[^A-Za-z0-9-]+', '-', name or 'agent').strip('-') or 'agent'}"
    )


def azd_connection_hint(name: str, binding: str, server_url: str | None = None) -> str:
    """The command that creates the missing connection.

    An error that says "create it in the portal" sends you down the BYO-Entra-app path and
    into a Global Administrator's queue. `user-entra-token` needs neither.
    """
    if binding == FABRIC_IQ:
        target = server_url or "<the Fabric IQ MCP endpoint -- run --check to have it printed>"
        body = json.dumps(arm_connection_body(target), separators=(",", ":"))
        return (
            "   Create it once (no Entra app, no admin consent, no redirect URI).\n"
            "   Straight ARM -- this exact call was run and returned the connection:\n"
            "     $u=\"https://management.azure.com/subscriptions/<SUB>/resourceGroups/<RG>\"\n"
            "        + \"/providers/Microsoft.CognitiveServices/accounts/<ACCOUNT>\"\n"
            f"        + \"/projects/<PROJECT>/connections/{name}?api-version=2025-06-01\"\n"
            f"     az rest --method put --url $u --body '{body}'\n"
            "   `metadata.type: fabric_iq_preview` is what makes FabricIQPreviewTool accept\n"
            "   it -- a connection without it resolves but the tool refuses it.\n"
            "   Or, if you prefer azd (same result, extension required, NOT run here):\n"
            "     azd extension install microsoft.foundry\n"
            "     azd ai project set <your project endpoint>\n"
            f"     azd ai connection create {name} --kind remote-tool \\\n"
            f"         --target \"{target}\" --auth-type user-entra-token \\\n"
            f"         --audience {FABRIC_AUDIENCE}"
        )

    return (
        "   Create it once with Custom Keys -- two SECRET values off the published agent's URL\n"
        "   https://.../groups/<WORKSPACE-ID>/aiskills/<ARTIFACT-ID>?...\n"
        f"     azd ai connection create {name} --kind remote-tool ...\n"
        "     or portal: Management center -> Connected resources -> + New connection\n"
        "                Auth type: Custom Keys, keys `workspace-id` and `artifact-id`"
    )


def resolve_connection(client, name: str, binding: str = FABRIC_IQ,
                       server_url: str | None = None):
    """Resolve the project connection by NAME."""
    try:
        return client.connections.get(name)
    except Exception as e:
        raise SystemExit(
            f"Project connection '{name}' did not resolve ({type(e).__name__}: {e}).\n"
            f"{azd_connection_hint(name, binding, server_url)}\n"
            "   Names are exact, and the connection must live in THIS project."
        )


def app_insights_status(client) -> str:
    """Is Application Insights connected? Telemetry is emitted as a run happens.

    A conversation that ran before the connection existed is not retroactively traceable --
    so the run you most want to inspect, the first failed demo, is exactly the one you
    cannot. Check this BEFORE the runs you intend to cite.
    """
    try:
        conn = client.telemetry.get_application_insights_connection_string()
    except Exception as e:
        return f"NOT connected (or not readable): {type(e).__name__}: {e}"
    if not conn:
        return "NOT connected -- Foundry portal -> Traces -> Connect."
    ikey = conn.split("InstrumentationKey=")[-1].split(";")[0]
    return f"connected (InstrumentationKey ...{ikey[-6:]})"


def preflight(cfg, state, fnd) -> bool:
    """Everything checkable before creating anything. Returns False if a gate failed."""
    ok = True
    binding = fnd["binding"]
    print(f"   Binding             : {binding}")
    iq_url = None

    if binding == FABRIC_DATA_AGENT:
        agent_id = state.get("data_agent_id")
        mode = state.get("data_agent_mode")
        print(f"   Fabric data agent   : {cfg.get('data_agent_name')} "
              f"({'deployed' if agent_id else 'MISSING -- run deploy_data_agent.py'})")
        print(f"   Fabric agent mode   : {mode or 'unknown'}")
        if not agent_id:
            ok = False
        if mode and mode != "dual-source":
            print("   !  The Fabric agent is not dual-source. Numbers come from the semantic "
                  "model; an ontology-only agent answers value questions with nothing.")
    else:
        # Fabric IQ does not read the data agent at all -- the semantics are in the prompt.
        # What it does need is data in the workspace, so the lakehouse is the real dependency.
        lh = state.get("lakehouse_id")
        print(f"   Lakehouse           : {cfg.get('lakehouse_name')} "
              f"({'deployed' if lh else 'MISSING -- run deploy_all.py'})")
        print(f"   Ontology            : {cfg.get('ontology_name')} "
              f"({'deployed' if state.get('ontology_id') else 'not in state.json'})")
        if not lh:
            ok = False
        target = str(fnd.get("fabric_iq_target", IQ_ONTOLOGY))
        source = str(fnd.get("fabric_iq_endpoint", IQ_FROM_CONNECTION))
        if source not in IQ_ENDPOINT_SOURCES:
            print(f"   !  foundry.fabric_iq_endpoint = {source} -- expected "
                  f"{' or '.join(IQ_ENDPOINT_SOURCES)}.")
            ok = False
        iq_url = fabric_iq_server_url(cfg, state, target)
        print(f"   Fabric IQ target    : {target}")
        if source == IQ_FROM_CONNECTION:
            print("   Fabric IQ endpoint  : from the connection (portal OneLake Catalog "
                  "picker)")
            print(f"   (cross-check)       : the item this config describes is {iq_url}")
            print("   !  server_url is NOT sent. If the connection was bound to a DIFFERENT")
            print("      item in the catalog, nothing here will say so -- the agent will "
                  "answer,")
            print("      from the wrong item. Open the tool in the portal and read the item "
                  "name.")
        else:
            print(f"   Fabric IQ server_url: {iq_url}  (sent explicitly)")

        if target == IQ_DATA_AGENT:
            print("   Prompt contract     : pass-through (the data agent owns the semantics)")
            print("      Fabric IQ fronting the data agent inherits the churn threshold, "
                  "'buyers")
            print("      only' and the DAX measures. It returns real aggregates.")
        else:
            print("   Prompt contract     : containment (raw retrieval, no inherited "
                  "semantics)")
            print("      The churn threshold, 'buyers only' and the DAX measures are NOT in "
                  "play.")
        if target == IQ_SEMANTIC_MODEL:
            print("   !  The semantic model endpoint is a fixed hub route with no item id: it")
            print("      cannot be pointed at one specific model. Which model answers is not")
            print("      something this binding lets you state.")

    approval = str(fnd.get("require_approval", "")).strip().lower()
    if binding == FABRIC_IQ:
        if approval not in ("never", "always"):
            print(f"   !  foundry.require_approval = {approval or '(unset)'} -- expected "
                  "'never' or 'always'.")
            print("      Unset means the SDK default, which is 'always': an unattended "
                  "supervision replay will hang.")
        else:
            print(f"   Tool approval       : {approval}"
                  f"{' (unattended replay possible)' if approval == 'never' else ''}")

    client = project_client(fnd)
    name = connection_name(fnd)
    check_agent_name(fnd["agent_name"])
    resolve_connection(client, name, binding, iq_url)
    print(f"   Project connection  : {name} -> resolved")
    print(f"   Application Insights: {app_insights_status(client)}")

    try:
        client.agents.get(fnd["agent_name"])
        print(f"   Foundry agent       : '{fnd['agent_name']}' exists (a new version is added)")
    except Exception:
        print(f"   Foundry agent       : '{fnd['agent_name']}' not found (will be created)")

    client.close()
    return ok


def build_tool(fnd: dict, conn, server_url: str | None = None):
    """Construct the tool for the chosen binding. One binding, one tool, deliberately."""
    from azure.ai.projects.models import (  # noqa: PLC0415
        MicrosoftFabricPreviewTool, FabricDataAgentToolParameters,
        ToolProjectConnection, FabricIQPreviewTool,
    )
    if fnd["binding"] == FABRIC_IQ:
        return FabricIQPreviewTool(
            project_connection_id=conn.id,
            # server_url selects WHICH Fabric item is queried -- the connection only carries
            # the identity. Left out, the target is unstated.
            server_url=server_url,
            server_label=str(fnd.get("fabric_iq_server_label", "fabriciq")),
            require_approval=str(fnd.get("require_approval", "always")).strip().lower(),
        )
    return MicrosoftFabricPreviewTool(
        fabric_dataagent_preview=FabricDataAgentToolParameters(
            # a list: one tool can front several Fabric data agents
            project_connections=[ToolProjectConnection(project_connection_id=conn.id)]
        )
    )


def main():
    ap = argparse.ArgumentParser(description="Bind a Foundry agent to Fabric IQ or to the "
                                             "Fabric data agent")
    ap.add_argument("--check", action="store_true",
                    help="preflight only -- resolves the connection and reports, creates nothing")
    ap.add_argument("--binding", choices=BINDINGS, default=None,
                    help=f"override foundry.binding in config.yaml (default: {FABRIC_IQ})")
    args = ap.parse_args()

    cfg = load_config()
    state = load_state()
    fnd = foundry_config(cfg, args.binding)
    binding = fnd["binding"]

    print_step(1, 2, "Preflight")
    ok = preflight(cfg, state, fnd)
    if args.check:
        print("\nOK  preflight done (nothing created)." if ok else
              "\n!!  preflight found a blocker -- see above.")
        sys.exit(0 if ok else 1)
    if not ok:
        sys.exit("Preflight failed. Fix the blocker above before creating the agent.")

    print_step(2, 2, f"Create version of '{fnd['agent_name']}' ({binding})")
    from azure.ai.projects.models import PromptAgentDefinition  # noqa: PLC0415

    client = project_client(fnd)

    target = str(fnd.get("fabric_iq_target", IQ_ONTOLOGY))
    source = str(fnd.get("fabric_iq_endpoint", IQ_FROM_CONNECTION))
    # Built BEFORE the connection is resolved: if the connection is missing, the error can
    # then print the exact azd command, target included, instead of a generic "create it".
    server_url = (fabric_iq_server_url(cfg, state, target)
                  if binding == FABRIC_IQ and source == IQ_EXPLICIT else None)
    conn = resolve_connection(client, connection_name(fnd), binding,
                              server_url or (fabric_iq_server_url(cfg, state, target)
                                             if binding == FABRIC_IQ else None))

    instructions = instructions_for(binding, target if binding == FABRIC_IQ else None)
    description = (
        "Marketing churn analyst for Fab-Marketing-Campaign, grounded on Fabric IQ. "
        "Owns its own semantics -- nothing is inherited from the Fabric data agent."
        if binding == FABRIC_IQ else
        "Front door for the Fab-Marketing-Campaign Fabric data agent. "
        "Pass-through wrapper: one tool, no business logic of its own."
    )

    version = client.agents.create_version(
        agent_name=fnd["agent_name"],
        description=description,
        metadata={
            "project": "Fab-Marketing-Campaign",
            # Recorded on the agent so the binding can be read back from Foundry itself.
            # A response never says which contract produced it.
            "binding": binding,
            "fabric_connection": connection_name(fnd),
            "fabric_iq_target": (str(fnd.get("fabric_iq_target", IQ_ONTOLOGY))
                                 if binding == FABRIC_IQ else ""),
            "fabric_data_agent": (str(cfg.get("data_agent_name", ""))
                                  if binding == FABRIC_DATA_AGENT else ""),
        },
        definition=PromptAgentDefinition(
            model=fnd["model_deployment"],
            instructions=instructions,
            tools=[build_tool(fnd, conn, server_url)],
        ),
    )
    client.close()

    print(f"   name    = {getattr(version, 'name', fnd['agent_name'])}")
    print(f"   version = {getattr(version, 'version', '?')}")

    state["foundry_agent_name"] = fnd["agent_name"]
    state["foundry_agent_version"] = str(getattr(version, "version", ""))
    state["foundry_project_endpoint"] = fnd["project_endpoint"]
    state["foundry_binding"] = binding
    save_state(state)
    print(f"   state.json updated ({', '.join(STATE_KEYS)})")

    print("\nNEXT -- do this before any workflow or any demo:")
    print("  1. Open the agent ALONE in the Foundry playground.")
    print("  2. Ask it something that forces the tool call "
          "(e.g. 'combien de clients sont a risque ?').")
    if binding == FABRIC_IQ:
        print("  3. Read the answer against the grain: did it return an AGGREGATE, or did it")
        print("     count the records it happened to receive? The prompt forbids the second,")
        print("     but only a trace shows what actually came back from the tool.")
        print("     require_approval='never' means no consent prompt will appear -- that is")
        print("     the setting that makes step 4 possible, and it is a deliberate posture.")
    else:
        print("  3. Approve the tool -- 'Always approve this tool'.")
        print("     Tool approval cannot be granted inside a workflow preview: a run that looks")
        print("     like an error is usually a consent waiting for a prompt with nowhere to appear.")
    print("  4. Then:  python src/foundry_supervision.py")


if __name__ == "__main__":
    main()
