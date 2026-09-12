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

## Does any of this work

The Method tab answers this with numbers rather than assurances.

The build walks forward through last season week by week. To grade week W, team
ratings are fitted using only games played in weeks 1 to W-1 of that same
season. No future result enters the prediction, and no cross-season inference is
made at all, so roster turnover between years is simply not part of the method.

An earlier version of this used season N-1 ratings to predict season N games.
That avoided lookahead but measured a model nobody runs, because a year-old
rating describes a roster that has partly graduated and partly transferred. It
has been replaced.

One limit worth knowing: ratings in the backtest are solved from margins alone,
which is simpler than the SP+ the live board uses. It remains a proxy, and a
mildly pessimistic one, but it is built the same structural way.

The backtest also produces the model's error out of sample, and that figure now
drives the board. Fitting error against the same games the ratings were built
from gives a flatteringly small number, because current-season ratings already
contain those results. Using it would inflate every cover probability shown. The
Method tab prints both so the difference is visible.

Where a gap bucket has at least 60 decided games, its measured hit rate replaces
the noise estimate inside the confidence score, and the play's breakdown says
so. Where it does not, confidence falls back to reasoning about the gap against
the model error and says that instead.

Break-even at -110 is 52.38%. If the 2 to 3 point bucket comes back under that,
your threshold is not producing edges and the Method tab will show it plainly.

## Parlays

Two decisions worth knowing before you use that tab.

Legs never come from the same game. Same-game legs are correlated, and
multiplying their probabilities as if they were independent overstates the odds
of hitting. FanDuel prices that correlation in; this tool cannot see their
pricing, so it does not pretend to.

No expected value is quoted. The free feed does not carry FanDuel's parlay
prices, and assuming a hold percentage would produce an authoritative-looking
number built on a guess. Instead each parlay shows the price it needs to break
even. Pull the same parlay up in the app and compare. If FanDuel's price is
shorter than the break-even, it is a pass, and no modelling is needed to see it.

Parlays are ranked by average leg confidence, not by payout. Ranking by payout
puts the worst bets at the top.

## Team profiles

The Teams tab describes how each team actually plays: tempo, run or pass lean,
whether the offence grinds or hits chunks, whether the defence creates havoc or
leaks big plays, and how it holds up against the run.

No API sells these descriptions. They are derived from CFBD's advanced stats,
which carry every ingredient: run and pass rates, success rate, explosiveness,
havoc, line yards, stuff rate and pace. Deriving them means they rebuild on
every run and stay current as of the last game played, rather than being a
preseason impression nobody went back to correct in November.

Everything is shown as a percentile against the FBS field, so a profile reads
the same in week 3 as in week 12 even as raw numbers drift. Run rate and tempo
are descriptive, not good or bad.

Where a genuine style mismatch exists between two teams, it appears in the
play's writeup and adds to confidence. Only real mismatches count. A decent
offence against a mediocre defence is not a style angle, it is a ratings
difference the model already counted.

## Testing

`python selftest.py` runs the whole pipeline on a synthetic 850-game season and
writes `docs/preview-sample-data.html`.

`node uitest.js` then loads that file in a real DOM and asserts on every view,
tab, filter, and interaction, including that no parlay contains two legs from
the same game. Needs `npm install jsdom` first.

Neither proves the live endpoints return the field names assumed here. The
first real run is the test for that.

## Closing line value, captured automatically

Every build snapshots every posted number, and freezes a game's last number
before kickoff into a store that persists after the game drops off the board.
So the closing line is already there when you open the bet log. Rows marked
auto need nothing from you; you can still type over one if you disagree.

This is the only measurement that gives a usable read inside a single season.
Over a large sample, bettors with positive CLV are profitable and bettors with
negative CLV are not, while win rate says very little either way until the
sample is far bigger than one season of your betting.

The log splits CLV six ways: graded A or B against C or D, openers against
settled lines, and plays that cleared 3 points against plays that only cleared
2. Each row is a question this tool cannot answer for you. If graded A plays do
not beat graded C plays, the confidence weights are wrong and should change.
Treat any bucket under about 100 bets as unreadable.

