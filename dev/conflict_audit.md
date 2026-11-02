# Conflict precision audit (Task 3.3)

Sample: `python dev/sample_conflicts.py --quotes` (seed 33, n=30) over the KB from the last harness
run. After the precision filters the builder emits **29** conflicts, fewer than 30, so the sample is
the whole list and every emitted conflict is judged here.

Rule used: **true** = the fact really took different values for the same entity (across snapshots, or
a stale page still showing an old value next to the current one). **false** = the difference is an
extraction or normalisation artifact (another product, another column, a qualifier we did not read).
Each verdict was checked against the quoted page rows in the snapshot markdown.

| # | Key (conflict name) | Values -> resolution | Verdict | Evidence |
|---|---|---|---|---|
| 1 | certificate_special apy term=10 tier=1000+ | 3.80% -> 3.90% | true | /rates "10 Month Fixed Certificate Special" 3.80% in 01-04, 3.90% in 05 (business and IRA rows mirror it, merged) |
| 2 | certificate_special apy jumbo term=10 tier=100000+ | 3.90% -> 4.00% | true | /rates jumbo special row 3.90% in 01-04, 4.00% in 05 |
| 3 | certificate apy term=12 | 2.90% -> 3.40% | true | /rates "12 Month" 2.90% in 01-02, 3.40% from 03 |
| 4 | mortgage_fixed rate term=180 | 5.50/5.375/5.625/6.00/5.875 -> 6.75% | true | /rates 15-yr purchase row 5.625 -> 6.00 -> 5.875 -> 6.00 -> 6.75; homebuying-guidance and checking-accounts keep stale widget values |
| 5 | certificate_special apy term=17 | 3.85/3.95 -> 4.10% | true | /rates 17-month special 3.85 (03), 3.95 (04), 4.10 (05) |
| 6 | certificate_special apy jumbo term=17 | 3.95/4.05 -> 4.20% | true | /rates 17-month jumbo special 3.95, 4.05, 4.20 |
| 7 | mortgage_fixed rate term=240 | 5.875/5.625/6.00/6.375/6.125/6.25 -> 7.13% | true | /rates 20-yr purchase row moves every snapshot; stale copies elsewhere |
| 8 | ira apy traditional term=25 | 3.60% -> 3.85% | true | "Traditional: 25 Month Fixed Special" 3.60% in 01-02, 3.85% in 03-04 |
| 9 | mortgage_fixed rate term=360 loan<=100k | ... -> 7.13% | true | /rates 30-yr <=$100k row 5.875 -> 6.25 -> 6.125 -> 6.375 -> 7.13 |
| 10 | mortgage_fixed rate term=360 loan<=200k | ... -> 7.25% | true | /rates 30-yr <=$200k row changes each snapshot |
| 11 | mortgage_fixed rate term=360 loan<=300k | ... -> 7.25% | true | /rates 30-yr <=$300k row changes each snapshot |
| 12 | mortgage_fixed rate term=360 loan<=832,750 | ... -> 7.25% | true | /rates 30-yr <=$832,750 row changes each snapshot |
| 13 | auto_loan apr 2016-and-older 48 mo | 8.54/8.39 -> 8.59% | true | /rates 8.39% in 01-04, 8.59% in 05; checking-accounts page shows stale 8.54% |
| 14 | auto_loan apr new (2023-26 -> 2024-27) 48 mo | 4.99% -> 5.19% | true | /rates "2023-26 Model Year 48 Months 4.99%" (01-04) relabelled "2024-27 ... 5.19%" (05) |
| 15 | auto_loan apr 2017-20 60 mo | 6.29/6.14 -> 6.34% | true | /rates 6.14% -> 6.34%; stale 6.29% on checking-accounts |
| 16 | auto_loan apr 2021-22 -> 2021-23 60 mo | 5.59/5.44 -> 5.64% | true | /rates 5.44% (2021-22) -> 5.64% (2021-23) |
| 17 | auto_loan apr 2023-26 -> 2024-27 60 mo | 5.34/5.19 -> 5.39% | true | /rates 5.19% -> 5.39%; switch-and-earn keeps stale 5.34% |
| 18 | auto_loan apr 2017-20 72 mo | 6.44/6.29 -> 6.49% | true | /rates 6.29% -> 6.49% |
| 19 | auto_loan apr 2021-22 -> 2021-23 72 mo | 5.74/5.59 -> 5.79% | true | /rates 5.59% -> 5.79% |
| 20 | auto_loan apr 2023-26 -> 2024-27 72 mo | 5.44/5.29 -> 5.49% | true | /rates 5.29% -> 5.49% |
| 21 | auto_loan apr 2021-22 -> 2021-23 84 mo | 5.99/5.84 -> 6.04% | true | /rates 5.84% -> 6.04% |
| 22 | auto_loan apr 2023-26 -> 2024-27 84 mo | 5.54/5.39 -> 5.59% | true | /rates 5.39% -> 5.59% |
| 23 | share_savings rate business $10k-$24,999 | 0.20% -> 0.15% | true | /business-rates dividend rate 0.20% in 01-04, 0.15% in 05 |
| 24 | org award forbes (2023, 2024 merged) | fourth -> fifth | true | Press releases: "the fourth time Forbes recognized Dupaco" (2023), "the fifth time" (2024): the ranking count moved year to year |
| 25 | checking apy personal $5k-$24,999 | 3.00% -> 2.50% | true | /rates 1st Rate Checking 3.00% in 01-02, 2.50% from 03; promo landing pages keep 3.00% |
| 26 | money_market apy achieve $100k-$249,999 | 3.25% / 3.66% | **false** | earn-more table: 3.66% is the "APY for 1st 6 Months" column; the "APY After Promotional Period" column is 3.25%, the same as every other page. Different column, not a change |
| 27 | money_market apy (plain) $25k-$99,999 | 0.35% / 3.25% | **false** | 3.25% comes from an Achieve Money Market table on makethemost-lp (03) whose rows carry no product label; the plain money market tier is 0.35% everywhere |
| 28 | money_market apy achieve $25k-$99,999 | 3.25% / 3.66% | **false** | same artifact as #26 |
| 29 | money_market apy achieve $250k-$1M | 3.25% / 3.66% | **false** | same artifact as #26 |

**Precision: 25 / 29 = 86.2%** (target >= 80%).

The 4 false positives are table-extractor artifacts (an introductory-period APY column read as the
APY, and an unlabelled Achieve table read as the plain money market). They are not filtered in
`kb/conflicts.py` because the only rules that would catch them there are product-specific; the fix
belongs in `kb/extract/tables.py` and changes fact-path answers (the Achieve APY), so it is left for
a separate change.
