"""Gate for the Marketing Churn supervisor -- offline, no Foundry and no credentials needed.

The supervisor is the one agent in this project that holds no data and therefore cannot be
checked by comparing its answer to a source. Everything that can go wrong with it goes wrong
in its prompt or in the plumbing around it, and both are checkable here.

Three failures are already on record and each has a test below.

1. It relayed "825 customers at risk by risk_band" as "800 clients." -- scope dropped, number
   changed. A figure without the measure and filter that produced it is not an answer.

2. "At risk" has three legitimate readings in this model that return three different totals:
   the High band alone, the actionable cohort spanning High and Critical, and the at_risk
   lifecycle stage. Resolving that silently is what makes two correct answers look like a
   contradiction in front of a customer.

3. The connection plumbing has no runtime safety net. The service accepts an A2A connection
   pointing at a nonexistent host with HTTP 200, and merge-patch REPLACES arrays -- so enabling
   A2A while forgetting to re-list `responses` would silently cut off every existing caller of
   the front door. Neither shows up until something is invoked.

Run with the rest of the gate:  python -m pytest tests/ -v --tb=short
"""
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

sys.path.insert(0, str(SRC))
from deploy_supervisor_agent import (  # noqa: E402
    A2A_AUDIENCE, A2A_CONNECTION_METADATA, DEFAULT_A2A_CONNECTION, DEFAULT_AGENT_NAME,
    DEFAULT_VOC_CONNECTION, STATE_KEYS, SUPERVISOR_INSTRUCTIONS_TEMPLATE, a2a_target_url,
    arm_a2a_connection_body, connection_arm_id, default_agent_card, ensure_incoming_a2a,
    parse_project_endpoint, supervisor_config, supervisor_instructions,
)
from verify_supervisor import _fired, _items, _routed, _split_source, _tool_names, _types  # noqa: E402

TOOL = "FrontDoorA2A"
VOC_TOOL = "VoiceOfCustomerA2A"
SUPERVISOR_INSTRUCTIONS = supervisor_instructions(TOOL, VOC_TOOL)
NORMALISED = " ".join(SUPERVISOR_INSTRUCTIONS.split()).lower()


def _cfg(**over):
    foundry = {"project_endpoint": "https://example.services.ai.azure.com/api/projects/p",
               "model_deployment": "gpt-5.4", "agent_name": "Front-Door",
               "binding": "fabric-iq", "fabric_iq_connection_name": "fabriciq-dataagent"}
    foundry.update(over.pop("foundry", {}))
    return {"foundry": foundry, **over}


# --- the prompt states no result of its own ------------------------------------------------

def test_instructions_carry_no_figure():
    """Same rule as the two Fabric prompts and the VoC one: a prompt holding the at-risk
    cutoff keeps answering confidently after config.yaml moves it, and the chat shows nothing.

    The tool name is excised before the check rather than the check being loosened -- "A2A"
    carries a digit because it is a protocol name, and a rule that tolerated stray digits
    would stop catching the thing it exists to catch.
    """
    bare = SUPERVISOR_INSTRUCTIONS_TEMPLATE.replace("{data_tool}", "").replace("{voc_tool}", "")
    assert not re.search(r"\d", bare)
    assert not re.search(r"\d", SUPERVISOR_INSTRUCTIONS.replace(TOOL, "").replace(VOC_TOOL, ""))


def test_the_no_figure_check_would_still_catch_a_smuggled_threshold():
    """Guards the guard: excising the tool name must not open a hole for a real figure."""
    smuggled = SUPERVISOR_INSTRUCTIONS_TEMPLATE + "\n- The at-risk threshold is 65."
    assert re.search(r"\d", smuggled.replace("{data_tool}", "").replace("{voc_tool}", ""))


# --- the A2A tool is named, or every question falls through to file search -------------------

@pytest.mark.parametrize("tool", [TOOL, VOC_TOOL])
def test_instructions_name_both_subordinates(tool):
    """Both tools are a2a_preview now, so the model can only tell them apart by name. An
    unnamed one is unreachable in practice: measured against file_search, the tool the model
    could not describe was never called once."""
    assert tool.lower() in NORMALISED


def test_instructions_name_the_tools_wherever_a_source_is_referenced():
    """A half-renamed prompt is worse than an unnamed one: it names the right tool for routing
    and a nonexistent 'data agent' or 'file search tool' for the relay rules."""
    assert "data agent" not in NORMALISED
    assert "file search" not in NORMALISED
    assert NORMALISED.count(TOOL.lower()) >= 6
    assert NORMALISED.count(VOC_TOOL.lower()) >= 4


def test_instructions_are_injected_not_frozen():
    """A renamed connection must move the prompt with it, or routing silently collapses onto
    the other tool."""
    other = supervisor_instructions("SomeOtherConnection", "SomeOtherCorpus")
    assert "someotherconnection" in other.lower()
    assert "someothercorpus" in other.lower()
    assert TOOL.lower() not in other.lower()
    assert VOC_TOOL.lower() not in other.lower()


@pytest.mark.parametrize("pair", [("   ", VOC_TOOL), (TOOL, ""), ("", "")])
def test_instructions_reject_an_empty_tool_name(pair):
    with pytest.raises(SystemExit):
        supervisor_instructions(*pair)


def test_instructions_reject_two_identically_named_subordinates():
    """Both tools under one name would make the routing rules contradict each other: the same
    tool told to hold every figure and to hold none."""
    with pytest.raises(SystemExit):
        supervisor_instructions(TOOL, TOOL)


