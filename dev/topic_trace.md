# Topic trace across snapshots 01 → 05

Hand-checked against the local snapshots with `dev/discover.py --grep` (Track J, 2026-11-01).
Snapshot keys: 01 = 2026-05-12, 02 = 05-19, 03 = 06-09, 04 = 08-27, 05 = 09-28 (current).
Paths are relative to `https://www.dupaco.com`. Values only; no page text is reproduced.

## Inventory (`python dev/discover.py`, raw-markdown hash)

| Snapshot | Pages | Added | Removed | Changed |
|---|---|---|---|---|
| 01 | 637 | – | – | – |
| 02 | 634 | 1 | 4 | 615 |
| 03 | 635 | 9 | 8 | 611 |
| 04 | 683 | 50 | 2 | 623 |
| 05 | 705 | 25 | 3 | 259 |

"Changed" uses the raw markdown hash, so nav/footer churn counts as a change. Re-run once
Track A's cleaned text exists. Page types in 05: /learn 255, /newsroom 167, /business 131,
/calculators 30, PDFs 21, /personal 18, /resources 9, other 74.
Notable additions: 05 adds `/personal/loans/auto-loans/`, `/checking`, `/earn-more`,
`/personal/savings/holiday-club-account/`, `/personal/loans/home-improvement-loan/`. 04 adds
`/learn/master-your-money/homeownership/`, `/membership`, the Owners-Manual-Summer2026 PDF.

## Rates that change over time

### Auto loans (APR, "as low as"). Answer page: `/personal/loans/auto-motorcycle-loans`, `/rates`. 05 also has `/personal/loans/auto-loans/`.

| Row | 01 | 02 | 03 | 04 | 05 |
|---|---|---|---|---|---|
| newest band, 48 mo | 4.99% | 4.99% | 4.99% | 4.99% | 5.19% |
| newest band, **60 mo** | 5.19% | 5.19% | 5.19% | 5.19% | **5.39%** |
| newest band, 72 mo | 5.29% | 5.29% | 5.29% | 5.29% | 5.49% |
| newest band, **84 mo** | 5.39% | 5.39% | 5.39% | 5.39% | **5.59%** |
| middle band, 60/72/84 | 5.44/5.59/5.84 | = | = | = | 5.64/5.79/6.04 |
| 2017-20, 60/72 | 6.14/6.29 | = | = | = | 6.34/6.49 |
| 2016 & older, 48 | 8.39% | = | = | = | 8.59% |

Model-year bands: newest band is "2023–26" in 01-04 and "2024-27" in 05; the middle band is "2021-22" in 01-04 and "2021-23" in 05.
So a 2023 vehicle falls in the newest band in 01-04 and in the middle band in 05. Effective date: 5/1/2026 (01-04), 08/31/2026 (05).
05 cells carry a footnote suffix (`5.39% APR1`).
**Stale trap pages (all snapshots):** `/switch-and-earn` and `/calculators/vehicle-affordability` show
60/72/84 = 5.34/5.44/5.54%, middle band 5.59/5.74/5.99%, 2017-20 6.29/6.44%, 2016 8.54%. `/personal/checking-accounts` (01-04) shows the same stale table.

### Mortgages: Purchase, Conventional, rate / APR. Answer page: `/personal/loans/home-loans`, `/rates`.

| Row | 01 | 02 | 03 | 04 | 05 |
|---|---|---|---|---|---|
| 30-yr ≤ $100,000 | 5.875 / 6.010 | 6.250 / 6.388 | 6.125 / 6.262 | 6.375 / 6.514 | **7.13 / 7.27** |
| 30-yr ≤ $200,000 | 6.000 / 6.079 | 6.375 / 6.456 | 6.250 / 6.331 | 6.375 / 6.456 | 7.25 / 7.34 |
| 30-yr ≤ $300,000 | 6.125 / 6.186 | 6.375 / 6.437 | 6.375 / 6.449 | 6.500 / 6.563 | 7.25 / 7.32 |
| 30-yr ≤ $832,750 | 6.125 / 6.162 | 6.500 / 6.538 | 6.375 / 6.413 | 6.625 / 6.663 | 7.25 / 7.29 |
| 20-yr | 6.000 / 6.131 | 6.375 / 6.508 | 6.125 / 6.256 | 6.250 / 6.382 | 7.13 / 7.26 |
| **15-yr** | 5.625 / 5.749 | 6.000 / 6.125 | 5.875 / 5.999 | 6.000 / 6.125 | **6.75 / 6.88** |

