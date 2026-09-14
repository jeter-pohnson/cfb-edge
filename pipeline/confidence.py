"""Confidence scoring.

Confidence is NOT the chance of winning. That is the cover probability, and it
already exists. Confidence here means how much the tool trusts its own number
on this particular game, which is a different question and mostly an unrelated
one.

The distinction matters because the two can point opposite ways. A play can
carry a big expected value precisely because the model is working from bad
inputs, and a small honest edge on clean data is the better bet of the two.

Every component is named and carries its own points, so the writeup can show
the arithmetic instead of asking you to trust a letter grade.
"""

from . import backtest, config, keynumbers

# Confidence starts here and moves on evidence.
BASE = 50

# Highest score reachable when the gap size has no measured history. Sits at
# the top of grade B on purpose.
UNVERIFIED_CEILING = 74

TIERS = [
    (78, "A", "High"),
    (64, "B", "Solid"),
    (50, "C", "Marginal"),
    (0, "D", "Thin"),
]


def _tier(score):
    for threshold, grade, label in TIERS:
        if score >= threshold:
            return grade, label
    return "D", "Thin"


def score_edge(game, edge, home, away, model, drivers, move,
               report=None, style_notes=None):
    """Return a confidence score, tier and the components that produced it."""
    parts = []

    def add(points, reason, always=False):
        # Zero-point components are dropped by default to keep the breakdown
        # readable, but some of them are the most important thing on the list.
        # "This bucket is indistinguishable from a coin flip" is worth nothing
        # to the score and everything to the reader.
        if points or always:
            parts.append({"points": points, "reason": reason})

    # ---- how big is the edge, measured against history where possible
    sigma = model["margin_sigma"] if edge["market"] != "total" else model["total_sigma"]
    measured = backtest.rate_for_gap(report, edge["market"], edge["gap"]) \
        if edge["market"] in ("spread", "total") else None

    unverified = False

    if measured:
        surplus = measured["rate"] - backtest.BREAK_EVEN
        stderr = measured.get("stderr")
        if measured.get("significant"):
            # Beat break-even by more than two standard errors. This is the
            # only case that earns positive points from history.
            points = int(round(max(-22, min(22, surplus * 260))))
            add(points,
                "Gaps of %s points went %d-%d against the closing line, a %.1f%% "
                "hit rate against the %.1f%% needed, and the margin is larger "
                "than the sampling error"
                % (measured["bucket"], measured["wins"], measured["losses"],
                   measured["rate"] * 100, backtest.BREAK_EVEN * 100))
        elif surplus < 0:
            # Measurably below break-even. Penalise it.
            points = int(round(max(-22, surplus * 260)))
            add(points,
                "Gaps of %s points went %d-%d against the closing line, a %.1f%% "
                "hit rate against the %.1f%% needed. This gap size lost money "
                "historically"
                % (measured["bucket"], measured["wins"], measured["losses"],
                   measured["rate"] * 100, backtest.BREAK_EVEN * 100))
            unverified = True
        else:
            # Above break-even but inside the error bar. That is not evidence,
            # and treating it as evidence is how a coin flip becomes a grade A.
            add(0,
                "Gaps of %s points went %d-%d, a %.1f%% hit rate. That is above "
                "break-even but within %.1f points of sampling error, so it is "
                "indistinguishable from chance and earns nothing"
                % (measured["bucket"], measured["wins"], measured["losses"],
                   measured["rate"] * 100, (stderr or 0) * 2),
                always=True)
            unverified = True
    elif sigma and edge["market"] in ("spread", "total"):
        # No historical sample for this gap size. A large disagreement is then
        # just as likely to be the model being wrong as the market being wrong,
        # so the upside is deliberately smaller than the measured version and
        # the grade gets capped below A further down. Paying a big bonus for a
        # disagreement you cannot verify is how a tool talks itself into its
        # own worst bets.
        ratio = edge["gap"] / sigma
        unverified = True
        if ratio >= 0.40:
            add(10, "Gap is %.0f%% of the model's own error, a large "
                    "disagreement. No historical sample for gaps this size, so "
                    "this is reasoning rather than evidence, and a gap this big "
                    "is as easily the model being wrong as the market being wrong"
                    % (ratio * 100))
        elif ratio >= 0.28:
            add(7, "Gap is %.0f%% of the model's own error, estimated from "
                   "noise rather than measured" % (ratio * 100))
        elif ratio >= 0.18:
            add(3, "Gap is %.0f%% of the model's own error, estimated from "
                   "noise rather than measured" % (ratio * 100))
        else:
            add(-8, "Gap is only %.0f%% of the model's own error, so it is well "
                    "inside noise" % (ratio * 100))

    # ---- threshold
    if edge.get("strict"):
        add(8, "Clears %.0f points, not just the %.0f point minimum"
               % (config.EDGE_STRICT, config.EDGE_FLAG))
    else:
        add(-6, "Sits between %.0f and %.0f points, the weaker half of the "
                "validated band" % (config.EDGE_FLAG, config.EDGE_STRICT))

    # ---- key numbers, measured rather than assumed
    mass = edge.get("key_mass")
    if mass and mass.get("mass"):
        share = mass["mass"]
        detail = ", ".join("%d at %.1f%%" % (n["number"], n["share"])
                           for n in mass["numbers"][:3])
        points = int(round(min(14, share * 170)))
        if points >= 2:
            add(points,
                "Moving from the posted number to fair crosses %.1f%% of the "
                "margin distribution for this spread range (%s)"
                % (share * 100, detail or "no single number dominates"))
    elif edge.get("keys"):
        add(4, "Fair value crosses %s, though the measured distribution was "
               "unavailable so this is the crude version"
               % ", ".join(str(k) for k in edge["keys"]))

    # ---- market movement, the part expected value cannot see
    delta = move.get("spread_delta") if edge["market"] != "total" else move.get("total_delta")
    hours = move.get("seen_for_hours") or 0

    if delta is None or move.get("snapshots", 0) < 2:
        # This used to score zero, on the grounds that betting openers was
        # untested. It has now been tested: across three seasons the same model
        # went 53.3% against opening numbers and 50.6% against closing ones,
        # consistently in all three. So freshness earns points, and the size of
        # the bonus is kept modest because the pooled result is only 0.7
        # standard errors clear of break-even.
        add(9, "Opener, first time the board has seen this line. The measured "
               "edge in this model lives against opening numbers and is gone by "
               "kickoff, so a fresh line is the only version of this play worth "
               "having", always=True)
    elif hours > config.OPENER_EDGE_WINDOW_HOURS:
        add(-8, "This line has been up %.0f hours. The backtest found no edge "
                "against numbers the market has had time to correct, so a stale "
                "line is the wrong half of the only effect that measured"
                % hours)
    else:
        toward = _moving_toward_us(edge, delta)
        if toward is None:
            add(2, "Line has not moved since first seen %.0f hours ago" % hours)
        elif toward:
            add(-10, "Line has moved %.1f points toward the model's number, so "
                     "the market is closing the gap and the edge is shrinking"
                     % abs(delta))
        else:
            add(7, "Line has moved %.1f points away from the model's number and "
                   "the gap has widened rather than closed" % abs(delta))

    # ---- input quality
    for team in (home, away):
        imputed = team.get("imputed") or []
        if imputed:
            add(-12, "%s is missing %s, so league average stood in and the "
                     "rating is softer than it looks"
                     % (team["school"], " and ".join(imputed[:2])))

    # ---- does the matchup profile actually back it
    if drivers:
        add(min(9, 3 * len(drivers)),
            "%d matchup advantage%s behind the flagged side"
            % (len(drivers), "" if len(drivers) == 1 else "s"))
    else:
        add(-9, "Nothing in the matchup profile supports it, the gap is coming "
                "from the overall rating alone")

    # ---- stylistic mismatch
    if style_notes:
        add(min(8, 4 * len(style_notes)),
            "Style mismatch: %s" % style_notes[0])

    # ---- early season
    if edge.get("early_season"):
        # Worth zero on purpose. The backtest has nothing to say about plays
        # this early, so any penalty would be a number I made up, and a made-up
        # penalty is indistinguishable from a measured one once it is inside a
        # score. The bet log splits these out instead.
        add(0, "Early season, before the week the backtest starts grading. "
               "Ratings lean on preseason projection and there is no evidence "
               "either way, so this scores nothing and is tracked separately "
               "in the bet log", always=True)

    # ---- market structure
    if game.get("slate") != "power":
        add(-4, "Group of Five game, so a thinner market on both sides")
    if not game.get("conference_game"):
        add(-5, "Non-conference, so no mandatory availability report")
    if game.get("neutral"):
        add(-3, "Neutral site, the fitted home edge does not apply")
    if game.get("review"):
        add(-15, "This game tripped a sanity check, treat the number as suspect")

    # ---- injuries, when the feed is on
    injury_note = _injury_component(game, edge)
    if injury_note:
        add(injury_note[0], injury_note[1])

    score = BASE + sum(part["points"] for part in parts)
    score = max(1, min(99, score))

    # An A grade is a claim that the tool is confident, and confidence without
    # a measurement behind it is just assertion. Unverified gaps cannot grade A.
    if unverified and score > UNVERIFIED_CEILING:
        parts.append({
            "points": UNVERIFIED_CEILING - score,
            "reason": "Capped below grade A because the gap size has no "
                      "historical record behind it. The tool is not allowed to "
                      "be its most confident on evidence it does not have",
        })
        score = UNVERIFIED_CEILING

    grade, label = _tier(score)

    return {
        "score": score,
        "grade": grade,
        "label": label,
        "parts": sorted(parts, key=lambda part: -abs(part["points"])),
    }


