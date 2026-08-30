"""
Official Allwyn KINO paytable, transcribed from Allwyn's own published guide
(pasted by the user, Aug 2026). This is business-set data (payout multipliers
Allwyn chooses), not something derivable from the game's odds alone - so
unlike kino_core.py's odds_catch_k_of_n(), this data has to come from the
official source and can only be as accurate as what's transcribed.

What's captured here, and its confidence level:

CONFIRMED, internally consistent (WIN_STRUCTURE): for each game size (how
many numbers you play, 1-12), how many winning combinations exist, the match
range needed to win, and the payout range at the EUR 0.50 minimum stake.
Cross-checked against kino_core.odds_catch_k_of_n() and the worked example
in the source text (game-of-6, 5/6 catches, base multiplier 50 -> EUR 25 at
EUR 0.50 stake - matches exactly).

PARTIAL (TOP_CATEGORY_MULTIPLIERS): base/PARA1/BONUS/BONUS+PARA1 multipliers,
but ONLY for the single best sub-category shown per game size 2-9 (e.g. game
6 only gives the 5/6 row, not 3/6 or 4/6) - not a full per-match-count
breakdown, and games 1, 10, 11, 12 aren't covered by this table at all.
Note this is deliberately NOT the same reference point as WIN_STRUCTURE's
payout_range_eur max, which is the PERFECT-match category at base stake only
(e.g. game 6's WIN_STRUCTURE max of EUR 800 = 6/6 at base multiplier 1600,
vs TOP_CATEGORY_MULTIPLIERS[6] describing the lesser 5/6 category with every
enhancement stacked, EUR 265) - the two tables describe different things and
aren't inconsistent with each other, just not directly comparable.

FLAGGED INCONSISTENCY: a separate fragment gave full per-category multipliers
for game 6 (6/6=1600, 5/6=50, 4/6=7, 3/6=1) plus a second unlabeled row
(4100, 300, 27, 9, 3, 2) that looks like it should be the PARA1 multipliers
for the same categories - but its 5/6 value (300) doesn't match
TOP_CATEGORY_MULTIPLIERS's stated PARA1 value for 5/6 (280). Both numbers are
kept below, separately, rather than silently picking one - if you have
access to Allwyn's page directly, worth checking which is right.
"""

TICKET_PRICE_EUR = 0.50

# numbers_played -> dict of win structure, per Allwyn's own summary table.
WIN_STRUCTURE = {
    12: {"winning_combos": 8, "match_range": "6-12 or 0 (money-back on 0)", "payout_range_eur": (2, 500_000)},
    11: {"winning_combos": 8, "match_range": "5-11 or 0 (money-back on 0)", "payout_range_eur": (0.5, 250_000)},
    10: {"winning_combos": 7, "match_range": "5-10 or 0 (money-back on 0)", "payout_range_eur": (1, 50_000)},
    9:  {"winning_combos": 6, "match_range": "4+", "payout_range_eur": (0.5, 20_000)},
    8:  {"winning_combos": 5, "match_range": "4+", "payout_range_eur": (1, 7_500)},
    7:  {"winning_combos": 5, "match_range": "3+", "payout_range_eur": (0.5, 2_500)},
    6:  {"winning_combos": 4, "match_range": "3+", "payout_range_eur": (0.5, 800)},
    5:  {"winning_combos": 3, "match_range": "3+", "payout_range_eur": (1, 225)},
    4:  {"winning_combos": 3, "match_range": "2+", "payout_range_eur": (0.5, 50)},
    3:  {"winning_combos": 2, "match_range": "2+", "payout_range_eur": (1.25, 12.5)},
    2:  {"winning_combos": 2, "match_range": "1+", "payout_range_eur": (0.5, 2.5)},
    1:  {"winning_combos": 1, "match_range": "1 (exact)", "payout_range_eur": (1.25, 1.25)},
}

# Only the single best sub-category shown per game in the source table -
# NOT a full per-match-count breakdown. category is "matches/numbers_played".
TOP_CATEGORY_MULTIPLIERS = {
    2: {"category": "1/2", "base": 1, "para1": 3, "bonus": 16, "bonus_para1": 18},
    3: {"category": "2/3", "base": 2.5, "para1": 7.5, "bonus": 18, "bonus_para1": 23},
    4: {"category": "3/4", "base": 4, "para1": 20, "bonus": 24, "bonus_para1": 40},
    5: {"category": "4/5", "base": 20, "para1": 80, "bonus": 90, "bonus_para1": 150},
    6: {"category": "5/6", "base": 50, "para1": 280, "bonus": 300, "bonus_para1": 530},
    7: {"category": "6/7", "base": 100, "para1": 1_000, "bonus": 400, "bonus_para1": 1_300},
    8: {"category": "7/8", "base": 1_000, "para1": 5_200, "bonus": 3_000, "bonus_para1": 7_200},
    9: {"category": "8/9", "base": 4_000, "para1": 24_000, "bonus": 10_000, "bonus_para1": 30_000},
}

# Full per-category base multipliers for game 6 specifically, from a separate
# fragment - the only game we have a complete breakdown for.
GAME_6_FULL_MULTIPLIERS = {
    "base": {6: 1600, 5: 50, 4: 7, 3: 1},
    # Unconfirmed - see module docstring. Kept for reference, not used anywhere
    # as authoritative, since it disagrees with TOP_CATEGORY_MULTIPLIERS[6]["para1"].
    "unconfirmed_second_row": {6: 4100, 5: 300, 4: 27, 3: 9, 2: 3, 1: 2},
}

# KINO BONUS side-bet: winning combos and payout range per game size, when
# playing the BONUS option (extra EUR 0.50) and correctly including the
# KINO BONUS number among your matches.
BONUS_STRUCTURE = {
    12: {"winning_combos": 12, "payout_range_eur": (1, 1_000_000)},
    11: {"winning_combos": 11, "payout_range_eur": (1, 600_000)},
    10: {"winning_combos": 10, "payout_range_eur": (1, 125_000)},
    9:  {"winning_combos": 9, "payout_range_eur": (1, 50_000)},
    8:  {"winning_combos": 8, "payout_range_eur": (1, 20_000)},
    7:  {"winning_combos": 7, "payout_range_eur": (1, 7_500)},
    6:  {"winning_combos": 6, "payout_range_eur": (1, 2_050)},
    5:  {"winning_combos": 5, "payout_range_eur": (1.5, 675)},
    4:  {"winning_combos": 4, "payout_range_eur": (2.5, 300)},
    3:  {"winning_combos": 3, "payout_range_eur": (4, 87.5)},
    2:  {"winning_combos": 2, "payout_range_eur": (8, 35)},
    1:  {"winning_combos": 1, "payout_range_eur": (26.25, 26.25)},
}

DRAW_SCHEDULE = "Every 5 minutes, 00:00-23:55, 24/7 (~288 draws/day)"
