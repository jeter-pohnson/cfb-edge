"""Offline smoke test. Feeds synthetic data through the real build path.

Run: python selftest.py
Proves the pipeline, the model fit and the HTML render work without touching
either API. It does not prove the live endpoints return the shapes assumed.
"""

import os
import random

os.environ.setdefault("CFBD_API_KEY", "test")
os.environ.setdefault("ODDS_API_KEY", "test")

from pipeline import build, cfbd_client, odds_client  # noqa: E402

random.seed(7)

SCHOOLS = [
    ("Alabama", "SEC"), ("Georgia", "SEC"), ("Texas", "SEC"), ("Ole Miss", "SEC"),
    ("Ohio State", "Big Ten"), ("Michigan", "Big Ten"), ("Oregon", "Big Ten"),
    ("Penn State", "Big Ten"), ("Texas Tech", "Big 12"), ("Kansas State", "Big 12"),
    ("Clemson", "ACC"), ("Miami", "ACC"), ("NC State", "ACC"),
    ("Notre Dame", "FBS Independents"),
    ("Boise State", "Mountain West"), ("UNLV", "Mountain West"),
    ("Memphis", "American Athletic"), ("Tulane", "American Athletic"),
    ("Miami (OH)", "Mid-American"), ("Ohio", "Mid-American"),
    # Stands in for an FCS program: present in the team list, no SP+ rating.
    ("North Dakota State", "FCS"),
]

TRUE = {name: random.uniform(-18, 26) for name, _ in SCHOOLS}


def fake_teams(year):
    return [{"school": n, "conference": c, "logos": []} for n, c in SCHOOLS]


def fake_sp(year):
    # North Dakota State is deliberately absent, exactly as an FCS team is
    # absent from SP+, so its rating has to be imputed.
    return [{
        "team": n,
        "rating": round(TRUE[n] + random.gauss(0, 1.4), 2),
        "ranking": i + 1,
        "offense": {"rating": round(29 + TRUE[n] * 0.45 + random.gauss(0, 2), 2)},
        "defense": {"rating": round(25 - TRUE[n] * 0.42 + random.gauss(0, 2), 2)},
    } for i, (n, _) in enumerate(SCHOOLS) if n != "North Dakota State"]


def fake_advanced(year):
    return [{
        "team": n,
        "offense": {"ppa": round(0.16 + TRUE[n] * 0.006, 4), "plays": 820, "drives": 132,
                    "successRate": round(0.40 + TRUE[n] * 0.004, 4),
                    "explosiveness": round(1.10 + TRUE[n] * 0.012, 3),
                    "lineYards": round(2.6 + TRUE[n] * 0.02, 3),
                    "rushingPlays": {"rate": round(0.40 + (hash(n) % 25) / 100.0, 3),
                                     "ppa": round(0.10 + TRUE[n] * 0.004, 4)},
                    "passingPlays": {"rate": round(0.60 - (hash(n) % 25) / 100.0, 3),
                                     "ppa": round(0.20 + TRUE[n] * 0.005, 4)}},
        "defense": {"ppa": round(0.12 - TRUE[n] * 0.005, 4), "plays": 810, "drives": 130,
                    "successRate": round(0.44 - TRUE[n] * 0.003, 4),
                    "explosiveness": round(1.30 - TRUE[n] * 0.010, 3),
                    "stuffRate": round(0.16 + TRUE[n] * 0.003, 4),
                    "havoc": {"total": round(0.14 + TRUE[n] * 0.002, 4)},
                    "rushingPlays": {"ppa": round(0.10 - TRUE[n] * 0.003, 4)},
                    "passingPlays": {"ppa": round(0.16 - TRUE[n] * 0.004, 4)}},
    } for n, _ in SCHOOLS]


def fake_returning(year):
    return [{"team": n, "offense": 0.62, "defense": 0.58} for n, _ in SCHOOLS]


def _synth_games(year, completed):
    rng = random.Random(year * 7 + (1 if completed else 0))
    out, gid = [], year * 1000
    for _ in range(850):
        home, away = rng.sample([s for s, _ in SCHOOLS], 2)
        gid += 1
        margin = TRUE[home] - TRUE[away] + 2.4 + rng.gauss(0, 13.5)
        total = 52 + (TRUE[home] + TRUE[away]) * 0.12 + rng.gauss(0, 11)
        home_pts = max(0, round((total + margin) / 2))
        away_pts = max(0, round((total - margin) / 2))
        out.append({
            # Completed games spread across the season; upcoming games sit in
            # week 8 so the live-week gate is exercised rather than short
            # circuiting the whole board.
            "id": gid, "week": ((gid % 14) + 1) if completed else 8,
            "homeTeam": home, "awayTeam": away,
            "neutralSite": False, "conferenceGame": True, "completed": completed,
            "homePoints": home_pts if completed else None,
            "awayPoints": away_pts if completed else None,
        })
    return out


