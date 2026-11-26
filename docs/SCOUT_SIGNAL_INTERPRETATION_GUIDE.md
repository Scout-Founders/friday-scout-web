# Scout signal interpretation guide

**Status:** Documentation checkpoint, 2026-11-26. Practical reading of the scoring behavior documented in the Phase I–III audits.
**Audience:** A Scout user who needs to read a score, a direction, and a gate list.
**Related:** [Scoring methodology](./SCOUT_SCORING_METHODOLOGY.md) · [Research register](./SCOUT_SCORING_RESEARCH_REGISTER.md)

The examples are stored Horizon-1 scans from 17–21 May 2026. They are single-ticker scans. They are not outbound email picks.

## What Scout Score means

Scout Score is a single number from 0 to 100. It starts at 50. Points are added for fundamentals, forward estimates, analyst and insider readings, catalysts, a clean threat check, sector wind, agreement between trend and direction, and quiet volatility. Points are removed for threat headlines, a near earnings date, and weak component readings.

The number is a blend of those items. It is not a probability, a price target, or a forecast that the trade will make money. Two stocks can share a score for different reasons. A score of 94 and a score of 100 can be one point apart before rounding into the display, or they can be far apart before the display ceiling.

On a single-ticker scan the words attached to the number are:

| Score | Label you will see on the scan |
|---|---|
| 80–100 | STRONG |
| 60–79 | MODERATE |
| 40–59 | WEAK |
| 0–39 | AVOID |

The website ring and the batch email use different words at the same cuts: STRONG BUY, MODERATE, WATCH, and AVOID. A batch ranking line can still say WATCH when the detail line says AVOID. Read the number first, then check which surface printed the word.

## What Net Score means

Net Score is a second number, computed in Horizon after the scan returns. It is the bullish evidence total minus the bearish evidence total, after Horizon boosts the side that matches the scan’s direction.

Net Score does not change Scout Score. It does not decide the direction. It does not rank the email. A positive Net Score means the bullish evidence pile is larger after that boost. A negative Net Score means the bearish pile is larger. The boost can make the two piles look closer, or push one of them to the 100 ceiling.

NVDA on 19 May 2026 (scan 153) shows the split. The scan direction is Bearish and the Scout Score is 100. The stored bullish pile is 84 and the bearish pile is 83, so Net Score is +1. The direction was chosen by the price-extension rule, not by which pile was larger.

## How to read bullish, bearish, and neutral

On a single-ticker scan, direction is a rule about price location:

- **Bullish** means the stock is at least 20% below its 52-week high, or the cash-flow value is more than 20% above the price, or RSI is under 40, and the bearish rule is not also true.
- **Bearish** means the stock is at least 60% above its 52-week low, or the cash-flow value is more than 30% below the price, or RSI is above 75, and the bullish rule is not also true.
- **Neutral** means both rules are true, or neither rule is true.

Neutral still receives a full Scout Score. PLTR on 17 May 2026 (scan 32) is Neutral with a score of 94. It was 35% below its high, which satisfies the bullish rule, and its cash-flow value was about 92% below the price, which satisfies the bearish rule. Both rules fired, so the label is Neutral. The score stayed high because the other components were strong and the sector wind was +3.

TLT on 18 May 2026 (scan 77) is Bullish with a score of 55. RSI was 31, which is enough for the bullish rule. The trend was a downtrend. The score did not receive the trend-agreement bonus, because a bullish label and a downtrend do not agree.

The outbound email does not use these three words. A batch pick is CALL or PUT, meaning which screen the name entered: a drawdown screen for calls, or a run-up screen for puts. A stock can be Neutral on a manual scan and never appear in the email. A stock can appear in the email as a CALL even though a manual scan of the same inputs would be Neutral.

## What a score of 100 means

A displayed 100 means the formula reached at least 100 and was cut off there. It does not mean every input was perfect, and it does not mean every gate passed.

BSX on 21 May 2026 (scan 210) is the clean case. The reconstructed total is 101, the displayed score is 100, the direction is Bullish, and all fourteen gate flags are true. The extra point is hidden by the ceiling.

NVDA scan 153 is a different 100. The reconstructed total is 103. Threat Scan failed because of about $204 million of insider selling. The earnings line says “0 days” and “extreme risk.” The earnings penalty was not applied, because a count of zero is treated as no date. The trend was an uptrend while the direction was Bearish, so there was no trend-agreement bonus. The displayed score is still 100.

