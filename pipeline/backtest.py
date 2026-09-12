"""Walk-forward backtest.

Why this replaced the previous version: the old backtest used season N-1 ratings
to predict season N games. That avoided lookahead, but at the cost of measuring
a model nobody runs. In the transfer portal era a year-old rating is a badly
degraded description of a team, so the hit rates it produced described a worse
model than the live one. Importing that model's gap-to-hit-rate curve onto a
better model is not conservatism, it is measuring the wrong thing.

This version walks forward through a completed season. To grade week W, team
ratings are fitted using only games from weeks 1 to W-1 of that same season.
Nothing from week W or later touches the prediction, so there is no lookahead,
and the ratings describe the roster actually on the field. Roster turnover
between seasons stops mattering, because no cross-season inference is made.

Ratings here are solved from margins directly, ridge-regularised because early
season schedules are barely connected. That is a simpler object than SP+, which
is opponent-adjusted, efficiency-based and filters garbage time, so this stays a
proxy and a mildly pessimistic one. But it is built the same structural way as
the live model and fed the same kind of information.

The second output matters as much as the first. Residuals here are genuinely out
of sample, so they give an honest standard error. An in-sample fit understates
it, and an understated error inflates every cover probability on the board.
"""

import json
import math
import os
import time

import numpy as np

from . import cfbd_client

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
PATH = os.path.join(CACHE_DIR, "backtest.json")
MAX_AGE_DAYS = 30

BREAK_EVEN = 0.5238

# Weeks before this have too few games to rate anyone.
FIRST_GRADED_WEEK = 5

# Ridge penalty, keeping ratings finite while the schedule graph is sparse.
RIDGE = 6.0

BUCKETS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 6), (6, 9), (9, 99)]
MIN_SAMPLE = 60


def _bucket_label(low, high):
    return "%d+" % low if high >= 99 else "%d to %d" % (low, high)


def load_cached(target_year):
    try:
        if time.time() - os.path.getmtime(PATH) > MAX_AGE_DAYS * 86400:
            return None
        with open(PATH) as fh:
            payload = json.load(fh)
        return payload if payload.get("target_year") == target_year else None
    except (OSError, ValueError):
        return None


def _save(payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(PATH, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))


# ---------------------------------------------------------------- ratings


def fit_ratings(games):
    """Ridge-regularised margin ratings from completed games.

    Returns (ratings by team, home edge). The penalty matters in September,
    when many teams have played twice and the schedule graph barely connects.
    """
    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    if len(teams) < 8 or len(games) < len(teams):
        return None, None

    index = {team: i for i, team in enumerate(teams)}
    n = len(teams)

    rows, targets = [], []
    for game in games:
        row = [0.0] * (n + 1)
        row[index[game["home"]]] = 1.0
        row[index[game["away"]]] = -1.0
        row[n] = 0.0 if game["neutral"] else 1.0
        rows.append(row)
        targets.append(game["margin"])

    design = np.array(rows, dtype=float)
    target = np.array(targets, dtype=float)

    # Penalise the ratings but not the home edge, and pin the mean rating to
    # zero so the system has a unique solution.
    penalty = np.zeros((n, n + 1))
    for i in range(n):
        penalty[i, i] = math.sqrt(RIDGE)
    anchor = np.zeros((1, n + 1))
    anchor[0, :n] = 1.0

    stacked = np.vstack([design, penalty, anchor])
    padded = np.concatenate([target, np.zeros(n), np.zeros(1)])

    solution, *_ = np.linalg.lstsq(stacked, padded, rcond=None)
    ratings = {team: float(solution[index[team]]) for team in teams}
    return ratings, float(solution[n])


def fit_totals(games):
    """Per-team scoring and concession levels, same regularised approach."""
    teams = sorted({g["home"] for g in games} | {g["away"] for g in games})
    if len(teams) < 8 or len(games) < len(teams):
        return None, None, None

    index = {team: i for i, team in enumerate(teams)}
    n = len(teams)
    rows, targets = [], []

    for game in games:
        row = [0.0] * (2 * n + 1)
        row[index[game["home"]]] += 1.0
        row[n + index[game["away"]]] += 1.0
        row[index[game["away"]]] += 1.0
        row[n + index[game["home"]]] += 1.0
        row[2 * n] = 1.0
        rows.append(row)
        targets.append(game["total"])

    design = np.array(rows, dtype=float)
    target = np.array(targets, dtype=float)
    penalty = np.zeros((2 * n, 2 * n + 1))
    for i in range(2 * n):
        penalty[i, i] = math.sqrt(RIDGE)

    stacked = np.vstack([design, penalty])
    padded = np.concatenate([target, np.zeros(2 * n)])
    solution, *_ = np.linalg.lstsq(stacked, padded, rcond=None)

    offence = {team: float(solution[index[team]]) for team in teams}
    defence = {team: float(solution[n + index[team]]) for team in teams}
    return offence, defence, float(solution[2 * n])


# ---------------------------------------------------------------- lines


def _closing_lines(year):
    rows = cfbd_client.historical_lines(year)
    out = {}
    preferred = ["DraftKings", "consensus", "ESPN Bet", "Bovada"]
    for row in rows or []:
        home = row.get("homeTeam") or row.get("home_team")
        away = row.get("awayTeam") or row.get("away_team")
        if not home or not away:
            continue
        lines = row.get("lines") or []
        chosen = None
        for provider in preferred:
            for line in lines:
                if (line.get("provider") or "").lower() == provider.lower():
                    chosen = line
                    break
            if chosen:
                break
        if chosen is None and lines:
            chosen = lines[0]
        if chosen is None:
            continue
        try:
            spread = chosen.get("spread")
            total = chosen.get("overUnder", chosen.get("over_under"))
            out[(home, away)] = {
                "spread": float(spread) if spread is not None else None,
                "total": float(total) if total is not None else None,
            }
        except (TypeError, ValueError):
            continue
    return out


