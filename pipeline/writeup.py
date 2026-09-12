"""Build the per-play explanation.

Three things go into every writeup, and the third matters most: how the fair
number was built, what in the matchup profile supports the flagged side, and
what would have to be true for FanDuel to be right instead. A tool that only
argues its own side is a tool that will talk you into bad bets.

Stat directions are worked out empirically at runtime rather than assumed. If
CFBD flips a sign on a rating, correlations against PPA catch it.
"""

import math

from . import config

# Stats used to describe a matchup, with how they are read.
# key, label, higher_is_better
OFFENSE_STATS = [
    ("off_ppa", "offensive efficiency", True),
    ("off_success", "success rate", True),
    ("off_explosive", "explosiveness", True),
]
DEFENSE_STATS = [
    ("def_ppa", "defensive efficiency", False),
    ("def_success", "success rate allowed", False),
    ("def_havoc", "havoc rate", True),
]


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def resolve_directions(table):
    """Work out whether a higher SP+ offence or defence rating is better.

    SP+ expresses defence as points allowed, so lower is better, but that is a
    convention rather than a guarantee. Correlating against PPA settles it from
    the data in front of us.
    """
    rows = [r for r in table.values()
            if r.get("sp_off") is not None and r.get("off_ppa") is not None]
    off_dir = 1 if _pearson([r["sp_off"] for r in rows],
                            [r["off_ppa"] for r in rows]) >= 0 else -1
    rows = [r for r in table.values()
            if r.get("sp_def") is not None and r.get("def_ppa") is not None]
    # def_ppa is points allowed per play, so a good defence has a low value.
    def_dir = -1 if _pearson([r["sp_def"] for r in rows],
                             [r["def_ppa"] for r in rows]) >= 0 else 1
    return {"sp_off": off_dir, "sp_def": def_dir}


def build_ranks(table):
    """Rank every team on each stat. Rank 1 is always the good end."""
    ranks = {}
    fields = ([(k, hib) for k, _, hib in OFFENSE_STATS]
              + [(k, hib) for k, _, hib in DEFENSE_STATS]
              + [("pace", True), ("sp", True)])

    for field, higher_is_better in fields:
        rows = [(r["school"], r.get(field)) for r in table.values()
                if r.get(field) is not None]
        rows.sort(key=lambda pair: pair[1], reverse=higher_is_better)
        for position, (school, _) in enumerate(rows, start=1):
            ranks.setdefault(school, {})[field] = position
    ranks["_count"] = len([r for r in table.values() if r.get("off_ppa") is not None])
    return ranks


def _rank_text(rank, total):
    if rank is None:
        return "unranked"
    return "%d of %d" % (rank, total)


PAIRS = [
    ("off_ppa", "def_ppa", "efficiency per play"),
    ("off_success", "def_success", "staying on schedule"),
    ("off_explosive", "def_explosive", "explosive plays"),
]


