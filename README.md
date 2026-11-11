# kalshi-implied

Prices any range on a Kalshi stat or index from the markets Kalshi already lists on the same
number. For each range it reports a fitted fair value and the bounds around it: bounds forced by
the listed quotes alone, and, where a stale options chain has to be rolled forward, model bounds
that also rest on stated assumptions.

Two products use it.

- **NFL yardage ranges.** Kalshi lists passing yards two ways. KXNFLPASSYDS is a ladder every
  25 yards (250+, 275+, ...) with two-sided books. KXNFLPASSYDSPRECISE ("Pass Yards Precise") is
  a ladder every yard, 0+ to 601+, 602 markets per player. Ten minutes before kickoff, 2 of the
  2,047 one-yard books this repo sampled had a bid, and every ask was 99c (Results). A whole-yard
  range [a, b] is S(a - 0.5) - S(b + 0.5), where S(t) = P(yards > t); this prices it off the
  25-yard ladder. Receiving yards (KXNFLRECYDS) and rushing yards (KXNFLRSHYDS) are threshold
  ladders as well, with two-sided strikes 5 to 15 yards apart, and the same curve reads them.
- **S&P 500 close buckets.** Kalshi's rules settle KXINX on the end-of-day S&P 500 value. Cboe's
  specifications list SPXW options as P.M.-settled and trading to 16:00 ET on expiration day
  (13:00 on a half day). Vertical spreads on the SPXW options expiring that afternoon bound the
  probability of every bucket.

Python 3 standard library only (on Windows, `zoneinfo` also needs the `tzdata` package).
Read-only: it places no orders and needs no API key. Data comes from Kalshi's public API, Cboe's
delayed-quotes feed (`cdn-api.cboe.com`, 15 minutes behind) and Yahoo's chart endpoint for a live
S&P 500 print (`query1.finance.yahoo.com`, unofficial, sent with a User-Agent naming this repo).

## Method

**Forced bounds** come from bid/ask alone. A threshold book says bid <= S(t) <= ask at one
strike. A call spread over (K1, K2) says (K2 - K1) S(K2) <= C(K1) - C(K2) <= (K2 - K1) S(K1),
and puts give the mirror image. Since S cannot increase, each bound also binds every other
point; the envelope is the tightest result of that, taking each quote on its own. Constraints
that tie strikes together, such as a spread bounding the integral of S between its strikes, are
not used, so between option strikes the envelope can be wider than no-arbitrage allows.

The **fair value** is fitted to mid prices: an isotonic fit through the two-sided quotes
(weighted by inverse quote width), monotone cubic interpolation between them, and probit-normal
tails beyond them, clipped into the envelope. Inputs that contradict each other are reported as
crossings; the fair value is then still isotonic and within [0, 1] but is not clipped.

Kalshi's S&P buckets include their floor ("7,675 to 7,699.9999") and the close prints to the
cent, so each bucket is P(close >= floor) minus P(close >= the next cent above its cap). Both
are read exactly at strikes: a call spread pays its full width whenever the close is at or above
its upper strike, so the spread's ceiling there bounds P(close >= K), and the floor at its lower
strike bounds P(close > K), which is no larger.

On the S&P side, the chain is 15 minutes old by the time Kalshi's book is read, so two curves
are built. The **recorded** curve is the chain as of its own timestamp; its bounds are forced, as
of that time. The **rolled** curve moves it to the live S&P print and that print's timestamp,
assuming a forward for the rest of the session, that the distribution of log(close / forward)
keeps its shape in units of its own standard deviation, and that variance accrues evenly to the
close. Its bounds are model bounds. The fair value comes from the rolled curve. The forward is
the live spot by default; `--keep-basis` adds the chain's own forward-minus-spot instead.

Options prices are risk-neutral. No adjustment to real-world probabilities is made.

## Results

### NFL: hold one strike out, games Sep 9 to Nov 2 2026

