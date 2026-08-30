"""
Tracks real picks (from the Pick Generator or AI Insights) against what
actually gets drawn next, building a genuine forward-looking track record -
distinct from backtest(), which replays HISTORY. This resolves picks
against draws that happen AFTER the pick was saved.

Same honesty framing as everywhere else: this measures whether the scoring
method's picks land above, at, or below the pure-chance baseline over time.
It is a record of what happened, not evidence future picks will do better -
each draw is still independent.
"""

import json
import os

TRACKING_FILE_DEFAULT = "kino_tracking.json"


def _load(filepath):
    if not os.path.exists(filepath):
        return {"pending": [], "resolved": []}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("pending", [])
            data.setdefault("resolved", [])
            return data
    except (json.JSONDecodeError, OSError):
        return {"pending": [], "resolved": []}


def _save(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def save_pending_pick(numbers, generated_at_draw_id, source="engine", filepath=TRACKING_FILE_DEFAULT):
    """source: 'engine' (deterministic scoring) or 'claude' (AI Insights pick)."""
    data = _load(filepath)
    data["pending"].append({
        "numbers": sorted(numbers),
        "numbers_to_pick": len(numbers),
        "generated_at_draw_id": generated_at_draw_id,
        "source": source,
    })
    _save(data, filepath)


def resolve_pending_picks(current_df, filepath=TRACKING_FILE_DEFAULT):
    """
    Check every pending pick against the earliest real draw that happened
    after it was generated. Resolved picks move to the 'resolved' list with
    their actual match count. Returns the number newly resolved.
    """
    data = _load(filepath)
    if not data["pending"] or current_df.empty:
        return 0

    still_pending = []
    newly_resolved = 0
    # earliest draw at/after each draw_id, precomputed once
    sorted_draws = current_df.sort_values("draw_id")

    for pick in data["pending"]:
        candidates = sorted_draws[sorted_draws["draw_id"] > pick["generated_at_draw_id"]]
        if candidates.empty:
            still_pending.append(pick)
            continue
        actual_draw = candidates.iloc[0]
        actual_numbers = set(int(n) for n in actual_draw["numbers"])
        matches = len(set(pick["numbers"]) & actual_numbers)
        data["resolved"].append({
            **pick,
            "resolved_against_draw_id": int(actual_draw["draw_id"]),
            "actual_numbers": sorted(actual_numbers),
            "matches": matches,
        })
        newly_resolved += 1

    data["pending"] = still_pending
    _save(data, filepath)
    return newly_resolved


def has_pending_pick(numbers_to_pick, source=None, filepath=TRACKING_FILE_DEFAULT):
    """True if there's already an unresolved pick of this game size (and
    optionally source) waiting to be resolved - used to avoid queuing up
    duplicate pending picks every time a refresh runs."""
    data = _load(filepath)
    for p in data["pending"]:
        if p["numbers_to_pick"] == numbers_to_pick and (source is None or p["source"] == source):
            return True
    return False


def get_tracking_summary(filepath=TRACKING_FILE_DEFAULT):
    """Returns the raw data plus a per-game-size summary of resolved picks."""
    data = _load(filepath)
    by_size = {}
    for r in data["resolved"]:
        n = r["numbers_to_pick"]
        by_size.setdefault(n, []).append(r["matches"])

    summary = {}
    for n, match_list in by_size.items():
        summary[n] = {
            "count": len(match_list),
            "avg_matches": round(sum(match_list) / len(match_list), 2),
            "match_distribution": {k: match_list.count(k) for k in sorted(set(match_list))},
        }
    return data, summary
