"""
Number-selection engine for a KINO game (defaults to KINO 7 - see rationale
below - but works for any numbers_to_pick from 1-12).

Same honesty rules as the Tzoker engine (tzoker_llm.py):
  - Scoring describes PAST frequency/recency/overdue patterns only. It does
    not and cannot predict an independent future draw - every combination of
    numbers_to_pick from 80 is exactly as likely as any other, regardless of
    history.
  - Because KINO draws every 5 minutes (vs Tzoker's 3/week), the natural
    scale of a "recent window" is completely different - a few hundred
    draws is still under a day. Windows here default much larger than
    Tzoker's to represent a comparable real-world timespan.
  - backtest() replays the same scoring against real history, using only
    prior data at each point, so you can see for yourself that it performs
    in line with random chance - the same honest check the Tzoker Backtest
    tab provides, not a promise this beats the odds.

Why KINO 7 as the default: independently verified against Tzoker's 4+1 -
KINO 7's "7 of 7" pays the same EUR 2,500 as Tzoker 4+1, at roughly 3x
better odds (1 in 40,979 vs 1 in 122,176) and half the ticket price. That
comparison is about the GAME's fixed structure, not this engine's picks -
this module doesn't change those odds, it only helps organize a consistent
play within them.
"""

from collections import Counter

import numpy as np

import kino_core
import kino_paytable


def score_numbers(analyzer, recent_window=2000):
    """
    Composite 0-1 score per number 1-80, blending overall frequency, a
    recent-window frequency, and overdue gap - same three-factor approach as
    tzoker_core.JokerAnalyzer.score_numbers(), reweighted for KINO's field
    size and draw frequency. recent_window defaults to 2000 draws (~1 week
    at KINO's ~288 draws/day pace) rather than Tzoker's 150 (~1 year at 3/
    week) - different games need different-sized "recent".
    """
    total_draws = len(analyzer.all_draws)
    if total_draws == 0:
        return {n: 0.0 for n in range(1, kino_core.TOTAL_NUMBERS + 1)}

    freq = analyzer._frequency_array()[1:]  # index 0 unused, numbers are 1-80
    overall = {n: freq[n - 1] / total_draws for n in range(1, kino_core.TOTAL_NUMBERS + 1)}

    recent_matrix = analyzer._numbers_matrix[-recent_window:] if recent_window else analyzer._numbers_matrix
    recent_freq = analyzer._frequency_array(recent_matrix)[1:]
    recent_n = len(recent_matrix)
    recent = {n: recent_freq[n - 1] / max(1, recent_n) for n in range(1, kino_core.TOTAL_NUMBERS + 1)}

    gaps = dict(analyzer.overdue_numbers(top_n=kino_core.TOTAL_NUMBERS))
    max_gap = max(gaps.values()) if gaps else 1
    overdue = {n: gaps.get(n, 0) / max(1, max_gap) for n in range(1, kino_core.TOTAL_NUMBERS + 1)}

    max_overall = max(overall.values()) or 1
    max_recent = max(recent.values()) or 1
    score = {}
    for n in range(1, kino_core.TOTAL_NUMBERS + 1):
        score[n] = 0.45 * overall[n] / max_overall + 0.35 * recent[n] / max_recent + 0.20 * overdue[n]
    return score


def generate_pick(analyzer, numbers_to_pick=7, recent_window=2000):
    """Top-scored `numbers_to_pick` numbers, sorted ascending."""
    scores = score_numbers(analyzer, recent_window=recent_window)
    ranked = sorted(scores, key=scores.get, reverse=True)
    return sorted(ranked[:numbers_to_pick])