def fake_games(year, season_type="regular"):
    return _synth_games(year, completed=False) + _synth_games(year, completed=True)[:120]


def fake_calibration_games(year):
    return _synth_games(year, completed=True)


def fake_lines(year, season_type="regular"):
    rng = random.Random(year * 31)
    out = []
    for game in _synth_games(year, completed=True):
        edge = TRUE[game["homeTeam"]] - TRUE[game["awayTeam"]] + 2.4
        close = -round((edge + rng.gauss(0, 2.2)) * 2) / 2
        # Opener deliberately noisier than the close, which is the whole
        # hypothesis being tested.
        opener = -round((edge + rng.gauss(0, 3.4)) * 2) / 2
        total_close = round((52 + rng.gauss(0, 5)) * 2) / 2
        out.append({
            "homeTeam": game["homeTeam"], "awayTeam": game["awayTeam"],
            "lines": [{"provider": "DraftKings",
                       "spread": close, "spreadOpen": opener,
                       "overUnder": total_close,
                       "overUnderOpen": total_close + rng.gauss(0, 2)}],
        })
    return out


def fake_media(year, season_type="regular"):
    return [{"id": g["id"], "outlet": random.choice(["ESPN", "FOX", "CBSSN", "ESPN+"])}
            for g in fake_games(year)]


def fake_board():
    board, seen = [], set()
    mascots = {n: n + " Wildcats" for n, _ in SCHOOLS}
    mascots["Ole Miss"] = "Ole Miss Rebels"
    mascots["Miami (OH)"] = "Miami (OH) RedHawks"
    mascots["Miami"] = "Miami Hurricanes"
    mascots["NC State"] = "NC State Wolfpack"
    mascots["Texas Tech"] = "Texas Tech Red Raiders"

    for game in _synth_games(2026, completed=False)[:60]:
        pair = (game["homeTeam"], game["awayTeam"])
        if pair in seen:
            continue
        seen.add(pair)
        edge = TRUE[game["homeTeam"]] - TRUE[game["awayTeam"]] + 2.4
        spread = -round((edge + random.gauss(0, 2.6)) * 2) / 2
        total = round((52 + random.gauss(0, 6)) * 2) / 2
        # Moneyline derived from the same spread so the synthetic book is
        # internally consistent, the way a real one is.
        import math as _m
        p_home = 0.5 * (1 + _m.erf((-spread) / (13.5 * _m.sqrt(2))))
        p_home = min(0.97, max(0.03, p_home))
        home_ml = int(round(-(p_home / (1 - p_home)) * 100)) if p_home >= 0.5 \
            else int(round(((1 - p_home) / p_home) * 100))
        p_away = 1 - p_home
        away_ml = int(round(-(p_away / (1 - p_away)) * 100)) if p_away >= 0.5 \
            else int(round(((1 - p_away) / p_away) * 100))

        board.append({
            "id": "evt%s" % game["id"],
            "home_team": mascots[game["homeTeam"]],
            "away_team": mascots[game["awayTeam"]],
            "commence_time": "2026-09-13T19:00:00Z",
            "bookmakers": [{
                "key": "fanduel", "last_update": "2026-09-11T14:00:00Z",
                "markets": [
                    {"key": "spreads", "outcomes": [
                        {"name": mascots[game["homeTeam"]], "point": spread, "price": -110},
                        {"name": mascots[game["awayTeam"]], "point": -spread, "price": -110}]},
                    {"key": "totals", "outcomes": [
                        {"name": "Over", "point": total, "price": -110},
                        {"name": "Under", "point": total, "price": -110}]},
                    {"key": "h2h", "outcomes": [
                        {"name": mascots[game["homeTeam"]], "price": home_ml},
                        {"name": mascots[game["awayTeam"]], "price": away_ml}]},
                ],
            }],
        })
    odds_client.quota.update({"remaining": "488", "used": "12", "last_cost": "3"})
    return board


cfbd_client.teams = fake_teams
cfbd_client.sp_ratings = fake_sp
cfbd_client.calibration_sp = fake_sp
cfbd_client.season_advanced = fake_advanced
cfbd_client.returning_production = fake_returning
cfbd_client.games = fake_games
cfbd_client.calibration_games = fake_calibration_games
cfbd_client.media = fake_media
cfbd_client.historical_lines = fake_lines
odds_client.fetch_board = fake_board

_original_write = build.write_output


def _write_preview(payload):
    payload["sample"] = True
    _original_write(payload)
    import os
    import shutil
    docs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    shutil.move(os.path.join(docs, "index.html"),
                os.path.join(docs, "preview-sample-data.html"))
    os.remove(os.path.join(docs, "board.json"))
    print("  wrote docs/preview-sample-data.html (synthetic data, clearly labelled)")


build.write_output = _write_preview

if __name__ == "__main__":
    build.main()
