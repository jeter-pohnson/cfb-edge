# CFB Edge Board

A self-updating college football betting board. Pulls FanDuel lines and
CollegeFootballData stats, fits a power rating model against completed games,
and flags every spread, total and moneyline where the posted number is far
enough from the model to be worth a look.

Slate is Power 4 plus Notre Dame, plus Group of Five games that are televised.
Unit is 20 dollars. Flag threshold is 2 points, with a separate tag for plays
that also clear 3.

## What runs where

Nothing runs on your machine. A GitHub Action rebuilds the board three times a
day Tuesday through Saturday and publishes it to GitHub Pages. You get one
bookmark that is always current. Your API keys live as encrypted repo secrets
and never appear in the published file.

## Setup

1. Create a new **public** repo on GitHub and put these files in it, keeping
   the folder structure.

   Public is required, not a preference: on a free GitHub account, Pages only
   publishes from a public repo. Nothing sensitive is exposed by this. Your two
   API keys live in encrypted repo secrets and never appear in the code or the
   published page, and your notes and bet log stay in your own browser and are
   never uploaded anywhere. What is public is the model code and a page of
   college football lines at an unlisted URL.

   If you want the code itself private, deploy through Netlify instead. Its free
   tier builds from a private repo. The published page is still reachable by URL
   either way, on both services.

2. Add your two keys under Settings, Secrets and variables, Actions, New
   repository secret:

   - `CFBD_API_KEY`
   - `ODDS_API_KEY`

3. Under Settings, Pages, set Source to **GitHub Actions**.

4. Go to the Actions tab, pick "Build board", and press Run workflow. The first
   run takes about a minute. When it finishes, Settings, Pages shows your URL.

Rotate both keys at the end of the season, since they were pasted into a chat.

A repo secret is encrypted and is not visible to anyone browsing the repo, including
in a public one. Never paste a key into a file instead.

## Running it by hand

```
cp .env.example .env        # then fill in your keys
pip install -r requirements.txt
set -a; source .env; set +a
python -m pipeline.build
open docs/index.html
```

## Cost

Both APIs stay on their free tiers.

The Odds API bills markets multiplied by regions. We pull three markets from a
single bookmaker, so one full board costs 3 credits of the 500 a month. Fifteen
runs a week is about 195 credits. The board shows the remaining credits in the
header so you never find out by hitting the wall.

CFBD allows roughly 1000 calls a month. A run uses about six, and the prior
season is cached for a year since it never changes. Call it 400 a month.

To change the cadence, edit the cron line in `.github/workflows/build.yml`.

## How the number is built

Ratings come from SP+ and CFBD advanced stats. Nothing in the model assumes a
sign convention or a scale. The coefficients are fitted by least squares against
completed games from this season and last, so if CFBD changes how a rating is
expressed the fit absorbs it rather than silently inverting your picks.

That same fit returns the residual standard deviation, which is the number that
matters most and is printed in the header. It is the model's own error on a
single game, and it will be somewhere around 13 points on margin. A 2 point gap
sits well inside that. This is why the board tags which plays also clear 3 and
tracks them separately in the log: by late October your own closing line value
data will tell you which threshold is real, instead of either of us guessing.

Cover probability comes from a normal distribution around the predicted margin,
with a half point push band on whole numbers. Expected value uses the actual
FanDuel price, not an assumed minus 110.

### Guards

Three checks suppress a play rather than print a fake edge:

- A spread more than 21 points off the model, or a total more than 24 off, goes
  to a review list. In practice that means a team has no advanced stats yet, an
  early season SP+ rating is still junk, or a name matched the wrong school.
- A moneyline is only scored when the model agrees with the market on the shape
  of the game.
- A moneyline that disagrees with FanDuel's own posted spread is treated as a
  stale price in the feed, not an edge.

## Injuries

The board has a note field on every game that persists in your browser. Power 4
conferences now publish official availability reports starting three days before
kickoff, so for a Saturday game you have real status by Wednesday night. The
note box links out to the aggregators. G5 and non-conference games have no
official reporting, which is what the manual panel is for.

## Bet log

Logging a play stores the line and price you took. After the game, enter the
number FanDuel closed at and the result. The log tracks record, profit at your
unit, return on risk, average closing line value, and a separate return figure
for the plays that cleared 3 points.

Closing line value is the one to watch. It tells you whether the edges are real
long before the win rate can.

## Known limits

- Notes and the bet log live in browser storage, so they are per device. Moving
  them to a shared store means a backend, which this deliberately avoids.
- Every game links to the FanDuel NCAAF board rather than the specific game. The
  free feed does not carry FanDuel's own event URLs, and guessing a deep link
  format would break silently.
- First half and team totals are not covered. They sit behind paid odds tiers.
- `selftest.py` runs the whole pipeline on synthetic data and proves the logic,
  the model fit and the rendering all work. It does not prove the live endpoints
  return the field names assumed here. The first real run is the test for that,
  and field name drift is the most likely thing to break.