def test_instructions_forbid_answering_a_quantity_from_the_verbatims():
    """The observed failure mode was not inventing a number -- it was searching verbatims over
    and over in the hope one would appear."""
    assert f"never answer a quantity from {VOC_TOOL.lower()}" in NORMALISED
    assert "asking it repeatedly" in NORMALISED


def test_instructions_list_concrete_triggers_for_the_data_route():
    """Abstract phrasing ('anything that resolves to a number') did not route. Concrete nouns
    the question will actually contain do."""
    for trigger in ("a count", "a score", "a threshold", "a column", "a measure",
                    "a segment", "a campaign"):
        assert trigger in NORMALISED


@pytest.mark.parametrize("leak", ["camp_007", "black friday", "email_pressure",
                                  "pression email", "seg_high_value"])
def test_instructions_never_name_the_expected_finding(leak):
    """The supervisor exists to DISCOVER that the campaign caused the churn. Naming it in the
    prompt turns the demo into a recital that survives the data being replaced."""
    assert leak not in NORMALISED


# --- the relay contract: the failure actually observed --------------------------------------

@pytest.mark.parametrize("clause", [
    "relay every figure exactly",
    "never round a figure",
    "never restate it in your own words",
    "compute nothing yourself",
])
def test_instructions_forbid_restating_or_computing_figures(clause):
    assert clause in NORMALISED


def test_instructions_require_scope_with_every_figure():
    """A bare number is the defect. The measure or column and the filter must travel with it."""
    assert "with the scope it gave" in NORMALISED
    assert "the measure" in NORMALISED and "the filter applied" in NORMALISED


def test_instructions_forbid_carrying_a_figure_across_turns():
    """Reusing last turn's number after the filter changed is how a stale figure survives."""
    assert "never carry a figure from an earlier turn" in NORMALISED


# --- interpretation: added after the relay contract silenced the analysis --------------------
# The relay rules above were written to stop 825 becoming 800, and they worked so completely
# that the supervisor stopped saying anything of its own. Observed live: a revenue question
# answered with the amount, its measure and its scope, then those same three facts repeated as
# bullets underneath. Half the reply carried no information, for thirty-odd seconds of latency
# and two subordinate agents. A prompt that forbids recomputing and forbids restating reads to
# the model as forbidding thought.


def test_instructions_separate_recomputing_restating_and_reading():
    """The three acts must be named apart. Forbidding two of them without naming the third is
    what produced an echo instead of an answer."""
    assert "recomputing a figure is forbidden" in NORMALISED
    assert "restating a figure in your own words is forbidden" in NORMALISED
    assert "reading a figure is your job" in NORMALISED
    assert "it produces sentences, never numbers" in NORMALISED


def test_the_reading_may_never_introduce_a_digit():
    """The single rule that lets interpretation exist without reopening the recompute hole:
    prose is unrestricted, arithmetic is not. A share the supervisor worked out is the same
    defect as 800, wearing a percent sign."""
    assert "every digit in your reply must be a digit a subordinate returned" in NORMALISED
    for forbidden in ("no share you worked out", "no difference", "no ratio", "no rounding",
                      "no order of magnitude", "no proportion of a total"):
        assert forbidden in NORMALISED


def test_a_missing_figure_is_named_rather_than_estimated():
    """A visible gap beats a plausible number -- the same contract the Fabric IQ prompt carries."""
    assert "do not estimate it and do not imply it" in NORMALISED
    assert "a named gap is worth more" in NORMALISED


def test_instructions_forbid_a_cause_of_the_supervisors_own():
    """Interpretation is exactly where a grounded agent starts inventing causation, and this
    demo's whole point is that the cause has to be DISCOVERED in the data."""
    assert "never offer a cause of your own" in NORMALISED


def test_instructions_ask_for_the_one_thing_only_the_supervisor_can_do():
    """With a single source the supervisor has nothing to add and is a thirty-second relay.
    The juxtaposition of a size and a motive is the only output neither subordinate can
    produce alone, so it is asked for by name."""
    assert "side by side" in NORMALISED
    assert "neither subordinate could have produced alone" in NORMALISED


def test_instructions_forbid_the_duplicate_restatement_that_was_observed():
    """The literal defect from the screenshot: lead sentence, then the same three facts as
    three bullets under a 'Data' heading."""
    assert "never state the same fact twice" in NORMALISED
    assert "does not reappear as a bullet" in NORMALISED


def test_the_reading_comes_after_the_measurement_and_is_labelled():
    """Order is load-bearing. A reader skimming must never take a sentence the supervisor
    wrote for a figure the data returned, so interpretation is last and under its own
    heading -- which is also what lets the app style the two apart without parsing prose."""
    assert NORMALISED.index("then keep the sources apart") < NORMALISED.index("then the reading")
    assert "markdown heading" in NORMALISED


def test_the_answer_follows_the_language_of_the_question():
    """The prompt is English, the demo is French. Unstated, the model drifts between them."""
    assert "answer in the language the question was asked in" in NORMALISED


# --- economy: added after interpretation was granted and the answer became unreadable -------
#
# The interpretation contract fixed the echo and immediately produced the opposite defect: an
# answer stating its provenance twice (lead sentence, then the same five facts as bullets),
# quoting seventeen verbatims across seven themes, and closing on a paragraph about which
# subordinate could be asked what next. Every sentence was true and sourced, and the whole was
# unusable -- the dominant grievance sat third of seven, printed at the same weight as a
# complaint voiced once. These rules are the ones that were missing, so they are pinned.