TSM on 19 May 2026 (scan 167) is a third 100. The reconstructed total is 105. Threat Scan failed on insider selling. Direction is Bearish and the trend is a downtrend, so this one did receive the agreement bonus. Net Score is −46, which lines up with the bearish label more closely than NVDA’s +1.

Treat 100 as a saturated grade. Use the gate list, the earnings line, and Net Score to see what the ceiling is hiding.

## What “fourteen gates” means

The fourteen names, in order, are:

1. Sentinel — Market Filter
2. Atlas — Core Strength
3. Oracle — Forward Vision
4. Phantom — Smart Money
5. Catalyst — Event Trigger
6. Specter — Threat Scan
7. Meridian — Sector Wind
8. Aegis — Earnings Shield
9. Compass — Trend Lock
10. Pulse — Volatility Read
11. Signal — Intel Feed
12. Current — Flow Analysis
13. Archer — Strategy Select
14. Fortress — Risk Gate

On a single-ticker scan, a pass is a yes/no flag stored with the result. Horizon prefers a final pick that passes every flag. If nobody passes every flag, Horizon still picks the highest Scout Score.

Passing the list means those fourteen flags were true on that scan. It does not mean:

- The score was built only from passing gates. Failed gates can still leave their points in the score.
- Signal, Current, and Archer measured a live condition. On the inspected single-scan path those three flags are set to pass every time.
- Fortress ran a separate risk model. Fortress passes when the score is 40 or higher, which is the same line as leaving the AVOID band.
- Compass means the trend agrees with the trade. Compass passes whenever a trend was identified, including a downtrend. Agreement is a separate +5 inside the score.
- The email’s phrase “ALL 14 GATES CLEARED” used this same list. On the batch path that sentence is printed for every name that survived the smart-money and threat drops. Catalyst, earnings, trend, and volatility do not have to pass.

## Why a high score can sit next to a failed gate

The score and the flags are written separately. Failing a flag does not subtract the whole score.

PLTR scan 32 scored 94 with Threat Scan failed. The insider-selling flag removed a 5-point “clean” bonus and did not apply a further point penalty. The rest of the components still summed to 94, and the verdict word is STRONG.

NVDA scan 153 scored 100 with the same kind of Threat Scan failure. The other components summed to 103 before the ceiling.

IWM on 18 May 2026 (scan 53) is the low end of the same design. News penalties of −15 and −5 were applied, the total is 30, and the verdict is AVOID. Core Strength, Forward Vision, Event Trigger, Threat Scan, Trend Lock, and Risk Gate all failed. Here the failed threat check did change the number, because the headline categories carry point penalties. Insider selling by itself does not.

## Why a strong score can sit next to a negative, or tiny, Net Score

Scout Score adds quality, wind, and a few direction-agreement points into one pile. Net Score splits evidence into a bullish pile and a bearish pile, then boosts the pile that matches the direction label.

A high Scout Score can therefore coexist with:

- A small positive Net Score on a bearish label, as in NVDA scan 153 (score 100, Net +1).
- A large negative Net Score on a bearish label, as in TSM scan 167 (score 100, Net −46).
- A positive Net Score on a neutral label, as in PLTR scan 32 (score 94, bull 93, bear 80, Net +13). Neutral does not receive the directional boost. The piles are whatever the evidence weights summed to.

Read Scout Score as the blended grade. Read Net Score as Horizon’s later split of the write-up. When they disagree, the scan’s direction label is still the direction rule in the methodology document, not the sign of Net Score.

## How ranking and the final pick work

A manual scan returns one ticker, so it has no rank.

When Horizon scans a list, it first looks for tickers that passed all fourteen flags. Among those, the highest Scout Score wins. If none passed all fourteen, the highest Scout Score in the whole list wins anyway. If two scores are equal, the one that appears first in the list is kept. There is no alphabetical tie-break.

The outbound email ranks differently. It scores only names that survived the call screen or the put screen and then survived the smart-money and threat checks. It sorts by Scout Score, highest first. Equal scores keep the earlier name. Call survivors are listed before put survivors, so a call wins a tie against a put. The first name is the TOP PICK. If nobody survives, the fallback list is sorted by a different point total, the sum of the gate points, not by Scout Score.

