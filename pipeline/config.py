"""Central configuration. No secrets live here; keys come from the environment."""

import os

# ---------------------------------------------------------------- season

SEASON = int(os.environ.get("CFB_SEASON", "2026"))
CALIBRATION_SEASON = SEASON - 1

# ---------------------------------------------------------------- keys

CFBD_API_KEY = os.environ.get("CFBD_API_KEY", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# ---------------------------------------------------------------- endpoints

CFBD_BASE = "https://api.collegefootballdata.com"
ODDS_BASE = "https://api.the-odds-api.com/v4"
ODDS_SPORT = "americanfootball_ncaaf"
ODDS_BOOKMAKER = "fanduel"
ODDS_MARKETS = "h2h,spreads,totals"

# ---------------------------------------------------------------- slate

POWER_4 = {"SEC", "Big Ten", "Big 12", "ACC"}

# Independents that price like power teams and belong on the board.
ALWAYS_INCLUDE = {"Notre Dame"}

# Group of Five conferences. These are included only when the game is on a
# national or streaming network, which is our proxy for "a real market".
GROUP_OF_5 = {
    "American Athletic",
    "Mountain West",
    "Sun Belt",
    "Mid-American",
    "Conference USA",
    "Pac-12",
}

# Outlets that count as televised for the G5 filter.
TELEVISED_OUTLETS = {
    "ABC", "CBS", "CBSSN", "NBC", "FOX", "FS1", "FS2",
    "ESPN", "ESPN2", "ESPNU", "ESPNEWS", "ESPN+",
    "TNT", "TBS", "truTV", "The CW", "CW Network",
    "Peacock", "Paramount+", "Netflix", "Prime Video",
}

# ---------------------------------------------------------------- model

# Threshold, set by measurement rather than preference.
#
# Three seasons of walk-forward backtesting, 3,108 games graded, said this
# plainly. Against opening lines every gap bucket below 4 points came in at or
# below break-even: 50.2%, 51.2%, 48.7%, 50.5%. Every bucket above 4 points beat
# it: 56.4%, 54.7%, 57.9%. Pooled, the large buckets went 458-355, 56.3%, which
# is 2.2 standard errors clear of the 52.38% needed at -110.
#
# The original 2-point threshold did not survive that test at any book number.
# Neither did 3. So the board now flags at 4.
EDGE_FLAG = 4.0
EDGE_STRICT = 6.0

# Markets the board will evaluate.
#
# Totals are excluded, and that is a measurement too, not a preference. Across
# the same three seasons totals went 50.6% against closing lines and 49.4%
# against openers, with not one bucket clearing significance in either
# direction. There is no threshold that rescues them, so showing them would just
# be inviting bets the evidence says lose.
ACTIVE_MARKETS = ("spread",)

# The edge measured above exists against opening numbers and disappears by
# kickoff. That makes this a timing strategy or nothing, so the board marks how
# stale a line is by the time you are looking at it.
OPENER_EDGE_WINDOW_HOURS = 48

UNIT_DOLLARS = 20

# Above these gaps the model is almost certainly wrong rather than the market.
# In practice it means a team has no advanced stats yet, an early-season SP+
# rating is still junk, or a name matched to the wrong school. Those games go
# to a review list instead of the plays board.
MAX_SPREAD_DISAGREEMENT = 21.0
MAX_TOTAL_DISAGREEMENT = 24.0

# A moneyline edge only counts when the model broadly agrees with the market on
# the shape of the game. Without this a single bad rating prints a 99% play.
ML_SHAPE_TOLERANCE = 10.0
ML_MIN_PROB_EDGE = 0.03

# Key numbers in college football. A fair number that crosses one of these on
# the way to the posted line is worth more than the raw gap suggests.
KEY_NUMBERS = [3, 7, 10, 14, 17, 21]

# Refresh the previous-season calibration pull at most this often.
CACHE_MAX_AGE_DAYS = 7
