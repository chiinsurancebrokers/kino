"""
Core data pipeline and statistics for Greek KINO (Allwyn).

KINO is structurally nothing like Tzoker: 20 numbers are drawn from a field of
80, every ~5 minutes, around the clock. A single month is already ~9,000
draws; full history is genuinely in the millions. That changes the
engineering requirements completely:

  - Raw exports (like Allwyn's official monthly xlsx) have ~328 columns per
    draw - almost all of it is Allwyn's own per-draw payout accounting across
    every bet type, which we don't need and which makes the file very slow
    to parse (>25s for a single month via openpyxl, confirmed by timing it).
  - So: convert_raw_kino_xlsx() is a ONE-TIME preprocessing step, run once
    per raw file you're given, that keeps only draw_id/datetime/20 numbers/
    kb/odd_even and writes a compact Parquet file. Measured: 287KB vs the
    original 11MB, and 0.1s load time vs 26.8s - a ~270x speedup. This is
    what the app actually reads at runtime; it should never touch a raw
    328-column export directly.
  - Frequency/hot/cold stats use numpy vectorized counting (bincount), not
    Python-level loops - required for this to stay fast at millions of rows.

KINO odds below are computed directly via combinatorics (hypergeometric:
20 drawn from 80), not copied from any published table - a figure pasted
from an AI assistant during development of this app turned out to be wrong
by 4x when checked this way, which is exactly why every figure here is
derived, not trusted.
"""

import os
from math import comb

import numpy as np
import pandas as pd

TOTAL_NUMBERS = 80
DRAWN_PER_ROUND = 20
TICKET_PRICE_EUR = 0.50  # per numbers.gr: "ελάχιστο ποντάρισμα 0,50€ ανά στήλη"


def odds_catch_k_of_n(n_picked: int, k_matched: int) -> float:
    """
    Exact odds (as 1-in-X) of matching exactly k of n numbers you picked,
    when 20 numbers are drawn from a field of 80. Verified against known
    published figures for n=4,5,10 during development; n=12/"catch all"
    matched independently-known real-world KINO 12 odds.
    """
    count = comb(DRAWN_PER_ROUND, k_matched) * comb(TOTAL_NUMBERS - DRAWN_PER_ROUND, n_picked - k_matched)
    total = comb(TOTAL_NUMBERS, n_picked)
    return total / count if count else float("inf")


def catch_all_odds_table(max_n=12):
    """odds of catching ALL n of n, for each KINO game size 1-12."""
    return [{"numbers_played": n, "odds_1_in": round(odds_catch_k_of_n(n, n))} for n in range(1, max_n + 1)]


# --------------------------------------------------------------------------
# Raw export -> compact Parquet (one-time preprocessing per file)
# --------------------------------------------------------------------------
def convert_raw_kino_xlsx(filepath: str, output_path: str) -> int:
    """
    Convert one raw Allwyn monthly KINO export (328 columns) into a compact
    Parquet file (draw_id, datetime, numbers, kb, odd_even, column). Returns
    the number of draws converted. Run this once per raw file you're given -
    never at app-startup time.
    """
    df = pd.read_excel(
        filepath, header=None, skiprows=3, usecols=list(range(26)),
        names=["draw_id", "date", "time"] + [f"n{i}" for i in range(1, 21)]
              + ["kb", "odd_even", "column"],
    )
    df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"], format="%d/%m/%Y %H:%M")
    num_cols = [f"n{i}" for i in range(1, 21)]
    numbers_arr = df[num_cols].values.astype(np.int16)
    numbers_arr.sort(axis=1)
    df["numbers"] = list(numbers_arr)

    compact = df[["draw_id", "datetime", "numbers", "kb", "odd_even", "column"]].copy()
    compact["draw_id"] = compact["draw_id"].astype(np.int64)
    compact["kb"] = pd.to_numeric(compact["kb"], errors="coerce").astype("Int16")
    compact = compact.sort_values("datetime").reset_index(drop=True)
    compact.to_parquet(output_path)
    return len(compact)


def load_all_kino_draws(data_dir: str = ".") -> pd.DataFrame:
    """Load and concatenate every *.parquet file found in data_dir (one per converted month)."""
    parts = []
    for fname in sorted(os.listdir(data_dir)):
        if fname.endswith(".parquet"):
            parts.append(pd.read_parquet(os.path.join(data_dir, fname)))
    if not parts:
        return pd.DataFrame(columns=["draw_id", "datetime", "numbers", "kb", "odd_even", "column"])
    df = pd.concat(parts, ignore_index=True)
    df = df.drop_duplicates(subset="draw_id").sort_values("datetime").reset_index(drop=True)
    return df


