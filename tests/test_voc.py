"""Gate for the Voice-of-Customer corpus -- offline, no Fabric and no Foundry needed.

A document corpus fails silently in two ways, and both have already cost this project once:

  1. It PARAPHRASES the tables. The predecessor corpus (customer_knowledge_notes) is 1500
     files of 115 characters restating total_orders / total_spend_eur / nps_last -- columns the
     semantic model answers exactly and instantly. Retrieval over it adds latency and nothing
     else. The fix is that the motive must be drawn from BEHAVIOUR (campaign send intensity)
     and must carry information absent from the tables.

  2. It ASSERTS A NUMBER. Measured against the deployed Lakehouse: the population matches
     (SEG_HIGH_VALUE 3989 = 3989, CAMP_007 targeted 3971 = 3971) but the per-row draws do not
     (send rows 15218 vs 15430, unsubscribes 439 vs 247). A verbatim saying "j'ai recu quatre
     mails" or "je me suis desabonne" would therefore contradict the data agent on stage,
     with no way to tell which one is lying. Numbers belong to the semantic model.

These tests enforce both, plus the direction of effect -- if email pressure were not markedly
more frequent among the over-mailed, the root-cause analysis would have nothing to find.

Run with the rest of the gate:  python -m pytest tests/ -v --tb=short
"""
import pathlib
import random
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))
from generate_voc_corpus import (  # noqa: E402
    CHANNELS, CLOSINGS, EMAIL_PRESSURE, FORBIDDEN_WORDS, GRIEVANCES, KIND_CHANNELS, KINDS,
    MOTIVES, OPENINGS, OTHER_WEIGHTS, CAMPAIGN_END, CAMPAIGN_START, compose,
    contains_forbidden, pick_date, pick_motive, pressure_probability, render,
)

ALL_FRAGMENTS = (
    list(OPENINGS)
    + [g for frags in GRIEVANCES.values() for g in frags]
    + [c for frags in CLOSINGS.values() for c in frags]
)


# --- the no-figure rule -------------------------------------------------------------------

@pytest.mark.parametrize("fragment", ALL_FRAGMENTS)
def test_no_fragment_carries_a_figure_or_a_state(fragment):
    """Every building block must be free of digits and of verifiable-state words.

    Checked fragment by fragment rather than only on the composed output: a violation buried
    in one rarely-drawn closing would otherwise slip through on most seeds and surface in
    front of a customer.
    """
    assert not contains_forbidden(fragment), f"{fragment!r} breaks the no-figure rule"


def test_contains_forbidden_catches_digits():
    assert "digit" in contains_forbidden("j'ai recu 4 mails")


def test_contains_forbidden_catches_spelled_out_numbers():
    assert "quatre" in contains_forbidden("j'ai recu quatre mails cette semaine")


def test_contains_forbidden_catches_unsubscribe_claim():
    assert "desabonne" in contains_forbidden("je me suis desabonne de vos listes")


def test_contains_forbidden_accepts_a_vague_complaint():
    """The allowed form: a motive with no number and no assertable state."""
    assert contains_forbidden("je recois beaucoup trop de messages de votre part") == []


def test_indefinite_article_is_not_treated_as_a_number():
    """'un'/'une' are articles -- banning them would gut the French, so they must pass."""
    assert contains_forbidden("j'ai eu une mauvaise experience avec un conseiller") == []


@pytest.mark.parametrize("motive", MOTIVES)
def test_composed_verbatims_never_carry_a_figure(motive):
    rng = random.Random(7)
    for _ in range(400):
        assert not contains_forbidden(compose(rng, motive))


# --- behaviour drives the motive, not the label -------------------------------------------

def test_pressure_probability_rises_with_send_intensity():
    probs = [pressure_probability(n) for n in range(0, 8)]
    assert probs == sorted(probs), probs
    assert probs[5] > probs[1], "more mail must mean more pressure complaints"


def test_pressure_probability_is_capped():
    """Even the most over-mailed customer complains about other things too."""
    assert pressure_probability(99) <= 0.70


def test_untargeted_customers_keep_a_small_baseline():
    """A perfect separator would make the RCA a lookup instead of a discrimination."""
    assert 0 < pressure_probability(0) < 0.15


def test_email_pressure_dominates_among_the_over_mailed():
    rng = random.Random(3)
    heavy = [pick_motive(rng, 4) for _ in range(3000)]
    light = [pick_motive(rng, 0) for _ in range(3000)]
    heavy_share = heavy.count(EMAIL_PRESSURE) / len(heavy)
    light_share = light.count(EMAIL_PRESSURE) / len(light)
    assert heavy_share > 0.5, heavy_share
    assert light_share < 0.12, light_share
    assert heavy_share > 3 * light_share


