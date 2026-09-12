"""Team style profiles, computed rather than written.

There is no API that hands you "fast-tempo, pass-happy, bad on the back end".
What there is, in CFBD's advanced stats, is every ingredient of that sentence:
run and pass rates, success rate, explosiveness, havoc, line yards, stuff rate
and pace. Deriving the description from those means it rebuilds itself on every
run and is current as of the last game played, rather than being someone's
preseason impression that nobody went back to correct in November.

Everything is expressed as a percentile against the FBS field, so a profile
reads the same in week 3 as in week 12 even as the raw numbers drift.
"""


DIMENSIONS = [
    # key, label, higher percentile means
    ("off_success", "efficiency", "stays on schedule", "struggles on schedule"),
    ("off_explosive", "explosiveness", "hits big plays", "grinds it out"),
    ("off_ppa", "offensive output", "moves the ball", "stalls out"),
    ("run_rate", "run lean", "run-leaning", "pass-leaning"),
    ("pace", "tempo", "plays fast", "plays slow"),
    ("def_success_inv", "defensive efficiency", "hard to move on", "gives up yards"),
    ("def_explosive_inv", "big play defence", "limits big plays", "gives up big plays"),
    ("def_havoc", "havoc", "disruptive front", "passive front"),
    ("line_yards", "run blocking", "strong line play", "weak line play"),
    ("stuff_rate_inv", "run defence", "stuffs the run", "soft against the run"),
]


def enrich(table, advanced):
    """Attach the raw style fields the profiler needs."""
    by_team = {}
    for row in advanced or []:
        if row.get("team"):
            by_team[row["team"]] = row

    for school, team in table.items():
        row = by_team.get(school) or {}
        offense = row.get("offense") or {}
        defense = row.get("defense") or {}
        rushing = offense.get("rushingPlays") or {}
        passing = offense.get("passingPlays") or {}

        run_rate = _num(rushing.get("rate"))
        if run_rate is None:
            pass_rate = _num(passing.get("rate"))
            run_rate = (1.0 - pass_rate) if pass_rate is not None else None

        team["run_rate"] = run_rate
        team["line_yards"] = _num(offense.get("lineYards"))
        team["stuff_rate"] = _num(defense.get("stuffRate"))
        team["off_rush_ppa"] = _num(rushing.get("ppa"))
        team["off_pass_ppa"] = _num(passing.get("ppa"))
        team["def_rush_ppa"] = _num((defense.get("rushingPlays") or {}).get("ppa"))
        team["def_pass_ppa"] = _num((defense.get("passingPlays") or {}).get("ppa"))

        # Inverted copies so every dimension reads "higher is better".
        team["def_success_inv"] = _invert(team.get("def_success"))
        team["def_explosive_inv"] = _invert(team.get("def_explosive"))
        team["stuff_rate_inv"] = team.get("stuff_rate")
    return table