# --------------------------------------------------------------------------
# Vectorized statistics
# --------------------------------------------------------------------------
class KinoAnalyzer:
    """
    Statistics over loaded KINO draw history. All frequency computation is
    vectorized (numpy) rather than Python-level loops, since a full-history
    load could be millions of rows - a Python Counter loop over
    millions_of_draws x 20 numbers would be far too slow for an interactive
    app; np.bincount over the same data is effectively instant.
    """

    def __init__(self, all_draws: pd.DataFrame):
        self.all_draws = all_draws
        if len(all_draws):
            self._numbers_matrix = np.stack(all_draws["numbers"].values)  # (n_draws, 20)
        else:
            self._numbers_matrix = np.empty((0, 20), dtype=np.int16)

    def _frequency_array(self, matrix=None):
        """Returns a length-81 array (index 0 unused) of counts per number 1-80."""
        m = self._numbers_matrix if matrix is None else matrix
        if m.size == 0:
            return np.zeros(TOTAL_NUMBERS + 1, dtype=np.int64)
        return np.bincount(m.ravel(), minlength=TOTAL_NUMBERS + 1)

    def hot_numbers(self, top_n=10, last_n_draws=None):
        matrix = self._numbers_matrix[-last_n_draws:] if last_n_draws else self._numbers_matrix
        freq = self._frequency_array(matrix)[1:]  # drop index 0
        order = np.argsort(-freq)[:top_n]
        return [(int(n + 1), int(freq[n])) for n in order]

    def cold_numbers(self, top_n=10, last_n_draws=None):
        matrix = self._numbers_matrix[-last_n_draws:] if last_n_draws else self._numbers_matrix
        freq = self._frequency_array(matrix)[1:]
        order = np.argsort(freq)[:top_n]
        return [(int(n + 1), int(freq[n])) for n in order]

    def overdue_numbers(self, top_n=10):
        """Numbers ranked by how many draws since their last appearance."""
        total = len(self.all_draws)
        if total == 0:
            return []
        n_cols = self._numbers_matrix.shape[1]
        row_idx_repeated = np.repeat(np.arange(total), n_cols)
        flat_numbers = self._numbers_matrix.ravel().astype(np.int64)
        last_seen = np.full(TOTAL_NUMBERS + 1, -1, dtype=np.int64)
        np.maximum.at(last_seen, flat_numbers, row_idx_repeated)
        gaps = {n: total - 1 - int(last_seen[n]) for n in range(1, TOTAL_NUMBERS + 1)}
        return sorted(gaps.items(), key=lambda x: -x[1])[:top_n]

    def frequency_pct(self, last_n_draws=None):
        """Every number 1-80 with its count and % of draws it appeared in, over the window."""
        matrix = self._numbers_matrix[-last_n_draws:] if last_n_draws else self._numbers_matrix
        total = len(matrix)
        freq = self._frequency_array(matrix)[1:]
        return [
            {"number": n + 1, "count": int(freq[n]), "pct": round(100 * freq[n] / total, 2) if total else 0}
            for n in range(TOTAL_NUMBERS)
        ]

    def odd_even_tie_counts(self):
        """Counts from the 'odd_even' field Allwyn publishes per draw (Μ=odd, Ζ=even, ΙΣ=tie)."""
        return self.all_draws["odd_even"].value_counts().to_dict()

    def column_stats(self):
        """Counts from the 'column' (ΣΤΗΛΗ) field - which board column had the most numbers that draw."""
        return self.all_draws["column"].value_counts().to_dict()

    def repeat_counts(self, top_n=10):
        """
        'Επαναλήψεις' per Allwyn's stats page: how many times each number was
        drawn in two CONSECUTIVE draws (appeared, then appeared again
        immediately next draw). Vectorized: for consecutive draw pairs, find
        numbers present in both via a boolean membership matrix.
        """
        total = len(self.all_draws)
        if total < 2:
            return []
        # membership[i, n] = True if number n+1 appears in draw i
        membership = np.zeros((total, TOTAL_NUMBERS + 1), dtype=bool)
        rows = np.repeat(np.arange(total), self._numbers_matrix.shape[1])
        cols = self._numbers_matrix.ravel()
        membership[rows, cols] = True
        # a number "repeats" at draw i if it was present in draw i-1 AND draw i
        repeats = membership[:-1] & membership[1:]
        counts = repeats.sum(axis=0)
        result = [(n, int(counts[n])) for n in range(1, TOTAL_NUMBERS + 1)]
        return sorted(result, key=lambda x: -x[1])[:top_n]

    def multi_window_stats(self, numbers, windows=(500, 2000, None)):
        """
        For the given numbers, their count and hot/cold rank (1=hottest of 80)
        across several windows - same purpose as JokerAnalyzer's equivalent,
        rescaled: KINO draws ~288/day, so 500/2000 draws represent ~1.7 days
        and ~1 week, not the 100/1000-draw windows that make sense at
        Tzoker's 3-draws/week pace.
        """
        results = {n: {} for n in numbers}
        for w in windows:
            label = "all_time" if w is None else f"last_{w}"
            matrix = self._numbers_matrix[-w:] if w else self._numbers_matrix
            freq = self._frequency_array(matrix)[1:]
            n_draws = len(matrix)
            ranked = sorted(range(1, TOTAL_NUMBERS + 1), key=lambda i: -freq[i - 1])
            rank_map = {num: i + 1 for i, num in enumerate(ranked)}
            for n in numbers:
                results[n][label] = {
                    "count": int(freq[n - 1]),
                    "rank_of_80": rank_map.get(n),
                    "draws_in_window": n_draws,
                }
        return results

    def draws_per_day(self):
        return self.all_draws.groupby(self.all_draws["datetime"].dt.date).size()
