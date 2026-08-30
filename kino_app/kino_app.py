"""
KINO Analysis app - Dashboard, Pick Generator (defaults to the KINO 7 game),
and an honest Backtest, mirroring the same principles as the Tzoker app:
descriptive statistics only, every odds figure independently verified via
combinatorics (not trusted from any published table), and a backtest that
proves the scoring doesn't beat random chance rather than pretending it does.

Kept as a separate app from Tzoker rather than a tab, since KINO's mechanics
(pick N of 80, no system-betting wheel, continuous 5-minute draws) are
different enough that folding it into the same navigation would be
confusing rather than convenient.
"""

import json
import os

import pandas as pd
import streamlit as st

import kino_core
import kino_engine
import kino_paytable
from kino_llm import ask_claude_chat, ask_claude_for_pick, build_analysis_payload, get_api_key
from kino_openai import get_openai_api_key, review_pick_with_chatgpt

st.set_page_config(page_title="KINO Analysis", page_icon="🔢", layout="wide")


@st.cache_data(show_spinner=False)
def get_data():
    data_dir = os.path.join(os.path.dirname(__file__) or ".", "kino_data")
    df = kino_core.load_all_kino_draws(data_dir)
    return df


def number_badges(numbers):
    cols = st.columns(len(numbers))
    for i, n in enumerate(numbers):
        cols[i].markdown(
            f"<div style='text-align:center;background:#0984e3;color:white;"
            f"border-radius:50%;width:44px;height:44px;line-height:44px;"
            f"font-weight:700;margin:auto'>{n}</div>",
            unsafe_allow_html=True,
        )


def page_dashboard(df, analyzer):
    st.header("📊 Dashboard")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total draws loaded", f"{len(df):,}")
    c2.metric("Date range", f"{df['datetime'].min().strftime('%d %b %Y')} – {df['datetime'].max().strftime('%d %b %Y')}")
    c3.metric("Draws per day (avg)", f"{analyzer.draws_per_day().mean():.0f}")

    st.caption(
        "KINO draws every 5 minutes, ~288 times a day — a completely different "
        "scale from Tzoker's 3-times-a-week draws. Statistics here converge toward "
        "the uniform-random expectation much faster as a result."
    )

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🔥 Hottest (all-time)")
        st.dataframe(pd.DataFrame(analyzer.hot_numbers(10), columns=["Number", "Times drawn"]),
                     hide_index=True, width='stretch')
    with col2:
        st.subheader("🧊 Coldest (all-time)")
        st.dataframe(pd.DataFrame(analyzer.cold_numbers(10), columns=["Number", "Times drawn"]),
                     hide_index=True, width='stretch')

    st.subheader("Most overdue")
    st.dataframe(pd.DataFrame(analyzer.overdue_numbers(10), columns=["Number", "Draws since last seen"]),
                 hide_index=True, width='stretch')


def page_pick_generator(analyzer):
    st.header("🎯 Pick Generator")
    st.info(
        "Every combination of N numbers is exactly as likely as any other, regardless "
        "of history — this organizes a pick using a consistent statistical method, it "
        "doesn't predict the next draw. KINO 7 is the default because its \"7 of 7\" "
        "payout (€2,500) matches Tzoker's 4+1, at roughly 3x better odds and half the "
        "ticket price — a fact about the game's fixed structure, not about which "
        "numbers get picked.",
        icon="ℹ️",
    )

    n_pick = st.slider("How many numbers to play (KINO game size)", 1, 12, 7)
    window = st.select_slider(
        "Recent-window size", options=[500, 2000, 5000, None], value=2000,
        format_func=lambda x: "All-time" if x is None else f"Last {x:,} draws",
    )

    pick = kino_engine.generate_pick(analyzer, numbers_to_pick=n_pick, recent_window=window or len(analyzer.all_draws))
    st.subheader(f"Your KINO {n_pick} pick")
    number_badges(pick)

    with st.expander("Real evidence behind this pick"):
        evidence = kino_engine.evidence_for_pick(analyzer, pick, recent_window=window or len(analyzer.all_draws))
        st.dataframe(pd.DataFrame(evidence), hide_index=True, width='stretch')

    st.subheader("Odds and payout for this game size")
    odds_payout = kino_engine.game_odds_and_payout(n_pick)
    ws = odds_payout["win_structure"]
    if ws:
        c1, c2, c3 = st.columns(3)
        c1.metric("Winning combinations", ws["winning_combos"])
        c2.metric("Match range to win", ws["match_range"])
        c3.metric("Payout range (€0.50 stake)", f"€{ws['payout_range_eur'][0]}–€{ws['payout_range_eur'][1]:,}")

    st.dataframe(pd.DataFrame(odds_payout["odds_by_match_count"]), hide_index=True, width='stretch')
    st.caption(
        f"Ticket price: €{odds_payout['ticket_price_eur']}/line. Odds computed directly "
        f"from the game's hypergeometric structure (20 drawn from 80), not copied from "
        f"any published table."
    )

    tc = odds_payout.get("top_category_multipliers")
    if tc:
        st.caption(
            f"Known multiplier reference (category {tc['category']}): base ×{tc['base']}, "
            f"ΠΑΡΑ1 ×{tc['para1']}, BONUS ×{tc['bonus']}, BONUS+ΠΑΡΑ1 ×{tc['bonus_para1']} "
            f"— see kino_paytable.py for what's confirmed vs partial in this table."
        )


