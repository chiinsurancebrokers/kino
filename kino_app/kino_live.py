"""
Live KINO draw fetching from OPAP's public draws API.

IMPORTANT - read before relying on this: the endpoint shape below is
independently confirmed by THREE separate open-source projects (a GitHub
repo collecting KINO stats, a Go client library, and a published PyPI
package `opap`), all agreeing exactly on the URL pattern and KINO's game ID
(1100). That's real, cross-validated precedent. What it is NOT: a live test
against today's API. This was built in a sandboxed environment with no
network access to api.opap.gr, so whether the endpoint still responds the
same way after OPAP's 2026 rebrand to Allwyn is unverified. Every function
here fails loudly and specifically (never silently returns bad data), and
the app is built to keep working from the existing manual-upload path if
this doesn't work - check the first real call's result before trusting it.

Endpoints used (game ID 1100 = KINO):
  GET /draws/v3.0/1100/active
      -> {"drawId": <int>}  - the currently in-progress (not yet completed) draw
  GET /draws/v3.0/1100/draw-id/{start}/{stop}?limit=180
      -> {"content": [{"drawId": ..., "winningNumbers": {"list": [20 ints]}, ...}]}
  GET /draws/v3.0/1100/last/{n}?limit=180
      -> [{"drawId": ..., "winningNumbers": {"list": [...]}, "drawTime": "..."?}, ...]
      (the published PyPI package skips the first item here on the theory
      that it's the active/incomplete draw, not a result - kept here for
      consistency, but this specific detail is the least-confirmed part of
      the whole module.)
"""

import numpy as np
import pandas as pd

BASE_URL = "https://api.opap.gr/draws/v3.0/1100"
REQUEST_TIMEOUT = 15


def get_active_draw_id():
    """Returns (draw_id, error)."""
    try:
        import requests
    except ImportError:
        return None, "The 'requests' package isn't installed."
    try:
        resp = requests.get(f"{BASE_URL}/active", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return int(resp.json()["drawId"]), None
    except Exception as e:
        return None, f"Couldn't fetch the active draw ID: {e}"


def _parse_draw_item(item):
    """Extract (draw_id, numbers, draw_time_or_None) from one API draw record,
    tolerating whatever subset of fields is actually present."""
    try:
        draw_id = int(item["drawId"])
        numbers = sorted(int(n) for n in item["winningNumbers"]["list"])
    except (KeyError, TypeError, ValueError):
        return None
    if len(numbers) != 20 or not all(1 <= n <= 80 for n in numbers):
        return None  # malformed - skip rather than propagate bad data
    draw_time = item.get("drawTime") or item.get("draw_time")
    return draw_id, numbers, draw_time


def get_draws_by_id_range(start_id, stop_id):
    """Returns (list of (draw_id, numbers, draw_time_or_None), error)."""
    try:
        import requests
    except ImportError:
        return None, "The 'requests' package isn't installed."

    url = f"{BASE_URL}/draw-id/{start_id}/{stop_id}"
    try:
        resp = requests.get(url, params={"limit": "180"}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        content = resp.json().get("content", [])
    except Exception as e:
        return None, f"Couldn't fetch draws {start_id}-{stop_id}: {e}"

    parsed = [_parse_draw_item(item) for item in content]
    return [p for p in parsed if p is not None], None


def fetch_new_draws(known_max_draw_id, known_max_datetime, max_fetch=2000):
    """
    Fetch every completed draw newer than known_max_draw_id, deriving each
    one's datetime from the 5-minute draw cadence when the API doesn't
    expose an explicit timestamp (rather than stamping everything with
    "now", which would corrupt chronological ordering for every stats
    function that depends on it).

    Returns (DataFrame matching kino_core's schema, error). DataFrame is
    empty (not None) if there's simply nothing new yet - that's success,
    not failure.
    """
    active_id, error = get_active_draw_id()
    if error:
        return None, error

    stop_id = active_id - 1  # the active draw hasn't completed yet
    start_id = known_max_draw_id + 1
    if start_id > stop_id:
        return pd.DataFrame(columns=["draw_id", "datetime", "numbers", "kb", "odd_even", "column"]), None

    if stop_id - start_id + 1 > max_fetch:
        stop_id = start_id + max_fetch - 1  # cap a single refresh - fetch the rest next time

    draws, error = get_draws_by_id_range(start_id, stop_id)
    if error:
        return None, error
    if not draws:
        return None, (
            f"API returned no draws for range {start_id}-{stop_id}, but the active "
            f"draw ID ({active_id}) suggests there should be some. Possible endpoint "
            f"or response-shape change since this was last verified."
        )

    rows = []
    for draw_id, numbers, draw_time in draws:
        if draw_time:
            dt = pd.to_datetime(draw_time)
        else:
            # ~5 minutes per draw ID step - reasonable given confirmed cadence
            dt = known_max_datetime + pd.Timedelta(minutes=5 * (draw_id - known_max_draw_id))
        rows.append({
            "draw_id": draw_id, "datetime": dt, "numbers": numbers,
            "kb": None, "odd_even": None, "column": None,
        })

    df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)
    df["draw_id"] = df["draw_id"].astype(np.int64)
    return df, None