def test_every_motive_is_reachable():
    """A motive present in the fragments but never drawn is dead weight in the corpus."""
    rng = random.Random(11)
    drawn = {pick_motive(rng, n % 5) for n in range(4000)}
    assert drawn == set(MOTIVES), set(MOTIVES) - drawn


def test_other_weights_sum_to_one():
    assert round(sum(OTHER_WEIGHTS.values()), 6) == 1.0


def test_other_weights_cover_every_non_pressure_motive():
    assert set(OTHER_WEIGHTS) == set(MOTIVES) - {EMAIL_PRESSURE}


# --- timing ------------------------------------------------------------------------------

def test_pressure_complaints_cluster_on_the_campaign_window():
    """Lets a time-scoped question separate the cohorts without anyone labelling them."""
    rng = random.Random(5)
    dates = [pick_date(rng, EMAIL_PRESSURE) for _ in range(500)]
    assert all(d >= CAMPAIGN_START for d in dates)
    assert all((d - CAMPAIGN_END).days <= 21 for d in dates)


def test_other_motives_spread_across_the_year():
    rng = random.Random(5)
    dates = [pick_date(rng, "delivery") for _ in range(500)]
    assert max(dates) - min(dates) > (CAMPAIGN_END - CAMPAIGN_START)


# --- the rendered file --------------------------------------------------------------------

def _meta(**over):
    base = {"customer_id": "CUST_000003", "segment": "SEG_HIGH_VALUE", "channel": "email",
            "kind": "ticket", "occurred_at": "2026-02-05", "motive": EMAIL_PRESSURE,
            "campaign_sends": 4}
    base.update(over)
    return base


def test_rendered_file_never_leaks_the_motive():
    """If the file named its own motive the agent would filter on it instead of reading.

    The motive stays in _manifest.json, which the underscore keeps out of the upload set.
    """
    out = render("VOC_00001", _meta(), "Bonjour, je recois trop de messages. Merci.")
    assert EMAIL_PRESSURE not in out
    assert "motive" not in out


def test_rendered_file_never_leaks_the_send_count():
    out = render("VOC_00001", _meta(), "Bonjour, je recois trop de messages. Merci.")
    assert "campaign_sends" not in out
    assert not re.search(r"\b4\b", out.split("- Date")[0])


def test_rendered_file_is_citable():
    """Retrieval is useless if the chunk cannot be traced back to a customer."""
    out = render("VOC_00042", _meta(), "Bonjour, je recois trop de messages. Merci.")
    assert "VOC_00042" in out
    assert "CUST_000003" in out
    assert "2026-02-05" in out


# --- diversity ----------------------------------------------------------------------------

def test_the_corpus_is_not_one_template():
    """The failure mode of the predecessor corpus: 1500 files that retrieve as one document."""
    rng = random.Random(13)
    texts = {compose(rng, EMAIL_PRESSURE) for _ in range(600)}
    assert len(texts) > 200, len(texts)


def test_forbidden_words_list_is_lowercase():
    """contains_forbidden lowercases the text, so an uppercase entry would never match."""
    assert all(w == w.lower() for w in FORBIDDEN_WORDS)


# --- channel/kind coherence ---------------------------------------------------------------

def test_every_kind_has_plausible_channels():
    """Drawn independently, these produced 'Canal: phone / Type: chat' -- visibly broken data."""
    assert set(KIND_CHANNELS) == set(KINDS)
    assert all(chans for chans in KIND_CHANNELS.values())


def test_kind_channels_only_use_known_channels():
    for kind, chans in KIND_CHANNELS.items():
        assert set(chans) <= set(CHANNELS), (kind, chans)


def test_a_chat_is_never_a_phone_call():
    assert "phone" not in KIND_CHANNELS["chat"]
    assert KIND_CHANNELS["call"] == ("phone",)
    assert KIND_CHANNELS["email"] == ("email",)


# --- readability --------------------------------------------------------------------------

def test_no_capital_letter_straight_after_a_comma():
    """Guards against 'Bonjour, Je recois...' -- the giveaway of assembled text."""
    rng = random.Random(17)
    for motive in MOTIVES:
        for _ in range(300):
            assert not re.search(r",\s+[A-Z]", compose(rng, motive))


def test_sentence_still_starts_with_a_capital():
    rng = random.Random(19)
    for motive in MOTIVES:
        for _ in range(100):
            assert compose(rng, motive)[0].isupper()