def _send_kino_chat_message(analyzer, result, user_msg, claude_key, cross_check_rows):
    api_history = st.session_state.get("_kino_api_history")
    if not api_history:
        api_history = [
            {"role": "user", "content": json.dumps(st.session_state.get("_kino_payload", {}))},
            {"role": "assistant", "content": json.dumps(result)},
        ]

    numbers_to_pick = st.session_state.get("_kino_n_pick", 7)
    budget_eur = st.session_state.get("_kino_budget_eur", 5.0)

    with st.spinner("Thinking..."):
        new_result, updated_history, error = ask_claude_chat(
            analyzer, st.session_state["_kino_payload"], api_history, user_msg,
            numbers_to_pick, budget_eur, claude_key, cross_check=cross_check_rows,
        )

    if error:
        st.error(error)
        return

    display = st.session_state.setdefault("_kino_chat_display", [])
    display.append(("user", user_msg))

    reply_text = new_result.get("reply", "")
    if new_result.get("numbers_changed"):
        old_numbers = result.get("numbers", [])
        reply_text += f"\n\n*Pick updated: numbers {old_numbers} → {new_result['numbers']}.*"
    display.append(("assistant", reply_text))

    st.session_state["_kino_result"] = new_result
    st.session_state["_kino_api_history"] = updated_history
    st.session_state.pop("_kino_chatgpt_review", None)
    st.rerun()


def page_ai_insights(analyzer):
    st.header("🤖 AI Insights (Claude + ChatGPT second opinion)")
    st.info(
        "Neither model predicts draws — every combination of numbers_to_pick from 80 is "
        "exactly as likely as any other, regardless of history. Claude reads the same "
        "statistical scores the Pick Generator uses and picks from within them — it "
        "can't invent its own numbers. ChatGPT then reviews that pick against the same "
        "evidence, rather than proposing a competing pick of its own.",
        icon="ℹ️",
    )

    claude_key = get_api_key()
    if not claude_key:
        st.warning(
            "No Anthropic API key found. Set `ANTHROPIC_API_KEY` (Railway: Project → "
            "Variables), or paste one below for this session only.",
            icon="🔑",
        )
        pasted = st.text_input("Anthropic API key", type="password", key="_kino_key_input_field")
        if pasted:
            st.session_state["_kino_anthropic_key_input"] = pasted
            st.rerun()
        return

    c1, c2 = st.columns(2)
    with c1:
        n_pick = st.slider("Game size (numbers to play)", 1, 12, 7, key="_kino_n_pick_widget")
    with c2:
        budget_eur = st.number_input("Budget (€)", min_value=0.5, max_value=100.0, value=5.0, step=0.5)

    if st.button("Ask Claude"):
        with st.spinner("Analyzing historical scores..."):
            result, error = ask_claude_for_pick(analyzer, n_pick, budget_eur, claude_key)
        if error:
            st.error(error)
            st.session_state.pop("_kino_result", None)
        else:
            payload = build_analysis_payload(analyzer, n_pick, budget_eur)
            st.session_state["_kino_result"] = result
            st.session_state["_kino_payload"] = payload
            st.session_state["_kino_n_pick"] = n_pick
            st.session_state["_kino_budget_eur"] = budget_eur
            st.session_state["_kino_api_history"] = [
                {"role": "user", "content": json.dumps(payload)},
                {"role": "assistant", "content": json.dumps(result)},
            ]
            st.session_state["_kino_chat_display"] = []
            st.session_state.pop("_kino_chatgpt_review", None)

    result = st.session_state.get("_kino_result")
    if not result:
        return

    st.subheader("Claude's recommended pick")
    number_badges(result["numbers"])
    st.metric("Estimated cost", f"€{result.get('estimated_cost_eur', 0):.2f}")
    if result.get("over_budget"):
        st.warning(
            f"This costs €{result['estimated_cost_eur']:.2f}, over your stated budget.",
            icon="💸",
        )
    st.write("**Pattern notes:**", result.get("pattern_notes", ""))
    st.write("**Rationale:**", result.get("rationale", ""))
    st.caption(result.get("caveat", ""))

    with st.expander("Real evidence behind this pick (computed independently in Python)"):
        st.caption("Actual historical counts for exactly the numbers above — not Claude's restatement.")
        st.dataframe(pd.DataFrame(result.get("evidence_numbers", [])), hide_index=True, width='stretch')

    cross_check_rows = None
    with st.expander("Cross-check against the Dashboard (same numbers, other time windows)"):
        st.caption(
            "Claude scores off one fixed recent window. This shows the same picked "
            "numbers' hot/cold rank (1=hottest of 80) across last 500 draws (~1.7 days), "
            "last 2000 (~1 week), and all-time."
        )
        mw = analyzer.multi_window_stats(result["numbers"])
        cross_check_rows = []
        for n in result["numbers"]:
            row = {"number": n}
            for label, pretty in [("last_500", "Last 500"), ("last_2000", "Last 2000"), ("all_time", "All-time")]:
                stats = mw[n].get(label, {})
                row[f"{pretty} rank"] = stats.get("rank_of_80")
                row[f"{pretty} count"] = stats.get("count")
            cross_check_rows.append(row)
        st.dataframe(pd.DataFrame(cross_check_rows), hide_index=True, width='stretch')

    st.divider()
    st.subheader("💬 Discuss or revise this pick")
    if st.button("Ask Claude to reconsider given the cross-check table above"):
        _send_kino_chat_message(
            analyzer, result,
            "Given the cross-check table above showing these numbers' ranks over other "
            "time windows, should any be reconsidered? Suggest changes if warranted, "
            "otherwise explain why the current pick still holds up.",
            claude_key, cross_check_rows,
        )

    for role, text in st.session_state.get("_kino_chat_display", []):
        with st.chat_message(role):
            st.write(text)

    user_msg = st.chat_input("Ask about this pick, or ask Claude to reconsider…")
    if user_msg:
        _send_kino_chat_message(analyzer, result, user_msg, claude_key, cross_check_rows)

    st.divider()
    st.subheader("Second opinion (ChatGPT)")

    openai_key = get_openai_api_key()
    if not openai_key:
        st.warning(
            "No OpenAI API key found. Set `OPENAI_API_KEY` (Railway: Project → "
            "Variables), or paste one below for this session only.",
            icon="🔑",
        )
        pasted_oa = st.text_input("OpenAI API key", type="password", key="_kino_oa_key_input_field")
        if pasted_oa:
            st.session_state["_kino_openai_key_input"] = pasted_oa
            st.rerun()
        return

    if st.button("Get ChatGPT's review"):
        with st.spinner("Checking Claude's pick..."):
            review, rev_error = review_pick_with_chatgpt(
                st.session_state["_kino_payload"], result, openai_key
            )
        if rev_error:
            st.error(rev_error)
        else:
            st.session_state["_kino_chatgpt_review"] = review

    review = st.session_state.get("_kino_chatgpt_review")
    if review:
        verdict = review.get("verdict", "unknown")
        if verdict == "pass":
            st.success(f"ChatGPT verdict: **pass** — {review.get('summary', '')}")
        else:
            st.warning(f"ChatGPT verdict: **{verdict}** — {review.get('summary', '')}")
        st.dataframe(pd.DataFrame(review.get("checks", [])), hide_index=True, width='stretch')


