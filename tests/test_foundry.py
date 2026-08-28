"""Foundry plane gate for Fab-Marketing-Campaign -- offline, no Foundry and no Fabric needed.

The Foundry hop fails in ways nothing at runtime catches:

  - a wrapper prompt that carries business facts answers confidently after the data moves,
    and the chat gives you no way to tell (the Microsoft lab this pattern comes from does
    exactly that -- ten product ids pinned as "at risk of stockout" next to a clause saying
    the response must come only from the tool);
  - a containment clause quietly dropped, so the model summarises the rows away;
  - a golden question referencing a metric the truth function no longer computes, which
    surfaces mid-run as a KeyError;
  - a French-formatted figure ('4,96 M EUR') parsed as 4.96, scoring a correct answer as a
    miss and sending someone hunting a defect that is not there.

These tests are that catch. They import the modules but never construct an SDK client, so
they run with no credentials and without azure-ai-projects installed.

Run with the rest of the gate:  python -m pytest tests/ -v --tb=short
"""
import pathlib
import json
import re
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
RAW = ROOT / "data" / "raw"

sys.path.insert(0, str(SRC))
from deploy_foundry_agent import (WRAPPER_INSTRUCTIONS, FABRIC_IQ_INSTRUCTIONS,  # noqa: E402
                                  STATE_KEYS, BINDINGS, FABRIC_IQ, FABRIC_DATA_AGENT,
                                  BINDING_CONNECTION_KEY, resolve_binding, connection_name,
                                  FABRIC_IQ_TARGETS, IQ_ONTOLOGY, IQ_SEMANTIC_MODEL,
                                  IQ_DATA_AGENT, IQ_FROM_CONNECTION, fabric_iq_server_url,
                                  azd_connection_hint, instructions_for, check_agent_name,
                                  FABRIC_AUDIENCE, arm_connection_body)
from foundry_supervision import (GOLDEN, ONTOLOGY, SEMANTIC_MODEL,  # noqa: E402
                                 local_truth, numbers_in, check_answer, is_raw_retrieval,
                                 headline_number, stability_of, print_report)
from deploy_all import STEP_NAMES  # noqa: E402

FOUNDRY_KEYS = {"project_endpoint", "model_deployment", "agent_name", "binding",
                "fabric_iq_connection_name", "fabric_connection_name", "require_approval",
                "fabric_iq_target", "fabric_iq_server_label"}

# The prompts are hard-wrapped for readability, so a clause can straddle a newline. Compare on
# a whitespace-normalised copy or the gate fails on formatting instead of on meaning.
WRAPPER_FLAT = re.sub(r"\s+", " ", WRAPPER_INSTRUCTIONS).lower()
IQ_FLAT = re.sub(r"\s+", " ", FABRIC_IQ_INSTRUCTIONS).lower()


