"""Build the board.

Run: python -m pipeline.build

Pulls CFBD, calibrates the model on completed games, pulls the FanDuel board,
scores every game on the slate, and writes docs/index.html as a self-contained
file with the data baked in. No keys end up in the output.
"""

import datetime as dt
import json
import os
import sys
import traceback

from . import cfbd_client, config, names, odds_client, ratings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs")
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")

INJURY_LINKS = [
    {"label": "CBS Sports injury report",
     "url": "https://www.cbssports.com/college-football/injuries/"},
    {"label": "Covers NCAAF injuries",
     "url": "https://www.covers.com/sport/football/ncaaf/injuries"},
    {"label": "CFBDepth depth charts",
     "url": "https://www.cfbdepth.com/injury-report/"},
]


def log(message):
    print(message, flush=True)


# ---------------------------------------------------------------- slate


def on_slate(home_row, away_row, tv_outlet, has_full_market):
    """P4 plus Notre Dame always. G5 only when the game is televised."""
    confs = {home_row.get("conference"), away_row.get("conference")}
    schools = {home_row.get("school"), away_row.get("school")}

    if confs & config.POWER_4 or schools & config.ALWAYS_INCLUDE:
        return True, "power"

    if confs & config.GROUP_OF_5:
        if tv_outlet and _is_televised(tv_outlet):
            return True, "g5-tv"
        # Fall back to market existence when the media feed has no entry yet.
        if tv_outlet is None and has_full_market:
            return True, "g5-market"
    return False, None


def _is_televised(outlet):
    outlet = (outlet or "").strip()
    if outlet in config.TELEVISED_OUTLETS:
        return True
    return any(known.lower() in outlet.lower() for known in config.TELEVISED_OUTLETS)


# ---------------------------------------------------------------- calibration


def gather_calibration(team_tables, game_sets):
    """Build fitting samples from completed games across the given seasons."""
    samples = []
    for table, games in zip(team_tables, game_sets):
        for game in games:
            home_points = game.get("homePoints", game.get("home_points"))
            away_points = game.get("awayPoints", game.get("away_points"))
            if home_points is None or away_points is None:
                continue
            home = table.get(game.get("homeTeam") or game.get("home_team"))
            away = table.get(game.get("awayTeam") or game.get("away_team"))
            if not home or not away:
                continue
            neutral = bool(game.get("neutralSite") or game.get("neutral_site"))
            samples.append({
                "margin_row": ratings.margin_row(home, away, neutral),
                "total_row": ratings.total_row(home, away),
                "margin": float(home_points) - float(away_points),
                "total": float(home_points) + float(away_points),
            })
    return samples


# ---------------------------------------------------------------- scoring


