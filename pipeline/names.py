"""Reconcile team names between CFBD and The Odds API.

CFBD calls a team "Ole Miss". The Odds API calls it "Ole Miss Rebels". Most
pairs resolve by stripping the mascot, but a stubborn handful never will, so
those live in ALIASES and anything that still fails is reported by name at
build time instead of silently dropping off the board.
"""

import re

# Odds API name on the left, CFBD school on the right. Only for pairs that
# token matching gets wrong or cannot see.
ALIASES = {
    "ole miss rebels": "Ole Miss",
    "miami hurricanes": "Miami",
    "miami (oh) redhawks": "Miami (OH)",
    "miami redhawks": "Miami (OH)",
    "southern miss golden eagles": "Southern Mississippi",
    "louisiana ragin cajuns": "Louisiana",
    "louisiana ragin' cajuns": "Louisiana",
    "louisiana monroe warhawks": "Louisiana Monroe",
    "ul monroe warhawks": "Louisiana Monroe",
    "texas am aggies": "Texas A&M",
    "texas a&m aggies": "Texas A&M",
    "florida international golden panthers": "Florida International",
    "fiu panthers": "Florida International",
    "fau owls": "Florida Atlantic",
    "utsa roadrunners": "UT San Antonio",
    "utep miners": "UTEP",
    "unlv rebels": "UNLV",
    "ucf knights": "UCF",
    "smu mustangs": "SMU",
    "tcu horned frogs": "TCU",
    "byu cougars": "BYU",
    "lsu tigers": "LSU",
    "usc trojans": "USC",
    "ucla bruins": "UCLA",
    "nc state wolfpack": "NC State",
    "north carolina state wolfpack": "NC State",
    "pitt panthers": "Pittsburgh",
    "penn state nittany lions": "Penn State",
    "san jose state spartans": "San José State",
    "hawaii rainbow warriors": "Hawai'i",
    "hawai'i rainbow warriors": "Hawai'i",
    "app state mountaineers": "Appalachian State",
    "appalachian state mountaineers": "Appalachian State",
    "sam houston bearkats": "Sam Houston",
    "jacksonville state gamecocks": "Jacksonville State",
    "middle tennessee blue raiders": "Middle Tennessee",
    "western kentucky hilltoppers": "Western Kentucky",
    "massachusetts minutemen": "UMass",
    "umass minutemen": "UMass",
    "connecticut huskies": "UConn",
    "uconn huskies": "UConn",
    "army black knights": "Army",
    "navy midshipmen": "Navy",
    "air force falcons": "Air Force",
}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")


def normalise(name):
    if not name:
        return ""
    text = name.lower()
    text = text.replace("&", "and")
    text = text.replace("'", "").replace("\u2019", "")
    text = text.replace("é", "e").replace("ʻ", "")
    text = _PUNCT.sub(" ", text)
    text = text.replace(" st ", " state ")
    if text.endswith(" st"):
        text = text[:-3] + " state"
    return _SPACE.sub(" ", text).strip()


def build_index(cfbd_teams):
    """Map normalised CFBD school name to the school name as CFBD spells it."""
    index = {}
    for team in cfbd_teams:
        school = team.get("school")
        if not school:
            continue
        index[normalise(school)] = school
    return index


def resolve(odds_name, index):
    """Return the CFBD school for an Odds API team name, or None."""
    norm = normalise(odds_name)
    if not norm:
        return None

    if norm in ALIASES:
        return ALIASES[norm]

    # Exact hit, which happens when the feed omits the mascot.
    if norm in index:
        return index[norm]

    # The mascot sits at the end, so the school is a leading prefix. Take the
    # longest prefix that is a real school to avoid matching "Miami" when the
    # feed means "Miami (OH)".
    best = None
    for candidate_norm, school in index.items():
        if norm.startswith(candidate_norm + " ") or norm == candidate_norm:
            if best is None or len(candidate_norm) > len(normalise(best)):
                best = school
    if best:
        return best

    # Deliberately no fuzzy fallback. Token-overlap matching was attaching FCS
    # opponents to whatever FBS school looked closest, so North Dakota State
    # became a rated team and Alabama A&M inherited Alabama's rating and showed
    # up as Alabama playing twice on the same afternoon. A missed match costs
    # one game off the board. A wrong match puts a fictional number in front of
    # you and grades it. Unmatched names are reported at build time so real
    # gaps can be fixed with an alias.
    return None
