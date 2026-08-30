"""
Claude (Anthropic API) integration for the KINO app.

Same rules as tzoker_llm.py, adapted for KINO's structure:
  - No system-betting wheel here - you just pick `numbers_to_pick` numbers
    directly, so cost is always numbers_to_pick x ticket_price_eur. Python
    recomputes this deterministically from whatever numbers Claude actually
    returns, exactly like the Tzoker engine - never trusted from the
    model's own arithmetic.
  - Every combination of numbers_to_pick from 80 is exactly as likely as any
    other. Claude may describe historical frequency, never future odds.
  - KINO's payouts are fixed multipliers per ticket (not a shared pot) for
    every game size EXCEPT the ΠΑΡΑ1 option on the largest games, which has
    a per-draw payout cap - see kino_paytable.py. Because that's a narrow,
    only-partially-confirmed edge case (unlike Tzoker's clearly documented
    5/5+1 pot-sharing), this module does NOT build pot-avoidance reasoning
    into the prompt - there isn't a solid enough basis for it here yet.
"""

import json
import os
from math import comb

import streamlit as st

import kino_core
import kino_paytable

MODEL = "claude-sonnet-5"


def get_api_key():
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    return st.session_state.get("_kino_anthropic_key_input")


def build_analysis_payload(analyzer, numbers_to_pick, budget_eur, candidate_pool_size=20):
    """
    candidate_pool_size defaults larger than Tzoker's (20 vs 15) since KINO
    games can need up to 12 numbers - the model needs enough room to choose
    numbers_to_pick from within a meaningfully larger candidate set.
    """
    import kino_engine

    scores = kino_engine.score_numbers(analyzer)
    ranked = sorted(scores, key=scores.get, reverse=True)
    top_candidates = ranked[:candidate_pool_size]

    win_structure = kino_paytable.WIN_STRUCTURE.get(numbers_to_pick, {})
    top_category = kino_paytable.TOP_CATEGORY_MULTIPLIERS.get(numbers_to_pick)

    return {
        "total_draws_in_history": len(analyzer.all_draws),
        "date_range": [
            str(analyzer.all_draws["datetime"].min()),
            str(analyzer.all_draws["datetime"].max()),
        ],
        "numbers_to_pick": numbers_to_pick,
        "budget_eur": budget_eur,
        "ticket_price_eur": kino_core.TICKET_PRICE_EUR,
        "cost_for_this_pick_eur": kino_core.TICKET_PRICE_EUR,  # one line, standard price
        "win_structure_for_this_game_size": win_structure,
        "known_multiplier_reference": top_category,
        "top_candidate_numbers": [
            {"number": n, "score": round(scores[n], 4)} for n in top_candidates
        ],
        "evidence_for_candidate_numbers": kino_engine.evidence_for_pick(analyzer, top_candidates),
    }


SYSTEM_PROMPT = """You are a statistics-literate assistant helping organize a KINO play \
(Allwyn Greece - pick numbers_to_pick numbers from 80; 20 are drawn every 5 minutes). You \
will be given precomputed statistical scores, ground-truth per-number evidence, the win \
structure and known payout multipliers for the chosen game size, a budget_eur, and a \
ticket price.

Hard constraints, non-negotiable:
- Every combination of numbers_to_pick numbers from 80 is EXACTLY as likely to come up \
as any other, regardless of history. NEVER state or imply a number or combination has \
"better odds" or is "more likely" for a FUTURE draw. The only valid odds figures are \
the fixed match-count odds already given to you (from the game's hypergeometric \
structure) - those don't change based on which numbers you pick.
- What you ARE allowed to say: a number "appeared more often historically" or "scores \
higher on the frequency/recency/overdue formula" - purely descriptive of the past. \
Every such claim must cite the specific value from evidence_for_candidate_numbers (e.g. \
"41 appeared in 25.6% of draws in the last 2000" - not just "41 is hot").
- Do NOT invent numbers. Choose exactly numbers_to_pick numbers FROM top_candidate_numbers \
- do not substitute numbers outside that list.
- Do NOT do your own cost math. The cost for one line of numbers_to_pick numbers is \
always ticket_price_eur - Python recomputes and corrects this after you respond \
regardless of what you say, so don't worry about being exact.
- KINO payouts are fixed multipliers per winning ticket (not a shared pot) for every \
game size covered here. Do not invent claims about splitting a prize with other \
players - that reasoning applies to Tzoker's "5"/"5+1" categories, not to KINO's \
standard payout structure.

Respond with ONLY a JSON object (no markdown fences, no preamble):
{
  "numbers": [list of ints, length = numbers_to_pick, drawn only from top_candidate_numbers],
  "estimated_cost_eur": number,
  "pattern_notes": "1-3 sentences, each citing a specific value from the evidence, describing the observed historical pattern - never framed as future likelihood",
  "rationale": "1-2 sentences on why this game size/pick fits the budget and stated goal",
  "caveat": "1 sentence restating that draws are independent and this doesn't predict the future"
}
"""

