# Page contract — engine (src/build.py) ↔ app shell (templates/page.html)

Two sides, one seam. `src/build.py` renders **fragments** (plain semantic HTML using only the
classes below) and string-replaces them into `templates/page.html`. `templates/page.html` owns
**all** styling, layout, the tab bar, the app-install tags, and the tiny tab-switching script.
Neither side reaches into the other: build.py never emits `style=` attributes (one exception:
the stop-loss bar width, below) or `<script>`; the template never assumes content that isn't in
this file. If a new element is needed, add it here first.

## Tokens (all required; build.py always replaces every one)

| Token | Content |
|---|---|
| `{{TODAY}}` | Today panel fragment |
| `{{WEEKEND}}` | Weekend panel fragment |
| `{{LEGS}}` | Legs panel fragment |
| `{{RECORD}}` | Record panel fragment |
| `{{UPDATED_AT}}` | plain text, e.g. `Updated Sun 9:04 AM ET` |
| `{{NEXT_UPDATE}}` | plain text, e.g. `Next card: Mon ~9 AM` |
| `{{SAMPLE_BANNER}}` | `""` or `<div class="sample-banner">…</div>` |

The template wraps them: `<section class="panel" id="panel-today">{{TODAY}}</section>` (same for
weekend/legs/record), header shows UPDATED_AT/NEXT_UPDATE, bottom `<nav class="tabbar">` links
`#today #weekend #legs #record`. Panel ids are deliberately `panel-*`, never the bare tab name:
an element whose id matches the URL hash makes the browser anchor-jump on launch (start_url is
`./#today`), sliding the card under the sticky header. Fragments must never emit `id="today"`,
`id="weekend"`, `id="legs"`, or `id="record"` either.

## Shared pieces

```html
<!-- slot header (Today, and each Weekend slot) -->
<header class="slot-head">
  <h2 class="slot-title">Sunday · Sep 27</h2>
  <p class="slot-sub">NFL Week 3 · 14 games</p>
</header>

<!-- budget line for a slot -->
<div class="budget">
  <span class="budget-amount">$0.55</span>
  <span class="budget-label">to play this slot</span>
  <p class="budget-why">Down $3.50 this weekend → $1.50 left of the $5 stop-loss across 2 slots, holding 25% back.</p>
</div>

<!-- result badge (anywhere a bet has a result) -->
<span class="result-badge won">Won +$0.91</span>   <!-- won | lost | push | void | open -->

<!-- positive/negative money or edge -->
<span class="money positive">+$0.91</span> <span class="money negative">-$2.25</span>
<span class="edge positive">+4.2%</span> <span class="edge negative">-1.3%</span>

<!-- empty state -->
<div class="empty-state">
  <p class="empty-title">No card yet today</p>
  <p class="empty-sub">Next card: Sunday · Sep 27, around 9 AM</p>
</div>
```

## A card bet (Today panel; tiers in order easy → fun → lottery)

```html
<article class="bet tier-easy">                  <!-- tier-easy | tier-fun | tier-lottery -->
  <div class="bet-top">
    <span class="tier-badge">1 · Easy Bet</span>  <!-- "2 · Fun Parlay", "3 · Lottery Ticket" -->
    <span class="result-badge open">Open</span>   <!-- present only once published; won/lost after grading -->
  </div>
  <div class="bet-head">
    <h3 class="bet-title">Drake London 5+ receptions</h3>   <!-- parlays: "2-leg parlay" -->
    <span class="odds">-150</span>
  </div>
  <p class="bet-meta">
    <span class="stake">$0.35</span>
    <span class="game">ATL @ CAR</span>
    <span class="kickoff">Sun 1:00 PM</span>
  </p>
  <div class="prob-row">                          <!-- tiers 1–2 -->
    <span class="reconstructed-tag">Reconstructed</span>  <!-- historical_import slots only -->
    <span class="prob"><span class="k">Implied</span> <b>60.0%</b></span>
    <span class="prob"><span class="k">Est.</span> <b>63.5%</b></span>
    <span class="prob"><span class="k">Edge</span> <b class="edge positive">+3.5%</b></span>
  </div>
  <div class="prob-row lottery-odds">             <!-- tier 3 instead of the above -->
    <span class="reconstructed-tag">Reconstructed</span>  <!-- historical_import slots only -->
    <span class="prob"><span class="k">Payout-implied</span> <b>0.7%</b></span>
    <span class="prob"><span class="k">Real chance</span> <b>0.1%</b></span>
    <span class="prob one-in-x"><b>~1 in 1,024</b></span>
  </div>
  <ul class="leg-list">                           <!-- parlays only -->
    <li><span class="leg-sel">Bijan Robinson 60+ rush yds</span> <span class="leg-prob">68%</span></li>
    <li><span class="leg-sel">…</span> <span class="leg-prob reconstructed">68%</span></li>  <!-- historical_import slots: same leg-prob, "reconstructed" modifier added -->
  </ul>
  <p class="reason-summary">One line Gus scans.</p>
  <details class="reason-detail">
    <summary>Why &amp; sources</summary>
    <p class="reason">Full reasoning…</p>
    <p class="prob-source">Kalshi 64% + numberFire 63% = 63.5%</p>
    <p class="verify">Source · fetched · confirm price in the DK app</p>
  </details>
</article>
```

