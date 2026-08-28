"""Gate for the Voice-Of-Customer agent -- offline, no Foundry and no credentials needed.

The RAG agent fails in one specific way, and it fails looking correct.

Retrieval hands back the documents it matched: a few dozen. Asked "how many customers
complain about email pressure", a model counts those and answers with the count. The corpus
holds 403 such complaints among High Value customers -- a retrieval of 20 answering "20" is
not approximate, it is wrong, and the answer carries nothing that reveals it was a sample.
It is the exact mirror of the counting trap already guarded on the Fabric IQ binding.

Nothing at runtime catches this: the tool call succeeded, the documents are real, the quote
is genuine. Only the prompt contract stands between a sample and a business figure, so these
tests check that the contract is present, and that the prompt states no result of its own --
an agent told which motive dominates will report it whatever the documents say, and will keep
reporting it after the corpus changes.

Run with the rest of the gate:  python -m pytest tests/ -v --tb=short
"""
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))
from deploy_voc_agent import (  # noqa: E402
    BATCH_SIZE, CLI_TIMEOUT_SECONDS, CORPUS_DIR, CORPUS_GLOB, DEFAULT_AGENT_NAME,
    DEFAULT_VECTOR_STORE, STATE_KEYS, UPLOAD_CONCURRENCY, VOC_INSTRUCTIONS, corpus_files,
    indexed_count, is_complete, voc_config,
)
from deploy_foundry_agent import check_agent_name  # noqa: E402

NORMALISED = " ".join(VOC_INSTRUCTIONS.split()).lower()


def _cfg(**over):
    foundry = {"project_endpoint": "https://example.services.ai.azure.com/api/projects/p",
               "model_deployment": "gpt-5.4", "agent_name": "Front-Door",
               "binding": "fabric-iq", "fabric_iq_connection_name": "fabriciq-dataagent"}
    foundry.update(over.pop("foundry", {}))
    return {"foundry": foundry, **over}


# --- the prompt states no result of its own ------------------------------------------------

def test_instructions_carry_no_figure():
    """A grounded agent with a hardcoded number looks sourced and keeps answering after
    the corpus moves. Same rule as the two Fabric prompts."""
    assert not re.search(r"\d", VOC_INSTRUCTIONS)


@pytest.mark.parametrize("leak", ["email_pressure", "pression email", "camp_007",
                                  "black friday", "high value", "seg_high_value"])
def test_instructions_never_name_the_expected_finding(leak):
    """Naming the answer in the prompt turns the demo into a recital: the agent would report
    email pressure as dominant even from a corpus that no longer says so."""
    assert leak not in NORMALISED


# --- the counting contract -----------------------------------------------------------------

@pytest.mark.parametrize("clause", [
    "you do not produce figures",
    "that is a sample",
    "quantities come from the semantic model",
])
def test_counting_contract_is_present(clause):
    assert clause in NORMALISED, clause


def test_prompt_forbids_ranking_by_volume():
    """'The most frequent theme' is a count wearing a word."""
    assert "most frequent" in NORMALISED
    assert "majority" in NORMALISED


def test_prompt_requires_citation():
    assert "file identifier" in NORMALISED
    assert "customer identifier" in NORMALISED


def test_prompt_allows_an_empty_answer():
    """An invented quote is worse than a gap, and the model must be told so explicitly."""
    assert "empty result is a valid answer" in NORMALISED


def test_prompt_forbids_inferring_customer_state():
    """The corpus describes an experience; risk and value live in the semantic model."""
    assert "do not infer" in NORMALISED
    assert "not a state" in NORMALISED


def test_prompt_declares_a_single_source():
    assert "only source is the file search tool" in NORMALISED


# --- names and wiring -----------------------------------------------------------------------

def test_default_agent_name_is_accepted_by_the_service_rules():
    """Underscores are rejected by the service with an error that names no field."""
    assert check_agent_name(DEFAULT_AGENT_NAME) == DEFAULT_AGENT_NAME
    assert "_" not in DEFAULT_AGENT_NAME


def test_default_vector_store_name_is_set():
    assert DEFAULT_VECTOR_STORE


def test_batch_size_respects_the_service_cap():
    assert 0 < BATCH_SIZE <= 500


def test_state_keys_are_namespaced_away_from_the_front_door():
    """Reusing foundry_agent_name would overwrite the front door's entry in state.json."""
    assert all(k.startswith("foundry_voc_") for k in STATE_KEYS)


def test_voc_config_defaults_need_no_config_edit():
    fnd = voc_config(_cfg())
    assert fnd["voc_agent_name"] == DEFAULT_AGENT_NAME
    assert fnd["voc_vector_store_name"] == DEFAULT_VECTOR_STORE


def test_voc_config_honours_an_override():
    cfg = _cfg()
    cfg["foundry"]["voc"] = {"agent_name": "Custom-Voice", "vector_store_name": "vs-custom"}
    fnd = voc_config(cfg)
    assert fnd["voc_agent_name"] == "Custom-Voice"
    assert fnd["voc_vector_store_name"] == "vs-custom"


def test_voc_config_rejects_an_illegal_agent_name():
    cfg = _cfg()
    cfg["foundry"]["voc"] = {"agent_name": "Voice_Of_Customer"}
    with pytest.raises(SystemExit):
        voc_config(cfg)


# --- the corpus it will upload --------------------------------------------------------------

def test_corpus_glob_excludes_the_manifest():
    """_manifest.json names each motive; uploading it would let the agent filter, not read."""
    assert CORPUS_GLOB.startswith("VOC_")
    assert not CORPUS_GLOB.endswith(".json")


@pytest.mark.skipif(not CORPUS_DIR.exists(), reason="corpus not generated yet")
def test_corpus_files_are_found_and_carry_no_manifest():
    files = corpus_files()
    assert files, "corpus directory exists but no VOC_*.md matched"
    assert all(f.suffix == ".md" for f in files)
    assert not any(f.name.startswith("_") for f in files)


# --- resuming a half-finished deploy ---------------------------------------------------------

class _Counts:
    def __init__(self, completed):
        self.completed = completed


class _Store:
    def __init__(self, completed, sid="vs_test"):
        self.id = sid
        self.file_counts = _Counts(completed)


def test_an_empty_store_is_not_complete():
    """The observed failure: the store was created, the upload died on a credential timeout,
    and reusing it by name would have wired the agent to an empty index."""
    assert not is_complete(_Store(0), 1000)


def test_a_partial_store_is_not_complete():
    assert not is_complete(_Store(500), 1000)


def test_a_full_store_is_complete():
    assert is_complete(_Store(1000), 1000)


def test_indexed_count_survives_a_store_without_counts():
    class Bare:
        id = "vs_bare"
    assert indexed_count(Bare()) == 0


def test_upload_concurrency_is_below_the_sdk_default():
    """5 concurrent threads means 5 concurrent `az` calls, which is what blew the timeout."""
    assert 0 < UPLOAD_CONCURRENCY < 5


def test_cli_timeout_is_larger_than_the_azure_identity_default():
    assert CLI_TIMEOUT_SECONDS > 10