def page_backtest(analyzer):
    st.header("📈 Backtest")
    st.warning(
        "This replays the scoring strategy against draws that already happened, using "
        "only data available before each point tested. It tells you how the heuristic "
        "would have scored historically — since each draw is independent, it is not "
        "evidence about how future draws will go.",
        icon="⚠️",
    )

    n_pick = st.slider("Game size to backtest", 1, 12, 7, key="bt_n")
    lookback = st.slider("How many recent draws to backtest over", 500, 5000, 2000, step=500)
    step = st.slider("Test every Nth draw (larger = faster, coarser sample)", 10, 200, 50, step=10)

    if st.button("Run backtest"):
        with st.spinner("Replaying history..."):
            hist, tested = kino_engine.backtest(analyzer, numbers_to_pick=n_pick, lookback_draws=lookback,
                                                 window=min(lookback, 2000), step=step)
        if tested == 0:
            st.error("Not enough history to backtest with the current settings.")
            return

        baseline = {row["matches"]: row["chance_pct"] for row in kino_engine.chance_baseline(n_pick)}
        rows = []
        for k in range(n_pick, -1, -1):
            observed = hist.get(k, 0)
            rows.append({
                "Matches": k,
                "Observed occurrences": observed,
                "Observed rate": f"{100 * observed / tested:.1f}%",
                "Pure-chance rate": f"{baseline.get(k, 0):.1f}%",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        st.metric("Draws tested", tested)
        st.caption(
            "Compare the two rate columns — if the scoring were doing anything the "
            "random-chance baseline doesn't, you'd see a consistent gap. In testing, "
            "these tracked each other closely, which is the expected, honest result."
        )


def main():
    df = get_data()
    if df.empty:
        st.error(
            "No converted KINO data found in kino_data/. Convert a raw monthly export "
            "with kino_core.convert_raw_kino_xlsx() first."
        )
        return

    analyzer = kino_core.KinoAnalyzer(df)

    st.sidebar.title("🔢 KINO Analysis")
    page = st.sidebar.radio("Section", ["Dashboard", "Pick Generator", "AI Insights", "Backtest"])

    if page == "Dashboard":
        page_dashboard(df, analyzer)
    elif page == "Pick Generator":
        page_pick_generator(analyzer)
    elif page == "AI Insights":
        page_ai_insights(analyzer)
    elif page == "Backtest":
        page_backtest(analyzer)


if __name__ == "__main__":
    main()