def test_provenance_is_stated_in_exactly_one_place():
    """The observed duplication was not a repeated *sentence* -- the model read a prose lead and
    a bulleted block as two different things. So the provenance fields are named as one unit."""
    assert "one single statement of provenance" in NORMALISED
    assert "give it in one place and nowhere else" in NORMALISED


def test_verbatims_are_quoted_sparingly_and_ranked():
    """Relaying every verbatim received is not neutrality: printing a grievance voiced once
    beside the dominant one asserts they are comparable, which is a false claim built entirely
    out of true quotations. First wording said 'a couple of quotations' and the model read that
    as per-theme, then walked four themes -- so the cap is on THEMES, not on quotations."""
    assert "quote customers sparingly" in NORMALISED
    assert "report the theme that dominates" in NORMALISED
    assert "never walk through every theme you found" in NORMALISED
    assert "a false picture assembled out of true quotations" in NORMALISED


def test_a_single_figure_does_not_get_a_section_of_its_own():
    """The duplication survived the 'one place and nowhere else' rule because the SHAPE mandated
    two places: a lead carrying the scope, and a 'what the data measured' section. A prose rule
    cannot win against a structural instruction, so the structure is what changed."""
    assert "when the measured part is a single figure" in NORMALISED
    assert "do not then open a section to restate it" in NORMALISED
    assert "the duplication rule being broken by the layout" in NORMALISED


def test_quotations_are_not_glossed():
    """Every quotation came with a sentence explaining what it 'really' meant -- longer than the
    verbatim, and it double-counts one grievance as evidence and as analysis."""
    assert "do not gloss a quotation" in NORMALISED
    assert "one grievance charged twice" in NORMALISED


def test_one_grievance_may_not_be_printed_under_three_names():
    """Capping the THEMES left the quotations inside a theme unbounded, and the corpus happily
    returns the same generated sentence attributed to different customers. Observed live: one
    identical verbatim printed three times under three references, which reads as three
    independent complaints and is one."""
    assert "never print the same grievance twice under different customer references" in NORMALISED
    assert "without counting them" in NORMALISED


def test_a_listed_record_is_one_line_not_a_form():
    """Asked which customers to call back, the answer dumped nine attributes per customer.
    Told to use one line per record, it obeyed the line and kept seven attributes on it, so the
    cap has to be on the facts, not on the layout. Told 'no more than two other facts', it still
    printed four -- a limit the model cannot count against is a limit it cannot check."""
    assert "one line per record" in NORMALISED
    assert "no more than two other facts" in NORMALISED
    assert "names more than three things in total is over the limit" in NORMALISED
    assert "never print every attribute a subordinate handed you" in NORMALISED


def test_a_list_of_records_has_a_ceiling():
    """The failure that capping facts-per-line produced: the model obeyed the per-line cap and
    spent the budget on line COUNT instead, printing fifty ranked customers and doubling the
    answer it replaced. Every cap must leave no neighbouring unit free -- facts per line, AND
    how many lines."""
    assert "a list has an end" in NORMALISED
    assert "the first ten at most" in NORMALISED
    assert "the list is the head of a longer one" in NORMALISED
    assert "printing every row you were handed is not thoroughness" in NORMALISED


def test_a_record_may_not_open_sub_bullets():
    """Third orientation of the same form. Told 'one line per record' and 'no more than three
    things', the model kept both promises per line and hung two sub-bullets under each record --
    six facts per customer again. Naming layouts one at a time loses; the rule has to close the
    orientation it has not yet been shown."""
    assert "one line means one" in NORMALISED
    assert "sub-bullets beneath it has become a form again" in NORMALISED


def test_the_whole_reply_has_a_total_budget():
    """Three runs of one prompt version, same question: two thousand, two thousand seven hundred
    and six thousand characters -- twenty-nine, twenty-nine and fifty-five lines. Every local cap
    was in place for all three. A limit on the whole is the only one that no re-orientation can
    route around, and it must not be paid for by dropping a figure's scope."""
    assert "the whole reply fits on one screen" in NORMALISED
    assert "about thirty lines, headings included" in NORMALISED
    assert "the one limit that no layout can get around" in NORMALISED
    assert "cut records and cut themes -- never the scope of a figure" in NORMALISED


def test_the_lead_sentence_does_not_enumerate_the_list_below_it():
    """Observed live: the lead printed all ten customer ids, and the section beneath printed the
    same ten with their attributes. Every identifier stated twice -- the duplication rule broken
    by layout again, one level down from the provenance case that produced it."""
    assert "a lead sentence does not enumerate" in NORMALISED
    assert "the duplication rule broken by the layout a second time" in NORMALISED


# --- two registers: the provenance moves out of the prose rather than being deleted ----------
#
# The relay contract says a figure travels with its scope, and the model applied that to the
# sentence itself. Observed live, second turn of a demo: a lead reading "la part ... definie par
# `crm_customer_profile[risk_band] IN {"High","Critical"}` ... est `crm_customer_profile[At Risk
# %]`", then a six-bullet block naming the measure, the value, the filter, the threshold, the
# denominator and the scope. True and sourced, and unreadable to the marketing lead it was for.
#
# Deleting the provenance was never an option -- it is the difference between this and a chatbot
# that sounds confident. So it MOVES: prose in the body, identifiers in a trailing block behind
# a fixed marker that the application splits on and folds behind a button.