Option strikes in that email are chosen from the gate-point sum as well. Horizon’s own contract picker runs only for names that passed all fourteen flags, and it does not change the score.

## Scanner results and outbound email picks

These are different products.

| | Single-ticker scan | Outbound email |
|---|---|---|
| What it covers | The ticker you asked for | Names that passed a call screen or a put screen |
| Direction words | Bullish, Bearish, Neutral | CALL or PUT |
| Score formula | Includes a forward-estimate term | Uses a broader fundamental score, omits that forward term, and adds a catalyst bonus or penalty |
| Failed threat check | Score is still shown | Name is removed before a score is published |
| Who writes the message | The scan text | Claude writes the email after the score already exists |
| What Horizon stored | 227 research-eligible scans, 17 May–2 June 2026 | No emailed picks in `research_sent_picks` |

A win, loss, or average from the 227 scans describes those manual scans. It does not describe the email.

## What the historical evidence supports

The stored scans support these reading rules:

- The displayed score matches the single-scan formula, including the 0–100 ceiling. BSX reconstructs to 101 and is stored as 100. NVDA reconstructs to 103 and is stored as 100. TSM reconstructs to 105 and is stored as 100. PLTR reconstructs to 94 and is stored as 94. TLT reconstructs to 55 and is stored as 55. IWM reconstructs to 30 and is stored as 30.
- A STRONG word can appear with a failed Threat Scan (PLTR 94, NVDA 100, TSM 100).
- A WEAK word can appear on a Bullish label with a downtrend (TLT 55).
- An AVOID word can appear on a Neutral label when news penalties are large (IWM 30).
- Net Score can be near zero, or positive, on a Bearish label (NVDA, Net +1).
- All 227 eligible rows are single-ticker scans. Their dates, scores, directions, and gate flags are the evidence base for those statements.

Among those 227 rows, 15 are stored at 100. Direction counts are 103 Neutral, 85 Bearish, and 39 Bullish. That is a description of this bank, not a claim about how often those labels win.

## What has not been validated

- No study in this bank shows that a higher Scout Score produces a better outcome.
- No study shows that passing all fourteen gates produces a better outcome than a high score with a failed gate.
- The 227 rows cannot be used as the track record of the email. The email uses a different formula, a different universe, and different direction words, and this database has no emailed-pick rows.
- The September 2026 source snapshot has not been checked against the function Google is running now. Deployment time, runtime, and entry point were not retrieved.
- Claude’s email prose has not been checked, pick by pick, against the stored score. The score is fixed before Claude writes.
- The position-size percentages in the batch write-up use a fixed win rate of 0.667 and a fixed win/loss ratio of 1.61. Those figures are not measured from this bank.

## Common misreadings

**“100 means a perfect setup.”** It means the blended total reached the ceiling. BSX passed every gate at 101. NVDA failed Threat Scan at 103. Both display as 100.

**“Failed a gate, so the score should be low.”** The flag and the points are separate. PLTR failed Threat Scan and scored 94.

**“Fourteen passes means fourteen independent risk checks.”** Three flags are set to pass on every single-ticker scan. Fortress repeats the “score is at least 40” line. Compass means a trend was identified, including a downtrend.

**“The trend warning failed the trend gate.”** A downtrend is printed as a warning and still passes Compass when the moving average was found. TLT’s downtrend left Compass passing. IWM failed Compass because no trend could be classified.

**“Bearish means the bearish evidence won.”** Bearish means the extension rule fired. NVDA is Bearish with bullish evidence 84 and bearish evidence 83.

**“Net Score chose the direction.”** Direction is chosen first. Net Score is Horizon’s later reading, and it boosts the side that matches that direction.

**“The email score is the same number the scanner would show.”** The email omits the forward-estimate term, uses a different fundamental score, adds a catalyst-level adjustment, and drops names the scanner would still grade. The same inputs can be STRONG on the scanner and MODERATE on the email path, or present on one and absent on the other.

**“ALL 14 GATES CLEARED on an email means the fourteen flags above.”** On the batch path that sentence is printed for smart-money and threat survivors. It is not a check of these fourteen flags.

**“A zero-day earnings warning was penalized.”** NVDA’s text says earnings in 0 days and extreme risk. The stored earnings count is 0, the earnings shield passed, and no earnings points were removed.

**“Historical wins in Horizon are the email’s track record.”** The eligible history is 227 single-ticker scans. The sent-pick table is empty.