Every settled pregame passing-yards ladder with at least four two-sided strikes, with each book
taken as it stood ten minutes before kickoff (the close of its last one-minute candle at or
before then): 121 games, 235 player-games, 1,641 interior strikes. Of the 2,309 settled markets,
16 settled neither Yes nor No (all of them Jalen Hurts' and Tua Tagovailoa's ladders in one
game each) and 171 were listed after the snapshot
(in-game listings); both are left out. Each strike was hidden in turn and estimated from the
rest.

| Estimate of the hidden strike | Mean gap to the hidden book's mid | Brier score vs settlement |
|---|---|---|
| This curve | 0.82c (95% CI 0.77 to 0.87) | 0.18220 |
| Straight line between neighbours | 1.37c | 0.18210 |
| The hidden book's own mid | 0 | 0.18211 |

- The curve is closer to the book than the straight line by 0.55c (95% CI 0.51 to 0.59).
- The hidden books were 1.28c wide on average. The curve fell inside the hidden bid/ask on 46.3%
  of strikes and the straight line on 27.5%.
- The Brier differences are not distinguishable from zero: curve minus book is +0.00009 (95% CI
  -0.00015 to +0.00033). The curve tracks the market's quotes; it does not predict outcomes
  better than the market, and no edge is claimed.
- 1,633 hidden strikes are 25 yards from both neighbours, so the test interpolates across 50
  yards; 5 sit between a 25-yard and a 50-yard gap (75 yards) and 3 between two 50-yard gaps
  (100 yards). Pricing a range inside a listed 25-yard ladder interpolates across 25.

Intervals are percentile bootstraps over 2,000 draws that resample whole games.

### NFL: receiving and rushing yards, games Sep 9 to Nov 9 2026

The same test on the receiving-yards and rushing-yards ladders: the same snapshot, the same
four-strike minimum, the same bootstrap. Each series covers 136 games. Markets that settled
neither Yes nor No and markets listed after the snapshot are left out, as above.

| | Receiving (KXNFLRECYDS) | Rushing (KXNFLRSHYDS) |
|---|---|---|
| Settled markets | 15,157 | 7,789 |
| Settled neither Yes nor No | 287 | 133 |
| Listed after the snapshot | 1,423 | 699 |
| Player-games | 1,573 | 772 |
| Hidden interior strikes | 10,047 | 5,352 |
| Curve: mean gap to the hidden book's mid | 0.82c (95% CI 0.79 to 0.85) | 0.87c (95% CI 0.84 to 0.92) |
| Straight line: mean gap | 1.32c | 1.24c |
| Curve closer than the line by | 0.50c (95% CI 0.46 to 0.54) | 0.37c (95% CI 0.35 to 0.39) |
| Mean width of the hidden books | 1.61c | 1.48c |
| Inside the hidden bid/ask: curve / line | 54.8% / 43.5% | 49.9% / 39.4% |
| Brier score: curve / line / book's own mid | 0.16755 / 0.16758 / 0.16765 | 0.16731 / 0.16770 / 0.16729 |
| Brier, curve minus book | -0.00009 (95% CI -0.00019 to +0.00002) | +0.00003 (95% CI -0.00014 to +0.00018) |

- 2 receiving markets and 1 rushing market fall in both of the left-out rows, so the test reads
  13,449 receiving and 6,958 rushing markets, the number of quotes in each shipped file.
- Against the book, neither Brier difference is distinguishable from zero. As on passing yards,
  the curve tracks the market's quotes and no edge is claimed.
- Against the straight line, the curve's Brier score is lower on rushing by 0.00039 (95% CI
  0.00008 to 0.00073). On receiving the two are not distinguishable: curve minus line is
  -0.00003 (95% CI -0.00033 to +0.00029).
- These ladders are finer than passing's, so the test interpolates across less. The two
  neighbours of a hidden receiving strike are 20 yards apart on 6,442 strikes, 25 on 1,796, 10 on
  1,233 and 15 on 576. On rushing they are 20 apart on 3,281, 10 on 1,504 and 15 on 567.