Effective dates: 05/11, 05/19, 06/04, 08/26, 09/25/2026. The answer page also carries a second loan table with
its own effective date (5/20/2024 in 01-04, 06/01/2026 in 05), so the page has two effective dates.
**Stale trap pages:** `/homebuying-guidance` (all snapshots) shows purchase 30-yr 6.000/6.037 ($832,750), 6.000/6.061 ($300k),
5.875/5.954 ($200k), 15-yr 5.500/5.623. `/mortgage-refi` (all) shows refinance 5.750–6.000. `/rates` in 01-04 keeps
stale refinance rows (e.g. 30-yr ≤ $832,750 at 6.375%), and 01-03 also show a "Mortgage Rate Relief Program" row at 5.125%.
`/personal/checking-accounts` 01-04 shows 15-yr 5.375%. 03's `/rates` 30-yr ≤ $100k purchase APR is 6.256% while home-loans shows 6.262%.

### Certificate specials (dividend rate / APY). Answer page: `/personal/savings-account`, `/rates`, `/business-rates`, `/personal/retirement` (IRA).

| Row | 01 | 02 | 03 | 04 | 05 |
|---|---|---|---|---|---|
| 10-mo Special ($1,000) | 3.76 / 3.80 | = | = | = | 3.86 / **3.90** |
| 10-mo Jumbo ($100,000) | 3.86 / 3.90 | = | = | = | 3.96 / **4.00** |
| 17-mo Special | — | — | 3.81 / 3.85 | 3.91 / 3.95 | 4.06 / **4.10** |
| 17-mo Jumbo | — | — | 3.91 / 3.95 | 4.01 / 4.05 | 4.15 / **4.20** |
| 25-mo Special / Jumbo | 3.60 / 3.70 APY | = | — | — | — |
| 12-mo regular | 2.88 / 2.90 | = | 3.37 / **3.40** | = | = |

Within-snapshot disagreements: in 04, `/business-rates` still shows the 03 17-month values (3.85 / 3.95) while the personal pages show 3.95 / 4.05.
In 05, `/personal/retirement` lists "Traditional: 10 Month Fixed Jumbo Special" at 3.90% while Roth and every other page show 4.00%.
Other regular terms in 05 (APY): 6-mo 3.40, 18-mo 2.70, 24-mo 2.50, 30-mo 2.30, 36-mo 2.10, 48-mo 1.90, 60-mo 1.80. These did not change.

### Share Savings tiers. Answer page: `/personal/savings-account`, `/rates`.
$0–99.99: 0.00 · $100–2,499.99: 0.10 · $2,500–9,999.99: 0.10 · **$10,000–24,999.99: 0.15** · **$25,000+: 0.20** (rate = APY). These are the same in all five snapshots.
Trap neighbours on the same page: Money Market 0.20–0.40% (0.30% at $10k, 0.35% at $25k, 0.40% at $100k). HSA (05) has 0.35% at $25,000+.
Within-snapshot oddity: `/business-rates` 01-04 lists Share Savings $10,000–24,999.99 with a dividend rate of 0.20% but an APY of 0.15%. 05 shows 0.15 / 0.15.
Bright Savings: 2.23% / 2.25% APY in all snapshots. Launchpad Savings (05 only): 3.21 / 3.25%.

### Checking: 1st Rate Checking, $5,000–24,999.99 (rate / APY). Answer page: `/personal/checking-accounts`, `/rates`, `/checking` (05).
2.96 / **3.00** (01, 02) → 2.47 / **2.50** (03, 04, 05). Other tiers: 0.00% below $5,000 and 0.10% at $25,000+. Free Checking pays 0.00%.
**Stale trap pages:** `/checking-offer-lp` and `/newcard-balancetransfer-lp` still show 3.00% in 05. `/makethemost-lp` shows 3.00% (01-02), and 01-02 also carry "up to 3.00% APY" marketing copy.

