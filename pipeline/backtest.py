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

# How many completed seasons to pool. One season leaves the opener sample too
# small to separate a real half-point edge from nothing at all.
SEASONS_TO_GRADE = 3

# Bumped whenever the method changes. A cached result produced by different code
# is not a saving, it is a stale answer wearing a fresh timestamp, and it already
# cost one wasted run that looked like a real result.
METHOD_VERSION = 4


def _bucket_label(low, high):
    return "%d+" % low if high >= 99 else "%d to %d" % (low, high)


def load_cached(target_year):
    try:
        if time.time() - os.path.getmtime(PATH) > MAX_AGE_DAYS * 86400:
            return None
        with open(PATH) as fh:
            payload = json.load(fh)
        if payload.get("method_version") != METHOD_VERSION:
            return None
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
        def pick(*keys):
            for key in keys:
                value = chosen.get(key)
                if value is not None:
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return None
            return None

        out[(home, away)] = {
            "spread": pick("spread"),
            "total": pick("overUnder", "over_under"),
            "spread_open": pick("spreadOpen", "spread_open"),
            "total_open": pick("overUnderOpen", "over_under_open"),
        }
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


def run(target_year, seasons=SEASONS_TO_GRADE, force=False):
    """Walk forward through several completed seasons and pool the results.

    Pooling matters more than it sounds. One season of openers gave 553 decided
    bets and a standard error near 2.1 points, which is wide enough that a real
    edge of half a point and no edge at all look identical. Three seasons roughly
    triples the sample and cuts that error bar to around 1.2 points, which is the
    difference between a result you can act on and one you cannot.
    """
    if not force:
        cached = load_cached(target_year)
        if cached and cached.get("seasons_graded") == seasons:
            return cached

    years = list(range(target_year - seasons + 1, target_year + 1))
    per_season = []
    pooled = {"spread_close": _fresh(), "total_close": _fresh(),
              "spread_open": _fresh(), "total_open": _fresh()}
    margin_residuals, total_residuals = [], []
    graded = 0
    weeks_used = 0
    opener_rows = 0
    years_used = []

    for year in years:
        try:
            outcome = _grade_season(year)
        except Exception as exc:  # noqa: BLE001
            per_season.append({"year": year, "available": False, "note": str(exc)})
            continue
        if not outcome:
            per_season.append({"year": year, "available": False,
                               "note": "no gradable games"})
            continue

        years_used.append(year)
        graded += outcome["graded"]
        weeks_used += outcome["weeks_used"]
        opener_rows += outcome["opener_rows"]
        margin_residuals.extend(outcome["margin_residuals"])
        total_residuals.extend(outcome["total_residuals"])
        for book in pooled:
            for key, row in outcome["books"][book].items():
                pooled[book][key]["wins"] += row["wins"]
                pooled[book][key]["losses"] += row["losses"]
                pooled[book][key]["pushes"] += row["pushes"]

        per_season.append({
            "year": year,
            "available": True,
            "graded": outcome["graded"],
            "spread_close": _overall(outcome["books"]["spread_close"]),
            "spread_open": _overall(outcome["books"]["spread_open"]),
        })

    if not years_used:
        return {"available": False,
                "note": "No season between %d and %d produced gradable games."
                        % (years[0], years[-1])}

    payload = {
        "available": graded > 0,
        "method": "walk-forward, pooled across seasons",
        "method_version": METHOD_VERSION,
        "target_year": target_year,
        "seasons_graded": seasons,
        "years": years_used,
        "per_season": per_season,
        "graded": graded,
        "weeks_used": weeks_used,
        "first_week": FIRST_GRADED_WEEK,
        "spread": _summarise(pooled["spread_close"]),
        "total": _summarise(pooled["total_close"]),
        "spread_open": _summarise(pooled["spread_open"]),
        "total_open": _summarise(pooled["total_open"]),
        "openers_available": opener_rows > 100,
        "opener_rows": opener_rows,
        "break_even": BREAK_EVEN,
        "oos_margin_sigma": round(_sigma(margin_residuals), 2) if margin_residuals else None,
        "oos_total_sigma": round(_sigma(total_residuals), 2) if total_residuals else None,
        "note": ("Graded week by week across %s. Each week is predicted using "
                 "only games played earlier in that same season, so no future "
                 "result and no cross-season roster assumption enters the "
                 "prediction." % ", ".join(str(y) for y in years_used)),
    }
    _save(payload)
    return payload