def _moving_toward_us(edge, delta):
    """True when the line is drifting toward the model, shrinking the edge."""
    if delta is None or abs(delta) < 0.25:
        return None

    if edge["market"] == "spread":
        # fair below posted means we back the home side, and the edge shrinks
        # as the posted number falls toward it.
        backing_home = edge["side"] == "home"
        return delta < 0 if backing_home else delta > 0

    if edge["market"] == "total":
        return delta > 0 if edge["side"] == "over" else delta < 0

    return None


def _injury_component(game, edge):
    """Reported availability only counts when it points at the faded side."""
    injuries = game.get("injuries") or {}
    backing_side = edge.get("side")
    if backing_side not in ("home", "away"):
        return None

    fading = "away" if backing_side == "home" else "home"
    summary = injuries.get(fading + "_summary")
    own = injuries.get(backing_side + "_summary")

    if summary and summary.get("out", 0) >= 2:
        return (6, "%d players reported out for the side being faded"
                   % summary["out"])
    if own and own.get("out", 0) >= 2:
        return (-8, "%d players reported out for the side being backed"
                    % own["out"])
    return None


def summarise_board(rows):
    """Counts per tier, for the header."""
    counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    for row in rows:
        for edge in row.get("edges", []):
            grade = (edge.get("confidence") or {}).get("grade")
            if grade in counts:
                counts[grade] += 1
    return counts
