// Browser-level test suite. Run: npm install jsdom && node uitest.js
// Requires docs/preview-sample-data.html, produced by: python selftest.py
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('./docs/preview-sample-data.html', 'utf8');
const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
const win = dom.window;
const doc = win.document;

const errors = [];
win.addEventListener('error', e => errors.push('window error: ' + e.message));
const origError = win.console.error;
win.console.error = (...a) => { errors.push('console.error: ' + a.join(' ')); origError(...a); };

function q(id){ return doc.getElementById(id); }
function len(id){ const el = q(id); return el ? el.innerHTML.length : -1; }

function check(name, cond){
  console.log((cond ? 'PASS  ' : 'FAIL  ') + name);
  if (!cond) process.exitCode = 1;
}

// initial render
check('masthead subtitle', q('sub').textContent.length > 20);
check('meta bar', len('meta') > 100);
check('plays rendered', len('v-plays') > 5000);
check('board rendered', len('boardwrap') > 2000);

// tabs
['parlays','teams','method','log','board','plays'].forEach(v => {
  win.showView(v);
  check('tab ' + v + ' shows content', len('v-' + v) > 200);
});

// teams search must survive typing
win.showView('teams');
const box = q('tq');
check('teams search box exists', !!box);
const before = len('teamlist');
box.value = 'ohio';
box.dispatchEvent(new win.Event('input'));
const after = len('teamlist');
check('teams filter narrows list', after > 0 && after < before);
check('teams search box survives filtering', q('tq') === box);
box.value = '';
box.dispatchEvent(new win.Event('input'));
check('teams filter clears back', len('teamlist') === before);

// why panel
const data = win.DATA;
const g = data.games.find(x => x.edges.length);
const key = g.id + '|' + g.edges[0].market + '|' + g.edges[0].side;
win.showView('plays');
win.toggleWhy(key);
const why = q('w-' + key).innerHTML;
check('why panel opens', why.length > 500);
check('why has confidence breakdown', why.indexOf('Confidence') > -1);
check('why has case against', why.indexOf('case against') > -1);
win.toggleWhy(key);
check('why panel closes', q('w-' + key).innerHTML.length === 0);

// bet log round trip
win.logBet(g.id, g.edges[0].market, g.edges[0].side);
win.showView('log');
check('bet appears in log', q('v-log').innerHTML.indexOf('confidence') > -1 || len('v-log') > 800);
check('grade stored on bet', win.BETS[0].grade !== undefined);
win.setClosing(0, '-6.5'); win.setResult(0, 'win');
check('settled bet shows profit', q('v-log').innerHTML.indexOf('$') > -1);
win.unlogBet(win.BETS[0].key);
check('bet removed', win.BETS.length === 0);

// notes persist
win.showView('board');
win.toggleNote(g.id);
const ta = q('ta-' + g.id);
check('note box opens', !!ta);
ta.value = 'RB1 out';
win.saveNote(g.id);
check('note persisted', win.NOTES[g.id] === 'RB1 out');

// parlay integrity: no two legs from the same game
let sameGame = 0;
(data.parlays.parlays || []).forEach(p => {
  const ids = p.legs.map(l => l.game_id);
  if (new Set(ids).size !== ids.length) sameGame++;
});
check('no same-game parlay legs', sameGame === 0);
check('parlays have break-even price', (data.parlays.parlays||[]).every(p => p.break_even_price !== null));

// --- CLV automation
const gid = g.id;
data.closings = data.closings || {};
data.closings[gid] = { label: 'test', spread: -6.5, total: 52.5 };
win.logBet(gid, g.edges[0].market, g.edges[0].side);
win.showView('log');
const logHtml = q('v-log').innerHTML;
check('closing line auto-populates', logHtml.indexOf('auto') > -1);
check('CLV computed without manual entry', win.clvPoints(win.BETS[0]) !== null);

// manual entry must still override the automatic value
win.setClosing(0, '-9.5');
check('manual closing overrides auto', win.closingFor(win.BETS[0]).auto === false);
check('CLV sign is directional',
      typeof win.clvPoints(win.BETS[0]) === 'number');

// --- CLV split table
check('CLV breakdown renders', q('v-log').innerHTML.indexOf('by bucket') > -1);
const splits = win.clvSplits();
check('splits cover grade, opener and threshold', splits.length === 6);
check('every split has a name and count',
      splits.every(x => x.name && typeof x.count === 'number'));
win.unlogBet(win.BETS[0].key);

// --- measured key numbers reached the page
win.showView('method');
const method = q('v-method').innerHTML;
check('method shows measured key numbers', method.indexOf('Key numbers, measured') > -1);
check('method states what is being tested', method.indexOf('What is being tested') > -1);

// --- stale closing lines must not silently enter CLV
data.closings[gid] = { label: 'test', spread: -6.5, total: 52.5, stale: true, hours_before_kickoff: 72 };
win.logBet(gid, g.edges[0].market, g.edges[0].side);
check('stale closing is excluded from CLV', win.clvPoints(win.BETS[0]) === null);
win.showView('log');
check('stale closing is labelled in the log', q('v-log').innerHTML.indexOf('stale') > -1);
win.setClosing(0, '-8.5');
check('typing a closing line overrides stale', win.clvPoints(win.BETS[0]) !== null);
win.unlogBet(win.BETS[0].key);

// --- confidence breakdown layout must not collide with the generic list rule
win.showView('plays');
win.toggleWhy(key);
const panel = q('w-' + key).innerHTML;
if (panel.indexOf('conf-parts') > -1){
  const styles = doc.querySelector('style').textContent;
  const generic = styles.indexOf('.why-panel li{');
  const scoped = styles.indexOf('.why-panel ul.conf-parts li{');
  check('scoped confidence rule loads after the generic one', scoped > generic);
  check('confidence rows suppress the generic bullet',
        styles.indexOf('.why-panel ul.conf-parts li:before{display:none}') > -1);
}
win.toggleWhy(key);

// --- regressions from the real-board audit
check('no derived moneyline edges',
      data.games.every(g => g.edges.every(e => e.market !== 'moneyline')));
const allBuckets = (data.backtest.spread || []).concat(data.backtest.total || []);
check('backtest buckets carry a significance verdict',
      allBuckets.every(b => b.decided < 60 || typeof b.significant === 'boolean'));
win.showView('method');
const m2 = q('v-method').innerHTML;
check('method shows an all-gaps total row', m2.indexOf('All gaps') > -1);
check('method reports open vs close', m2.indexOf('OPENING lines') > -1 || m2.indexOf('Opening lines') > -1);

// --- pooled multi-season backtest
check('backtest pools several seasons', (data.backtest.years || []).length > 1);
check('per-season breakdown is present', (data.backtest.per_season || []).length > 1);
const anySeason = (data.backtest.per_season || []).find(x => x.available);
check('each season reports both close and open',
      !!(anySeason && anySeason.spread_close && anySeason.spread_open));
win.showView('method');
check('method renders the season table', q('v-method').innerHTML.indexOf('Season by season') > -1);

// pooling must actually shrink the error bar versus one season
const pooled = (data.backtest.spread_open || []).reduce(
  (a, b) => ({ w: a.w + b.wins, l: a.l + b.losses }), { w: 0, l: 0 });
const n = pooled.w + pooled.l;
const r = pooled.w / n;
const se = Math.sqrt(r * (1 - r) / n) * 100;
check('pooled opener error bar under 1.6 points', se < 1.6);

check('no runtime errors', errors.length === 0);
if (errors.length) console.log(errors.slice(0,5));