## What is deliberately not scored

Two things are flagged and tracked but carry zero weight in confidence.

Openers. Betting early into a soft number is the most repeated advice in the
literature, and it is completely untested here. Baking it into the weights
would mean assuming the answer. It is marked on every play, stored on every
logged bet, and split out in the CLV table instead.

The confidence grade itself. It is recorded on each bet so that its own
predictive value can be checked at the end of the season rather than assumed
during it.

## Running it

Manual only. Nothing fires on a timer, so no credits are spent unless you press
the button: Actions, Build board, Run workflow. Each run costs 3 of the 500
monthly odds credits.

One consequence to know about. Closing lines are captured from whatever the
last build saw before kickoff. If your last run was Tuesday and the game is on
Saturday, the captured number is three days old and is not a close. Those are
flagged stale, shown as such in the bet log, and deliberately left out of every
CLV figure, because a confident number resting on a stale price is worse than
no number.

Three ways to deal with that, in order of effort:

- Run a build on Saturday before kickoff. One press, 3 credits.
- Type the closing numbers in yourself. A manually entered number always
  overrides the automatic one.
- Uncomment the schedule block in `.github/workflows/build.yml`. Four Saturday
  runs a week, 12 credits a month, and you never think about it again.

Worth weighing against the point of the exercise: CLV is the measurement that
tells you whether any of this works, and it is the one thing a manual schedule
degrades.

## Confidence grades

Every play carries a confidence score from 1 to 100 and a grade from A to D,
and the Plays tab is sorted by it.

Confidence is not the chance of winning. That is the cover probability and it
is already shown. Confidence answers a different question: how much does the
tool trust its own number on this game. The two can point opposite ways, and
that is the point of having both. A play can carry a fat expected value
precisely because the model is working from bad inputs.

Scoring starts at 50 and moves on named components, all of them visible in the
writeup:

- how large the gap is relative to the model's own error
- whether it clears 3 points or only 2
- how much of the measured margin distribution sits between the posted number
  and the fair one, within the same spread range
- which way the line has moved since the board first saw it
- whether either team's rating relied on substituted league-average data
- whether the matchup profile actually supports the side, or the gap is coming
  from the overall rating alone
- market structure: G5, non-conference, neutral site, failed sanity check
- reported injuries, when the optional feed is switched on

The line movement component is the one raw expected value cannot see. A gap
sitting untouched at 3 points for four days is a different bet from one that
opened level and has been moving against you all week. The board stores a
snapshot of every posted number on each run and compares against the first one
it saw, so movement builds up over the first day or two of a game being listed.

## Why this play

Every flagged play has a "Why this play" button that opens four sections: how
the fair number was built from the ratings and the fitted home edge, which
matchup ranks support the flagged side, the probability and expected value
maths with the numbers shown, and a case against.

The case against is the part worth reading. It names when the gap is inside the
model's own error, when a team's rating leaned on substituted league-average
data, when the play clears 2 points but not 3, and when there is no mandatory
injury report for that game. If nothing in the matchup profile supports the
play, it says so outright rather than padding the list.

Supporting points are only listed when they are real advantages. A rank 6
offence against a rank 4 defence is not a point in favour and will not appear.

## Injuries

The board has a note field on every game that persists in your browser. Power 4
conferences now publish official availability reports starting three days before
kickoff, so for a Saturday game you have real status by Wednesday night. The
note box links out to the aggregators. G5 and non-conference games have no
official reporting, which is what the manual panel is for.

### Optional automated feed

CollegeFootballData has no injury endpoint. If you want injuries pulled in
automatically, add a third repo secret named `BDL_API_KEY` with a BALLDONTLIE
key and the build will fetch them and show reported availability inside each
writeup. Without that secret the build skips it silently and nothing changes.

Their NCAAF injuries sit behind a paid tier, roughly 10 dollars a month. Before
paying, know what it buys: these providers compile the same conference
availability reports and beat reporting you can read yourself. It is automation
and completeness, not earlier or better information, and G5 coverage is thin
wherever the underlying reporting is thin.

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
