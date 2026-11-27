# CHANGELOG

## v0.2.0

### Added

- Accounts page showing linked accounts and their status, with a flow to close an account.
- Attention-needed page for reviewing accounts and transactions that need follow-up.
- Shared navigation across dashboard pages.
- Rules can match transaction amounts, including amount-based rule creation from transactions.
- Automatic chunking for large SimpleFIN transaction queries, with a configurable daily request limit.
- Optional staging-instance banner.
- On-demand transfer pair suggestions across accounts, using a 10-day window and preserving every ambiguous candidate.
- Transfer review page with pair approval, persistent rejection, and unmark actions.
- Transfer categories that can be hidden from transactions and excluded from spend and income summaries, with transfer status inherited by subcategories. Existing `Transfer` and `Transfers` categories are flagged during migration.
- Nested categories and category selection improvements.
- Hover over a spend chart category to see its name.

### Development

- Migrated the frontend to TypeScript and added the `npm run build` bundle step.
- Added Playwright navigation regression coverage and included end-to-end tests in CI.
- Added pre-commit formatting checks and expanded automated test coverage.

### Fixed

- Account ownership now refreshes when stale, and closing an account records its closure correctly.
- Spend chart filters now honor ignored transaction amounts.
- Partial data-fetch failures are no longer cached as successful results.
- Improved dashboard security and corrected several analysis, cache, and spreadsheet edge cases.
- Aligned shared navigation across pages and reduced layout jitter.
- Every balance snapshot was overwriting the last one.
- Removed the nonfunctional Refresh button from the balances page.

## Unreleased

## v0.1.0

### Added

- SimpleFIN setup-token claiming, mapped account balances, daily SQLite balance snapshots, and
  transaction imports.
- SQLite categories, ordered categorization rules, automatic categorization on transaction upsert,
  and manual category overrides.
- Transactions, rules, and balances dashboard pages, including a category spend chart and balance
  history.
- JSON endpoints for spend and balance trends.

### Fixed

- Enforced the daily SimpleFIN request limit with atomic SQLite reservations.
- Preserved pending-transaction updates when repeated imports return changed transaction state.