def _fresh():
    return {_bucket_label(a, b): {"wins": 0, "losses": 0, "pushes": 0}
            for a, b in BUCKETS}


def _overall(buckets):
    wins = sum(r["wins"] for r in buckets.values())
    losses = sum(r["losses"] for r in buckets.values())
    decided = wins + losses
    if not decided:
        return None
    rate = wins / decided
    stderr = math.sqrt(rate * (1.0 - rate) / decided)
    return {"wins": wins, "losses": losses, "decided": decided,
            "rate": round(rate, 4), "stderr": round(stderr * 100, 2),
            "edge_vs_breakeven": round((rate - BREAK_EVEN) * 100, 2),
            "significant": (rate - BREAK_EVEN) > 2.0 * stderr}


def _grade_season(target_year):
    games = _normalise_games(cfbd_client.calibration_games(target_year))
    if len(games) < 200:
        return None

    lines = _closing_lines(target_year)
    weeks = sorted({g["week"] for g in games})

    books = {
        "spread_close": _fresh(), "total_close": _fresh(),
        "spread_open": _fresh(), "total_open": _fresh(),
    }
    margin_residuals, total_residuals = [], []
    graded = 0
    weeks_used = 0
    opener_rows = 0

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

            fair_spread = -predicted
            for key, bucket in (("spread", "spread_close"),
                                ("spread_open", "spread_open")):
                posted = market.get(key)
                if posted is None:
                    continue
                if key == "spread_open":
                    opener_rows += 1
                gap = abs(fair_spread - posted)
                result = (game["margin"] + posted) if fair_spread < posted \
                    else -(game["margin"] + posted)
                _record(books[bucket], gap, result)

            if offence is not None and game["home"] in offence and game["away"] in offence:
                predicted_total = (offence[game["home"]] + defence[game["away"]]
                                   + offence[game["away"]] + defence[game["home"]]
                                   + base)
                total_residuals.append(game["total"] - predicted_total)
                for key, bucket in (("total", "total_close"),
                                    ("total_open", "total_open")):
                    posted = market.get(key)
                    if posted is None:
                        continue
                    gap = abs(predicted_total - posted)
                    result = (game["total"] - posted) if predicted_total > posted \
                        else (posted - game["total"])
                    _record(books[bucket], gap, result)

    return {
        "graded": graded,
        "weeks_used": weeks_used,
        "opener_rows": opener_rows,
        "books": books,
        "margin_residuals": margin_residuals,
        "total_residuals": total_residuals,
    }


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

        # Standard error of the hit rate. Without this, a bucket sitting three
        # points above break-even on 120 games looks like evidence when it is
        # one coin-flip run. Requiring two standard errors is what stops the
        # confidence score from treating noise as measurement, which is exactly
        # what it was doing.
        stderr = None
        significant = False
        if rate is not None and decided >= MIN_SAMPLE:
            stderr = math.sqrt(rate * (1.0 - rate) / decided)
            significant = (rate - BREAK_EVEN) > 2.0 * stderr

        out.append({
            "bucket": key, "low": low, "high": high,
            "wins": row["wins"], "losses": row["losses"], "pushes": row["pushes"],
            "decided": decided,
            "rate": round(rate, 4) if rate is not None else None,
            "stderr": round(stderr * 100, 2) if stderr is not None else None,
            "significant": significant,
            "edge_vs_breakeven": round((rate - BREAK_EVEN) * 100, 2)
                                 if rate is not None else None,
        })
    return out


def rate_for_gap(report, market, gap):
    """The measured record for a gap size, or None when the sample is too thin.

    Returns the row whether or not it is statistically significant. The caller
    decides what to do with an insignificant result, because "measured and
    indistinguishable from a coin flip" is itself information worth showing.
    """
    if not report or not report.get("available"):
        return None
    rows = report.get("spread" if market != "total" else "total") or []
    for row in rows:
        if row["low"] <= gap < row["high"]:
            if row["decided"] >= MIN_SAMPLE and row["rate"] is not None:
                return row
            return None
    return None