def _normalise_games(raw):
    out = []
    for game in raw or []:
        home_points = game.get("homePoints", game.get("home_points"))
        away_points = game.get("awayPoints", game.get("away_points"))
        home = game.get("homeTeam") or game.get("home_team")
        away = game.get("awayTeam") or game.get("away_team")
        week = game.get("week")
        if None in (home_points, away_points, home, away, week):
            continue
        out.append({
            "week": int(week),
            "home": home,
            "away": away,
            "neutral": bool(game.get("neutralSite") or game.get("neutral_site")),
            "margin": float(home_points) - float(away_points),
            "total": float(home_points) + float(away_points),
        })
    return out


# ---------------------------------------------------------------- run


def run(target_year, force=False):
    if not force:
        cached = load_cached(target_year)
        if cached:
            return cached

    games = _normalise_games(cfbd_client.calibration_games(target_year))
    if len(games) < 200:
        return {"available": False,
                "note": "Only %d completed games found for %d."
                        % (len(games), target_year)}

    lines = _closing_lines(target_year)
    weeks = sorted({g["week"] for g in games})

    spread_buckets = {_bucket_label(a, b): {"wins": 0, "losses": 0, "pushes": 0}
                      for a, b in BUCKETS}
    total_buckets = {_bucket_label(a, b): {"wins": 0, "losses": 0, "pushes": 0}
                     for a, b in BUCKETS}
    margin_residuals, total_residuals = [], []
    graded = 0
    weeks_used = 0

    for week in weeks:
        if week < FIRST_GRADED_WEEK:
            continue
        prior = [g for g in games if g["week"] < week]
        current = [g for g in games if g["week"] == week]
        if len(prior) < 150 or not current:
            continue

        ratings, hfa = fit_ratings(prior)
        if ratings is None:
            continue
        offence, defence, base = fit_totals(prior)
        weeks_used += 1

        for game in current:
            if game["home"] not in ratings or game["away"] not in ratings:
                continue
            market = lines.get((game["home"], game["away"]))
            if not market:
                continue
            graded += 1

            predicted = (ratings[game["home"]] - ratings[game["away"]]
                         + (0.0 if game["neutral"] else hfa))
            margin_residuals.append(game["margin"] - predicted)

            if market.get("spread") is not None:
                posted = market["spread"]
                fair_spread = -predicted
                gap = abs(fair_spread - posted)
                result = (game["margin"] + posted) if fair_spread < posted \
                    else -(game["margin"] + posted)
                _record(spread_buckets, gap, result)

            if offence is not None and game["home"] in offence and game["away"] in offence:
                predicted_total = (offence[game["home"]] + defence[game["away"]]
                                   + offence[game["away"]] + defence[game["home"]]
                                   + base)
                total_residuals.append(game["total"] - predicted_total)
                if market.get("total") is not None:
                    posted = market["total"]
                    gap = abs(predicted_total - posted)
                    result = (game["total"] - posted) if predicted_total > posted \
                        else (posted - game["total"])
                    _record(total_buckets, gap, result)

    payload = {
        "available": graded > 0,
        "method": "walk-forward",
        "target_year": target_year,
        "graded": graded,
        "weeks_used": weeks_used,
        "first_week": FIRST_GRADED_WEEK,
        "spread": _summarise(spread_buckets),
        "total": _summarise(total_buckets),
        "break_even": BREAK_EVEN,
        "oos_margin_sigma": round(_sigma(margin_residuals), 2) if margin_residuals else None,
        "oos_total_sigma": round(_sigma(total_residuals), 2) if total_residuals else None,
        "note": ("Graded week by week through %d. Each week is predicted using "
                 "only games played earlier in that same season, so no future "
                 "result and no cross-season roster assumption enters the "
                 "prediction." % target_year),
    }
    if payload["available"]:
        _save(payload)
    return payload


def _sigma(residuals):
    array = np.array(residuals, dtype=float)
    return float(np.sqrt(np.mean(array ** 2)))


def _record(buckets, gap, result):
    for low, high in BUCKETS:
        if low <= gap < high:
            key = _bucket_label(low, high)
            if abs(result) < 1e-9:
                buckets[key]["pushes"] += 1
            elif result > 0:
                buckets[key]["wins"] += 1
            else:
                buckets[key]["losses"] += 1
            return


def _summarise(buckets):
    out = []
    for low, high in BUCKETS:
        key = _bucket_label(low, high)
        row = buckets[key]
        decided = row["wins"] + row["losses"]
        rate = (row["wins"] / decided) if decided else None
        out.append({
            "bucket": key, "low": low, "high": high,
            "wins": row["wins"], "losses": row["losses"], "pushes": row["pushes"],
            "decided": decided,
            "rate": round(rate, 4) if rate is not None else None,
            "edge_vs_breakeven": round((rate - BREAK_EVEN) * 100, 2)
                                 if rate is not None else None,
        })
    return out


def rate_for_gap(report, market, gap):
    if not report or not report.get("available"):
        return None
    rows = report.get("spread" if market != "total" else "total") or []
    for row in rows:
        if row["low"] <= gap < row["high"]:
            if row["decided"] >= MIN_SAMPLE and row["rate"] is not None:
                return row
            return None
    return None