def _article(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def _matchup_drivers(attack, defend, ranks, total, limit=3):
    """Where the attacking side's strengths meet the defending side's weaknesses.

    Only genuine advantages are returned. A rank 6 offence against a rank 4
    defence is not a point in favour, and listing it as one is how a tool talks
    you into a bet it has no case for.
    """
    drivers = []
    for off_key, def_key, label in PAIRS:
        off_rank = ranks.get(attack["school"], {}).get(off_key)
        def_rank = ranks.get(defend["school"], {}).get(def_key)
        if off_rank is None or def_rank is None:
            continue
        advantage = def_rank - off_rank
        if advantage <= 0:
            continue
        drivers.append({
            "label": label,
            "advantage": advantage,
            "text": "%s ranks %s in %s, against %s %s defence ranked %s"
                    % (attack["school"], _rank_text(off_rank, total), label,
                       _article(defend["school"]), defend["school"],
                       _rank_text(def_rank, total)),
        })
    drivers.sort(key=lambda d: -d["advantage"])
    return drivers[:limit]


def _defensive_drivers(defend, attack, ranks, total, limit=3):
    """The mirror case, used for unders: good defence against poor offence."""
    drivers = []
    for off_key, def_key, label in PAIRS:
        def_rank = ranks.get(defend["school"], {}).get(def_key)
        off_rank = ranks.get(attack["school"], {}).get(off_key)
        if off_rank is None or def_rank is None:
            continue
        advantage = off_rank - def_rank
        if advantage <= 0:
            continue
        drivers.append({
            "label": label,
            "advantage": advantage,
            "text": "%s defence ranks %s in %s, against %s %s offence ranked %s"
                    % (defend["school"], _rank_text(def_rank, total), label,
                       _article(attack["school"]), attack["school"],
                       _rank_text(off_rank, total)),
        })
    drivers.sort(key=lambda d: -d["advantage"])
    return drivers[:limit]


def spread_writeup(game, home, away, edge, model, ranks, directions):
    """Narrative for a flagged spread."""
    total_teams = ranks.get("_count", 0)
    backing = home if edge["side"] == "home" else away
    fading = away if edge["side"] == "home" else home

    sigma = model["margin_sigma"]
    fair = edge["fair"]
    posted = game["posted"]["spread"]
    gap = edge["gap"]

    build = (
        "%s grades out at %s on SP+, %s at %s, and the fitted home edge on this "
        "season's completed games is %s points. That produces a fair line of %s "
        "against FanDuel's %s, a gap of %s points."
        % (home["school"], _fmt(home.get("sp")), away["school"], _fmt(away.get("sp")),
           _fmt(model["margin_coeffs"][0]), _fmt_line(fair), _fmt_line(posted),
           _fmt(gap))
    )

    drivers = _matchup_drivers(backing, fading, ranks, total_teams)

    maths = (
        "Around a predicted margin the model's error is %s points per game, so a "
        "%s point gap converts to a %s cover probability. At %s that is an "
        "expected return of %s per dollar risked, or %s on a %s unit."
        % (_fmt(sigma), _fmt(gap), _pct(edge["prob"]), _price(edge["price"]),
           _fmt(edge["ev"], 3), _dollars(edge["ev"] * config.UNIT_DOLLARS),
           "$%d" % config.UNIT_DOLLARS)
    )

    against = _counter_case(game, backing, fading, edge, model, ranks, "spread",
                            drivers)
    return {
        "headline": "%s %s" % (backing["school"], edge["line"]),
        "build": build,
        "drivers": drivers,
        "maths": maths,
        "against": against,
    }


def total_writeup(game, home, away, edge, model, ranks, directions):
    total_teams = ranks.get("_count", 0)
    sigma = model["total_sigma"]
    side = edge["side"]

    home_pace = ranks.get(home["school"], {}).get("pace")
    away_pace = ranks.get(away["school"], {}).get("pace")

    build = (
        "The total model reads both offences against both defences and adjusts "
        "for pace. It lands on %s against FanDuel's %s, a gap of %s points. On "
        "tempo, %s ranks %s in plays per drive and %s ranks %s, where rank 1 is "
        "the fastest."
        % (_fmt(edge["fair"]), _fmt(game["posted"]["total"]), _fmt(edge["gap"]),
           home["school"], _rank_text(home_pace, total_teams),
           away["school"], _rank_text(away_pace, total_teams))
    )

    if side == "over":
        drivers = (_matchup_drivers(home, away, ranks, total_teams, limit=2)
                   + _matchup_drivers(away, home, ranks, total_teams, limit=2))
    else:
        drivers = (_defensive_drivers(home, away, ranks, total_teams, limit=2)
                   + _defensive_drivers(away, home, ranks, total_teams, limit=2))
    drivers.sort(key=lambda d: -d["advantage"])
    drivers = drivers[:3]

    maths = (
        "Total error runs %s points a game, so a %s point gap gives the %s a %s "
        "chance. At %s that is %s per dollar, or %s on a unit."
        % (_fmt(sigma), _fmt(edge["gap"]), side, _pct(edge["prob"]),
           _price(edge["price"]), _fmt(edge["ev"], 3),
           _dollars(edge["ev"] * config.UNIT_DOLLARS))
    )

    against = _counter_case(game, home, away, edge, model, ranks, "total",
                            drivers)
    return {
        "headline": edge["line"],
        "build": build,
        "drivers": drivers,
        "maths": maths,
        "against": against,
    }


def moneyline_writeup(game, home, away, edge, model, ranks, directions):
    backing = home if edge["side"] == "home" else away
    fading = away if edge["side"] == "home" else home
    build = (
        "Stripping the vig from FanDuel's two-way price puts %s at a fair %s to "
        "win outright. The model's margin distribution puts them at %s. The "
        "posted price of %s pays more than that gap deserves."
        % (backing["school"], _pct(edge["prob"] - edge["gap"] / 100.0),
           _pct(edge["prob"]), _price(edge["price"]))
    )
    maths = (
        "At %s, break-even is %s. The model is %s points of probability above "
        "that, worth %s per dollar risked."
        % (_price(edge["price"]), _pct(_breakeven(edge["price"])),
           _fmt(edge["gap"]), _fmt(edge["ev"], 3))
    )
    drivers = _matchup_drivers(backing, fading, ranks, ranks.get("_count", 0))
    return {
        "headline": "%s %s" % (backing["school"], edge["line"]),
        "build": build,
        "drivers": drivers,
        "maths": maths,
        "against": _counter_case(game, backing, fading, edge, model, ranks,
                                 "moneyline", drivers),
    }


def _counter_case(game, backing, fading, edge, model, ranks, market,
                  drivers=None):
    """What would have to be true for FanDuel to be right."""
    points = []

    if drivers is not None and not drivers:
        points.append(
            "Nothing in the matchup profile supports this. The gap is coming "
            "from the overall rating difference alone, with no efficiency, "
            "success rate or explosiveness edge behind it."
        )

    sigma = model["margin_sigma"] if market != "total" else model["total_sigma"]
    if market in ("spread", "total") and sigma and edge["gap"] < sigma / 4.0:
        points.append(
            "The gap is %s points against a model error of %s. That is well "
            "inside noise, so this flag is closer to a coin flip than the "
            "expected value figure makes it look."
            % (_fmt(edge["gap"]), _fmt(sigma))
        )

    if not edge.get("strict"):
        points.append(
            "This clears your 2 point trigger but not 3. Track it, but it is the "
            "weaker half of the sample you are testing."
        )

    for team in (backing, fading):
        imputed = team.get("imputed") or []
        if imputed:
            points.append(
                "%s is missing %s, so league average was substituted. The rating "
                "is softer than it looks."
                % (team["school"], " and ".join(imputed[:3]))
            )

    if game.get("slate") != "power":
        points.append(
            "Group of Five game. Thinner market, but also less public money "
            "shaping the number, which cuts both ways."
        )

    if game.get("neutral"):
        points.append(
            "Neutral site, so the fitted home edge is switched off. Crowd "
            "composition at these is rarely actually neutral."
        )

    if not game.get("conference_game"):
        points.append(
            "Non-conference game, which means no mandatory availability report. "
            "Your injury picture here is whatever you gather yourself."
        )

    points.append(
        "The market has information the model does not: injuries, suspensions, "
        "weather, motivation, and what sharp money already did to this number. "
        "A gap is a question to look into, not an answer."
    )
    return points


# ---------------------------------------------------------------- formatting


def _fmt(value, places=1):
    if value is None:
        return "n/a"
    try:
        return ("%." + str(places) + "f") % float(value)
    except (TypeError, ValueError):
        return "n/a"


def _fmt_line(value):
    if value is None:
        return "n/a"
    value = float(value)
    return ("+%.1f" if value > 0 else "%.1f") % value


def _pct(value):
    if value is None:
        return "n/a"
    return "%.1f%%" % (float(value) * 100.0)


def _price(value):
    if value is None:
        return "n/a"
    value = int(value)
    return ("+%d" % value) if value > 0 else str(value)


def _dollars(value):
    if value is None:
        return "n/a"
    return "$%.2f" % float(value)


def _breakeven(american):
    if american is None:
        return None
    american = float(american)
    if american < 0:
        return (-american) / ((-american) + 100.0)
    return 100.0 / (american + 100.0)


def build_writeup(game, home, away, edge, model, ranks, directions):
    if edge["market"] == "spread":
        return spread_writeup(game, home, away, edge, model, ranks, directions)
    if edge["market"] == "total":
        return total_writeup(game, home, away, edge, model, ranks, directions)
    return moneyline_writeup(game, home, away, edge, model, ranks, directions)