- The pregame books of these two series were read through Kalshi's batch candlestick endpoint
  (`fetch-quotes`), 40 markets to a request. Run on the 2,122 passing markets, whose books
  were read one market at a time, it returns the identical quote for every one, with none
  falling back to the single-market search.

### NFL: outside the quoted strikes

The hold-one-out test hides interior strikes only. Here the lowest and the highest two-sided
strike of each ladder are hidden in turn and read off the tail fitted to the rest. Gaps are
the estimate minus the hidden book's mid; "inside" is the share of ladders where the estimate
fell within the hidden bid/ask. Passing covers games to Nov 2, the other two series to Nov 9.

| | Passing | Receiving | Rushing |
|---|---|---|---|
| Ladders | 235 | 1,573 | 772 |
| Lowest strike hidden: mean absolute gap | 1.10c | 5.04c | 4.30c |
| Lowest strike hidden: mean signed gap | -0.11c | -4.99c | -4.21c |
| Lowest strike hidden: inside | 40.0% | 4.1% | 4.3% |
| Highest strike hidden: mean absolute gap | 0.94c | 1.27c | 1.45c |
| Highest strike hidden: mean signed gap | -0.84c | -1.25c | -1.41c |
| Highest strike hidden: inside | 45.1% | 39.5% | 31.0% |
| Median mid at the lowest strike | 86.5c | 70.0c | 76.0c |
| Fitted mass below zero yards: mean | 0.1% | 20.7% | 17.4% |
| Fitted mass below zero yards: median / largest | 0.1% / 2.2% | 17.3% / 71.3% | 11.8% / 79.5% |

- The tail reads low at the highest strike on all three series and at the lowest strike on
  receiving and rushing, where it misses by 4c to 5c and lands inside the hidden bid/ask on
  about 4% of ladders. At the lowest passing strike the signed gap is -0.11c.
- On the median receiving ladder the lowest two-sided strike is quoted at 70c, so 30% of the
  distribution sits below every quote; on rushing 24%, on passing 13.5%.
- The lower tail is a normal, and nothing quoted constrains it below the lowest strike. Fitted
  to a whole receiving or rushing ladder, it puts more than 1% below zero yards on every one.
  On passing it does so on 2 of 235.

### NFL: Kalshi's one-yard books, Oct 29 to Nov 2 2026

Of the 16,856 one-yard markets settled as of Nov 4 2026 (14 games, 28 players), 860 ever
traded. A fixed sample of 3,360, 120 of each player's 602 markets (the yards from 100 to 397
ending in 0, 2, 5 or 7), was read ten minutes before kickoff the same way as above, looking back to each market's listing for its
last book; 2,047 of them fall strictly between two strikes of the same player's 25-yard ladder.
A first sample took only yards ending in 2 or 7; it was widened after cached files outside it
showed bids on round yards.

- All 2,047 had an ask, and every ask was 99c. 2 had a bid, both on round yards: Josh Allen
  220+ at 9c, where the curve puts 68.4%, and Sam Darnold 210+ at 10c, where it puts 67.2%.
- On these strikes the curve fitted to the 25-yard ladder put the probability between 3.5% and
  92.9%.
- Against settlement, the curve's Brier score on these strikes is 0.18563 and the straight line
  between ladder mids scores 0.18550; 37.2% settled Yes.

### S&P 500: one session, Nov 4 2026

Twelve snapshots, each a Kalshi KXINX book, a Cboe chain and a live S&P 500 print. The close was
7,688.62, and the 7,675 to 7,699.9999 bucket was finalized Yes. "Two-sided" counts the buckets
with both a bid and an ask.

