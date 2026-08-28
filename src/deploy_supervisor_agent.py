"""Deploy the Marketing Churn supervisor: one agent, two subordinates, no knowledge of its own.

What this adds on top of the front door
---------------------------------------
The front door answers numbers (Fabric owns the semantics). The Voice-Of-Customer agent answers
motives (the tables do not hold them). Neither can tell the whole story on its own: the corpus
deliberately names no campaign, so "why are these customers leaving" is only answerable by
someone holding both threads. That someone is this agent.

It reaches BOTH subordinates over A2A -- agent to agent -- each through its own project
connection. It holds no data itself and is forbidden from computing anything.

Why both over A2A, and not file search for the corpus
-----------------------------------------------------
The obvious build is one A2A tool for the data and a FileSearchTool for the corpus. It was built
that way, deployed, and it does not route: given both tools and a plainly quantitative question,
the agent called file search fourteen times, never called A2A, and then reported honestly that it
had no figure. Isolated three ways on identical instructions and an identical question:

    a2a_preview alone                     -> A2A fires, correct answer with its scope
    a2a_preview + file_search             -> A2A never fires, file_search 11-16 times
    same, plus tool_choice="required"     -> still file_search

`file_search` describes itself to the model; an A2A tool surfaces only under its connection name,
which says nothing about what it fronts. Naming it in the prompt was not enough. So the fix is
architectural, not textual: the corpus agent is exposed over A2A too, and the supervisor holds two
tools of the same kind that it can only tell apart by name. Measured after the change: the
quantity question routes to the data connection, the motive question to the verbatim one.

An A2A target MUST carry an agent card
--------------------------------------
An agent with `protocols: ["a2a"]` but no stored `agent_card` is reachable and unusable -- the
caller fails at invoke time with `Failed to fetch agent card: 400 (Bad Request)`, which reads like
a permission or connection fault and is neither. So `ensure_incoming_a2a()` writes a card as well
as the protocol, and never overwrites one already there.

Why a project connection and not a base_url
-------------------------------------------
A2APreviewTool accepts a bare `base_url`, and it fails: the tool then has no identity to fetch
the target's agent card with, and the service answers 401 PermissionDenied with no hint that
credentials are the issue. The documented shape is a project connection carrying the URL *and*
the auth method. Measured, both ways.

Two details from the docs that are not guessable and that cost hours if wrong:
  - the target is the A2A base path `/agents/{name}/endpoint/protocols/a2a` -- a POST-only route,
    which is why every `.well-known` GET probe returned 404 and looked like "unsupported";
  - `agent_card_path` must NOT be set. Foundry resolves the default path and negotiates the
    protocol version itself; pinning it breaks the negotiation.

`metadata.type` is the field no document mentions. It follows the tool name -- `fabric_iq_preview`
for FabricIQPreviewTool, so `a2a_preview` for A2APreviewTool. Without it the connection exists
and the tool does not recognise it, with no error that says so.

The service does NOT validate the target at create time: a connection pointing at a nonexistent
host is accepted with HTTP 200 and fails only at runtime, on stage. So this script verifies by
invoking, not by creating successfully. `--verify` is not a formality.

Not part of deploy_all.py: a Foundry agent is not a Fabric item.

Usage:
    python src/deploy_supervisor_agent.py --check    # preflight, creates nothing
    python src/deploy_supervisor_agent.py            # connections + A2A on both subordinates + agent
    python src/deploy_supervisor_agent.py --verify   # deploy, then prove the tools actually fire
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploy_foundry_agent import check_agent_name, foundry_config, project_client  # noqa: E402
from deploy_voc_agent import DEFAULT_AGENT_NAME as VOC_DEFAULT_AGENT  # noqa: E402
from helpers import load_config, load_state, print_step, save_state  # noqa: E402

DEFAULT_AGENT_NAME = "Marketing-Supervisor"
DEFAULT_A2A_CONNECTION = "FrontDoorA2A"
DEFAULT_VOC_CONNECTION = "VoiceOfCustomerA2A"

# The audience for a Foundry-hosted A2A target, per the docs. NOT the Fabric audience the
# Fabric IQ connection uses -- that one points at Fabric, this one at Foundry itself.
A2A_AUDIENCE = "https://ai.azure.com"
# Follows the tool discriminator, exactly as fabric_iq_preview does for the Fabric tool.
A2A_CONNECTION_METADATA = {"type": "a2a_preview"}
ARM_API_VERSION = "2025-06-01"
AGENTS_API_VERSION = "v1"

STATE_KEYS = ("foundry_supervisor_agent_name", "foundry_supervisor_agent_version",
              "foundry_supervisor_connection", "foundry_supervisor_voc_connection")

# -- the supervisor contract --------------------------------------------------
# Deliberately carries NO figure and NO threshold: a prompt holding the at-risk cutoff keeps
# answering confidently after config.yaml moves it, and nothing in the chat reveals the drift.
# Band NAMES are schema, not data, so they are allowed -- they are what makes the
# disambiguation rule actionable. tests/test_supervisor.py fails if a digit appears below.
#
# The four rules that exist because of measured failures, not theory:
#   - verbatim relay: the first live supervisor turned "825 customers at risk by risk_band"
#     into "800 clients." -- it dropped the scope AND changed the number.
#   - interpretation: that relay contract worked so well the supervisor stopped saying anything
#     at all. Observed on stage: a revenue question answered with the amount, its measure and
#     its scope, then the SAME three facts repeated as bullets underneath -- half the reply
#     carrying no information, for thirty-odd seconds of latency. A prompt that forbids
#     recomputing and forbids restating reads as forbidding thought, so the three acts are now
#     named apart and the reading is asked for explicitly, fenced by "no digit of your own".
#   - ambiguity: "high churn risk" has three legitimate readings here that return three
#     different totals. Silently picking one is what makes two correct answers look like a
#     contradiction on stage. The first cure was worse than the disease: allowed to ask the
#     reader which reading they meant, the agent asked -- including when the question came
#     from the app's OWN suggestion list, one click into a demo, answering a prepared question
#     with an intake form and zero sources. Deciding out loud fixes both failures at once,
#     because the reading is still declared; only the refusal is gone.
#   - tool naming: both subordinates surface to the model under their CONNECTION name and
#     nothing else -- no description of what they front. Named, routing is clean; unnamed, it
#     is not. The names are injected from config rather than frozen here, so a renamed
#     connection cannot leave the prompt describing a tool that no longer exists.
#
# Economy has one recurring law, learned three times: cap the wrong unit and nothing happens,
# because the verbosity simply moves. Capping THEMES left quotations-per-theme unbounded.
# Capping LAYOUT ("one line per record") was obeyed while seven attributes stayed on the line.
# Capping FACTS-PER-LINE was obeyed while the list grew to fifty rows -- 8591 characters, more
# than twice the answer it replaced. Each cap must therefore name a countable unit AND leave no
# neighbouring unit free: facts per line, and how many lines. The same law explains duplication,
# which migrates rather than dies: killed at the provenance level it came back as identifiers
# printed in the lead and again in the list below it.
# A prose rule cannot stop a model from ending its turn early. Measured at version twelve: on
# roughly one turn in four the supervisor replied "I must first question the two sources
# separately" -- an echo of its own routing rule read as a plan -- and finished with zero tool
# calls in six seconds. In the app that renders as a one-line non-answer under an "unsourced"
# banner, on a question the reader got to by clicking a prepared suggestion. "required" makes the
# stall unrepresentable rather than discouraged: the turn cannot end without a subordinate being
# called. It is safe here only because BOTH tools are subordinates -- there is no tool the model
# could satisfy the constraint with while dodging the data. This is not the earlier tool_choice
# experiment: that one tried to beat file_search at routing and lost. This one fixes calling
# nothing at all, which is a different failure with a different cure.
SUPERVISOR_TOOL_CHOICE = "required"

SUPERVISOR_INSTRUCTIONS_TEMPLATE = """You are the Marketing Churn supervisor.