def test_the_body_of_the_answer_carries_no_identifier():
    """The readability defect is in the SENTENCE, not in the bulleted block below it. Hiding the
    block alone would have left a lead sentence still built out of table and column names."""
    assert "the body of the reply" in NORMALISED
    assert "contains no identifier of any kind" in NORMALISED
    assert "name the population in the words the reader already uses" in NORMALISED


@pytest.mark.parametrize("banned", [
    "no table name", "no column name", "no measure name", "no dax or gql fragment",
])
def test_the_body_names_each_kind_of_identifier_it_excludes(banned):
    """Naming the categories one at a time is what the model needs: told only 'no jargon' it
    kept the bracketed field names, which it does not read as jargon."""
    assert banned in NORMALISED


def test_the_marker_is_fixed_ascii_and_never_translated():
    """The application splits the reply on this line. A marker translated into the language of
    the answer -- and every answer here is French -- silently stops matching, and the detail is
    printed as prose again with nothing reporting that the contract broke."""
    assert "`### source`" in NORMALISED
    assert "that word, in capitals, in english, whatever language" in NORMALISED
    assert "splits your reply on that line" in NORMALISED


def test_the_scope_in_words_is_not_a_second_statement_of_provenance():
    """Two rules would otherwise contradict each other: 'a number without its scope is not an
    answer' and 'give the provenance in one place and nowhere else'. They are reconciled by
    register, not by dropping either -- so the reconciliation is pinned."""
    assert "the scope travels in two registers and they never mix" in NORMALISED
    assert "that is not a statement of provenance" in NORMALISED
    assert "the single statement of provenance that the duplication rule names" in NORMALISED


def test_the_source_block_is_never_omitted():
    """The application reads a missing block as an answer with no source, which is the same
    signal it uses for a supervisor that called no subordinate at all. An omitted block would
    therefore accuse a perfectly good answer of being ungrounded."""
    assert "it is never omitted and never empty" in NORMALISED


def test_the_source_block_is_outside_the_line_budget():
    """The reply has a hard thirty-line budget. Counting the provenance against it would make
    the model pay for the block by cutting the reading -- the one thing only it can produce."""
    assert "the source block excluded" in NORMALISED
    assert "that block is counted separately and is never what you cut" in NORMALISED


def test_a_share_may_not_be_asked_for_under_its_own_filter():
    """Observed live and reported as unusable: the supervisor asked for the at-risk SHARE while
    also filtering to the at-risk bands, and relayed the answer -- the whole population, in the
    cohort it had just selected. Arithmetically true, informationally empty, and on a demo it
    reads as the model claiming the entire customer base is churning."""
    assert "a share is not a count wearing a percent sign" in NORMALISED
    assert "do not also apply the condition that defines it" in NORMALISED
    assert "is the symptom of this mistake" in NORMALISED


def test_a_share_is_given_once_in_one_unit():
    """Observed on the live agent: `0,0784742699514886, soit 7,84742699514886 %` -- the same
    figure twice, in two units, each carrying every decimal of the division. Every word of it
    true; unreadable to the person it was written for."""
    assert "one figure, one unit, once" in NORMALISED
    assert "drop the tail" in NORMALISED


def test_shortening_is_permitted_for_a_share_and_for_nothing_else():
    """The narrow exception must stay narrow, or it swallows the rule that keeps 825 from
    becoming 800 -- which is the defect this whole prompt was written against."""
    assert "never round a figure" in NORMALISED          # the original rule, still standing
    assert "the single place a figure may be shortened" in NORMALISED
    assert "a count, a sum or an amount is relayed to its last digit" in NORMALISED


def test_the_one_unit_rule_carries_no_figure_of_its_own():
    from deploy_supervisor_agent import SUPERVISOR_INSTRUCTIONS_TEMPLATE as t
    assert "0,07" not in t and "7,84" not in t


def test_the_share_rule_carries_no_figure_of_its_own():
    """The rule is about a percentage and the prompt may hold no digit -- the temptation to
    write '100 %' here is exactly the hole `test_instructions_carry_no_figure` guards."""
    assert "100" not in SUPERVISOR_INSTRUCTIONS_TEMPLATE
    assert "the whole population is the symptom" in NORMALISED


# --- the splitter: one contract, two implementations, and they must not drift ----------------
#
# `_split_source` here and `splitAnswer` in app-v2/src/services/answer.ts implement the same
# rule in two languages. The table below is deliberately the same table the vitest suite uses,
# so a change made on one side and forgotten on the other shows up as a red test rather than as
# an answer folded into a collapsed panel on stage.
#
# The governing rule is asymmetric on purpose: the PROMPT is strict (`### SOURCE`, English,
# capitals), the PARSER is tolerant. A marker that fails to match must never hide content.

@pytest.mark.parametrize("marker", [
    "### SOURCE", "SOURCE", "## Source", "**Source :**", "**Source:**", "### SOURCE :",
    "#### source", "   ### SOURCE   ", "Sources",
])
def test_the_marker_is_recognised_however_it_was_dressed(marker):
    """The first implementation was one regex and it missed `**Source :**` -- French typography
    puts the colon INSIDE the bold markers. Decoration is stripped, not enumerated."""
    body, block = _split_source(f"Réponse en clair.\n\n{marker}\nTable : t")
    assert body == "Réponse en clair."
    assert block == "Table : t"


def test_an_answer_with_no_marker_stays_whole():
    """The invariant that outranks the others: what the parser does not recognise stays
    visible. A silent failure here hides an answer behind a button that was never clicked."""
    plain = "Une réponse sans bloc technique."
    assert _split_source(plain) == (plain, None)