def evidence_for_pick(analyzer, numbers, recent_window=2000):
    """Real per-number counts/pct/gap for exactly the given numbers - ground truth,
    same pattern as tzoker_core.JokerAnalyzer.number_evidence()."""
    total = len(analyzer.all_draws)
    recent_matrix = analyzer._numbers_matrix[-recent_window:] if recent_window else analyzer._numbers_matrix
    recent_freq = analyzer._frequency_array(recent_matrix)[1:]
    recent_n = len(recent_matrix)
    overall_freq = analyzer._frequency_array()[1:]
    gaps = dict(analyzer.overdue_numbers(top_n=kino_core.TOTAL_NUMBERS))

    rows = []
    for n in numbers:
        rows.append({
            "number": n,
            "overall_count": int(overall_freq[n - 1]),
            "overall_pct": round(100 * overall_freq[n - 1] / total, 2) if total else 0,
            "recent_count": int(recent_freq[n - 1]),
            "recent_window_draws": recent_n,
            "draws_since_last_seen": gaps.get(n),
        })
    return rows


def game_odds_and_payout(numbers_to_pick=7):
    """
    Odds (independently derived combinatorics) and known payout structure
    for a given game size, pulled together for display. Odds are always
    trustworthy (pure math); payout figures are only as good as what's in
    kino_paytable.py - see that module's docstring for exactly what's
    confirmed vs partial.
    """
    win_structure = kino_paytable.WIN_STRUCTURE.get(numbers_to_pick, {})
    top_category = kino_paytable.TOP_CATEGORY_MULTIPLIERS.get(numbers_to_pick)

    odds_by_match = []
    for k in range(0, numbers_to_pick + 1):
        odds_by_match.append({
            "matches": k,
            "odds_1_in": round(kino_core.odds_catch_k_of_n(numbers_to_pick, k)),
        })

    return {
        "numbers_to_pick": numbers_to_pick,
        "win_structure": win_structure,
        "top_category_multipliers": top_category,
        "odds_by_match_count": odds_by_match,
        "ticket_price_eur": kino_core.TICKET_PRICE_EUR,
    }


def backtest(analyzer, numbers_to_pick=7, lookback_draws=2000, window=2000, step=50):
    """
    Honest backtest: replays score_numbers()-based picks against real history,
    using only draws BEFORE each tested point, and tallies how many of the
    numbers_to_pick numbers actually matched. Same purpose as
    tzoker_core.JokerAnalyzer.backtest_strategy() - proving (or disproving)
    that the scoring beats random chance, not promising that it will.

    `step` skips ahead this many draws between each backtest point rather
    than testing every single draw - at KINO's draw volume, re-scoring from
    scratch at every one of thousands of draws would be far slower than
    it's worth for the same statistical picture; every `step`-th draw gives
    a representative sample without the cost.
    """
    n = len(analyzer.all_draws)
    start = max(window + 1, n - lookback_draws)
    hit_histogram = Counter()
    tested = 0

    for i in range(start, n, step):
        history = analyzer.all_draws.iloc[:i]
        if len(history) < window:
            continue
        temp_analyzer = kino_core.KinoAnalyzer(history)
        scores = score_numbers(temp_analyzer, recent_window=window)
        top_picks = set(sorted(scores, key=scores.get, reverse=True)[:numbers_to_pick])

        actual = set(int(x) for x in analyzer._numbers_matrix[i])
        matches = len(top_picks & actual)
        hit_histogram[matches] += 1
        tested += 1

    return hit_histogram, tested


def chance_baseline(numbers_to_pick=7):
    """
    Pure-random-chance match distribution for numbers_to_pick candidates
    against 20 drawn from 80 - the honest comparison point for backtest()
    results, same purpose as the random-chance table used for the Tzoker
    Backtest explanation.
    """
    from math import comb
    total = comb(kino_core.TOTAL_NUMBERS, kino_core.DRAWN_PER_ROUND)
    rows = []
    for k in range(0, numbers_to_pick + 1):
        count = comb(numbers_to_pick, k) * comb(
            kino_core.TOTAL_NUMBERS - numbers_to_pick, kino_core.DRAWN_PER_ROUND - k
        )
        p = count / total if total else 0
        rows.append({"matches": k, "chance_pct": round(p * 100, 2)})
    return rows