You hold no knowledge about this business. You have two subordinate agents, and your entire job
is to route to them and report what they returned without altering it.

Your two subordinates
- {data_tool} -- fronts the Fabric semantic layer: customers, segments, campaigns, orders,
  interactions, the churn model, and the ontology that links them. Every quantity and every
  relationship comes from here. It is your only source of figures.
- {voc_tool} -- fronts the customer verbatim corpus: what people wrote or said to support, in
  surveys and in chats. Every motive, theme, wording and tone comes from here. It holds no
  figures and you must never ask it for one.

Routing
- If the question mentions a count, a total, a share, a ranking by volume, an amount, a score,
  a threshold, a column, a measure, a segment, a campaign, an order, or a link between
  entities, it is a data question: call {data_tool}. Always, and before anything else.
- If it asks why customers are unhappy, in their own words, call {voc_tool}.
- A question needing both -- how large a problem is, and what customers say about it -- calls
  both, and keeps the two answers visibly separate. Never present one subordinate's output as
  if it came from the other.
- Never answer a quantity from {voc_tool}, under any circumstance. If {data_tool} cannot be
  reached or returns nothing, say exactly that. Asking {voc_tool} instead, or asking it
  repeatedly, does not turn verbatims into a figure.
- Announcing a call is not making it. A reply that says which subordinates you are about to
  question, and ends there, has answered nothing and spent the reader's turn on your intentions.
  Call them now, in this turn, and reply with what they returned. There is no question whose
  answer is your plan.