def test_the_split_takes_the_last_marker_not_the_first():
    """'Source' is an ordinary French word. Splitting on the first occurrence would file the
    whole answer as provenance and leave the reader a collapsed button where the answer was."""
    body, block = _split_source("Selon la source\ninterrogée, c'est stable.\n\n### SOURCE\nTable : t")
    assert "c'est stable" in body
    assert block == "Table : t"


def test_the_word_source_inside_a_sentence_is_not_a_marker():
    text = "La source de cette mesure est le modèle sémantique, et rien d'autre."
    assert _split_source(text) == (text, None)


@pytest.mark.parametrize("malformed", [
    "Réponse complète.\n\n### SOURCE\n",   # marker with nothing under it
    "### SOURCE\nTable : t",               # marker with nothing above it
])
def test_a_malformed_block_never_empties_the_answer(malformed):
    """Both shapes would otherwise render an empty panel or an empty answer. Neither is a thing
    a reader can recover from, so the safe read is 'that was not the block'."""
    body, block = _split_source(malformed)
    assert block is None
    assert body.strip() == malformed.strip()


def test_the_prose_check_looks_at_the_body_not_the_whole_reply():
    """The point of splitting inside the harness. Before it, 'the answer names the column'
    passed identically whether the column sat in a readable trailing block or in the middle of
    the lead sentence -- so the check could not see the defect that was reported."""
    from verify_supervisor import QUERY_SHAPES
    good = "Environ un acheteur sur treize.\n\n### SOURCE\nColonne : crm_customer_profile[risk_band]"
    body, block = _split_source(good)
    assert block is not None
    assert not [s for s in QUERY_SHAPES if s in body.lower()]
    # ... and the same identifiers in the prose must be caught.
    bad = "La part definie par crm_customer_profile[risk_band] IN {\"High\"} est stable.\n\n### SOURCE\nx : y"
    body, _ = _split_source(bad)
    assert [s for s in QUERY_SHAPES if s in body.lower()]


def test_a_bare_column_name_in_the_prose_is_a_leak_too():
    """The first QUERY_SHAPES only held bracketed forms (`[churn_risk_score`), so the live reply
    "825 clients acheteurs ont un churn_risk_score superieur ou egal au seuil" walked straight
    past it -- while another check in the same run counted that very word as proof the scope had
    survived. A column name is an identifier whether or not DAX punctuation came with it."""
    from verify_supervisor import _registers
    ok, detail = _registers("825 clients ont un churn_risk_score eleve.\n\n### SOURCE\nx : y")
    assert not ok
    assert "churn_risk_score" in detail


def test_the_register_check_wants_the_block_present():
    from verify_supervisor import _registers
    ok, detail = _registers("Une reponse en clair, sans provenance nulle part.")
    assert not ok
    assert "MISSING" in detail


def test_an_undigested_ratio_is_caught_wherever_it_sits():
    """`0,0784742699514886` and `7,84742699514886 %` are the same defect in two units; the check
    is on the digits, so it does not need to know which unit the model picked."""
    from verify_supervisor import _registers
    for figure in ("0,0784742699514886", "7,84742699514886 %", "0.0784742699514886"):
        ok, detail = _registers(f"La part est de {figure}.\n\n### SOURCE\nx : y")
        assert not ok, figure
        assert "undigested float" in detail


def test_a_readable_share_passes():
    from verify_supervisor import _registers
    ok, detail = _registers("Environ 7,8 % de la base, soit 825 acheteurs.\n\n### SOURCE\nx : y")
    assert ok, detail


def test_the_supervisor_may_not_end_a_turn_without_calling_a_subordinate():
    """Measured at version twelve: about one turn in four ended in six seconds with zero tool
    calls, replying with the plan instead of the answer -- which the app renders as a one-line
    unsourced non-answer. The prose rule forbidding it was already in the prompt and did not
    hold, so the constraint moves into the definition where the model cannot decline it."""
    from deploy_supervisor_agent import SUPERVISOR_TOOL_CHOICE

    assert SUPERVISOR_TOOL_CHOICE == "required"


def test_announcing_a_call_is_not_making_it():
    """Observed live on the demo question: the supervisor replied 'I must first question the two
    sources separately' -- an echo of the routing rule read as a plan -- and ended the turn with
    zero tool calls in six seconds. The routing rules said WHICH subordinates to call and never
    said that describing the call is not performing it."""
    assert "announcing a call is not making it" in NORMALISED
    assert "call them now, in this turn" in NORMALISED
    assert "there is no question whose answer is your plan" in NORMALISED


def test_the_reading_does_not_close_on_a_proposed_next_step():
    """Asking for 'the next useful question' put process talk at the end of every single answer,
    where the insight should have been. The mandate is removed and its return is forbidden."""
    assert "says what stands out, not what could be asked next" in NORMALISED
    assert "that is process talk" in NORMALISED
    assert "worth asking next -- naming the subordinate" not in SUPERVISOR_INSTRUCTIONS


def test_brevity_is_stated_as_a_requirement_not_a_preference():
    """A prompt that only demands provenance gets a bibliography. Being read is the goal that
    was missing, so it is stated as one."""
    assert "length is not thoroughness" in NORMALISED
    assert "being read is" in NORMALISED
    assert "the least scope that makes it unambiguous" in NORMALISED


# --- the ambiguity rule ---------------------------------------------------------------------

def test_instructions_reject_vague_magnitude_words_as_filters():
    assert "are not filters" in NORMALISED
    for word in ("high", "elevated", "at risk", "low", "strong", "big"):
        assert word in NORMALISED


