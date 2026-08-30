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
import kino_live
import kino_paytable
import kino_tracking
from kino_llm import ask_claude_chat, ask_claude_for_pick, build_analysis_payload, get_api_key
from kino_openai import get_openai_api_key, review_pick_with_chatgpt

st.set_page_config(page_title="KINO Analysis", page_icon="🔢", layout="wide")


@st.cache_data(show_spinner=False)
def get_data():
    data_dir = os.path.join(os.path.dirname(__file__) or ".", "kino_data")
    df = kino_core.load_all_kino_draws(data_dir)
    return df


LIVE_DATA_PATH = os.path.join(os.path.dirname(__file__) or ".", "kino_data", "kino_live.parquet")
AUTO_TRACK_GAME_SIZE = 7  # matches the app's default KINO 7 focus


def live_refresh():
    """
    Fetch any draws newer than what's loaded, persist them alongside the
    historical monthly files, clear the data cache so they're picked up,
    resolve any pending tracked picks against the fresh data, and - if
    there's no pending auto-generated pick already queued - generate and
    save the next one, so tracking runs continuously without a manual
    "track this pick" click after every single draw. Returns a dict
    summary for display - never raises, always returns an 'error' key on
    failure so the UI can show it plainly.
    """
    current_df = get_data()
    if current_df.empty:
        return {"error": "No historical data loaded yet - nothing to refresh against."}

    known_max_draw_id = int(current_df["draw_id"].max())
    known_max_datetime = current_df["datetime"].max()

    new_draws, error = kino_live.fetch_new_draws(known_max_draw_id, known_max_datetime)
    if error:
        return {"error": error}
    if new_draws.empty:
        return {"new_draws": 0, "resolved_picks": 0, "new_pick_queued": False}

    if os.path.exists(LIVE_DATA_PATH):
        existing_live = pd.read_parquet(LIVE_DATA_PATH)
        combined_live = pd.concat([existing_live, new_draws], ignore_index=True)
        combined_live = combined_live.drop_duplicates(subset="draw_id")
    else:
        combined_live = new_draws
    combined_live.to_parquet(LIVE_DATA_PATH)

    get_data.clear()
    refreshed_df = get_data()

    n_resolved = kino_tracking.resolve_pending_picks(refreshed_df)

    new_pick_queued = False
    if not kino_tracking.has_pending_pick(AUTO_TRACK_GAME_SIZE, source="engine_auto"):
        analyzer = kino_core.KinoAnalyzer(refreshed_df)
        next_pick = kino_engine.generate_pick(analyzer, numbers_to_pick=AUTO_TRACK_GAME_SIZE)
        max_draw_id = int(refreshed_df["draw_id"].max())
        kino_tracking.save_pending_pick(next_pick, max_draw_id, source="engine_auto")
        new_pick_queued = True

    return {
        "new_draws": len(new_draws), "resolved_picks": n_resolved,
        "new_pick_queued": new_pick_queued,
    }


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

    with st.expander("🔄 Fetch latest draws"):
        st.caption(
            "Pulls any draws newer than what's loaded from OPAP's public draws API "
            "(unverified against the live endpoint from this build environment - if "
            "it fails, the error will say why; existing data keeps working either way). "
            "Also resolves any pending tracked pick against the new draws, and — if "
            "none is queued — automatically generates and queues the next one, so "
            "tracking runs continuously without a manual click after every draw."
        )
        if st.button("Fetch latest draws now"):
            with st.spinner("Checking for new draws..."):
                result = live_refresh()
            st.session_state["_kino_last_refresh_result"] = result
            if not result.get("error") and result.get("new_draws"):
                st.rerun()

        last_result = st.session_state.get("_kino_last_refresh_result")
        if last_result:
            if last_result.get("error"):
                st.error(last_result["error"])
            else:
                msg = (
                    f"Fetched {last_result['new_draws']} new draw(s). "
                    f"Resolved {last_result['resolved_picks']} pending tracked pick(s)."
                )
                if last_result.get("new_pick_queued"):
                    msg += f" Queued a new KINO {AUTO_TRACK_GAME_SIZE} pick for the next draw."
                st.success(msg)

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

    st.divider()
    if st.button("💾 Track this pick against future draws"):
        max_draw_id = int(analyzer.all_draws["draw_id"].max())
        saved = kino_tracking.save_pending_pick(pick, max_draw_id, source="engine")
        if saved:
            st.success(
                f"Saved. It'll resolve against the next real draw after #{max_draw_id} "
                f"once you fetch new draws — check the Track Record tab."
            )
        else:
            st.info("This exact pick is already pending for the same target draw — not saved again.")


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

    if st.button("💾 Track this pick against future draws", key="_kino_track_ai_pick"):
        max_draw_id = int(analyzer.all_draws["draw_id"].max())
        saved = kino_tracking.save_pending_pick(result["numbers"], max_draw_id, source="claude")
        if saved:
            st.success("Saved. Check the Track Record tab after fetching new draws.")
        else:
            st.info("This exact pick is already pending for the same target draw — not saved again.")

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