def score_game(home_row, away_row, posted, neutral, model):
    fair_margin = ratings.predict(model["margin_coeffs"],
                                  ratings.margin_row(home_row, away_row, neutral))
    fair_total = ratings.predict(model["total_coeffs"],
                                 ratings.total_row(home_row, away_row))

    fair_spread = -fair_margin if fair_margin is not None else None

    edges = []
    review = []
    margin_sigma = model["margin_sigma"]
    total_sigma = model["total_sigma"]

    spread_ok = True
    total_ok = True

    if posted.get("spread") is not None and fair_spread is not None:
        if abs(float(fair_spread) - float(posted["spread"])) > config.MAX_SPREAD_DISAGREEMENT:
            spread_ok = False
            review.append("spread disagrees by %.1f pts, past the sanity ceiling"
                          % abs(float(fair_spread) - float(posted["spread"])))
    if posted.get("total") is not None and fair_total is not None:
        if abs(float(fair_total) - float(posted["total"])) > config.MAX_TOTAL_DISAGREEMENT:
            total_ok = False
            review.append("total disagrees by %.1f pts, past the sanity ceiling"
                          % abs(float(fair_total) - float(posted["total"])))

    # ---- spread
    line = posted.get("spread") if spread_ok else None
    if line is not None and fair_margin is not None:
        gap = abs(float(fair_spread) - float(line))
        home_p, away_p, push = ratings.cover_probability(fair_margin, line, margin_sigma)
        price = posted.get("spread_price") or -110
        # Our number says take whichever side the line is wrong against.
        if float(fair_spread) < float(line):
            side, team, prob = "home", home_row["school"], home_p
            display = _fmt_line(line)
        else:
            side, team, prob = "away", away_row["school"], away_p
            display = _fmt_line(-float(line))
        ev = ratings.expected_value(prob, push, price)
        if gap >= config.EDGE_FLAG:
            edges.append({
                "market": "spread", "side": side, "team": team,
                "line": display, "price": price,
                "gap": round(gap, 2), "prob": prob, "push": push, "ev": ev,
                "strict": gap >= config.EDGE_STRICT,
                "keys": ratings.key_number_crossings(fair_spread, line),
                "fair": round(float(fair_spread), 2),
            })

    # ---- total
    line = posted.get("total") if total_ok else None
    if line is not None and fair_total is not None:
        gap = abs(float(fair_total) - float(line))
        over_p, under_p, push = ratings.over_probability(fair_total, line, total_sigma)
        if float(fair_total) > float(line):
            side, prob, price = "over", over_p, posted.get("over_price") or -110
            display = "o%s" % _fmt_number(line)
        else:
            side, prob, price = "under", under_p, posted.get("under_price") or -110
            display = "u%s" % _fmt_number(line)
        ev = ratings.expected_value(prob, push, price)
        if gap >= config.EDGE_FLAG:
            edges.append({
                "market": "total", "side": side, "team": None,
                "line": display, "price": price,
                "gap": round(gap, 2), "prob": prob, "push": push, "ev": ev,
                "strict": gap >= config.EDGE_STRICT,
                "keys": [],
                "fair": round(float(fair_total), 2),
            })

    # ---- moneyline, derived from the same margin distribution
    home_ml = posted.get("home_ml")
    away_ml = posted.get("away_ml")
    shape_ok = (
        spread_ok
        and posted.get("spread") is not None
        and fair_spread is not None
        and abs(float(fair_spread) - float(posted["spread"])) <= config.ML_SHAPE_TOLERANCE
    )
    if shape_ok and home_ml is not None and away_ml is not None and fair_margin is not None:
        model_home = ratings.phi(fair_margin / margin_sigma) if margin_sigma else None
        fair_home, fair_away = odds_client.devig_pair(home_ml, away_ml)

        # FanDuel's own spread and moneyline should quote the same game. When
        # they do not, one of the two is stale in the feed and the disagreement
        # is a plumbing artefact rather than an edge.
        if fair_home is not None:
            quoted = ratings.implied_margin(fair_home, margin_sigma)
            posted_margin = -float(posted["spread"])
            if quoted is None or abs(quoted - posted_margin) > config.ML_SHAPE_TOLERANCE:
                review.append("moneyline and spread disagree by %.1f pts, price looks stale"
                              % abs((quoted or 0) - posted_margin))
                fair_home = None

        if model_home is not None and fair_home is not None:
            for side, team, prob, market_prob, price in (
                ("home", home_row["school"], model_home, fair_home, home_ml),
                ("away", away_row["school"], 1.0 - model_home, fair_away, away_ml),
            ):
                ev = ratings.expected_value(prob, 0.0, price)
                if (ev is not None and ev > 0
                        and prob - market_prob > config.ML_MIN_PROB_EDGE):
                    edges.append({
                        "market": "moneyline", "side": side, "team": team,
                        "line": _fmt_price(price), "price": price,
                        "gap": round((prob - market_prob) * 100, 1),
                        "prob": prob, "push": 0.0, "ev": ev,
                        "strict": (prob - market_prob) >= 0.05,
                        "keys": [],
                        "fair": odds_client.probability_to_american(prob),
                    })

    return {
        "fair_spread": round(float(fair_spread), 2) if fair_spread is not None else None,
        "fair_total": round(float(fair_total), 2) if fair_total is not None else None,
        "fair_margin": round(float(fair_margin), 2) if fair_margin is not None else None,
        "edges": edges,
        "review": review,
    }


def _fmt_line(value):
    value = float(value)
    return ("+%s" if value > 0 else "%s") % _fmt_number(value)


def _fmt_number(value):
    value = float(value)
    return str(int(value)) if abs(value - int(value)) < 1e-6 else ("%.1f" % value)


def _fmt_price(value):
    value = int(value)
    return ("+%d" % value) if value > 0 else str(value)


# ---------------------------------------------------------------- main