@pytest.mark.parametrize("reading", ["high risk band", "actionable cohort",
                                     "at_risk lifecycle stage"])
def test_instructions_name_every_competing_reading(reading):
    """All three must be named. Listing two would still let the agent pick silently between
    the one it knows and the one it does not."""
    assert reading in NORMALISED


@pytest.mark.parametrize("band", ["low", "medium", "high", "critical", "prospect"])
def test_instructions_name_the_bands(band):
    """Band names are schema, not data, so they are allowed here -- and without them the
    disambiguation rule is not actionable."""
    assert band in NORMALISED


def test_instructions_require_declaring_the_reading_used():
    assert "which reading" in NORMALISED
    assert "in the same breath as the number" in NORMALISED


# --- decide, never ask back: the demo failure -----------------------------------------------
# The rule that cured silent disambiguation created a worse defect. Allowed to ask the reader
# which reading they meant, the agent asked -- and it asked on a question taken straight from
# the app's own suggestion list, one click into a demo. What the room saw was a prepared
# question answered with an intake form, no subordinate called, no source, no figure. An
# assistant that interviews the person who clicked its own suggestion is not usable on stage.

def test_the_supervisor_may_never_hand_the_ambiguity_back():
    assert "never hand the ambiguity back" in NORMALISED
    assert "a stated assumption is an answer; a question back is not" in NORMALISED


@pytest.mark.parametrize("escape", [
    "ask the reader which they mean",
    "let them choose",
    "and let them",
])
def test_no_escape_hatch_survives_in_the_ambiguity_rule(escape):
    """The old wording is not merely superseded, it is absent. Leaving it in place alongside
    the new rule would let the model satisfy the prompt by taking the easier branch."""
    assert escape not in NORMALISED


def test_the_reading_chosen_is_the_actionable_one():
    """Deciding is only safe if which way to decide is written down. Told to choose without a
    criterion, the model picks differently across runs and the oscillation this whole section
    exists to kill comes straight back."""
    assert "the widest actionable cohort rather than the narrowest label" in NORMALISED


def test_an_unspecified_second_dimension_is_decided_too():
    """The question that failed on stage carried two open choices, not one: which reading of
    'high risk', and on what criterion to rank the call-backs. Settling only the first would
    have left the same refusal, one clause further down."""
    assert "how to rank" in NORMALISED
    assert "pick a defensible one" in NORMALISED


# --- empty results stay visible -------------------------------------------------------------

def test_instructions_forbid_silent_retry_on_empty_retrieval():
    """Measured at roughly one run in nine, retrieval comes back empty. Retrying until
    documents appear manufactures confidence that was never earned."""
    assert "do not repeat the request hoping for a better draw" in NORMALISED
    assert "an empty result is a finding" in NORMALISED


def test_instructions_forbid_filling_an_empty_retrieval_from_the_other_source():
    assert "do not fill the gap" in NORMALISED


def test_instructions_keep_the_two_sources_separate():
    assert "never present one subordinate's output as if it came from the other" in NORMALISED


# --- routing --------------------------------------------------------------------------------

def test_instructions_route_every_quantity_to_the_data_agent():
    assert "it is your only source of figures" in NORMALISED
    assert "never ask it for one" in NORMALISED


# --- the A2A target -------------------------------------------------------------------------

def test_a2a_target_is_the_documented_post_only_route():
    """`/endpoint/protocols/a2a`. Every `.well-known` GET returns 404 because the route is
    POST-only -- which reads as 'A2A unsupported' and is not."""
    url = a2a_target_url(supervisor_config(_cfg()))
    assert url.endswith("/agents/Front-Door/endpoint/protocols/a2a")


def test_a2a_target_follows_the_configured_endpoint():
    """Built from config, never hardcoded: a stale host deploys cleanly and fails on stage."""
    cfg = _cfg(foundry={"project_endpoint": "https://other.services.ai.azure.com/api/projects/q",
                        "agent_name": "Other-Door"})
    assert a2a_target_url(supervisor_config(cfg)) == (
        "https://other.services.ai.azure.com/api/projects/q"
        "/agents/Other-Door/endpoint/protocols/a2a")


# --- the connection body --------------------------------------------------------------------

def test_connection_audience_is_foundry_not_fabric():
    """The Fabric IQ connection uses the Fabric audience; copying it here yields a token the
    Foundry endpoint rejects."""
    assert arm_a2a_connection_body("https://x")["properties"]["audience"] == A2A_AUDIENCE
    assert "fabric" not in A2A_AUDIENCE


def test_connection_metadata_type_follows_the_tool_name():
    """The field no document mentions. Without it the connection exists and the tool does not
    recognise it -- with no error that says so."""
    assert A2A_CONNECTION_METADATA == {"type": "a2a_preview"}
    assert arm_a2a_connection_body("https://x")["properties"]["metadata"] == \
        A2A_CONNECTION_METADATA


def test_connection_never_pins_an_agent_card_path():
    """Foundry resolves the card path and negotiates the protocol version itself. Pinning it
    was one of the three variants that failed with 401 PermissionDenied."""
    body = json.dumps(arm_a2a_connection_body("https://x")).lower()
    assert "agent_card_path" not in body and "agentcardpath" not in body


def test_connection_body_is_not_mutated_between_calls():
    """The metadata dict is shared module state; handing out a reference lets one caller
    poison the next."""
    first = arm_a2a_connection_body("https://x")
    first["properties"]["metadata"]["type"] = "tampered"
    assert arm_a2a_connection_body("https://y")["properties"]["metadata"]["type"] == \
        "a2a_preview"