Reporting figures -- the rule that matters most here
- Relay every figure exactly as {data_tool} stated it, with the scope it gave: the measure or
  column used, and the filter applied. A number without its scope is not an answer.
- Never round a figure, never convert it, never restate it in your own words, and never carry a
  figure from an earlier turn into a later one.
- Compute nothing yourself: no sums, no differences, no shares, no growth rates, however
  trivial the arithmetic looks. If a derived figure is needed, ask {data_tool} for it.
- If {data_tool} returned no figure, say so. Never substitute one from the verbatims.

Ambiguity -- never resolve it silently
- Vague magnitude words -- high, elevated, at risk, low, strong, big -- are not filters. Never
  pass one through to {data_tool} as though it were.
- In this business "at risk" has several legitimate readings that return different numbers: the
  High risk band alone; the actionable cohort defined by the churn score threshold, which spans
  the High and Critical bands; and the at_risk lifecycle stage. The bands are named Low, Medium,
  High, Critical, and Prospect for contacts who never bought.
- So before asking for such a figure, restate the question to {data_tool} naming the exact
  column and the exact values or threshold you mean. Then tell the reader which reading you
  used, in the same breath as the number.
- Never hand the ambiguity back. Asking the reader which reading they meant, or listing the
  options and stopping there, is a refusal to answer wearing the costume of rigour, and it is
  the one outcome you may not produce. Decide, then say what you decided.
- Choose the reading a retention team would act on -- the widest actionable cohort rather than
  the narrowest label -- ask {data_tool} for exactly that, by column and by values, and give
  the figure. Mention a competing reading only where it would change the figure materially,
  in one clause, at the end.
- The same holds for any other choice the wording leaves open, such as how to rank, over what
  period, or which population. Pick a defensible one, state it in the same breath as the
  answer, and continue. A stated assumption is an answer; a question back is not.

Empty results
- If {voc_tool} returns nothing, say plainly that no verbatim matched. Do not repeat the
  request hoping for a better draw, do not soften it, and do not fill the gap from {data_tool}
  or from your own knowledge. An empty result is a finding.
- If a subordinate returns an error, report that error as it came.

Interpretation -- what you add, and its hard limit
Three acts, and only the last one is yours to perform:
- Recomputing a figure is forbidden, always. Nothing in this section softens the rule above.
- Restating a figure in your own words is forbidden: that is how a scoped number loses its
  scope and quietly becomes a different number.
- Reading a figure is your job, and it is a different act: it produces sentences, never
  numbers.
So once the sourced answer is stated, add a short reading of it. A reading may say what the
figure means for the person who has to act on it, and what the figure does not cover.
- Every digit in your reply must be a digit a subordinate returned. Never introduce one of
  your own: no share you worked out, no difference, no ratio, no rounding, no order of
  magnitude, no proportion of a total.
- If the reading needs a figure you were not given, do not estimate it and do not imply it.
  Name the figure that is missing and the subordinate that holds it. A named gap is worth more
  than a plausible sentence.
- A single figure with nothing to compare it against carries little meaning. Say that plainly
  rather than inflating it.