CHAT_SYSTEM_PROMPT = SYSTEM_PROMPT + """

You are now in a follow-up conversation about the pick you already made. The user may \
ask questions, or ask you to reconsider - including based on a "cross_check" field \
showing how the currently-picked numbers rank (1=hottest of 80) over other time windows \
(last 500 draws, last 2000, all-time) than the one you originally scored against. This \
is still purely descriptive of the past - a number ranking well in one window and \
poorly in another is not evidence either way about the next draw.

If asked to reconsider, you may swap in a different number FROM top_candidate_numbers \
(never outside it), explained in terms of the historical pattern it reflects, never \
future odds. If just answering a question, keep the existing numbers exactly as they are.

Always respond with ONLY a JSON object (no markdown fences), always the full pick even \
if unchanged:
{
  "reply": "conversational answer, 1-4 sentences",
  "numbers_changed": true or false,
  "numbers": [current or revised list of ints from top_candidate_numbers],
  "estimated_cost_eur": number,
  "pattern_notes": "as before",
  "rationale": "as before",
  "caveat": "as before"
}
"""


def _parse_claude_json(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError:
        return None, f"Couldn't parse Claude's response as JSON:\n\n{raw_text}"


def _validate_and_correct(result, raw_text, payload, analyzer, numbers_to_pick, budget_eur):
    import kino_engine

    valid_numbers = {row["number"] for row in payload["top_candidate_numbers"]}
    numbers = [n for n in result.get("numbers", []) if n in valid_numbers]

    if len(numbers) < numbers_to_pick:
        return None, (
            f"Claude's response didn't include {numbers_to_pick} valid numbers from the "
            f"provided candidate list. Raw response:\n\n{raw_text}"
        )
    numbers = sorted(set(numbers))[:numbers_to_pick]
    result["numbers"] = numbers

    result["evidence_numbers"] = kino_engine.evidence_for_pick(analyzer, numbers)

    # Cost is always deterministic - one line at the standard ticket price. Never
    # trust what the model said.
    exact_cost = round(kino_core.TICKET_PRICE_EUR, 2)
    result["estimated_cost_eur"] = exact_cost
    result["over_budget"] = exact_cost > budget_eur

    return result, None


def _extract_text_and_diagnose(response):
    """
    Pull the text out of an Anthropic response, and if there's no text,
    give an actionable reason rather than a blank error. stop_reason ==
    'max_tokens' means the response was cut off before finishing - the
    actual fix is a higher max_tokens, not retrying the same call.
    """
    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    stop_reason = getattr(response, "stop_reason", None)
    if not raw_text.strip():
        if stop_reason == "max_tokens":
            return raw_text, (
                "Claude's response was empty because it hit the token limit before "
                "producing any output (stop_reason: max_tokens). This is a max_tokens "
                "setting that's too low, not a one-off glitch - try again; if it keeps "
                "happening, the max_tokens value in kino_llm.py needs raising further."
            )
        return raw_text, (
            f"Claude returned an empty response (stop_reason: {stop_reason}). "
            "Try again - if this repeats, something about the request itself may be "
            "the cause, not this specific call."
        )
    return raw_text, None


def ask_claude_for_pick(analyzer, numbers_to_pick, budget_eur, api_key, model=MODEL):
    try:
        import anthropic
    except ImportError:
        return None, "The 'anthropic' package isn't installed. Add it to requirements.txt."

    payload = build_analysis_payload(analyzer, numbers_to_pick, budget_eur)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        raw_text, empty_error = _extract_text_and_diagnose(response)
        if empty_error:
            return None, empty_error
    except Exception as e:
        return None, f"Claude API call failed: {e}"

    result, error = _parse_claude_json(raw_text)
    if error:
        return None, error

    result, error = _validate_and_correct(result, raw_text, payload, analyzer, numbers_to_pick, budget_eur)
    if error:
        return None, error

    return result, None


def ask_claude_chat(analyzer, payload, api_history, user_message, numbers_to_pick, budget_eur,
                     api_key, cross_check=None, model=MODEL):
    try:
        import anthropic
    except ImportError:
        return None, api_history, "The 'anthropic' package isn't installed. Add it to requirements.txt."

    user_payload = {"user_message": user_message}
    if cross_check is not None:
        user_payload["cross_check"] = cross_check
    new_messages = api_history + [{"role": "user", "content": json.dumps(user_payload)}]

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            system=CHAT_SYSTEM_PROMPT,
            messages=new_messages,
        )
        raw_text, empty_error = _extract_text_and_diagnose(response)
        if empty_error:
            return None, api_history, empty_error
    except Exception as e:
        return None, api_history, f"Claude API call failed: {e}"

    result, error = _parse_claude_json(raw_text)
    if error:
        return None, api_history, error

    result, error = _validate_and_correct(result, raw_text, payload, analyzer, numbers_to_pick, budget_eur)
    if error:
        return None, api_history, error

    updated_history = new_messages + [{"role": "assistant", "content": raw_text}]
    return result, updated_history, None