def test_connection_arm_id_shape():
    got = connection_arm_id("SUB", "RG", "acct", "proj", "Conn")
    assert got == ("/subscriptions/SUB/resourceGroups/RG/providers/Microsoft.CognitiveServices"
                   "/accounts/acct/projects/proj/connections/Conn")


# --- endpoint parsing -----------------------------------------------------------------------

def test_parse_project_endpoint():
    assert parse_project_endpoint(
        "https://cdr.services.ai.azure.com/api/projects/marketing-churn") == \
        ("cdr", "marketing-churn")


def test_parse_project_endpoint_tolerates_trailing_slash_free_form():
    assert parse_project_endpoint(
        "https://a.services.ai.azure.com/api/projects/p")[1] == "p"


@pytest.mark.parametrize("bad", ["", "not a url", "https://acct.services.ai.azure.com",
                                 "https://acct.services.ai.azure.com/api/foo/p"])
def test_parse_project_endpoint_rejects_anything_else(bad):
    """Failing loudly here beats building a plausible ARM id for a project that does not exist."""
    with pytest.raises(SystemExit):
        parse_project_endpoint(bad)


# --- config -------------------------------------------------------------------------------

def test_supervisor_config_defaults_need_no_config_edit():
    fnd = supervisor_config(_cfg())
    assert fnd["supervisor_agent_name"] == DEFAULT_AGENT_NAME
    assert fnd["supervisor_a2a_connection"] == DEFAULT_A2A_CONNECTION


def test_supervisor_config_honours_overrides():
    fnd = supervisor_config(_cfg(foundry={"supervisor": {
        "agent_name": "My-Supervisor", "a2a_connection_name": "MyConn"}}))
    assert fnd["supervisor_agent_name"] == "My-Supervisor"
    assert fnd["supervisor_a2a_connection"] == "MyConn"


def test_supervisor_name_with_underscore_is_rejected_before_the_network_call():
    """The service rejects it with a message naming no field, so it reads as a connection
    problem."""
    with pytest.raises(SystemExit):
        supervisor_config(_cfg(foundry={"supervisor": {"agent_name": "Bad_Name"}}))


def test_state_keys_are_written_by_the_deploy():
    assert set(STATE_KEYS) == {"foundry_supervisor_agent_name",
                               "foundry_supervisor_agent_version",
                               "foundry_supervisor_connection",
                               "foundry_supervisor_voc_connection"}


# --- enabling A2A must not cut off the existing callers ------------------------------------

class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def text(self):
        return json.dumps(self._payload)


class _FakeClient:
    """Just enough of the client to exercise the read-modify-write around merge-patch."""

    def __init__(self, protocols):
        self.protocols = list(protocols)
        self.patch_body = None

    def send_request(self, request):
        if request.method == "GET":
            return _Resp(200, {"agent_endpoint": {
                "protocols": list(self.protocols),
                "protocol_configuration": {p: {} for p in self.protocols}}})
        raw = request.content
        self.patch_body = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        self.protocols = list(self.patch_body["agent_endpoint"]["protocols"])
        return _Resp(200, {})


def test_enabling_a2a_preserves_the_existing_protocols():
    """merge-patch REPLACES arrays. Omitting `responses` here would disable the protocol the
    front door is actually called on, and the failure would surface as a 404 naming no cause."""
    client = _FakeClient(["responses"])
    ensure_incoming_a2a(client, "Front-Door")
    assert set(client.patch_body["agent_endpoint"]["protocols"]) == {"responses", "a2a"}


def test_enabling_a2a_configures_every_listed_protocol():
    client = _FakeClient(["responses"])
    ensure_incoming_a2a(client, "Front-Door")
    config = client.patch_body["agent_endpoint"]["protocol_configuration"]
    assert set(config) >= {"responses", "a2a"}


def test_enabling_a2a_is_idempotent():
    """Re-running the deploy must not churn the front door's endpoint."""
    client = _FakeClient(["responses", "a2a"])
    note = ensure_incoming_a2a(client, "Front-Door")
    assert client.patch_body is None
    assert "already" in note


def test_enabling_a2a_fails_loudly_if_the_patch_did_not_take():
    """A silent no-op here leaves the supervisor pointed at a door that will not open."""
    class _Stubborn(_FakeClient):
        def send_request(self, request):
            if request.method == "GET":
                return _Resp(200, {"agent_endpoint": {"protocols": ["responses"]}})
            return _Resp(200, {})

    with pytest.raises(SystemExit):
        ensure_incoming_a2a(_Stubborn(["responses"]), "Front-Door")


# --- the verification reads tool activity, not prose ---------------------------------------

def test_fired_detects_the_tool_call_items():
    items = [{"type": "a2a_preview_call", "name": "C"},
             {"type": "a2a_preview_call_output"}, {"type": "message"}]
    assert _fired(items, "a2a_preview")
    assert not _fired(items, "file_search")


def test_fired_is_false_when_only_a_message_came_back():
    """The control agent answered fluently with no tool call. Prose is not evidence."""
    assert not _fired([{"type": "message"}], "a2a_preview")


def test_items_tolerates_objects_and_dicts():
    class _Obj:
        @staticmethod
        def model_dump():
            return {"type": "file_search_call"}

    class _Resp2:
        output = [_Obj(), {"type": "message"}]

    assert _types(_items(_Resp2())) == ["file_search_call", "message"]


# --- an A2A target without an agent card is reachable and unusable --------------------------

