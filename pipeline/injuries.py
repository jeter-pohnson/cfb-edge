"""Optional automated injury feed.

CollegeFootballData has no injury endpoint, so this is a separate provider.
BALLDONTLIE gates its NCAAF injuries behind a paid tier, so the whole module is
opt-in: with no BDL_API_KEY set, the build skips it silently and the manual
availability panel in the app carries on exactly as before.

Worth being clear about what this buys. These providers largely compile the
same conference availability reports and beat reporting you could read
yourself, so this is automation and completeness, not earlier or better
information.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import names

BDL_BASE = "https://api.balldontlie.io/ncaaf/v1"
BDL_API_KEY = os.environ.get("BDL_API_KEY", "")

status = {"enabled": bool(BDL_API_KEY), "ok": False, "note": "", "count": 0}


def enabled():
    return bool(BDL_API_KEY)


def _get(path, params=None):
    url = BDL_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url)
    req.add_header("Authorization", BDL_API_KEY)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_injuries(cfbd_teams):
    """Return {cfbd school: [injury dicts]}. Never raises: a failure here must
    not take down a build whose main job is the board."""
    if not enabled():
        status["note"] = "No BDL_API_KEY set, using manual notes only."
        return {}

    index = names.build_index(cfbd_teams)
    out = {}
    cursor = None
    pages = 0

    try:
        while pages < 20:
            params = {"per_page": 100}
            if cursor:
                params["cursor"] = cursor
            payload = _get("/player_injuries", params)

            for row in payload.get("data", []):
                player = row.get("player") or {}
                team_name = (row.get("team") or {}).get("name") or player.get("team")
                if not team_name:
                    continue
                school = names.resolve(str(team_name), index)
                if not school:
                    continue
                out.setdefault(school, []).append({
                    "player": (" ".join(
                        filter(None, [player.get("first_name"),
                                      player.get("last_name")]))
                        or player.get("name") or "Unknown"),
                    "position": player.get("position"),
                    "status": row.get("status"),
                    "return_date": row.get("return_date"),
                    "description": (row.get("description") or "")[:280],
                })

            cursor = (payload.get("meta") or {}).get("next_cursor")
            pages += 1
            if not cursor:
                break
            time.sleep(0.4)

        status["ok"] = True
        status["count"] = sum(len(v) for v in out.values())
        status["note"] = "Injury feed live, %d entries." % status["count"]

    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            status["note"] = ("BALLDONTLIE rejected the key or the plan does not "
                              "include NCAAF injuries. Manual notes still work.")
        elif exc.code == 429:
            status["note"] = "BALLDONTLIE rate limit hit. Manual notes still work."
        else:
            status["note"] = "BALLDONTLIE error %s. Manual notes still work." % exc.code
    except Exception as exc:  # noqa: BLE001
        status["note"] = "Injury feed unavailable (%s). Manual notes still work." % exc

    return out


# Statuses that meaningfully change a team, ordered by severity.
SEVERE = {"out", "doubtful", "injured reserve", "suspended", "season"}
NOTABLE = {"questionable", "probable", "day-to-day", "game-time decision"}


def summarise(entries):
    """Condense a team's injuries into counts the writeup can use."""
    if not entries:
        return None
    out_count = 0
    questionable = 0
    for entry in entries:
        state = (entry.get("status") or "").strip().lower()
        if any(token in state for token in SEVERE):
            out_count += 1
        elif any(token in state for token in NOTABLE):
            questionable += 1
    return {"out": out_count, "questionable": questionable,
            "total": len(entries)}
