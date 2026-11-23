# CHANGELOG

## Unreleased

### Added

- On-demand transfer pair suggestions across accounts, using a 10-day window and preserving every ambiguous candidate.
- Transfer review page with pair approval, persistent rejection, and unmark actions.
- Transfer categories that can be hidden from transactions and excluded from spend and income summaries, with transfer status inherited by subcategories. Existing `Transfer` and `Transfers` categories are flagged during migration.
- Hover over a spend chart category to see its name.
- Nested Categories

### Development

- Migrated the frontend to TypeScript and added the `npm run build` bundle step.

### Fixed

- Every balance snapshot was overwriting the last one.
- Removed the nonfunctional Refresh button from the balances page.

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
