"""Parlay construction.

Two design decisions worth stating up front, because both cut against what a
parlay tool normally does.

Legs never come from the same game. Same-game legs are correlated, sometimes
heavily, and multiplying their probabilities as if they were independent
overstates the parlay's chances. FanDuel prices same-game parlays with that
correlation priced in; this tool cannot see that pricing, so it does not
pretend to.

No expected value is quoted. The free odds feed does not carry FanDuel's parlay
prices, and inventing one by assuming a hold percentage would produce an
authoritative-looking number built on a guess. Instead this reports the price
the parlay needs in order to break even. You read FanDuel's actual number off
the app and compare. If theirs is shorter than the break-even, it is a pass,
and no modelling is required to see it.

The arithmetic that follows is also the argument against parlays generally. A
three-leg parlay of 58% legs hits under 20% of the time, and the break-even
price it needs is usually higher than a book will offer.
"""

import itertools

from . import config

MAX_LEGS = 4
MIN_LEG_CONFIDENCE = 60
MAX_COMBINATIONS = 40


def _american(prob):
    if not prob or prob <= 0 or prob >= 1:
        return None
    if prob >= 0.5:
        return -round((prob / (1 - prob)) * 100)
    return round(((1 - prob) / prob) * 100)


def _fmt_price(value):
    if value is None:
        return "n/a"
    return ("+%d" % value) if value > 0 else str(int(value))


def _leg_label(game, edge):
    if edge["market"] == "total":
        return "%s at %s %s" % (game["away"], game["home"], edge["line"])
    return "%s %s" % (edge.get("team") or "", edge["line"])


def collect_legs(rows):
    """Eligible legs: confident enough, and one per game at most."""
    best_by_game = {}
    for row in rows:
        for edge in row.get("edges", []):
            conf = edge.get("confidence") or {}
            score = conf.get("score")
            if score is None or score < MIN_LEG_CONFIDENCE:
                continue
            if edge.get("prob") is None:
                continue
            current = best_by_game.get(row["id"])
            if current is None or score > current["score"]:
                best_by_game[row["id"]] = {
                    "game_id": row["id"],
                    "game": "%s at %s" % (row["away"], row["home"]),
                    "kickoff": row.get("kickoff"),
                    "label": _leg_label(row, edge),
                    "market": edge["market"],
                    "price": edge.get("price"),
                    "prob": edge["prob"],
                    "push": edge.get("push") or 0.0,
                    "score": score,
                    "grade": conf.get("grade"),
                    "gap": edge.get("gap"),
                }
    legs = list(best_by_game.values())
    legs.sort(key=lambda leg: -leg["score"])
    return legs[:8]


def build(rows):
    """Return ranked parlay candidates of two to four legs."""
    legs = collect_legs(rows)
    if len(legs) < 2:
        return {"legs_available": len(legs), "parlays": [],
                "note": "Not enough legs graded %d or better to build a parlay."
                        % MIN_LEG_CONFIDENCE}

    out = []
    for size in range(2, min(MAX_LEGS, len(legs)) + 1):
        for combo in itertools.combinations(legs, size):
            # One leg per game, enforced by construction, but check anyway.
            if len({leg["game_id"] for leg in combo}) != size:
                continue

            prob = 1.0
            for leg in combo:
                prob *= leg["prob"]

            fair = _american(prob)
            # The true multiplied price of the individual legs, which is what a
            # parlay would pay if the book took no extra margin on it.
            true_price = _true_multiplied(combo)

            out.append({
                "size": size,
                "legs": list(combo),
                "probability": round(prob, 4),
                "break_even_price": fair,
                "break_even_display": _fmt_price(fair),
                "true_multiplied": true_price,
                "true_multiplied_display": _fmt_price(true_price),
                "hits_per_hundred": round(prob * 100, 1),
                "avg_confidence": round(
                    sum(leg["score"] for leg in combo) / size, 1),
                "stake": config.UNIT_DOLLARS,
                "returns_at_breakeven": _payout(config.UNIT_DOLLARS, fair),
            })

    # Rank by average leg confidence, then by how often it hits. Ranking by
    # payout would put the worst bets on top, which is how parlay tools
    # normally work and precisely what this is trying not to do.
    out.sort(key=lambda p: (-p["avg_confidence"], -p["probability"]))
    return {
        "legs_available": len(legs),
        "parlays": out[:MAX_COMBINATIONS],
        "note": "",
    }


def _true_multiplied(combo):
    """Decimal product of the legs' own prices, converted back to American."""
    decimal = 1.0
    for leg in combo:
        price = leg.get("price")
        if price is None:
            return None
        price = float(price)
        decimal *= (1.0 + (100.0 / (-price) if price < 0 else price / 100.0))
    profit = decimal - 1.0
    if profit <= 0:
        return None
    if profit >= 1:
        return round(profit * 100)
    return -round(100.0 / profit)


def _payout(stake, american):
    if american is None:
        return None
    american = float(american)
    profit = stake * (100.0 / (-american) if american < 0 else american / 100.0)
    return round(stake + profit, 2)