def page_track_record(analyzer):
    st.header("🎯 Track Record")
    st.info(
        "This is a real record of picks checked against real draws that happened "
        "after they were saved — different from Backtest, which replays history. "
        "Same honest framing applies: this shows what happened, not evidence about "
        "what will happen next.",
        icon="ℹ️",
    )

    if st.button("Check for resolvable picks (fetches latest draws first)"):
        with st.spinner("Fetching latest draws..."):
            result = live_refresh()
        st.session_state["_kino_track_refresh_result"] = result

    last_result = st.session_state.get("_kino_track_refresh_result")
    if last_result:
        if last_result.get("error"):
            st.error(last_result["error"])
        else:
            msg = (f"Fetched {last_result.get('new_draws', 0)} new draw(s), "
                   f"resolved {last_result.get('resolved_picks', 0)} pick(s).")
            if last_result.get("new_pick_queued"):
                msg += f" Queued a new KINO {AUTO_TRACK_GAME_SIZE} pick for the next draw."
            st.success(msg)

    data, summary = kino_tracking.get_tracking_summary()

    st.subheader("Summary by game size")
    if summary:
        rows = []
        for n_pick, stats in sorted(summary.items()):
            baseline = kino_engine.chance_baseline(n_pick)
            expected_avg = sum(row["matches"] * row["chance_pct"] / 100 for row in baseline)
            rows.append({
                "Game size": n_pick,
                "Picks resolved": stats["count"],
                "Avg. matches (actual)": stats["avg_matches"],
                "Avg. matches (pure chance)": round(expected_avg, 2),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        st.caption(
            "If 'actual' consistently and substantially beats 'pure chance' here, "
            "that would be a genuinely interesting finding worth scrutinizing hard — "
            "on a large enough sample, it shouldn't happen by the same logic as the "
            "Backtest tab."
        )
    else:
        st.caption("No resolved picks yet.")

    st.subheader(f"Pending ({len(data['pending'])})")
    if data["pending"]:
        st.dataframe(pd.DataFrame(data["pending"]), hide_index=True, width='stretch')
    else:
        st.caption("Nothing pending.")

    st.subheader(f"Resolved ({len(data['resolved'])})")
    if data["resolved"]:
        rows = [{
            "Numbers": r["numbers"], "Source": r["source"],
            "Generated at draw": r["generated_at_draw_id"],
            "Resolved against draw": r["resolved_against_draw_id"],
            "Matches": r["matches"],
        } for r in reversed(data["resolved"])]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    else:
        st.caption("Nothing resolved yet.")


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
    page = st.sidebar.radio("Section", ["Dashboard", "Pick Generator", "AI Insights", "Track Record", "Backtest"])

    if page == "Dashboard":
        page_dashboard(df, analyzer)
    elif page == "Pick Generator":
        page_pick_generator(analyzer)
    elif page == "AI Insights":
        page_ai_insights(analyzer)
    elif page == "Track Record":
        page_track_record(analyzer)
    elif page == "Backtest":
        page_backtest(analyzer)


if __name__ == "__main__":
    main()