After the card on Today, optional collapsibles (each only if it has content):

```html
<details class="more angles"><summary>Angles</summary><ul><li>ATL is 0-2 — desperation spot</li></ul></details>
<details class="more avoid"><summary>Skip these</summary>
  <div class="avoid-item"><p class="avoid-sel">…</p><p class="avoid-why">…</p></div>
</details>
<details class="more boosts"><summary>Boosts</summary>
  <div class="boost-item no-fit"><p class="boost-verdict">No fit</p><p class="boost-desc">…</p></div>  <!-- fits | no-fit -->
</details>
```

## Weekend panel

```html
<div class="stoploss">
  <div class="stoploss-bar"><div class="stoploss-used" style="width:70%"></div></div>  <!-- only allowed style= -->
  <p class="stoploss-text">$3.50 of the $5 stop-loss used · $1.50 left</p>
</div>
<div class="stat-grid">…stat tiles (see Record)…</div>
<section class="slot-row graded">                <!-- graded | published | future | skipped -->
  <div class="slot-row-head">
    <span class="slot-name">Thursday</span> <span class="slot-date">Sep 24</span>
    <span class="slot-status">Graded</span>
  </div>
  <ul class="slot-bets">
    <li class="slot-bet">
      <span class="slot-bet-tier">Easy</span>
      <span class="slot-bet-sel">MarShawn Lloyd 25+ rush+rec yds</span>
      <span class="slot-bet-line">$2.25 · -216</span>
      <span class="result-badge lost">Lost -$2.25</span>
    </li>
  </ul>
</section>
```

## Legs panel

```html
<section class="leg-group">
  <h3 class="leg-group-title">ATL @ CAR <span class="kickoff">Sun 1:00 PM</span></h3>
  <div class="leg">
    <div class="leg-head"><span class="leg-sel">Drake London 5+ rec</span><span class="odds">-150</span></div>
    <p class="leg-meta">
      <span class="market">prop</span> <span class="prop-tag">Player prop</span>
      <span class="prob"><span class="k">Impl</span> 60.0%</span>
      <span class="prob"><span class="k">Est</span> 63.5%</span>
      <span class="edge positive">+3.5%</span>
    </p>
    <p class="leg-reason">One line.</p>
  </div>
</section>
```

## Record panel

```html
<div class="stat-grid">
  <div class="stat"><span class="label">Record</span><span class="value">0W-10L</span></div>
  <div class="stat"><span class="label">Cash P/L</span><span class="value negative">-$13.00</span></div>
  <div class="stat"><span class="label">Bankroll left</span><span class="value positive">$37.00</span></div>
  <div class="stat"><span class="label">Streak</span><span class="value">L10</span></div>
</div>
<p class="alltime-line">All-time since Mar 2026: …</p>
<section class="card-record"><h3>Card record</h3><p class="card-record-note">If every card bet was placed as written</p>…stat-grid…</section>
<section class="last-weekend"><h3>Last weekend</h3>…slot-row blocks as in Weekend…</section>
<p class="record-note">…e.g. "9/14 and 9/17 wins not logged yet"…</p>
```

Headings inside panels use `h2` for the panel/slot title and `h3` below it. Money is always
formatted `$0.00` with a sign where it's a result. Times are Eastern, e.g. `Sun 1:00 PM`.


## 2026-09-26 plain-language + glass redesign

The template also owns a fixed `<div class="aurora">` backdrop and a script that saves the
Today checklist's ticks to localStorage (key `plan:<data-key>`, per phone only). New fragment
pieces build.py emits (all styled by the template):

- Today plan: `section.plan.glass > .eyebrow, h2.plan-title, ul.checklist > li > label.check >
  input[type=checkbox][data-key] + span.box + span.check-body > .check-where, .check-text,
  .check-lines > span, .check-sub`
- Bet card face: `.bet-title` (plain words), `.bet-money`, `.bet-chance > .value-tag.good|fair|bad|long`,
  `.bet-when`, `.reason-summary` (the bet's `plain_summary`); the odds math lives inside
  `details.reason-detail` ("Why & the numbers"). Fun Parlay #2 cards carry `tier-fun2`.
- Legs: each `details.leg[data-cat] > summary > .leg-head, .leg-game, .leg-line` + `.leg-more`;
  `.chip` replaces the old prop/book/source tags.
- Lineup: `section.league.glass > .eyebrow, h2.league-team, .verdict.good|fair|bad
  (.verdict-word, .verdict-vs, .verdict-small), .todo-box[.soft|.done] (.box-title, ul, .why),
  .list-title, ul.roster > li > details.player > summary (.slot, .pname, .pmeta, .chip, .ppts) +
  .player-more (.player-note, .tiny)`, `details.more > .more-body`.
