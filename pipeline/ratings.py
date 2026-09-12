"""Power ratings, calibration, and edge scoring.

The design choice worth knowing: nothing here assumes a sign convention or a
scale for SP+. The coefficients are fitted by least squares against completed
games, so if CFBD flips how a rating is expressed the fit absorbs it. The same
fit hands back a residual standard deviation, which is what turns a raw points
gap into a cover probability and an expected value rather than a vibe.
"""

import math
from statistics import NormalDist

import numpy as np

from . import config

# Features used to predict margin and total. Kept explicit so the UI can show
# what went into a number.
MARGIN_FEATURES = ["sp_home", "sp_away", "neutral"]
TOTAL_FEATURES = [
    "off_home", "def_home", "off_away", "def_away", "pace_home", "pace_away",
]


def phi(x):
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def phi_inv(p):
    """Standard normal inverse CDF."""
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    return NormalDist().inv_cdf(p)


def implied_margin(win_prob, sigma):
    """The margin a two-way price is quoting, given the model's own spread."""
    z = phi_inv(win_prob)
    if z is None or not sigma:
        return None
    return z * sigma


# ---------------------------------------------------------------- team table


def build_team_table(teams, sp, advanced, returning):
    """One row per team holding everything the model and the UI need."""
    table = {}

    for team in teams:
        school = team.get("school")
        if not school:
            continue
        table[school] = {
            "school": school,
            "conference": team.get("conference"),
            "logo": (team.get("logos") or [None])[0],
            "sp": None, "sp_off": None, "sp_def": None, "sp_rank": None,
            "off_ppa": None, "def_ppa": None,
            "off_success": None, "def_success": None,
            "off_explosive": None, "def_explosive": None,
            "def_havoc": None,
            "plays": None, "drives": None, "pace": None,
            "returning_off": None, "returning_def": None,
        }

    for row in sp or []:
        school = row.get("team")
        if school not in table:
            continue
        offense = row.get("offense") or {}
        defense = row.get("defense") or {}
        table[school].update({
            "sp": _num(row.get("rating")),
            "sp_rank": row.get("ranking"),
            "sp_off": _num(offense.get("rating")),
            "sp_def": _num(defense.get("rating")),
        })

    for row in advanced or []:
        school = row.get("team")
        if school not in table:
            continue
        offense = row.get("offense") or {}
        defense = row.get("defense") or {}
        plays = _num(offense.get("plays"))
        drives = _num(offense.get("drives"))
        table[school].update({
            "off_ppa": _num(offense.get("ppa")),
            "def_ppa": _num(defense.get("ppa")),
            "off_success": _num(offense.get("successRate")),
            "def_success": _num(defense.get("successRate")),
            "off_explosive": _num(offense.get("explosiveness")),
            "def_explosive": _num(defense.get("explosiveness")),
            "def_havoc": _num((defense.get("havoc") or {}).get("total")),
            "plays": plays,
            "drives": drives,
            "pace": (plays / drives) if plays and drives else None,
        })

    for row in returning or []:
        school = row.get("team")
        if school not in table:
            continue
        # CFBD has shipped this endpoint under more than one field naming, so
        # take whichever is present rather than assuming.
        table[school]["returning_off"] = _num(
            row.get("offense")
            if not isinstance(row.get("offense"), dict)
            else (row.get("offense") or {}).get("percentPPA")
        )
        if table[school]["returning_off"] is None:
            table[school]["returning_off"] = _num(row.get("percentPPA"))
        table[school]["returning_def"] = _num(
            row.get("defense")
            if not isinstance(row.get("defense"), dict)
            else (row.get("defense") or {}).get("percentPPA")
        )

    _impute(table)
    return table


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _impute(table):
    """Fill gaps with the league mean so one missing stat cannot drop a game."""
    fields = ["sp", "sp_off", "sp_def", "off_ppa", "def_ppa", "pace",
              "off_success", "def_success", "off_explosive", "def_explosive"]
    for field in fields:
        values = [row[field] for row in table.values() if row.get(field) is not None]
        if not values:
            continue
        mean = sum(values) / len(values)
        for row in table.values():
            if row.get(field) is None:
                row[field] = mean
                row.setdefault("imputed", []).append(field)


# ---------------------------------------------------------------- features


def margin_row(home, away, neutral):
    return [home.get("sp") or 0.0, away.get("sp") or 0.0, 1.0 if neutral else 0.0]


