"""CollegeFootballData client.

Every call goes through _get so quota use stays countable and failures report
which endpoint broke rather than surfacing a bare KeyError three layers up.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import config

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")

call_count = 0


class CFBDError(RuntimeError):
    pass


def _cache_path(name):
    return os.path.join(CACHE_DIR, name + ".json")


def _read_cache(name, max_age_days):
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))
    if age > timedelta(days=max_age_days):
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def _write_cache(name, payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(name), "w") as fh:
        json.dump(payload, fh)


def _get(path, params=None, cache_as=None, max_age_days=None):
    """GET a CFBD endpoint. Returns parsed JSON, always a list for these routes."""
    global call_count

    if cache_as and max_age_days:
        cached = _read_cache(cache_as, max_age_days)
        if cached is not None:
            return cached

    if not config.CFBD_API_KEY:
        raise CFBDError(
            "CFBD_API_KEY is not set. Add it as a repo secret, or put it in a "
            "local .env file if you are running this by hand."
        )

    url = config.CFBD_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )

    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + config.CFBD_API_KEY)
    req.add_header("Accept", "application/json")

    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                call_count += 1
                data = json.loads(resp.read().decode("utf-8"))
            if cache_as:
                _write_cache(cache_as, data)
            return data
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:400]
            if exc.code == 401:
                raise CFBDError(
                    "CFBD rejected the key (401). Check the CFBD_API_KEY secret."
                ) from exc
            if exc.code == 429:
                raise CFBDError(
                    "CFBD monthly call limit hit (429). The free tier is about "
                    "1000 calls a month. Lower the build cadence in the workflow "
                    "file, or wait for the reset on the 1st."
                ) from exc
            last_error = CFBDError("CFBD %s returned %s: %s" % (path, exc.code, body))
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = CFBDError("CFBD %s unreachable: %s" % (path, exc))
        time.sleep(2 * (attempt + 1))

    # Fall back to a stale cache rather than failing the whole build.
    if cache_as:
        stale = _read_cache(cache_as, 3650)
        if stale is not None:
            print("  warning: %s failed, using stale cache" % path)
            return stale
    raise last_error


# ---------------------------------------------------------------- endpoints


def teams(year):
    """FBS teams with conference, so we can filter the slate."""
    return _get(
        "/teams/fbs",
        {"year": year},
        cache_as="teams_%s" % year,
        max_age_days=config.CACHE_MAX_AGE_DAYS,
    )


def sp_ratings(year):
    """SP+ ratings. Overall, offense, defense, expressed in points."""
    return _get("/ratings/sp", {"year": year})


def season_advanced(year):
    """Advanced season stats: PPA, success rate, explosiveness, havoc."""
    return _get("/stats/season/advanced", {"year": year, "excludeGarbageTime": "true"})


def games(year, season_type="regular"):
    """All games for a season, completed and scheduled."""
    return _get("/games", {"year": year, "seasonType": season_type})


def calibration_games(year):
    """Prior-season games, cached hard since they never change."""
    return _get(
        "/games",
        {"year": year, "seasonType": "regular"},
        cache_as="games_%s" % year,
        max_age_days=365,
    )


def calibration_sp(year):
    return _get(
        "/ratings/sp",
        {"year": year},
        cache_as="sp_%s" % year,
        max_age_days=365,
    )


def media(year, season_type="regular"):
    """Broadcast outlet per game. Drives the televised filter for G5 games."""
    return _get(
        "/games/media",
        {"year": year, "seasonType": season_type},
        cache_as="media_%s" % year,
        max_age_days=1,
    )


def returning_production(year):
    """Share of production returning. Proxy for roster continuity."""
    return _get(
        "/player/returning",
        {"year": year},
        cache_as="returning_%s" % year,
        max_age_days=config.CACHE_MAX_AGE_DAYS,
    )
