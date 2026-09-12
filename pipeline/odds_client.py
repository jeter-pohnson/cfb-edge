"""The Odds API client, pinned to FanDuel.

Cost is markets x regions per call. We ask for three markets from a single
bookmaker group, so one full board pull costs 3 credits against the 500 a
month on the free plan. The response headers tell us exactly what is left and
we surface that in the UI so the quota never runs out as a surprise.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

quota = {"used": None, "remaining": None, "last_cost": None}


class OddsError(RuntimeError):
    pass


def fetch_board():
    """Return the FanDuel NCAAF board: every game with h2h, spread and total."""
    if not config.ODDS_API_KEY:
        raise OddsError(
            "ODDS_API_KEY is not set. Add it as a repo secret, or put it in a "
            "local .env file if you are running this by hand."
        )

    params = {
        "apiKey": config.ODDS_API_KEY,
        "bookmakers": config.ODDS_BOOKMAKER,
        "markets": config.ODDS_MARKETS,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    url = "%s/sports/%s/odds/?%s" % (
        config.ODDS_BASE,
        config.ODDS_SPORT,
        urllib.parse.urlencode(params),
    )

    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as resp:
                headers = resp.headers
                quota["used"] = headers.get("x-requests-used")
                quota["remaining"] = headers.get("x-requests-remaining")
                quota["last_cost"] = headers.get("x-requests-last")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:400]
            if exc.code == 401:
                raise OddsError(
                    "The Odds API rejected the key (401). Check the ODDS_API_KEY "
                    "secret."
                ) from exc
            if exc.code == 429:
                raise OddsError(
                    "Odds quota exhausted (429). The free plan is 500 credits a "
                    "month and each board pull costs 3. Credits reset on the 1st."
                ) from exc
            last_error = OddsError("Odds API returned %s: %s" % (exc.code, body))
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = OddsError("Odds API unreachable: %s" % exc)
        time.sleep(2 * (attempt + 1))

    raise last_error


def parse_game(event):
    """Flatten one event into the numbers we actually model against.

    Returns None when FanDuel has not posted a spread and total yet, which is
    normal for games more than a week out and for low-market G5 matchups.
    """
    book = None
    for candidate in event.get("bookmakers", []):
        if candidate.get("key") == config.ODDS_BOOKMAKER:
            book = candidate
            break
    if book is None:
        return None

    out = {
        "odds_id": event.get("id"),
        "home": event.get("home_team"),
        "away": event.get("away_team"),
        "kickoff": event.get("commence_time"),
        "spread": None,
        "spread_price": None,
        "total": None,
        "over_price": None,
        "under_price": None,
        "home_ml": None,
        "away_ml": None,
        "last_update": book.get("last_update"),
    }

    for market in book.get("markets", []):
        key = market.get("key")
        outcomes = market.get("outcomes", [])

        if key == "spreads":
            for outcome in outcomes:
                if outcome.get("name") == out["home"]:
                    # Stored from the home side: negative means home favoured.
                    out["spread"] = outcome.get("point")
                    out["spread_price"] = outcome.get("price")

        elif key == "totals":
            for outcome in outcomes:
                if outcome.get("name") == "Over":
                    out["total"] = outcome.get("point")
                    out["over_price"] = outcome.get("price")
                elif outcome.get("name") == "Under":
                    out["under_price"] = outcome.get("price")

        elif key == "h2h":
            for outcome in outcomes:
                if outcome.get("name") == out["home"]:
                    out["home_ml"] = outcome.get("price")
                elif outcome.get("name") == out["away"]:
                    out["away_ml"] = outcome.get("price")

    if out["spread"] is None and out["total"] is None:
        return None
    return out


def american_to_probability(price):
    """Implied probability including the vig."""
    if price is None:
        return None
    price = float(price)
    if price < 0:
        return (-price) / ((-price) + 100.0)
    return 100.0 / (price + 100.0)


def devig_pair(price_a, price_b):
    """Strip the hold from a two-way market. Returns fair probabilities."""
    p_a = american_to_probability(price_a)
    p_b = american_to_probability(price_b)
    if p_a is None or p_b is None:
        return None, None
    total = p_a + p_b
    if total <= 0:
        return None, None
    return p_a / total, p_b / total


def probability_to_american(prob):
    if not prob or prob <= 0 or prob >= 1:
        return None
    if prob >= 0.5:
        return -round((prob / (1 - prob)) * 100)
    return round(((1 - prob) / prob) * 100)
