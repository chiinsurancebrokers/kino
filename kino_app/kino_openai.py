"""
OpenAI (ChatGPT) integration for the KINO app - second-opinion validator.

Same scope as tzoker_openai.py: reviews Claude's pick against the same real
evidence, doesn't propose a competing pick, checks for future-odds
overclaiming and unsupported claims.
"""

import json
import os

import streamlit as st

OPENAI_MODEL = "gpt-5"


def get_openai_api_key():
    try:
        if "OPENAI_API_KEY" in st.secrets:
            return st.secrets["OPENAI_API_KEY"]
    except Exception:
        pass
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    return st.session_state.get("_kino_openai_key_input")


REVIEW_SYSTEM_PROMPT = """You are a skeptical reviewer checking another AI's KINO pick \
for correctness, NOT generating your own pick from scratch.

You are given the same precomputed statistical scores/evidence the other AI was given,
and its response (numbers, estimated_cost_eur, over_budget, pattern_notes, rationale,
caveat, evidence_numbers - the REAL counts, attached independently in Python, not
written by the other AI).

Note: estimated_cost_eur has already been independently recomputed in Python, so you
don't need to re-check that arithmetic. Focus on:

1. Cross-check every quantitative claim in pattern_notes against evidence_numbers. Flag
   any number that's wrong or unsupported.
2. Does pattern_notes or rationale cross the line from "appeared more often
   historically" into implying the pick is more likely to WIN a FUTURE draw? Every
   combination is equally likely regardless of history - this is the single most
   important check.
3. Does rationale or pattern_notes ever suggest wins will exceed losses over time, or
   that the game's negative expected value can be overcome by number selection? Flag
   this - it's false regardless of framing.
4. Does rationale or pattern_notes incorrectly mention "splitting a prize" or "pot
   sharing"? KINO's payouts here are fixed multipliers per ticket, not a shared pot -
   that reasoning belongs to Tzoker's "5"/"5+1" categories, not KINO.
5. If over_budget is true, does rationale/caveat acknowledge it?
6. Is the independence caveat present and accurate?

Do not propose alternative numbers. Do not claim any pick is more or less likely to
win future draws.

Respond with ONLY a JSON object (no markdown fences):
{
  "verdict": "pass" | "issues_found",
  "checks": [
    {"check": "short name", "ok": true/false, "note": "1 sentence"}
  ],
  "summary": "1-2 sentence overall verdict in plain language"
}
"""


def review_pick_with_chatgpt(analysis_payload, claude_result, api_key, model=OPENAI_MODEL):
    try:
        import openai
    except ImportError:
        return None, "The 'openai' package isn't installed. Add it to requirements.txt."

    user_content = json.dumps({
        "given_to_other_ai": analysis_payload,
        "other_ai_response": claude_result,
    })

    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        raw_text = response.choices[0].message.content
    except Exception as e:
        return None, f"OpenAI API call failed: {e}"

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        return None, f"Couldn't parse ChatGPT's response as JSON:\n\n{raw_text}"

    if "verdict" not in result or "checks" not in result:
        return None, f"ChatGPT's response was missing expected fields:\n\n{raw_text}"

    return result, None