def total_row(home, away):
    return [
        home.get("sp_off") or 0.0,
        home.get("sp_def") or 0.0,
        away.get("sp_off") or 0.0,
        away.get("sp_def") or 0.0,
        home.get("pace") or 0.0,
        away.get("pace") or 0.0,
    ]


# ---------------------------------------------------------------- calibration


def _fit(rows, targets):
    """Least squares with an intercept. Returns coefficients and residual sd."""
    if len(rows) < 40:
        return None, None, 0
    design = np.column_stack([np.ones(len(rows)), np.array(rows, dtype=float)])
    target = np.array(targets, dtype=float)
    coeffs, *_ = np.linalg.lstsq(design, target, rcond=None)
    residuals = target - design.dot(coeffs)
    # Degrees of freedom adjusted, guarding against a tiny sample.
    dof = max(len(rows) - design.shape[1], 1)
    sigma = float(np.sqrt(float(np.sum(residuals ** 2)) / dof))
    return coeffs, sigma, len(rows)


def calibrate(samples):
    """samples: list of dicts with margin_row, total_row, margin, total."""
    margin_rows, margin_targets = [], []
    total_rows, total_targets = [], []

    for sample in samples:
        if sample.get("margin") is not None:
            margin_rows.append(sample["margin_row"])
            margin_targets.append(sample["margin"])
        if sample.get("total") is not None:
            total_rows.append(sample["total_row"])
            total_targets.append(sample["total"])

    margin_coeffs, margin_sigma, margin_n = _fit(margin_rows, margin_targets)
    total_coeffs, total_sigma, total_n = _fit(total_rows, total_targets)

    return {
        "margin_coeffs": margin_coeffs,
        "margin_sigma": margin_sigma,
        "margin_n": margin_n,
        "total_coeffs": total_coeffs,
        "total_sigma": total_sigma,
        "total_n": total_n,
    }


def predict(coeffs, row):
    if coeffs is None:
        return None
    return float(coeffs[0] + float(np.dot(coeffs[1:], np.array(row, dtype=float))))


# ---------------------------------------------------------------- scoring


def cover_probability(predicted_margin, line, sigma):
    """P(home covers) where line is the home spread, negative meaning favoured.

    Home covers when actual margin exceeds the negated line. A half point band
    around whole numbers is treated as a push and split out, because on a key
    number the push share is most of what the bet is.
    """
    if predicted_margin is None or line is None or not sigma:
        return None, None, None

    threshold = -float(line)
    is_whole = abs(threshold - round(threshold)) < 1e-6

    if is_whole:
        lower = (threshold - 0.5 - predicted_margin) / sigma
        upper = (threshold + 0.5 - predicted_margin) / sigma
        push = phi(upper) - phi(lower)
        home = 1.0 - phi(upper)
        away = phi(lower)
    else:
        home = 1.0 - phi((threshold - predicted_margin) / sigma)
        away = 1.0 - home
        push = 0.0

    return home, away, push


def over_probability(predicted_total, line, sigma):
    if predicted_total is None or line is None or not sigma:
        return None, None, None
    line = float(line)
    is_whole = abs(line - round(line)) < 1e-6
    if is_whole:
        lower = (line - 0.5 - predicted_total) / sigma
        upper = (line + 0.5 - predicted_total) / sigma
        push = phi(upper) - phi(lower)
        over = 1.0 - phi(upper)
        under = phi(lower)
    else:
        over = 1.0 - phi((line - predicted_total) / sigma)
        under = 1.0 - over
        push = 0.0
    return over, under, push


def decimal_payout(american):
    """Profit per unit staked."""
    if american is None:
        return None
    american = float(american)
    if american < 0:
        return 100.0 / (-american)
    return american / 100.0


def expected_value(win_prob, push_prob, american):
    """EV per unit staked, pushes returned rather than lost."""
    payout = decimal_payout(american)
    if win_prob is None or payout is None:
        return None
    push_prob = push_prob or 0.0
    lose_prob = max(0.0, 1.0 - win_prob - push_prob)
    return win_prob * payout - lose_prob


def key_number_crossings(fair, posted):
    """Key numbers the fair value sits across from the posted line."""
    if fair is None or posted is None:
        return []
    low, high = sorted([abs(float(fair)), abs(float(posted))])
    return [k for k in config.KEY_NUMBERS if low < k < high]