CARD = {"version": "1.0.0", "description": "d",
        "skills": [{"id": "s", "name": "s", "description": "d", "tags": [], "examples": []}]}


class _CardClient:
    """Models the field that actually broke the second subordinate: agent_card."""

    def __init__(self, protocols, card=None):
        self.protocols = list(protocols)
        self.card = card
        self.patch_body = None

    def send_request(self, request):
        if request.method == "GET":
            body = {"agent_endpoint": {
                "protocols": list(self.protocols),
                "protocol_configuration": {p: {} for p in self.protocols}}}
            if self.card:
                body["agent_card"] = self.card
            return _Resp(200, body)
        raw = request.content
        self.patch_body = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        if "agent_endpoint" in self.patch_body:
            self.protocols = list(self.patch_body["agent_endpoint"]["protocols"])
        if "agent_card" in self.patch_body:
            self.card = self.patch_body["agent_card"]
        return _Resp(200, {})


def test_a_card_is_written_when_the_target_has_none():
    """The VoC agent had A2A enabled and no card. It was reachable and still unusable: the
    caller failed at invoke time with 'Failed to fetch agent card: 400', which reads as a
    permission fault and is not one."""
    client = _CardClient(["responses", "a2a"], card=None)
    ensure_incoming_a2a(client, "Voice-Of-Customer", card=CARD)
    assert client.patch_body["agent_card"] == CARD


def test_an_existing_card_is_never_clobbered():
    """A card may have been tuned by hand. Rewriting it on every deploy makes it whatever the
    last script to run believed."""
    mine = dict(CARD, description="tuned by hand")
    client = _CardClient(["responses", "a2a"], card=mine)
    ensure_incoming_a2a(client, "Voice-Of-Customer", card=CARD)
    assert client.patch_body is None
    assert client.card == mine


def test_a_missing_card_alone_still_triggers_a_patch():
    """A2A already on and no card is exactly the state that looked healthy and was not."""
    client = _CardClient(["responses", "a2a"], card=None)
    ensure_incoming_a2a(client, "Voice-Of-Customer", card=CARD)
    assert client.patch_body is not None
    assert "agent_endpoint" not in client.patch_body


def test_a_card_that_did_not_take_fails_loudly():
    class _Deaf(_CardClient):
        def send_request(self, request):
            if request.method == "GET":
                return _Resp(200, {"agent_endpoint": {"protocols": ["responses", "a2a"]}})
            return _Resp(200, {})

    with pytest.raises(SystemExit):
        ensure_incoming_a2a(_Deaf(["responses", "a2a"]), "Voice-Of-Customer", card=CARD)


def test_default_agent_card_has_the_fields_the_service_expects():
    card = default_agent_card("what it does", "some-skill", ["an example"])
    assert card["version"] and card["description"]
    skill = card["skills"][0]
    assert set(skill) >= {"id", "name", "description", "tags", "examples"}


# --- routing is checked by CONNECTION NAME, not by item type --------------------------------

def _call(name):
    return [{"type": "a2a_preview_call", "name": name},
            {"type": "a2a_preview_call_output", "name": name},
            {"type": "message"}]


def test_tool_names_lists_only_the_calls_not_their_outputs():
    assert _tool_names(_call("FrontDoorA2A")) == ["FrontDoorA2A"]


def test_routing_passes_only_when_the_right_subordinate_answered():
    ok, detail = _routed(_call("FrontDoorA2A"), "FrontDoorA2A", "VoiceOfCustomerA2A")
    assert ok and "FrontDoorA2A" in detail


def test_routing_fails_when_the_other_subordinate_answered():
    """Both tools are a2a_preview, so a check on the item TYPE alone would pass here -- while
    the supervisor asked the corpus for a number, the exact failure being guarded against."""
    ok, _ = _routed(_call("VoiceOfCustomerA2A"), "FrontDoorA2A", "VoiceOfCustomerA2A")
    assert not ok


def test_routing_fails_when_no_tool_answered_at_all():
    ok, _ = _routed([{"type": "message"}], "FrontDoorA2A", "VoiceOfCustomerA2A")
    assert not ok


# --- the two subordinates must stay distinct ------------------------------------------------

def test_the_two_connections_default_to_different_names():
    """One name for both would make the second ARM PUT overwrite the first, and both tools
    would point at the same subordinate -- deploying cleanly and failing on stage."""
    assert DEFAULT_A2A_CONNECTION != DEFAULT_VOC_CONNECTION


def test_config_exposes_both_connections_and_the_voc_agent():
    fnd = supervisor_config(_cfg())
    assert fnd["supervisor_a2a_connection"] == DEFAULT_A2A_CONNECTION
    assert fnd["supervisor_voc_connection"] == DEFAULT_VOC_CONNECTION
    assert fnd["voc_agent_name"]


def test_the_voc_agent_name_comes_from_the_voc_block_not_a_second_copy():
    """Two declarations of the same agent name is one more thing that can disagree: the
    connection would point at an agent deploy_voc_agent.py never created."""
    fnd = supervisor_config(_cfg(foundry={"voc": {"agent_name": "Other-Voice"}}))
    assert fnd["voc_agent_name"] == "Other-Voice"


def test_the_voc_target_url_points_at_the_voc_agent():
    fnd = supervisor_config(_cfg())
    url = a2a_target_url(fnd, fnd["voc_agent_name"])
    assert url.endswith(f"/agents/{fnd['voc_agent_name']}/endpoint/protocols/a2a")
    assert url != a2a_target_url(fnd)