| Book (ET) | Spot, at (ET) | Two-sided | Fair inside bid/ask | Mean gap to mid | Mean bid/ask width | Mean model envelope width | Flags model / forced / both | Settled bucket: Kalshi mid / options fair |
|---|---|---|---|---|---|---|---|---|
| 14:08:05 | 7708.85 at 14:07:59 | 4 | 4 | 0.90c | 5.0c | 0.236 | 0 / 2 / 0 | 0.225 / 0.217 |
| 14:18:08 | 7705.72 at 14:18:03 | 4 | 4 | 0.97c | 4.7c | 0.233 | 0 / 1 / 0 | 0.285 / 0.271 |
| 14:28:09 | 7704.46 at 14:28:09 | 4 | 3 | 1.72c | 4.7c | 0.235 | 0 / 2 / 0 | 0.315 / 0.321 |
| 14:38:11 | 7702.00 at 14:38:11 | 4 | 1 | 2.68c | 4.5c | 0.224 | 0 / 0 / 0 | 0.345 / 0.387 |
| 14:48:14 | 7703.20 at 14:48:14 | 4 | 4 | 0.80c | 4.5c | 0.221 | 0 / 0 / 0 | 0.325 / 0.328 |
| 14:58:17 | 7695.87 at 14:58:16 | 4 | 2 | 1.95c | 3.5c | 0.217 | 0 / 3 / 0 | 0.530 / 0.565 |
| 15:08:18 | 7698.56 at 15:08:17 | 4 | 4 | 1.20c | 4.2c | 0.231 | 0 / 0 / 0 | 0.475 / 0.487 |
| 15:18:20 | 7701.18 at 15:18:20 | 4 | 4 | 1.36c | 4.5c | 0.232 | 0 / 0 / 0 | 0.385 / 0.404 |
| 15:28:22 | 7701.13 at 15:28:21 | 2 | 2 | 0.62c | 5.0c | 0.438 | 0 / 2 / 0 | 0.395 / 0.402 |
| 15:38:24 | 7703.11 at 15:38:22 | 2 | 0 | 3.19c | 5.0c | 0.403 | 0 / 0 / 0 | 0.285 / 0.318 |
| 15:48:28 | 7697.75 at 15:48:24 | 2 | 0 | 5.90c | 5.0c | 0.393 | 0 / 2 / 0 | 0.575 / 0.639 |
| 15:58:30 | 7688.08 at 15:58:11 | 1 | 0 | 32.62c | 62.0c | 0.023 | 0 / 0 / 0 | 0.670 / 0.996 |

- Over the 39 two-sided bucket-snapshots, the options fair value was inside Kalshi's bid/ask on
  28 (72%).
- 3 of the 39 show under 10 contracts on one side: two at 14:58:17, and the 15:58:30 bucket,
  bid 1 contract at 0.36 against 11 offered at 0.98.
- The model bounds flagged no quote. The forced bounds of the 15-minute-old chain flagged 12. No
  quote was outside both.
- The forward and clock of the rolled curve were chosen after the session closed. Under the
  alternatives, fair value was inside bid/ask on 29 of 39 with `--keep-basis`, 28 of 39 with
  `--clock book` (the time the Kalshi book was read), and 29 of 39 with both; each alternative
  gives one model flag.
- This is one session and one settlement. It describes how the two markets lined up on Nov 4
  and says nothing about edge.

## Reproduce

Every number in Results comes from a command in this repository, run on the data it ships with;
the 16,856 and 860 above come from `one-yard`. Two exceptions: the 13,449 and 6,958 are the
number of entries in the two shipped quote files, and `fetch-quotes` on the passing file, which
goes online, prints the 2,122 of 2,122 agreement with the single-market cache.

```
python3 -m implied backtest                                            # the hold-one-out table and intervals
python3 -m implied backtest --series KXNFLRECYDS                       # the receiving column
python3 -m implied backtest --series KXNFLRSHYDS                       # the rushing column
python3 -m implied extrapolate                                         # outside the quoted strikes, all three series
python3 -m implied one-yard                                            # the one-yard books
python3 -m implied spx-day --snapshot data/snapshots/20261104 \
    --settled data/snapshots/20261104/settled-KXINX-26NOV04H1600.json  # the S&P table, close and flags
python3 -m implied spx-day --snapshot data/snapshots/20261104 --keep-basis --clock book   # an alternative
python3 -m implied fetch-settled --until 2026-11-04 --out /tmp/s.json  # regenerates the settled 25-yard file
python3 -m implied fetch-settled --series KXNFLPASSYDSPRECISE --until 2026-11-05 --out /tmp/p.json   # and the one-yard file
python3 -m implied fetch-settled --series KXNFLRECYDS --until 2026-11-11 --out /tmp/r.json   # the receiving file (KXNFLRSHYDS: rushing)
python3 -m implied fetch-quotes --series KXNFLRECYDS --settled /tmp/r.json --out /tmp/q.json # and its pregame quotes
python3 -m implied fetch-quotes --series KXNFLPASSYDS --settled data/passyds_settled_20261104.json --out /tmp/c.json   # the 2,122 check
```