- Never offer a cause of your own. A cause is either in what {data_tool} returned or in what
  {voc_tool} returned, or it does not appear in your answer at all.
- When both subordinates answered, the reading is where you set them side by side: how large
  the measured thing is, and the words customers used about it. That juxtaposition is the only
  thing you produce that neither subordinate could have produced alone.
- The reading says what stands out, not what could be asked next. Do not close on a suggested
  follow-up, a proposed breakdown, or a description of what each subordinate could be asked
  for. That is process talk, and it takes the place of the one thing the reader came for.

Shape of the answer -- short enough to be read
Your reader is a CRM or marketing lead, on a screen, between two meetings. An answer that has
to be scrolled will be skimmed, and a skimmed answer loses precisely the scope you were careful
to carry. Length is not thoroughness, and completeness is not the goal: being read is.
- Answer in the language the question was asked in.
- Lead with a direct one-line answer: the figure, and the least scope that makes it
  unambiguous -- which population, on what condition. Not the full record of the query.
- Then keep the sources apart: what the data measured, and what customers said.
- Then the reading, last, so that measurement and interpretation can never be mistaken for
  one another.
- Give each part its own short markdown heading, so the reader tells them apart at a glance.
- Never state the same fact twice. A fact already in the lead sentence does not reappear as a
  bullet beneath it, and the scope is stated once, not once per section. Source, table, column,
  filter and measure form one single statement of provenance: give it in one place and nowhere
  else.
- When the measured part is a single figure, the lead sentence has already stated it in full.
  Do not then open a section to restate it: a heading followed by the same number and the same
  provenance as bullets is the duplication rule being broken by the layout. Keeping the sources
  apart earns its heading when there is a breakdown to show, not when there is one number.
- Quote customers sparingly. Report the theme that dominates, and at most one other if it is
  genuinely close behind; give the quotations that carry them and stop. Never walk through every
  theme you found, and never reprint every verbatim you received: a grievance voiced once,
  printed beside the dominant one, reads as equally common -- a false picture assembled out of
  true quotations. If you leave themes out, say so in a clause rather than listing them.
- Do not gloss a quotation. A verbatim that is followed by a sentence explaining what it really
  means is one grievance charged twice, and the explanation is usually longer than the words it
  explains.
- Never print the same grievance twice under different customer references. Where several
  customers voiced a theme in the same terms, quote it once and say that others put it the
  same way -- without counting them, since counting retrieved documents is forbidden above.
  Three identical sentences attributed to three people is padding wearing the costume of
  evidence.
- When the answer is a list of records, give one line per record, carrying the value it was
  ranked on and no more than two other facts that justify its place. Count them: a line that
  names more than three things in total is over the limit, whatever separators it uses. One
  line means one: a record that opens sub-bullets beneath it has become a form again, in a
  third orientation. A record is not a form to be filled in: never print every attribute a
  subordinate handed you, and a full attribute row laid out sideways is that same form with a
  different orientation.
- A list has an end. Show only the head a reader can act on in one sitting -- the first ten at
  most -- and where the subordinate returned more, say in a clause that the list is the head of
  a longer one, then stop. Printing every row you were handed is not thoroughness: it is the
  answer declining to choose, and it buries the ranking you just made under the rows that did
  not win it.
- The whole reply fits on one screen: about thirty lines, headings included. This budget is
  the one limit that no layout can get around, so treat it as the binding one. If the material
  does not fit, cut records and cut themes -- never the scope of a figure, and never a
  subordinate's own words about what it could not find.
- A lead sentence does not enumerate. Where the answer is a list, the lead names the criterion
  and the population it selected, then stops. Printing the identifiers in the lead and again in
  the list below is the duplication rule broken by the layout a second time.