def _num(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _invert(value):
    return None if value is None else -float(value)


def percentiles(table):
    """Percentile rank per team per dimension, 0 to 100."""
    out = {}
    for key, _, _, _ in DIMENSIONS:
        rows = [(school, team.get(key)) for school, team in table.items()
                if team.get(key) is not None]
        if len(rows) < 10:
            continue
        rows.sort(key=lambda pair: pair[1])
        n = len(rows)
        for position, (school, _) in enumerate(rows):
            pct = round(100.0 * position / (n - 1)) if n > 1 else 50
            out.setdefault(school, {})[key] = pct
    return out


def describe(school, table, pcts):
    """A short profile: headline sentence, plus the traits that drove it."""
    team = table.get(school) or {}
    marks = pcts.get(school) or {}
    if not marks:
        return None

    traits = []
    for key, label, high_text, low_text in DIMENSIONS:
        pct = marks.get(key)
        if pct is None:
            continue
        if pct >= 78:
            traits.append({"key": key, "label": label, "pct": pct,
                           "text": high_text, "strength": pct - 50})
        elif pct <= 22:
            traits.append({"key": key, "label": label, "pct": pct,
                           "text": low_text, "strength": 50 - pct})

    traits.sort(key=lambda t: -t["strength"])

    offense = _side_sentence(marks, school, side="offense")
    defense = _side_sentence(marks, school, side="defense")

    return {
        "school": school,
        "conference": team.get("conference"),
        "headline": " ".join(filter(None, [offense, defense])),
        "traits": traits[:5],
        "percentiles": marks,
        "splits": _splits(team),
    }


def _band(pct):
    if pct is None:
        return None
    if pct >= 85:
        return "elite"
    if pct >= 65:
        return "good"
    if pct >= 35:
        return "average"
    if pct >= 15:
        return "poor"
    return "bad"


def _side_sentence(marks, school, side):
    if side == "offense":
        eff = marks.get("off_success")
        exp = marks.get("off_explosive")
        run = marks.get("run_rate")
        pace = marks.get("pace")
        if eff is None:
            return None
        style = []
        if run is not None:
            style.append("run-heavy" if run >= 70 else
                         ("pass-heavy" if run <= 30 else "balanced"))
        if pace is not None:
            style.append("up-tempo" if pace >= 70 else
                         ("slow-paced" if pace <= 30 else "neutral-tempo"))
        shape = ""
        if eff is not None and exp is not None:
            if exp - eff >= 25:
                shape = " that lives on explosive plays more than sustained drives"
            elif eff - exp >= 25:
                shape = " that moves methodically rather than in chunks"
        return ("%s runs a %s offence rated %s in efficiency%s."
                % (school, " ".join(style) or "conventional", _band(eff), shape))

    eff = marks.get("def_success_inv")
    havoc = marks.get("def_havoc")
    big = marks.get("def_explosive_inv")
    if eff is None:
        return None
    bits = ["The defence grades %s at preventing successful plays" % _band(eff)]
    if havoc is not None and havoc >= 70:
        bits.append("with a front that generates havoc at a high rate")
    elif havoc is not None and havoc <= 30:
        bits.append("with a front that rarely disrupts")
    if big is not None and big <= 25:
        bits.append("and a clear vulnerability to explosive plays")
    elif big is not None and big >= 75:
        bits.append("and keeps things in front of it")
    return ", ".join(bits) + "."


def _splits(team):
    """Run versus pass, both sides, for the profile table."""
    return {
        "off_rush_ppa": team.get("off_rush_ppa"),
        "off_pass_ppa": team.get("off_pass_ppa"),
        "def_rush_ppa": team.get("def_rush_ppa"),
        "def_pass_ppa": team.get("def_pass_ppa"),
        "run_rate": team.get("run_rate"),
    }


def stylistic_note(backing, fading, pcts):
    """A style clash worth naming, or None.

    Only genuine mismatches are returned. A team that is merely decent against
    a team that is merely poor is not a stylistic angle, it is just a ratings
    difference the model already counted.
    """
    a = pcts.get(backing) or {}
    b = pcts.get(fading) or {}
    notes = []

    if a.get("off_explosive", 50) >= 75 and b.get("def_explosive_inv", 50) <= 25:
        notes.append("%s hits explosive plays at a high rate against a defence "
                     "that is among the worst at preventing them" % backing)

    if a.get("run_rate", 50) >= 75 and b.get("stuff_rate_inv", 50) <= 25:
        notes.append("%s leans run against a front that struggles to stuff it"
                     % backing)

    if a.get("pace", 50) >= 75 and b.get("pace", 50) <= 25:
        notes.append("a tempo clash, with %s pushing pace against a team that "
                     "prefers to shorten the game" % backing)

    if b.get("def_havoc", 50) >= 78 and a.get("off_success", 50) <= 30:
        notes.append("%s struggles to stay on schedule against a disruptive "
                     "front, which cuts the other way" % backing)

    return notes
