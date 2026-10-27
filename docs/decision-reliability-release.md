# Decision reliability and private exit progress

This release stops actionable advice when required data is missing, stale or
inconsistent. Missing evidence is not evidence to hold. ETF allocation rules
remain separate from company fundamental checks.

Cash conversion uses four consecutive quarterly operating-cash-flow amounts
divided by the same four quarters' net-income total. Nonpositive denominators,
missing quarters and non-finite values are unavailable. Legacy cached ratios
require rebuilding rather than being relabelled as the new method.

New purchases and additions share a fundamental assessment. Existing cash
conversion and two-quarter margin warnings in combination pause new exposure.
This guard remains a research rule; no performance improvement is claimed.

The history table distinguishes valuation-multiple percentile from one-year
closing-price percentile and distance from the highest closing price. A low
historical PER percentile can coexist with a high share price. Intraday-high
distance in the chip table uses a different explicitly labelled reference.

Private exit plans freeze their original share basis and trigger levels. Owner
reports of completed stages are versioned and idempotent. Seven calendar days is
a review interval, not a validated trading parameter. Records never place orders
or assert that broker fills were verified. A new thesis-failure warning suspends
an older protective plan for explicit review.

Taiwan-stock stage quantities are frozen in whole shares with cumulative-floor
rounding. Completion checks use those frozen quantities, including inventory
already updated after a sale and a fully exited position absent from holdings.

The owner-only progress document uses the existing private data repository.
Runtime caches and temporary files are ignored by Git. Prices, original shares,
stages and timestamps are retained; no new identity, credential or medical fields
are collected. Cancellation retains a record and repository version history.

Deployment uses the existing Render service. After publishing, dispatch
`rebuild-value-state.yml` with `fundamentals_batch=0` to re-evaluate stored public
financials. This workflow neither emails, accesses broker accounts nor creates
Decision Ledger signals. It saves current-state and the existing daily audit
history. Inspect public per-item data provenance and freshness after rebuilding.

Rollback: revert this scoped PR and let the existing deployment workflow publish
the reverted code. Private progress records are additive and remain private.