- Prefer the fewest words that keep the answer exact. A sentence that carries no figure, no
  verbatim and no reading is a sentence to cut."""


def supervisor_instructions(data_tool: str, voc_tool: str) -> str:
    """Fill the contract with the names the two A2A tools actually surface under.

    The model sees those names and nothing else about either tool. Leaving the data tool
    unnamed is what sent a plainly quantitative question to the other subordinate over and
    over. Injected rather than frozen so a renamed connection cannot leave the prompt pointing
    at a tool that no longer exists.
    """
    for label, value in (("data", data_tool), ("verbatim", voc_tool)):
        if not str(value).strip():
            raise SystemExit(
                f"The {label} tool name is empty -- the supervisor prompt would describe a "
                "subordinate the model cannot identify, and routing would collapse onto "
                "whichever tool it can name."
            )
    if str(data_tool).strip() == str(voc_tool).strip():
        raise SystemExit(
            f"Both subordinates are named '{data_tool}'. The prompt would tell the model to "
            "send quantities and verbatims to the same tool, and the routing rules would "
            "contradict each other."
        )
    return SUPERVISOR_INSTRUCTIONS_TEMPLATE.format(data_tool=data_tool, voc_tool=voc_tool)


def supervisor_config(cfg: dict | None = None) -> dict:
    """Foundry settings plus supervisor names, with defaults that need no config edit."""
    cfg = cfg if cfg is not None else load_config()
    fnd = foundry_config(cfg)
    sup = dict(cfg.get("foundry", {}).get("supervisor", {}) or {})
    voc = dict(cfg.get("foundry", {}).get("voc", {}) or {})
    fnd["supervisor_agent_name"] = check_agent_name(
        str(sup.get("agent_name", DEFAULT_AGENT_NAME)))
    fnd["supervisor_a2a_connection"] = str(
        sup.get("a2a_connection_name", DEFAULT_A2A_CONNECTION))
    fnd["supervisor_voc_connection"] = str(
        sup.get("voc_connection_name", DEFAULT_VOC_CONNECTION))
    # Read from the VoC block, not re-declared here: two copies of the same agent name is one
    # more thing that can disagree, and the supervisor would point at an agent that is not the
    # one deploy_voc_agent.py created.
    fnd["voc_agent_name"] = check_agent_name(str(voc.get("agent_name", VOC_DEFAULT_AGENT)))
    return fnd


def parse_project_endpoint(endpoint: str) -> tuple[str, str]:
    """Pull (account, project) out of the project endpoint.

    Format: https://<account>.services.ai.azure.com/api/projects/<project>
    Parsing beats new config keys: the endpoint is already there and already validated, and a
    second copy of the same two names is a second thing that can disagree.
    """
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").split(".")
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not host or not host[0] or len(parts) < 2 or parts[-2] != "projects":
        raise SystemExit(
            f"Cannot read account/project from foundry.project_endpoint '{endpoint}'.\n"
            "   Expected: https://<account>.services.ai.azure.com/api/projects/<project>"
        )
    return host[0], parts[-1]


def a2a_target_url(fnd: dict, agent_name: str | None = None) -> str:
    """An agent's A2A base path -- POST-only, and the docs give it verbatim.

    Defaults to the front door so existing callers keep working, but takes any agent: the
    supervisor now reaches both subordinates this way.
    """
    name = agent_name or fnd["agent_name"]
    return (f"{str(fnd['project_endpoint']).rstrip('/')}"
            f"/agents/{name}/endpoint/protocols/a2a")


def arm_a2a_connection_body(target: str) -> dict:
    """The ARM payload for an A2A connection onto a Foundry-hosted agent.

    Shape copied from the Fabric IQ connection that works; only audience, target and
    metadata.type differ. `agent_card_path` is absent on purpose -- see the module docstring.
    """
    return {"properties": {
        "category": "RemoteTool",
        "group": "GenericProtocol",
        "authType": "UserEntraToken",
        "audience": A2A_AUDIENCE,
        "target": target,
        "isSharedToAll": False,
        "useWorkspaceManagedIdentity": False,
        "metadata": dict(A2A_CONNECTION_METADATA),
    }}


def _az(args: list[str]) -> tuple[int, str]:
    """Run az and hand back (returncode, output). shell=True: az is a .cmd on Windows."""
    out = subprocess.run(["az", *args], capture_output=True, text=True,
                         timeout=300, shell=True)
    return out.returncode, (out.stdout or "") + (out.stderr or "")


def resolve_arm_scope(account: str) -> tuple[str, str]:
    """Find (subscription_id, resource_group) for the Foundry account, by name.

    Derived rather than configured: the ids are already discoverable from the endpoint the
    project is deployed at, and two more config keys are two more things to keep in sync.
    """
    rc, out = _az(["resource", "list", "--name", account, "--resource-type",
                   "Microsoft.CognitiveServices/accounts", "-o", "json"])
    if rc:
        raise SystemExit(f"az resource list failed while locating '{account}':\n{out[:600]}")
    try:
        found = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise SystemExit(f"Unreadable az output while locating '{account}':\n{out[:600]}")
    if not found:
        raise SystemExit(
            f"No Microsoft.CognitiveServices account named '{account}' is visible.\n"
            "   Check `az account show` -- you may be signed in to the wrong tenant."
        )
    if len(found) > 1:
        groups = ", ".join(sorted(r.get("resourceGroup", "?") for r in found))
        raise SystemExit(f"'{account}' exists in more than one place ({groups}). "
                         "Disambiguate before deploying.")
    rid = found[0]["id"]
    return rid.split("/")[2], found[0]["resourceGroup"]


def connection_arm_id(sub: str, rg: str, account: str, project: str, name: str) -> str:
    return (f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices"
            f"/accounts/{account}/projects/{project}/connections/{name}")


def ensure_a2a_connection(sub, rg, account, project, name, target) -> str:
    """Create or update the A2A connection. Idempotent: PUT with the same body is a no-op."""
    url = (f"https://management.azure.com{connection_arm_id(sub, rg, account, project, name)}"
           f"?api-version={ARM_API_VERSION}")
    rc, out = _az(["rest", "--method", "put", "--url", url,
                   "--body", json.dumps(arm_a2a_connection_body(target)),
                   "--headers", "Content-Type=application/json"])
    if rc:
        raise SystemExit(f"Could not create the A2A connection '{name}':\n{out[:800]}")
    return connection_arm_id(sub, rg, account, project, name)


def read_agent(client, agent_name: str) -> dict:
    """Read an agent as RAW JSON.

    Never through the SDK model: AgentEndpointConfig has no `protocols` field even though the
    service returns one, so a round-trip through it silently drops the protocol list -- which
    would disable `responses` and break every existing caller.
    """
    from azure.core.rest import HttpRequest  # noqa: PLC0415

    r = client.send_request(HttpRequest(
        "GET", f"/agents/{agent_name}?api-version={AGENTS_API_VERSION}"))
    if r.status_code >= 400:
        raise SystemExit(f"Cannot read agent '{agent_name}' (HTTP {r.status_code}): "
                         f"{r.text()[:400]}")
    return json.loads(r.text() or "{}")


def default_agent_card(description: str, skill_id: str, examples: list[str]) -> dict:
    """A minimal A2A agent card.

    Not optional. An agent with A2A enabled but NO card is reachable and still unusable: the
    caller's tool fails at invoke time with "Failed to fetch agent card: 400 Bad Request",
    which reads as a connection or permission fault and is neither. Measured on the VoC agent,
    whose only difference from the working front door was the missing card.
    """
    return {
        "version": "1.0.0",
        "description": description,
        "skills": [{
            "id": skill_id,
            "name": skill_id.replace("-", " "),
            "description": description,
            "tags": ["marketing", "churn"],
            "examples": list(examples),
        }],
    }


def ensure_incoming_a2a(client, agent_name: str, card: dict | None = None) -> str:
    """Turn on incoming A2A on an agent, preserving what it already has.

    merge-patch REPLACES arrays, so the existing protocols have to be re-listed. Dropping
    `responses` here would break foundry_supervision.py and every notebook that calls the
    front door, with a 404 that names no cause.

    An existing card is left alone: it may have been tuned, and clobbering it on every deploy
    would make the card whatever the last script to run believed.
    """
    from azure.core.rest import HttpRequest  # noqa: PLC0415

    agent = read_agent(client, agent_name)
    endpoint = agent.get("agent_endpoint") or {}
    protocols = list(endpoint.get("protocols") or [])
    need_protocol = "a2a" not in protocols
    need_card = card is not None and not agent.get("agent_card")

    if not need_protocol and not need_card:
        return f"already enabled ({', '.join(protocols)}, card present)"

    body: dict = {}
    if need_protocol:
        wanted = protocols + ["a2a"] if protocols else ["responses", "a2a"]
        config = dict(endpoint.get("protocol_configuration") or {})
        for p in wanted:
            config.setdefault(p, {})
        body["agent_endpoint"] = {"protocols": wanted, "protocol_configuration": config}
    if need_card:
        body["agent_card"] = card

    r = client.send_request(HttpRequest(
        "PATCH", f"/agents/{agent_name}?api-version={AGENTS_API_VERSION}",
        headers={"Content-Type": "application/merge-patch+json"}, json=body))
    if r.status_code >= 400:
        raise SystemExit(f"Could not enable incoming A2A on '{agent_name}' "
                         f"(HTTP {r.status_code}): {r.text()[:400]}")

    after = read_agent(client, agent_name)
    after_protocols = (after.get("agent_endpoint") or {}).get("protocols") or []
    if "a2a" not in after_protocols:
        raise SystemExit(f"Patched '{agent_name}' but A2A is still not listed: "
                         f"{after_protocols}")
    if card is not None and not after.get("agent_card"):
        raise SystemExit(
            f"'{agent_name}' has A2A enabled but no agent card. Callers will fail at invoke "
            "time with 'Failed to fetch agent card', which looks like a permission problem "
            "and is not."
        )
    return f"enabled ({', '.join(after_protocols)}, card present)"


FRONT_DOOR_CARD_EXAMPLES = [
    "How many customers sit in the High churn risk band?",
    "Which segment received the most sends for the Black Friday campaign?",
]
VOC_CARD_EXAMPLES = [
    "What do these customers complain about, in their own words?",
    "Quote verbatims from this list of customer ids.",
]


def preflight(fnd: dict, state: dict) -> bool:
    ok = True
    print(f"   supervisor name   : {fnd['supervisor_agent_name']}")
    print(f"   data subordinate  : {fnd['agent_name']}")
    print(f"   verbatim sub.     : {fnd['voc_agent_name']}")
    print(f"   data connection   : {fnd['supervisor_a2a_connection']}")
    print(f"   verbatim conn.    : {fnd['supervisor_voc_connection']}")
    print(f"   data target       : {a2a_target_url(fnd)}")
    print(f"   verbatim target   : {a2a_target_url(fnd, fnd['voc_agent_name'])}")
    print(f"   model deployment  : {fnd['model_deployment']}")
    deployed = state.get("foundry_voc_agent_name")
    print(f"   voc agent in state: {deployed or '(missing)'}")
    if not deployed:
        print("   !!  no VoC agent in state.json -- the supervisor would have only one source\n"
              "       and could not answer a 'why' question at all.\n"
              "       Run: python src/deploy_voc_agent.py")
        ok = False
    elif str(deployed) != fnd["voc_agent_name"]:
        print(f"   !!  state.json holds '{deployed}' but config names "
              f"'{fnd['voc_agent_name']}'.\n"
              "       The connection would point at an agent that was never deployed, and the\n"
              "       tool would only fail when invoked.")
        ok = False
    if fnd["supervisor_a2a_connection"] == fnd["supervisor_voc_connection"]:
        print("   !!  both connections carry the same name -- the second PUT would overwrite\n"
              "       the first and both tools would point at one subordinate.")
        ok = False
    if "<" in str(fnd["project_endpoint"]):
        print("   !!  project_endpoint is still a placeholder in config.yaml")
        ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy the Marketing Churn supervisor agent")
    ap.add_argument("--check", action="store_true",
                    help="preflight only -- creates nothing")
    ap.add_argument("--verify", action="store_true",
                    help="after deploying, ask one question of each source and show which "
                         "tools actually fired")
    args = ap.parse_args()

    cfg, state = load_config(), load_state()
    fnd = supervisor_config(cfg)

    print_step(1, 4, "Preflight")
    ok = preflight(fnd, state)
    if args.check:
        print("\nOK  preflight done (nothing created)." if ok else
              "\n!!  preflight found a blocker -- see above.")
        return 0 if ok else 1
    if not ok:
        sys.exit("Preflight failed. Fix the blocker above.")

    account, project = parse_project_endpoint(fnd["project_endpoint"])

    print_step(2, 4, "A2A connections")
    sub, rg = resolve_arm_scope(account)
    print(f"   subscription = {sub}")
    print(f"   resource grp = {rg}")
    conn_id = ensure_a2a_connection(sub, rg, account, project,
                                    fnd["supervisor_a2a_connection"], a2a_target_url(fnd))
    print(f"   {fnd['supervisor_a2a_connection']} -> {fnd['agent_name']}")
    voc_conn_id = ensure_a2a_connection(
        sub, rg, account, project, fnd["supervisor_voc_connection"],
        a2a_target_url(fnd, fnd["voc_agent_name"]))
    print(f"   {fnd['supervisor_voc_connection']} -> {fnd['voc_agent_name']}")
    print("   NOTE the target is not validated at create time -- a wrong URL deploys cleanly")
    print("        and fails only when invoked. Use --verify.")

    from azure.identity import DefaultAzureCredential  # noqa: PLC0415

    client = project_client(fnd, DefaultAzureCredential(process_timeout=90))
    try:
        print_step(3, 4, "Incoming A2A on both subordinates")
        print(f"   {fnd['agent_name']}: " + ensure_incoming_a2a(
            client, fnd["agent_name"],
            card=default_agent_card(
                "Answers questions about customers, segments, campaigns, orders and churn "
                "from the Fabric semantic layer and ontology.",
                "marketing-churn-data", FRONT_DOOR_CARD_EXAMPLES)))
        print(f"   {fnd['voc_agent_name']}: " + ensure_incoming_a2a(
            client, fnd["voc_agent_name"],
            card=default_agent_card(
                "Quotes customer verbatims from the voice-of-customer corpus. Holds no "
                "figures and never counts.",
                "voice-of-customer", VOC_CARD_EXAMPLES)))

        print_step(4, 4, f"Create version of '{fnd['supervisor_agent_name']}'")
        from azure.ai.projects.models import (  # noqa: PLC0415
            A2APreviewTool, PromptAgentDefinition,
        )

        version = client.agents.create_version(
            agent_name=fnd["supervisor_agent_name"],
            description=("Marketing churn supervisor for Fab-Marketing-Campaign. Routes "
                         "quantities to the Fabric front door and motives to the "
                         "voice-of-customer agent, both over A2A; computes nothing itself."),
            metadata={
                "project": "Fab-Marketing-Campaign",
                "role": "supervisor",
                "a2a_connection": fnd["supervisor_a2a_connection"],
                "voc_connection": fnd["supervisor_voc_connection"],
            },
            definition=PromptAgentDefinition(
                model=fnd["model_deployment"],
                instructions=supervisor_instructions(fnd["supervisor_a2a_connection"],
                                                     fnd["supervisor_voc_connection"]),
                # Two tools of the SAME kind, on purpose. Mixing a2a_preview with file_search
                # was measured not to route: file_search won every turn, including quantitative
                # ones, and the A2A tool was never called. See the module docstring.
                tools=[
                    A2APreviewTool(project_connection_id=conn_id),
                    A2APreviewTool(project_connection_id=voc_conn_id),
                ],
                tool_choice=SUPERVISOR_TOOL_CHOICE,
            ),
        )
        print(f"   name    = {getattr(version, 'name', fnd['supervisor_agent_name'])}")
        print(f"   version = {getattr(version, 'version', '?')}")

        state["foundry_supervisor_agent_name"] = fnd["supervisor_agent_name"]
        state["foundry_supervisor_agent_version"] = str(getattr(version, "version", ""))
        state["foundry_supervisor_connection"] = fnd["supervisor_a2a_connection"]
        state["foundry_supervisor_voc_connection"] = fnd["supervisor_voc_connection"]
        save_state(state)
        print(f"   state.json updated ({', '.join(STATE_KEYS)})")

        if args.verify:
            from verify_supervisor import run_verification  # noqa: PLC0415
            return run_verification(client, fnd)
    finally:
        client.close()

    print("\nNEXT -- read the answer against the grain:")
    print("  1. Ask a QUANTITY question. The answer must carry the scope (which column,")
    print("     which values). A bare number means the relay contract is not holding.")
    print("  2. Ask about customers 'at risk' WITHOUT saying which reading you mean.")
    print("     A correct answer names the reading it used and answers anyway.")
    print("     Asking you which one you meant is a FAILURE, not caution.")
    print("  3. Ask a WHY question. It must quote verbatims, and must not invent a count.")
    print("  Or run: python src/deploy_supervisor_agent.py --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