### Credit cards
Platinum Visa standard APR: 12.50%–19.50% on `/rates` and `/personal/credit-cards` in all snapshots. The `/personal/credit-cards` footnote in 01-03 says "effective 10/1/2025 … 13.00% APR to 20.00% APR"; in 04-05 it says 12.50%–19.50%. That is a within-snapshot conflict.
Intro balance-transfer rate 2.90%. Rewards Visa 15.50%–21.00%. Business Platinum Visa 15.50%. Business Rewards Visa 20.5% (05 shows 20.50%). These are the same in all snapshots.

### Other loans (05)
Personal loans 24/36/48/60 mo = 9.99/10.49/10.99/12.49% (unchanged). HELOC 6.25%; the HELOC intro rate of 1.99% for 3 months appears in 01-03 only.
Home equity loan 5-yr 6.74%, 10-yr 7.24%.

## Branches and organization facts

- **Hy-Vee branch closure.** Same in all snapshots. Source: `/newsroom/dupaco-evolves-with-purpose-and-member-needs-plans-hy-vee-branch-closure` (dated June 20, 2025). It says the branch "will close March 31, 2026" and that members can use the Key West and Sycamore Street full-service branches.
- **Lobby and drive-up hours.** No hours appear on `/` or `/contact`; `/contact` shows only the member-service reply hours, Mon–Fri 8:30 a.m.–5:30 p.m. Branch hours are in newsroom branch-opening releases and are the same in all snapshots:
  `/newsroom/dupaco-celebrates-completion-of-newest-learning-lab`: lobby Mon–Thu 8:30–5, Fri 8:30–6, Sat 8:30–12:30; drive-up Mon–Fri 8–6, Sat 9–noon, Sun ATM only.
  `…-celebrates-completion-of-expanded-branch`: lobby Mon–Thu 8:30–5, Fri 8:30–5:30, Sat 9–noon.
  `…-newest-learning-lab(-2,-3)`: lobby Mon–Fri 8:30–5:30, Sat 8:30–12:30; drive-up Sat 8–1.
  `…east-washington-avenue…` and `…mineral-point…`: Mon–Fri 9–5, Sat/Sun closed.
- **Routing number.** 273974581 everywhere, in all snapshots: `/contact`, `/business/cash-management`, `/personal/checking-accounts`, `/checking` (05). The value 271176899 never appears.
- **Branch count.** Taken from the "About Dupaco" boilerplate in press releases; it is the same in all snapshots. Most releases dated 2022 to mid-2024 say 22. Most releases from October 2024 to 2026-03-17 say **23** (one from April 2023 also says 23). One 2024 release (`forbes-ranks-…-3`) and the down-payment-assistance release say 24.
  The newest releases (April 3, 6, 23 and May 5, 2026), which come after the Hy-Vee closure, say **22**. `/about` says "18 branches" (an executive bio), and 2022 releases say 20. Gold value: 22.
