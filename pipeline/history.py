"""Line movement history and automatic closing line capture.

Two jobs. The first is movement: each build snapshots every posted number, so
the board can tell whether FanDuel has drifted toward the model or away from it
since the line was first seen.

The second matters more. Once a game kicks off it drops off the board, and with
it goes any chance of recording where the line closed. So before that happens,
the last snapshot is frozen into a separate store that persists. That is what
makes closing line value automatic rather than something you type in by hand,
and hand-entry is the kind of thing everyone abandons by October.

CLV is the best-evidenced measurement in betting and the only one that will
give a usable read inside a single season. It is worth the plumbing.
"""

import json
import os
import time

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
LIVE_PATH = os.path.join(CACHE_DIR, "line_history.json")
CLOSE_PATH = os.path.join(CACHE_DIR, "closing_lines.json")

MAX_SNAPSHOTS = 40
LIVE_MAX_AGE = 21 * 24 * 3600
CLOSE_MAX_AGE = 200 * 24 * 3600

# A game is treated as closed once kickoff has passed by this margin, which
# gives the last scheduled build time to land before the ball is in the air.
CLOSE_AFTER_KICKOFF = 900

# A line first seen inside this window is an opener rather than a settled
# number. Flagged, not scored: whether early is better is the thing we are
# trying to find out, not something to assume in the weights.
OPENER_WINDOW_HOURS = 36

# A captured number this far before kickoff is not a closing line. It gets
# stored, flagged, and kept out of closing line value averages.
STALE_AFTER_HOURS = 12


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(path, payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))


def load():
    return _load(LIVE_PATH)


def load_closings():
    return _load(CLOSE_PATH)


def record(history, game_id, label, kickoff_ts, posted):
    """Add a snapshot, but only when a number actually changed."""
    entry = history.setdefault(game_id, {"label": label, "kickoff": kickoff_ts,
                                         "snaps": []})
    entry["label"] = label
    entry["kickoff"] = kickoff_ts

    snap = {
        "ts": time.time(),
        "spread": posted.get("spread"),
        "total": posted.get("total"),
        "home_ml": posted.get("home_ml"),
        "away_ml": posted.get("away_ml"),
    }
    snaps = entry["snaps"]
    if snaps:
        last = snaps[-1]
        if all(last.get(k) == snap.get(k)
               for k in ("spread", "total", "home_ml", "away_ml")):
            return
    snaps.append(snap)
    entry["snaps"] = snaps[-MAX_SNAPSHOTS:]


def movement(history, game_id, spread, total):
    """Distance travelled since the number was first seen."""
    entry = history.get(game_id) or {}
    snaps = entry.get("snaps") or []
    if not snaps:
        return {"seen_for_hours": 0.0, "spread_delta": None, "total_delta": None,
                "snapshots": 0, "opener": True}

    first = snaps[0]
    hours = (time.time() - first.get("ts", time.time())) / 3600.0

    def delta(key, current):
        if current is None or first.get(key) is None:
            return None
        return round(float(current) - float(first[key]), 2)

    return {
        "seen_for_hours": round(hours, 1),
        "spread_delta": delta("spread", spread),
        "total_delta": delta("total", total),
        "snapshots": len(snaps),
        "opener": hours <= OPENER_WINDOW_HOURS,
    }


def finalise(history, closings, live_ids):
    """Freeze the last snapshot of any game that has kicked off.

    A game qualifies either because its kickoff time has passed or because it
    has dropped off the board entirely, which is what happens once it starts.
    Both paths lead to the same place: capture the number before it is gone.
    """
    now = time.time()
    captured = 0

    for game_id in list(history.keys()):
        entry = history[game_id]
        snaps = entry.get("snaps") or []
        if not snaps:
            history.pop(game_id, None)
            continue

        kickoff = entry.get("kickoff")
        kicked_off = kickoff is not None and now > (kickoff + CLOSE_AFTER_KICKOFF)
        dropped = game_id not in live_ids

        if not (kicked_off or dropped):
            continue

        last = snaps[-1]
        if game_id not in closings:
            # How close to kickoff the last observed number actually was. With
            # a manual build schedule this can be days, and a number from three
            # days out is not a closing line. Recording the distance means the
            # bet log can refuse to average those in rather than reporting a
            # confident CLV figure built on a stale price.
            lag_hours = None
            if kickoff is not None:
                lag_hours = round((kickoff - last.get("ts", now)) / 3600.0, 1)

            closings[game_id] = {
                "label": entry.get("label"),
                "kickoff": kickoff,
                "spread": last.get("spread"),
                "total": last.get("total"),
                "home_ml": last.get("home_ml"),
                "away_ml": last.get("away_ml"),
                "captured_at": now,
                "snapshots": len(snaps),
                "hours_before_kickoff": lag_hours,
                "stale": lag_hours is None or lag_hours > STALE_AFTER_HOURS,
            }
            captured += 1
        history.pop(game_id, None)

    return captured


def save(history, closings):
    now = time.time()

    live = {}
    for game_id, entry in history.items():
        snaps = [s for s in (entry.get("snaps") or [])
                 if s.get("ts", 0) > now - LIVE_MAX_AGE]
        if snaps:
            entry["snaps"] = snaps
            live[game_id] = entry
    _save(LIVE_PATH, live)

    kept = {k: v for k, v in closings.items()
            if v.get("captured_at", 0) > now - CLOSE_MAX_AGE}
    _save(CLOSE_PATH, kept)
    return len(live), len(kept)