def main():
    log("CFB edge board, season %s" % config.SEASON)

    log("Pulling CFBD reference data")
    teams = cfbd_client.teams(config.SEASON)
    sp_now = cfbd_client.sp_ratings(config.SEASON)
    advanced_now = cfbd_client.season_advanced(config.SEASON)
    returning = cfbd_client.returning_production(config.SEASON)
    games_now = cfbd_client.games(config.SEASON)

    try:
        media_rows = cfbd_client.media(config.SEASON)
    except cfbd_client.CFBDError as exc:
        log("  media feed unavailable (%s), falling back to market filter" % exc)
        media_rows = []

    table_now = ratings.build_team_table(teams, sp_now, advanced_now, returning)

    log("Pulling %s for calibration" % config.CALIBRATION_SEASON)
    sp_prev = cfbd_client.calibration_sp(config.CALIBRATION_SEASON)
    games_prev = cfbd_client.calibration_games(config.CALIBRATION_SEASON)
    table_prev = ratings.build_team_table(teams, sp_prev, advanced_now, returning)

    log("Calibrating")
    samples = gather_calibration([table_prev, table_now], [games_prev, games_now])
    model = ratings.calibrate(samples)

    if model["margin_coeffs"] is None:
        raise SystemExit(
            "Not enough completed games to calibrate. This only happens before "
            "week 2 of a season with no cached prior year."
        )

    hfa = float(model["margin_coeffs"][0])
    log("  margin: n=%d, sigma=%.2f pts, home edge=%.2f pts"
        % (model["margin_n"], model["margin_sigma"], hfa))
    if model["total_coeffs"] is not None:
        log("  total:  n=%d, sigma=%.2f pts" % (model["total_n"], model["total_sigma"]))

    log("Pulling the FanDuel board")
    board = odds_client.fetch_board()
    log("  %d events, %s credits left of the monthly 500"
        % (len(board), odds_client.quota.get("remaining")))

    index = names.build_index(teams)

    # Kickoff, neutral site and TV, keyed by school pair.
    meta = {}
    media_by_id = {row.get("id"): row.get("outlet") for row in media_rows or []}
    for game in games_now:
        home = game.get("homeTeam") or game.get("home_team")
        away = game.get("awayTeam") or game.get("away_team")
        if not home or not away:
            continue
        meta[(home, away)] = {
            "week": game.get("week"),
            "neutral": bool(game.get("neutralSite") or game.get("neutral_site")),
            "conference_game": bool(game.get("conferenceGame")
                                    or game.get("conference_game")),
            "tv": media_by_id.get(game.get("id")),
            "completed": game.get("completed"),
        }

    unmatched = set()
    rows = []

    for event in board:
        posted = odds_client.parse_game(event)
        if not posted:
            continue

        home_school = names.resolve(posted["home"], index)
        away_school = names.resolve(posted["away"], index)
        if not home_school:
            unmatched.add(posted["home"])
        if not away_school:
            unmatched.add(posted["away"])
        if not home_school or not away_school:
            continue

        home_row = table_now.get(home_school)
        away_row = table_now.get(away_school)
        if not home_row or not away_row:
            continue

        info = meta.get((home_school, away_school), {})
        if info.get("completed"):
            continue

        has_full_market = posted["spread"] is not None and posted["total"] is not None
        keep, reason = on_slate(home_row, away_row, info.get("tv"), has_full_market)
        if not keep:
            continue

        scored = score_game(home_row, away_row, posted,
                            info.get("neutral", False), model)

        best_ev = max((e["ev"] for e in scored["edges"] if e["ev"] is not None),
                      default=None)
        # Points only. A moneyline gap is measured in probability and mixing the
        # two units into one column makes the board unreadable.
        best_gap = max((e["gap"] for e in scored["edges"]
                        if e["market"] in ("spread", "total")), default=0.0)

        rows.append({
            "id": posted["odds_id"],
            "home": home_school,
            "away": away_school,
            "home_conf": home_row.get("conference"),
            "away_conf": away_row.get("conference"),
            "kickoff": posted["kickoff"],
            "week": info.get("week"),
            "neutral": info.get("neutral", False),
            "conference_game": info.get("conference_game", False),
            "tv": info.get("tv"),
            "slate": reason,
            "posted": {
                "spread": posted["spread"],
                "spread_price": posted["spread_price"],
                "total": posted["total"],
                "over_price": posted["over_price"],
                "under_price": posted["under_price"],
                "home_ml": posted["home_ml"],
                "away_ml": posted["away_ml"],
            },
            "fair_spread": scored["fair_spread"],
            "fair_total": scored["fair_total"],
            "edges": scored["edges"],
            "review": scored["review"],
            "best_ev": best_ev,
            "best_gap": best_gap,
            "last_update": posted["last_update"],
        })

    rows.sort(key=lambda r: (-(r["best_ev"] or -9), r["kickoff"] or ""))

    if unmatched:
        log("  unmatched team names (add to names.ALIASES): %s"
            % ", ".join(sorted(unmatched)))

    weeks = [r["week"] for r in rows if r.get("week")]
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "season": config.SEASON,
        "week": min(weeks) if weeks else None,
        "unit": config.UNIT_DOLLARS,
        "thresholds": {"flag": config.EDGE_FLAG, "strict": config.EDGE_STRICT},
        "model": {
            "margin_sigma": round(model["margin_sigma"], 2),
            "total_sigma": round(model["total_sigma"], 2) if model["total_sigma"] else None,
            "margin_n": model["margin_n"],
            "total_n": model["total_n"],
            "home_edge": round(hfa, 2),
        },
        "quota": {
            "odds_remaining": odds_client.quota.get("remaining"),
            "odds_used": odds_client.quota.get("used"),
            "cfbd_calls_this_run": cfbd_client.call_count,
        },
        "injury_links": INJURY_LINKS,
        "unmatched": sorted(unmatched),
        "games": rows,
        "teams": {k: v for k, v in table_now.items()
                  if any(k in (r["home"], r["away"]) for r in rows)},
    }

    log("Writing board: %d games, %d flagged plays"
        % (len(rows), sum(len(r["edges"]) for r in rows)))
    write_output(payload)
    log("Done")


def write_output(payload):
    with open(TEMPLATE) as fh:
        template = fh.read()
    data = json.dumps(payload, separators=(",", ":"), default=str)
    # Guard against a stray closing tag inside the JSON breaking the script block.
    data = data.replace("</", "<\\/")
    html = template.replace("__BOARD_DATA__", data)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "index.html"), "w") as fh:
        fh.write(html)
    with open(os.path.join(OUT_DIR, "board.json"), "w") as fh:
        json.dump(payload, fh, indent=2, default=str)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log("BUILD FAILED: %s" % exc)
        traceback.print_exc()
        sys.exit(1)