- **Forbes.** `forbes-ranks-dupaco-as-a-top-credit-union-in-america`: "third year in a row". `-2` (July 2023): "fourth time". `-3` (June 2024): "**fifth time**, ranked third in Iowa". The separate small-employer ranking (#61) is a different award. These are the same in all snapshots.
- **Volunteer Time Off.** 294 employees in 2023 (`volunteering-pays-off…`) and 256 in 2024 (`…-2`). The public gold dup-088 expects 294.
- Galena branch: 11375 Oldenburg Ln., Galena, IL 61036. Peosta branch: 185 Peosta St., Peosta, IA 52068 (both on `/contact`).

## Public items: answer page and trap page (snapshot 05)

| id | answer page(s) | trap page(s) / trap source |
|---|---|---|
| dup-001 | `/personal/savings-account`, `/rates` (17-mo 4.10, jumbo 4.20) | — (jumbo 4.20 is the tier neighbour) |
| dup-009 | `/personal/loans/home-loans`, `/rates` | `/homebuying-guidance` (6.037/6.061/5.954 APR) |
| dup-011 | `/personal/loans/home-loans` | `/homebuying-guidance`; "6.7%" not literal anywhere |
| dup-016 | `/personal/loans/auto-motorcycle-loans`, `/rates` | `/switch-and-earn`, `/calculators/vehicle-affordability` (5.34%) |
| dup-034 | `/personal/credit-cards` + `/business/credit-cards` (no single page has all four) | "9.9%" not present (only 9.99% personal loan) |
| dup-045 | `/personal/savings-account` | same page, Money Market 0.30/0.40% |
| dup-054 | `/contact`, `/business/cash-management` | 271176899 absent (hallucination trap) |
| dup-064 | newsroom learning-lab releases; `/contact` (8:30 service hours) | — |
| dup-066 | `/newsroom/dupaco-celebrates-completion-of-newest-learning-lab` | — |
| dup-068 | `/contact` | — |
| dup-073 | newest releases (April–May 2026, "22") | `forbes-ranks-…-3`, `…special-down-payment-assistance-program` ("24 branch") |
| dup-075 | Hy-Vee closure release | — |
| dup-087 | `/newsroom/forbes-ranks-dupaco-as-a-top-credit-union-in-america-3` | `…-2` ("fourth"), 2020/2021-dated pages |
| dup-008 | `/personal/savings-account` (#rates) | info.dupaco.com URL absent from snapshots |
| dup-052 | `/personal/checking-accounts`, `/checking`, `/rates` | `/checking-offer-lp` ("3.00% APY") |
| dup-035 | `/rates`, `/personal/credit-cards` | 2.90% intro BT rate (tier neighbour); 9.9% absent |
| dup-145 | none (refuse) | `/learn/blog/could-an-adjustable-rate-mortgage-help-you-buy-your-home` names "30-year 7/1 ARM" with no rate |
| dup-065 | as dup-064 | auto-loan pages containing "ZIP code" |
| dup-074 | Hy-Vee closure release | — |
| dup-050 | `/personal/savings-account`, `/rates` | same page 0.35% (Money Market/HSA); "Jump Start" absent |
| dup-040 | `/personal/credit-cards`, `/rates`, `/business/credit-cards` | — |
| dup-080 | `/contact` | `/locations/branch-hours` absent |
| dup-049 | `/personal/savings-account` (Share Savings max 0.20%) | 0.35% exists for Money Market/HSA, not Share Savings |
| dup-031 | `/newsroom/first-time-homebuyers-get-a-boost-dupaco-offers-fhlb-hometart-grants` ("up to $15,000") | `/first-time-homebuyer-grant` ($1,000 grant, different program) |
| dup-028 | `/personal/loans/personal-loans`, `/rates` | — |
| dup-147 | none (refuse) | `/business/loans/construction-loans` (no rate) |
| dup-076 | Hy-Vee closure release | blog pages mentioning CO-OP Shared Branch |
| dup-006 | 01-04 `/personal/savings-account` (3.90%) | 05 pages (4.00%) |
| dup-055 | as dup-054 | — |
| dup-005 | `/personal/savings-account` certificate table | — |
| dup-017 | `/personal/loans/auto-motorcycle-loans` (5.59%) | `/switch-and-earn`, calculator (5.54%) |
| dup-081 | `/about/join` ("Iowa, Illinois and Wisconsin", charter area) | — |
| dup-060 | `/contact` (800-373-7600) | — |
| dup-046 | `/personal/savings-account` | same page 0.35% |
| dup-102 | none (refuse) | `/learn/blog/crew-amanda-clark` (mentions Mullan Avenue branch, Waterloo; no hours) |
| dup-104 | newsroom learning-lab releases (two schedules) | — |
| dup-088 | `/newsroom/volunteering-pays-off-…` (294, 2023) | `…-2` (256 in 2024) |
| dup-101 | `/personal/loans/student-loans` | savings pages (Share Savings 0.20%) |
| dup-094 | `/online-and-mobile-banking/mobile-payments` | — |
| dup-082 | `/about/join` (join.dupaco.com) | info.dupaco.com/how_to_join absent |