@pytest.fixture(scope="module")
def example_cfg():
    return yaml.safe_load((SRC / "config.example.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def truth():
    if not (RAW / "crm" / "crm_customer_profile.csv").exists():
        pytest.skip("no generated dataset -- run python src/generate_data.py")
    cfg = yaml.safe_load((SRC / "config.yaml").read_text(encoding="utf-8"))
    return local_truth(cfg)


# -- config -------------------------------------------------------------------
def test_example_config_carries_the_foundry_block(example_cfg):
    assert "foundry" in example_cfg, "src/config.example.yaml must document the foundry block"
    assert FOUNDRY_KEYS <= set(example_cfg["foundry"]), (
        f"missing keys: {FOUNDRY_KEYS - set(example_cfg['foundry'])}")


def test_local_config_tracks_the_example():
    """A key added to the example and not to config.yaml fails only at deploy time."""
    local = SRC / "config.yaml"
    if not local.exists():
        pytest.skip("no local config.yaml")
    cfg = yaml.safe_load(local.read_text(encoding="utf-8"))
    if "foundry" not in cfg:
        pytest.skip("foundry plane not configured locally")
    assert FOUNDRY_KEYS <= set(cfg["foundry"]), (
        f"config.yaml -> foundry is missing {FOUNDRY_KEYS - set(cfg['foundry'])}")


# -- the wrapper prompt -------------------------------------------------------
def test_wrapper_carries_the_containment_clauses():
    assert "come only from the fabric data agent tool output" in WRAPPER_FLAT, (
        "without it the model may answer from training data and nothing in the chat says so")
    assert "do not generate summaries or remove any data" in WRAPPER_FLAT, (
        "without it the model trims the rows the caller asked for")


def test_wrapper_forbids_inventing_and_allows_saying_nothing():
    assert "never answer from general knowledge" in WRAPPER_FLAT
    assert "empty result is a valid answer" in WRAPPER_FLAT, (
        "an agent with no way to say 'nothing came back' will fill the gap")


def test_wrapper_contains_no_business_fact():
    """The inherited anti-pattern: a grounded agent with hardcoded facts looks sourced.

    Any digit in the wrapper is either a threshold, an id or a figure -- all three belong to
    the data, none of them to the prompt.
    """
    digits = [m.group(0) for m in re.finditer(r"\d+", WRAPPER_INSTRUCTIONS)]
    assert not digits, f"business facts hardcoded in the wrapper prompt: {digits}"


def test_wrapper_does_not_leak_the_storyline():
    """The demo's own answer must not sit in the prompt of the agent meant to discover it."""
    cfg = yaml.safe_load((SRC / "config.example.yaml").read_text(encoding="utf-8"))
    st = cfg["storyline"]
    for token in (st["culprit_campaign_id"], st["culprit_campaign_name"],
                  st["victim_segment_id"]):
        assert str(token).lower() not in WRAPPER_FLAT, (
            f"'{token}' is in the wrapper prompt -- the agent would 'find' the culprit it "
            f"was handed, and the whole root-cause demo becomes theatre")


def test_wrapper_does_not_claim_a_source():
    """Only a trace can say which source answered. A prompt that claims it teaches a lie."""
    assert "do not claim which underlying source" in WRAPPER_FLAT


def test_example_binding_is_a_known_one(example_cfg):
    assert example_cfg["foundry"]["binding"] in BINDINGS, example_cfg["foundry"]["binding"]


# -- the Fabric IQ prompt (default binding) -----------------------------------
def test_fabric_iq_prompt_carries_the_three_containment_clauses():
    """Fabric IQ inherits nothing from the Fabric data agent, so these are the only guard rails."""
    assert "only from the retrieved content and tool output" in IQ_FLAT, (
        "without it the model answers from training data and nothing in the chat says so")
    assert "do not summarise away, filter, or drop records" in IQ_FLAT, (
        "without it the model trims the records the caller asked for")
    assert "could not be found" in IQ_FLAT, (
        "an agent with no way to say 'nothing came back' will fill the gap")


def test_fabric_iq_prompt_forbids_turning_a_retrieval_into_a_total():
    """The failure this binding actually has.

    Retrieval hands back the records it matched, never a statement of completeness. A model
    asked "how many customers are at risk" will count what it received and present that as
    the business figure. No retrieval setting prevents it -- only this contract does, and
    only by making the agent say the total was not returned.
    """
    assert "never present the number of records you received as a total" in IQ_FLAT
    assert "never sum, average, or extrapolate" in IQ_FLAT
    assert "the total was not returned" in IQ_FLAT


def test_fabric_iq_prompt_contains_no_business_fact():
    digits = [m.group(0) for m in re.finditer(r"\d+", FABRIC_IQ_INSTRUCTIONS)]
    assert not digits, f"business facts hardcoded in the Fabric IQ prompt: {digits}"


def test_fabric_iq_prompt_does_not_leak_the_storyline():
    cfg = yaml.safe_load((SRC / "config.example.yaml").read_text(encoding="utf-8"))
    st = cfg["storyline"]
    for token in (st["culprit_campaign_id"], st["culprit_campaign_name"],
                  st["victim_segment_id"]):
        assert str(token).lower() not in IQ_FLAT, (
            f"'{token}' is in the Fabric IQ prompt -- the agent would 'find' the culprit it "
            f"was handed, and the whole root-cause demo becomes theatre")


def test_the_two_prompts_are_not_the_same_contract():
    """A copy-paste here silently gives a Fabric IQ agent a pass-through prompt.

    It would tell the model to return 'what the tool returns' from a tool that returns rows,
    which is exactly how a retrieval becomes a total.
    """
    assert IQ_FLAT != WRAPPER_FLAT
    assert "fabric data agent tool" not in IQ_FLAT, (
        "the Fabric IQ prompt names a tool the agent does not have")


# -- the binding --------------------------------------------------------------
def test_binding_resolution_prefers_cli_then_config_then_default():
    assert resolve_binding(None, None) == FABRIC_IQ
    assert resolve_binding(FABRIC_DATA_AGENT, None) == FABRIC_DATA_AGENT
    assert resolve_binding(FABRIC_DATA_AGENT, FABRIC_IQ) == FABRIC_IQ
    assert resolve_binding("  Fabric-IQ  ", None) == FABRIC_IQ


def test_unknown_binding_fails_loudly():
    """A typo in config.yaml must not silently fall back to the other contract."""
    with pytest.raises(SystemExit):
        resolve_binding("fabric", None)


def test_each_binding_reads_its_own_connection_key():
    assert set(BINDING_CONNECTION_KEY) == set(BINDINGS)
    assert len(set(BINDING_CONNECTION_KEY.values())) == len(BINDINGS), (
        "both bindings point at the same config key -- one of them resolves the wrong "
        "connection and the error will name the right one")
    fnd = {"binding": FABRIC_IQ, "fabric_iq_connection_name": "iq",
           "fabric_connection_name": "da"}
    assert connection_name(fnd) == "iq"
    assert connection_name({**fnd, "binding": FABRIC_DATA_AGENT}) == "da"


def test_only_one_fabric_tool_is_ever_attached():
    """Both attached is legal, silent, and almost always wrong.

    Two Fabric answers that can disagree, and nothing in the response saying which path
    produced either.
    """
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8")
    assert re.search(r"tools=\[build_tool\(fnd, conn[^\]]*\)\]", src), (
        "the tool list must come from build_tool(), which returns exactly one tool")
    assert src.count("return FabricIQPreviewTool(") == 1
    assert src.count("return MicrosoftFabricPreviewTool(") == 1


def test_fabric_iq_approval_is_set_explicitly_not_left_to_the_sdk_default():
    """The SDK default is 'always': every call waits for a human and the replay hangs."""
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8")
    assert "require_approval=" in src, (
        "require_approval unset means 'always' -- an unattended supervision run never returns")


def test_example_config_documents_the_approval_posture(example_cfg):
    assert str(example_cfg["foundry"]["require_approval"]).lower() in ("never", "always")
def test_binding_opts_into_preview_explicitly():
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8")
    assert "allow_preview=True" in src, (
        "the Fabric tool and the per-agent endpoint are preview surfaces; without the opt-in "
        "they are absent, with an error that never mentions preview")


def test_binding_resolves_the_connection_by_name_not_by_guid():
    """A GUID in code is how an environment promotion breaks."""
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8")
    guids = re.findall(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", src)
    assert not guids, f"hardcoded GUID(s) in deploy_foundry_agent.py: {guids}"
    assert 'connections.get(' in src


def test_sdk_import_is_lazy():
    """The gate must run on a machine that has never installed the Foundry SDK."""
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8")
    top_level = [ln for ln in src.splitlines()
                 if ln.startswith(("import azure", "from azure"))]
    assert not top_level, f"module-level Azure SDK import(s): {top_level}"


def test_foundry_is_not_a_fabric_deploy_step():
    """A Foundry agent is not a Fabric item.

    Adding it to deploy_all.py would put it on the workspace task flow, where every task
    must be backed by a deployed Fabric item -- and would break that gate.
    """
    assert "foundry" not in STEP_NAMES


def test_state_keys_are_declared():
    assert set(STATE_KEYS) == {"foundry_agent_name", "foundry_agent_version",
                               "foundry_project_endpoint", "foundry_binding"}
    assert "foundry_binding" in STATE_KEYS, (
        "the binding must be written to state.json: two run logs produced under different "
        "bindings are not comparable, and nothing else records which one was live")


# -- the golden set -----------------------------------------------------------
def test_golden_ids_and_questions_are_unique():
    ids = [q["id"] for q in GOLDEN]
    questions = [q["question"] for q in GOLDEN]
    assert len(ids) == len(set(ids))
    assert len(questions) == len(set(questions))
    assert all(q["question"].strip() for q in GOLDEN)


def test_golden_covers_both_sources():
    """Dual-source is the inherited lesson: DAX for numbers, GQL for relationships.

    A golden set that only asks for numbers never exercises the ontology path, and the
    routing rule goes untested for exactly as long as it takes to demo it.
    """
    sources = {q["expected_source"] for q in GOLDEN}
    assert sources == {ONTOLOGY, SEMANTIC_MODEL}, sources


def test_golden_has_a_refusal_and_an_unanswerable_case():
    """Two failure modes that only appear when you ask for them."""
    blob = " ".join(q["question"].lower() for q in GOLDEN)
    assert "jamais commande" in blob, (
        "no prospect question -- an agent that scores non-buyers has stopped reading the model")
    assert any("concurrent" in q["question"].lower() for q in GOLDEN), (
        "no unanswerable question -- nothing proves the agent can say 'I don't know'")


def test_every_golden_check_is_well_formed():
    for q in GOLDEN:
        assert q["expect"], f"{q['id']} asserts nothing"
        for exp in q["expect"]:
            assert exp["kind"] in {"number", "mentions", "non_empty"}, exp
            if exp["kind"] == "number":
                assert "metric" in exp, exp
            if exp["kind"] == "mentions":
                assert exp.get("any") or exp.get("any_literal"), exp


def test_golden_metrics_exist_in_the_truth(truth):
    """Drift alarm: rename a metric in local_truth() and this fails instead of a mid-run KeyError."""
    for q in GOLDEN:
        for exp in q["expect"]:
            if exp["kind"] == "number":
                assert exp["metric"] in truth, f"{q['id']}: unknown metric '{exp['metric']}'"
            for key in exp.get("any", []):
                assert key in truth, f"{q['id']}: unknown truth key '{key}'"


def test_truth_reflects_the_storyline(truth):
    """If the culprit stops being visible in the truth, the golden set is testing nothing."""
    assert truth["culprit_sends_per_customer"] > truth["other_sends_per_customer"] * 2
    assert truth["customers_at_risk"] > 0
    assert truth["band_prospect"] > 0


# -- the parser (the piece that silently scores a correct answer as wrong) -----
@pytest.mark.parametrize("text,expected", [
    ("981 clients", 981.0),
    ("1 234 clients", 1234.0),
    ("1\u00a0234 clients", 1234.0),          # non-breaking space
    ("1,234 customers", 1234.0),
    ("135,83 EUR", 135.83),
    ("135.83 EUR", 135.83),
    ("1 234,56 EUR", 1234.56),
    ("1,234.56 EUR", 1234.56),
    ("4,96 M EUR", 4960000.0),
    ("186 792 EUR", 186792.0),
    ("3.83 envois", 3.83),
])
def test_number_parsing(text, expected):
    assert any(abs(v - expected) < 0.01 for v in numbers_in(text)), numbers_in(text)


def test_number_parsing_survives_a_sentence():
    got = numbers_in("981 clients a risque, soit 8,2 % du portefeuille, pour 186 792 EUR de CLV.")
    for want in (981.0, 8.2, 186792.0):
        assert any(abs(v - want) < 0.01 for v in got), (want, got)


# -- the checker must be able to fail (a gate that cannot fail is decoration) --
def _truth_stub():
    return {"customers_at_risk": 981.0, "culprit_campaign_name": "Black Friday Blast"}


def test_checker_passes_a_good_answer():
    verdict, notes = check_answer(
        "981 clients sont a risque.",
        [{"kind": "number", "metric": "customers_at_risk"}], _truth_stub())
    assert verdict == "PASS", notes


def test_checker_reports_drift_not_failure_on_a_stale_figure():
    """The deployed Lakehouse may hold an earlier draw. That is drift, not a broken agent."""
    verdict, notes = check_answer(
        "825 clients sont a risque.",
        [{"kind": "number", "metric": "customers_at_risk"}], _truth_stub())
    assert verdict == "DRIFT", notes
    assert notes


def test_checker_misses_a_number_free_answer_to_a_number_question():
    verdict, _ = check_answer(
        "Un certain nombre de clients sont a risque.",
        [{"kind": "number", "metric": "customers_at_risk"}], _truth_stub())
    assert verdict == "MISS"


def test_checker_misses_a_missing_mention():
    verdict, _ = check_answer(
        "La campagne Summer Sale est la plus intense.",
        [{"kind": "mentions", "any": ["culprit_campaign_name"]}], _truth_stub())
    assert verdict == "MISS"


def test_checker_flags_an_empty_answer():
    verdict, _ = check_answer("", [{"kind": "non_empty"}], _truth_stub())
    assert verdict == "EMPTY"


# -- the Fabric IQ target: which item the tool actually queries ----------------
# The connection carries the identity, server_url carries the target. Omitting server_url
# on a workspace holding an ontology AND a semantic model AND a data agent leaves the
# target unstated -- which is not a preference, it is an unanswered question.
_STATE_STUB = {"workspace_id": "WS", "ontology_id": "ONT", "data_agent_id": "DA"}
_CFG_STUB = {"fabric_api_base": "https://api.fabric.microsoft.com/v1"}


def test_every_target_builds_a_distinct_endpoint():
    urls = {t: fabric_iq_server_url(_CFG_STUB, _STATE_STUB, t) for t in FABRIC_IQ_TARGETS}
    assert len(set(urls.values())) == len(FABRIC_IQ_TARGETS), (
        f"two targets resolve to the same endpoint -- the tool would query the wrong item: {urls}")


def test_ontology_endpoint_carries_the_deployed_ids():
    url = fabric_iq_server_url(_CFG_STUB, _STATE_STUB, IQ_ONTOLOGY)
    assert url.endswith("/workspaces/WS/items/ONT/ontologyEndpoint"), url
    assert "/mcp/dataPlane/" in url, "the ontology route goes through the dataPlane prefix"


def test_data_agent_endpoint_carries_the_deployed_ids():
    url = fabric_iq_server_url(_CFG_STUB, _STATE_STUB, IQ_DATA_AGENT)
    assert url.endswith("/workspaces/WS/dataagents/DA/agent"), url


def test_semantic_model_endpoint_takes_no_item_id():
    """A fixed hub route: this binding cannot be pointed at one specific model."""
    url = fabric_iq_server_url(_CFG_STUB, _STATE_STUB, IQ_SEMANTIC_MODEL)
    assert "WS" not in url and "ONT" not in url, (
        "if an id appears here the endpoint pattern changed -- re-read the docs")
    assert url.endswith("/mcp/fabricaihub/integrations/m365"), url


def test_no_endpoint_is_hardcoded_in_the_config_example(example_cfg):
    """The ids live in state.json. A URL pasted into config.yaml drifts silently."""
    fnd = example_cfg["foundry"]
    assert not any("mcp" in str(v) for v in fnd.values()), (
        "a Fabric IQ server_url in config.yaml would survive a redeploy into a new workspace")


def test_target_must_be_a_known_one():
    with pytest.raises(SystemExit):
        fabric_iq_server_url(_CFG_STUB, _STATE_STUB, "lakehouse")


def test_missing_state_ids_fail_loudly_instead_of_building_a_broken_url():
    with pytest.raises(SystemExit):
        fabric_iq_server_url(_CFG_STUB, {"workspace_id": "WS"}, IQ_ONTOLOGY)


def test_example_target_is_a_known_one(example_cfg):
    assert example_cfg["foundry"]["fabric_iq_target"] in FABRIC_IQ_TARGETS


# -- the connection is code-able, and the auth type decides who you have to ask -----------
# An error that says "create it in the portal" pushes the reader onto the BYO-Entra-app path
# and into a Global Administrator's queue. user-entra-token needs neither. This was wrong in
# an earlier revision; these tests stop it coming back.
def test_missing_connection_prints_the_command_that_creates_it():
    hint = azd_connection_hint("my-conn", FABRIC_IQ, "https://api.fabric.microsoft.com/v1/mcp/x")
    assert "azd ai connection create my-conn" in hint
    assert "--auth-type user-entra-token" in hint, (
        "the passthrough auth type is what removes the Entra app and the admin consent")
    assert "https://api.fabric.microsoft.com/v1/mcp/x" in hint, (
        "the target must be in the command -- a hint you still have to assemble is not a hint")


def test_the_fabric_iq_hint_does_not_send_the_reader_to_an_admin():
    hint = azd_connection_hint("c", FABRIC_IQ, "https://x").lower()
    assert "no entra app, no admin consent, no redirect uri" in hint, (
        "say what this path costs you, or the reader assumes it costs the same as the other")
    for trap in ("grant admin consent", "global administrator", "app registration",
                 "client secret"):
        assert trap not in hint, (
            f"'{trap}' belongs to the BYO-Entra-app path, which user-entra-token avoids")


def test_the_hint_separates_what_was_run_from_what_was_not():
    """The ARM call in the hint was executed against a live project and returned the
    connection. The azd variant was not. Presenting both as equally proven would make the
    reader debug the wrong one first."""
    hint = azd_connection_hint("c", FABRIC_IQ, "https://x")
    assert "this exact call was run" in hint.lower()
    assert "not run here" in hint.lower(), (
        "the azd variant is still second-hand; say so next to it")


def test_the_data_agent_hint_names_the_two_custom_keys():
    hint = azd_connection_hint("c", FABRIC_DATA_AGENT)
    assert "workspace-id" in hint and "artifact-id" in hint, (
        "the data agent connection is two custom keys; naming them is the whole recipe")


def test_the_module_no_longer_claims_the_connection_has_no_code_path():
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8").lower()
    assert "no rest equivalent" not in src, "corrected: azd ai connection create does it"
    assert "there is no code path for it" not in src


def test_the_module_documents_both_auth_paths_and_their_cost():
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8")
    assert "user-entra-token" in src
    assert "ADMIN CONSENT" in src.upper(), (
        "the BYO path must stay documented -- with its price, so it is chosen on purpose")


def test_publishing_the_fabric_item_is_recorded_as_not_code_able():
    """A 404 at runtime is an unpublished item far more often than a wrong URL."""
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8").upper()
    assert "PUBLISH" in src and "404" in src


# -- the prompt follows the TARGET, not the binding ---------------------------------------
# Fabric IQ is a router. Fronting the published data agent it inherits that agent's
# semantics and returns real aggregates; fronting an ontology it is raw retrieval. Handing
# the containment prompt to a data-agent-backed agent makes it refuse totals it was
# correctly given -- a wrong answer produced by a safety clause.
def test_fabric_iq_over_the_data_agent_uses_the_pass_through_contract():
    assert instructions_for(FABRIC_IQ, IQ_DATA_AGENT) is WRAPPER_INSTRUCTIONS, (
        "the data agent owns the churn semantics and returns aggregates; containment would "
        "make the agent decline numbers it legitimately received")


def test_fabric_iq_over_raw_retrieval_uses_the_containment_contract():
    for target in (IQ_ONTOLOGY, IQ_SEMANTIC_MODEL):
        assert instructions_for(FABRIC_IQ, target) is FABRIC_IQ_INSTRUCTIONS, target


def test_the_data_agent_binding_is_always_pass_through():
    assert instructions_for(FABRIC_DATA_AGENT) is WRAPPER_INSTRUCTIONS


def test_an_unstated_fabric_iq_target_does_not_silently_get_the_permissive_prompt():
    """Unknown target -> containment. Over-claiming is the failure that cannot be seen."""
    assert instructions_for(FABRIC_IQ, None) is FABRIC_IQ_INSTRUCTIONS


# -- endpoint source ----------------------------------------------------------------------
def test_endpoint_source_is_a_known_value(example_cfg):
    assert example_cfg["foundry"]["fabric_iq_endpoint"] in (IQ_FROM_CONNECTION, "explicit")


def test_server_url_is_withheld_when_the_connection_already_binds_the_item():
    """The portal catalog picker bakes the item into the connection.

    Sending server_url on top is a second statement of the target, and nothing reports which
    of the two won.
    """
    src = (SRC / "deploy_foundry_agent.py").read_text(encoding="utf-8")
    assert "source == IQ_EXPLICIT" in src, (
        "server_url must be conditional on the endpoint source, not always sent")


# -- the report must read the verdict the same way the prompt was written ------------------
def test_report_treats_fabric_iq_over_the_data_agent_as_measured():
    """The two files must agree on what the grounding is, or the run log lies.

    deploy_foundry_agent gives a data-agent-backed agent the pass-through prompt; if the
    report still calls the same run "raw retrieval" it prints the wrong caveat next to every
    number and the run log records the wrong `raw_retrieval` flag.
    """
    assert is_raw_retrieval("fabric-iq", "ontology") is True
    assert is_raw_retrieval("fabric-iq", "semantic_model") is True
    assert is_raw_retrieval("fabric-iq", "data_agent") is False
    assert is_raw_retrieval("fabric-data-agent", None) is False


def test_report_and_prompt_never_disagree_about_the_same_target():
    for target in FABRIC_IQ_TARGETS:
        containment = instructions_for(FABRIC_IQ, target) is FABRIC_IQ_INSTRUCTIONS
        assert containment == is_raw_retrieval(FABRIC_IQ, target), (
            f"{target}: prompt says raw={containment}, report says "
            f"raw={is_raw_retrieval(FABRIC_IQ, target)}")


# -- what the live project actually returned --------------------------------------------
# Everything below was read off a working connection or off a 200 from the MCP endpoint.
# It contradicts the docs in two places, so it is pinned here rather than trusted to memory.
def test_the_fabric_audience_is_the_fabric_one_not_the_power_bi_one():
    """The azd docs show the Power BI audience for Fabric connections. The connection that
    actually works carries the Fabric one. Doc lost to observation."""
    assert FABRIC_AUDIENCE == "https://api.fabric.microsoft.com"
    body = arm_connection_body("https://x/v1/mcp/y")
    assert body["properties"]["audience"] == FABRIC_AUDIENCE
    assert "powerbi" not in json.dumps(body)


def test_the_connection_carries_the_fabric_iq_marker():
    """`metadata.type: fabric_iq_preview` is undocumented and is what makes the SDK tool
    accept the connection. Drop it and the connection resolves but the tool refuses it."""
    body = arm_connection_body("https://x/v1/mcp/y")
    assert body["properties"]["metadata"] == {"type": "fabric_iq_preview"}


def test_the_arm_body_matches_the_shape_the_service_returned():
    p = arm_connection_body("https://x/v1/mcp/y")["properties"]
    assert p["category"] == "RemoteTool"
    assert p["group"] == "GenericProtocol"
    assert p["authType"] == "UserEntraToken"
    assert p["target"] == "https://x/v1/mcp/y"


def test_the_hint_shows_a_call_that_was_actually_run():
    hint = azd_connection_hint("c", FABRIC_IQ, "https://x/v1/mcp/y")
    assert "az rest --method put" in hint, (
        "azd needs an extension install; ARM is the path that was exercised here")
    assert "fabric_iq_preview" in hint, "the undocumented marker must be in the copy-paste"
    assert "untested here" not in hint


def test_the_generic_fabric_host_is_the_one_we_build():
    """Probed live: both the generic host and the per-workspace `<ws>.z04.w.` host returned
    200 on MCP initialize. We build the generic one because the `z04` segment is derivable
    from nothing we hold."""
    url = fabric_iq_server_url({}, {"workspace_id": "W", "data_agent_id": "D"}, IQ_DATA_AGENT)
    assert url.startswith("https://api.fabric.microsoft.com/v1/mcp/")
    assert ".w.api.fabric" not in url, (
        "the per-workspace host embeds a cluster segment we cannot compute")


def test_an_agent_name_with_underscores_is_caught_before_the_network():
    """Observed: create_version rejected "Marketing_Churn_Front_Door" with a message that
    names no field, so it reads like a connection fault. Catch it in preflight instead."""
    with pytest.raises(SystemExit) as e:
        check_agent_name("Marketing_Churn_Front_Door")
    msg = str(e.value)
    assert "underscore" in msg.lower(), "say WHICH character is the problem"
    assert "Marketing-Churn-Front-Door" in msg, "hand back a name that would work"


def test_legal_agent_names_pass():
    for ok in ("Marketing-Churn-Front-Door", "agent1", "a", "A-1-b"):
        assert check_agent_name(ok) == ok


def test_illegal_agent_names_are_rejected():
    for bad in ("", "-lead", "trail-", "a" * 64, "has space", "dot.name"):
        with pytest.raises(SystemExit):
            check_agent_name(bad)


def test_the_configured_agent_name_is_actually_deployable(example_cfg):
    """The example config is what people copy; a name in it that the service rejects
    guarantees a failed first deploy."""
    assert check_agent_name(example_cfg["foundry"]["agent_name"])


# -- stability: the verdict that needs no reference data ----------------------
# Live measurement, 2026-08-05: the Fabric data agent, asked the SAME question four times
# over MCP with no Foundry in the loop, answered 825 / 593 / 825 / 825. Both figures are
# right -- risk_band and lifecycle_stage are two legitimate readings of "at risk". Against a
# reference that moves, one comparison proves nothing; self-consistency still does.

def test_headline_number_takes_the_figure_the_answer_leads_with():
    assert headline_number("Il y a 593 clients a risque (lifecycle stage 'at_risk').") == 593.0
    assert headline_number("La CLV totale exposee est de 154 865,60 EUR.") == 154865.60


def test_headline_number_is_none_when_the_answer_carries_no_figure():
    assert headline_number("Je ne dispose pas de cette information.") is None
    assert headline_number("") is None


def test_identical_figures_across_repeats_are_stable():
    st = stability_of(["Il y a 825 clients a risque."] * 4)
    assert st["stable"] is True
    assert st["spread_pct"] == 0.0
    assert st["runs"] == 4


def test_the_measured_825_593_oscillation_is_caught():
    """The exact sequence observed against Fabric. If this ever scores stable, the check is
    not doing the one job it exists for."""
    st = stability_of(["Il y a 825 clients a risque (risk_band).",
                       "Il y a 593 clients a risque (lifecycle stage 'at_risk').",
                       "Il y a 825 clients a risque (risk_band).",
                       "Il y a 825 clients a risque (risk_band)."])
    assert st["stable"] is False
    assert "593" in st["note"] and "825" in st["note"]


def test_wording_that_differs_but_agrees_on_the_figure_is_not_flagged():
    """Only the figure is compared. Two phrasings of the same number is not instability --
    flagging it would bury the real signal in noise."""
    st = stability_of(["825 clients sont a risque de churn.",
                       "Le nombre de clients a risque s'eleve a 825, soit une part notable."])
    assert st["stable"] is True


def test_a_repeat_with_no_figure_is_not_agreement():
    """Silence is not a matching answer. Two figures and one refusal is a defect, and
    averaging over the present ones would hide it."""
    st = stability_of(["Il y a 825 clients.", "Je ne peux pas repondre.", "Il y a 825 clients."])
    assert st["stable"] is False
    assert "no figure" in st["note"]


def test_no_figure_anywhere_is_unknown_not_stable():
    st = stability_of(["Aucune donnee.", "Information indisponible."])
    assert st["stable"] is None, "absence of figures must never read as agreement"


# -- the questions name their measure -----------------------------------------
def test_the_at_risk_questions_name_the_measure_they_want():
    """Asked without naming it, the Fabric agent picks risk_band or lifecycle_stage roughly
    one call in four. The fix is the question, so the question has to keep carrying it."""
    for qid in ("sup-001", "sup-002"):
        q = next(g for g in GOLDEN if g["id"] == qid)["question"].lower()
        assert "risk_band" in q, f"{qid} must name the measure it expects"
        assert "lifecycle_stage" in q, f"{qid} must name the reading it excludes"


def test_no_golden_question_hardcodes_a_figure():
    """A digit in a question is a fact that goes stale silently -- the threshold lives in
    config. Disambiguating must not smuggle one back in."""
    for g in GOLDEN:
        assert not any(ch.isdigit() for ch in g["question"]), g["id"]


# -- the report hands over the trace, it does not just point at one -----------
def _row(**over):
    row = {"id": "sup-001", "verdict": "PASS", "expected_source": SEMANTIC_MODEL,
           "latency_s": 1.0, "response_id": "resp_x", "notes": []}
    row.update(over)
    return row


def test_the_report_hands_over_the_actual_trace_query(capsys):
    """Telling the reader to 'go read the trace' without the query sends them hunting in a
    portal. The span chain was measured on 2026-08-05 -- the report should just hand it over.
    """
    print_report([_row()], "fabric-iq", "data_agent")
    out = capsys.readouterr().out
    assert "app-insights query" in out, "the report must carry the query, not just the advice"
    assert "analyze_semantic_model" in out, "the measured span name is the whole point"
    assert "not the appId" in out, "the -g/appId trap cost a failed call once; keep the warning"


def test_the_report_surfaces_an_unstable_verdict(capsys):
    print_report([_row(stability={"runs": 4, "headline": [825.0, 593.0, 825.0, 825.0],
                                  "stable": False, "spread_pct": 28.1,
                                  "note": "the leading figure moved between 593.00 and 825.00"})],
                 "fabric-iq", "data_agent")
    out = capsys.readouterr().out
    assert "UNSTABLE" in out
    assert "risk_band" in out, "the reader needs the known cause, not just the symptom"


def test_a_stable_verdict_is_not_reported_as_a_problem(capsys):
    print_report([_row(stability={"runs": 4, "headline": [825.0] * 4, "stable": True,
                                  "spread_pct": 0.0, "note": ""})],
                 "fabric-iq", "data_agent")
    out = capsys.readouterr().out
    assert "stable" in out and "UNSTABLE" not in out


def test_the_drift_blurb_no_longer_prescribes_regenerating(capsys):
    """Regeneration was measured NOT to reconcile local data with the Lakehouse. Leaving the
    old advice in place sends the next reader down a path that cannot work."""
    print_report([_row(verdict="DRIFT", notes=["customers_at_risk: expected ~981"])],
                 "fabric-iq", "data_agent")
    out = capsys.readouterr().out.lower()
    assert "drift" in out
    assert "re-run the setup notebook" not in out
