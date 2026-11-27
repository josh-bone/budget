# Routes and API

| Method and path | Type | Behavior |
| --- | --- | --- |
| `GET /` | Page | SQLite monthly income, expense, and remaining totals. |
| `GET /transactions` | Page | Transaction table and spend chart. Shared account, owner, category, pending, description, month, and date filters apply to both. Month and date bounds are mutually exclusive; date bounds take precedence if both are supplied. |
| `GET /transfers` | Page | Pair-matching suggestions, rejection/approval controls, and manually approved transfers that can be unmarked. |
| `GET /rules` | Page | Category and categorization-rule editor. |
| `GET /balances` | Page | Latest per-account balances and net-worth history; optional `source=manual | simplefin`. |
| `GET /report-problem` | Page | Review and submit a local problem report with browser and runtime context; accepts `error_id` from the server error page. |
| `POST /report-problem` | Form action | Save a report under the app data directory; default `~/.local/share/budget/reports` or `BUDGET_REPORTS_DIR`. |
| `GET /admin/reports`, `/admin/reports/<id>` | Page | List and inspect locally stored reports, including associated server stack traces. |
| `GET /month/<sheet_name>` | Legacy page | Google Sheets month detail. Requires Sheets credentials and configuration. |
| `POST /refresh` | Legacy action | Redirects to `/balances`; no longer refreshes the overview. |
| `GET /api/balances/trend` | JSON | Balance history points, optionally filtered by `source`. |
| `GET /api/transactions/spend` | JSON | Spend total and category breakdown. Accepts shared transaction filters plus `month`, date bounds, and `leaf`. |
| `POST /transactions/<account_id>/<transaction_id>/category` | Form action or JSON response | Submit form-encoded `category_id`; an empty value clears the override and reapplies rules. Requests marked as JSON or fetch receive a JSON response. |
| `POST /transfers/approve`, `/transfers/reject`, `/transfers/unmark` | Form action | Approve a suggested pair with a selected transfer category, persist a rejected pair, or return one approved transaction to automatic categorization. |
| `POST /categories`, `/categories/<id>/rename`, `/categories/<id>/delete` | Form action | Create, rename, or delete categories. |
| `POST /rules`, `/rules/<id>/edit`, `/rules/<id>/delete`, `/rules/run` | Form action | Create, edit, delete, or rerun categorization rules. |

There is no authentication or CSRF protection. Do not expose the app directly to an untrusted network.