Live use:

```
python3 -m implied nfl KXNFLPASSYDS-26NOV08CLENO --player "Tyler Shough" --range 250 300
python3 -m implied nfl KXNFLRECYDS-26NOV12WASNYG --player "Darnell Mooney" --range 40 59
python3 -m implied spx                                                 # during market hours
python3 -m implied record                                              # snapshots to the KXINX close
python3 -m implied trim --snapshot data/snapshots/YYYYMMDD             # keeps the same-day SPXW legs
```

## Verification

- 213 unit tests, run with warnings as errors: `python3 -W error -m unittest discover -s tests -t .`
  Six of them rerun `backtest` on each of the three series, `extrapolate`, `one-yard` and
  `spx-day` offline on the shipped data and require byte-equal output with the committed result
  files.
- `python3 mutation_check.py` plants specific bugs one at a time (wrong fee rounding, an
  off-by-one strike, a look-ahead snapshot, a stale spot or clock, a stale forward basis, dropped
  weights, a batch read past the snapshot, a hidden strike left in the fit, and others) and exits
  1 unless the suite fails on every one, with each mutation compiling and importing.
- Every line in `implied/` runs under the tests except the `if __name__ == "__main__"` guard.

## Scope

- The Nov 4 S&P session was recorded by an earlier shell version of `record`. It fetched
  `cdn.cboe.com`, which now redirects to `cdn-api.cboe.com`, sent Yahoo a browser User-Agent,
  and read one page of up to 500 KXINX markets; all 60 open markets fit on that page. The
  Kalshi and Yahoo files are the raw responses; the Cboe chains are the raw responses cut by
  `trim` to the same-day SPXW legs, the only ones replay reads.
- A range is supported between a player's lowest and highest two-sided strikes, which is what
  the hold-one-out tests cover. Outside them the fair value is a fitted normal tail, and
  "outside the quoted strikes" shows it reading low, by 4c to 5c at the lowest receiving or
  rushing strike. The forced bounds still hold there; the fair value should not be quoted, and
  `nfl --range` marks any range that reaches outside.
- A receiving or rushing range that starts at 0 is the worst case of that. The tail puts 20.7%
  (receiving) and 17.4% (rushing) of the mass below zero on average, and S(-0.5) - S(b + 0.5)
  leaves all of it out. "b yards or fewer", 1 - S(b + 0.5) with b + 0.5 inside the quoted
  strikes, does not touch the tail. It also pays on a total below zero, which happens,
  mostly on rushing, so it is a different contract from 0 to b and not a repair of it.
- Receiving and rushing pregame quotes ship as one file per series
  (`data/recyds_quotes_20261110.json`, `data/rshyds_quotes_20261110.json`), keyed the way the
  one-file-per-market passing cache in `data/nfl` is named. The settled files are Kalshi's raw
  responses. A refetch later on Nov 10 returned the same markets with the same results; only
  the rolling 24-hour fields (`volume_24h_fp` and the three `previous_*` prices) differed, and
  nothing here reads them.
- The one-yard result is one week of games, sampled at 120 of each player's 602 yards.
- Kalshi taker fees are modelled as 7% times the series' published multiplier, times
  C x P x (1 - P), rounded up to the cent per order, on the displayed size. The API gives KXINX
  and KXNFLPASSYDS a multiplier of 1, and neither is among the 160 series on the non-standard
  list at kalshi.com/fee-schedule (checked Nov 4 2026). Maker fees and rebates are not modelled.
