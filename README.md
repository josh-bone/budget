# Budget

A personal budgeting tool with a SQLite transaction and balances dashboard, SimpleFIN imports,
legacy Google Sheets reports, and a retirement forecast. This project is licensed under
[GNU GPL version 3](LICENSE).

## Requirements

- Python 3.11 or newer
- SQLite (included with Python)
- SimpleFIN Bridge account for bank balances and transactions
- Node.js only when rebuilding the committed frontend bundle
- Google Cloud service account and spreadsheet for legacy Sheets features (`budget sheets`,
  `/month/<sheet_name>`, and `budget import-balances`) and for `budget retirement-forecast`, which
  still reads the latest Sheets budget to resolve contributions

## Installation

```sh
git clone <repository-url>
cd budget
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Copy `sample_config.toml` to a private location such as `config.toml`, then edit the example cell
references if you use the legacy Sheets tools. Set `BUDGET_CONFIG` to that file. The
`[simplefin.accounts]` mapping in that same config is required to import SimpleFIN data: it maps
each raw SimpleFIN account ID to the display name used by this app. Unmapped accounts are skipped.

For a SimpleFIN connection, create a setup token in SimpleFIN Bridge and claim it once:

```sh
budget simplefin-claim '<setup-token>'
```

Store the printed access URL as a secret. Example `.env` settings:

```dotenv
SIMPLEFIN_ACCESS_URL=https://bridge.simplefin.org/your-issued-access-path
SIMPLEFIN_DAILY_REQUEST_LIMIT=12
BUDGET_DB_PATH=/absolute/path/to/budget.sqlite3
FLASK_SECRET_KEY=replace-with-a-long-random-secret
BUDGET_CONFIG=/absolute/path/to/config.toml
```

The access URL shown above is only an illustration; use the URL returned by the claim command. For
an initial setup, `budget simplefin --list-raw-ids` prints IDs from the Bridge without applying the
config mapping. Add the IDs you want to import to `[simplefin.accounts]`, then run
`budget simplefin` or `budget transactions`.

`budget retirement-forecast` and legacy Sheets commands additionally require:

```dotenv
GOOGLE_SERVICE_ACCOUNT_KEY=/absolute/path/to/service-account.json
SPREADSHEET_ID=your_google_spreadsheet_id
```

The service account needs viewer access to the spreadsheet. `SPREADSHEET_ID` is the ID between `/d/`
and `/edit` in its URL. Keep credentials outside the repository.

## Command line

| Command                                                     | Purpose                                                                                                                                                                                                                                                                                  |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `budget simplefin-claim TOKEN`                              | Claim a one-time setup token and print the access URL.                                                                                                                                                                                                                                   |
| `budget simplefin`                                          | Show balances for mapped accounts; `--list-raw-ids` lists IDs before mapping.                                                                                                                                                                                                            |
| `budget snapshot`                                           | Fetch current mapped balances and save a dated SQLite snapshot.                                                                                                                                                                                                                          |
| `budget transactions [--start-date DATE] [--end-date DATE]` | Fetch and upsert transactions. Start is inclusive, end exclusive; longer windows are fetched in consecutive requests of up to 90 days each.                                                                                                                                              |
| `budget import-balances`                                    | Import historical balances from the legacy Sheets `Balances` tab into SQLite.                                                                                                                                                                                                            |
| `budget sheets`                                             | Fetch and display configured legacy Sheets budget values; this is also the default command.                                                                                                                                                                                              |
| `budget retirement-forecast`                                | Run the Monte Carlo forecast using SQLite balances and the latest legacy Sheets budget for contribution estimates. Supports `--csv-path`, `--initial-balance`, `--monthly-contribution`, `--account-patterns`, `--contribution-patterns`, `--contribution-fields`, and `--summary-keys`. |

SimpleFIN requests are limited to 24 per calendar day in the app database. Fetches of overlapping
transaction windows are safe: rows are keyed by account and transaction IDs and updated on conflict.
The default import window includes pending transactions; the app updates matching rows if their
status changes.

The forecast expects a CSV with `date`, `nominal_return`, and `inflation` columns. A small
illustrative file is included at
[docs/example_returns_inflation.csv](docs/example_returns_inflation.csv); replace it with your own
historical monthly series for meaningful results. This included file is the default, so the
forecast runs after a fresh clone; pass `--csv-path` to use another file.

Legacy Sheets summary expressions support two fields joined by `+`, `-`, `*`, or `/`; field names
must contain only ASCII letters, digits, and underscores and cannot begin with a digit. The Sheets
budget cache and its single-flight fetch lock are process-local. With multiple Gunicorn workers,
each worker maintains its own cache and may fetch independently.

## Web dashboard

Start the Flask development server from the repository root:

```sh
flask --app app run
```

The home page summarizes dated, non-pending SQLite transactions by month. `/transactions` lists
transactions, filters the table by account/category/status/description, allows manual
categorization, and displays a category spend chart with month/date filters. `/rules` manages
categories and categorization rules. `/balances` shows the latest per-account balances and net-worth
history. See [Routes and API](docs/ROUTES.md) for page and JSON endpoints.

Months on the home page link to `/transactions?month=YYYY-MM`. The `month` parameter filters the
spend chart; it does not filter the transaction table. The Sheets-backed `/month/<sheet_name>` page
and `/refresh` redirect remain legacy paths. The home page and current balances do not require
Sheets settings.

Keep the dashboard on a trusted machine or network, or put it behind an authenticated reverse
proxy or VPN. It has no user authentication. The `/refresh` POST accepts only same-host Origin or
Referer requests. See [Deployment](docs/DEPLOYMENT.md) and [Security](docs/SECURITY.md).

## Configuration and environment

| Setting                      | Purpose                                                                              | Default                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| `SIMPLEFIN_ACCESS_URL`       | SimpleFIN Bridge bearer access URL                                                   | Required for SimpleFIN commands                                                        |
| `SIMPLEFIN_DAILY_REQUEST_LIMIT` | Maximum all-account SimpleFIN requests per day for this database                  | `24`                                                                                    |
| `BUDGET_DB_PATH`             | SQLite database file path                                                            | `~/.local/share/budget/balances.sqlite3` (or `$XDG_DATA_HOME/budget/balances.sqlite3`) |
| `BUDGET_STAGING`             | Show a yellow staging warning banner on every web page (`1`, `true`, `yes`, or `on`) | Disabled                                                                               |
| `FLASK_SECRET_KEY`           | Stable Flask session signing key for flash messages                                  | Random per process if unset; set for multi-worker/restart persistence                  |
| `BUDGET_CONFIG`              | TOML config, including `[simplefin.accounts]`; also needed by legacy Sheets commands | Required by commands that load config                                                  |
| `GOOGLE_SERVICE_ACCOUNT_KEY` | Google service account JSON key for legacy Sheets paths                              | Required only by those paths                                                           |
| `SPREADSHEET_ID`             | Google spreadsheet ID for legacy Sheets paths                                        | Required only by those paths                                                           |
| `BUDGET_CACHE_TTL`           | Legacy Sheets dashboard cache lifetime, seconds                                      | `300`                                                                                  |
| `BUDGET_FETCH_WORKERS`       | Legacy Sheets dashboard parallel fetch workers                                       | `6`                                                                                    |
| `BUDGET_RESULT_TIMEOUT`      | Legacy Sheets dashboard timeout per sheet future, seconds                            | `20`                                                                                   |
| `BUDGET_FETCH_TIMEOUT`       | SimpleFIN and Sheets request timeout, seconds                                        | `15`                                                                                   |

See [Database, backup, and restore](docs/DATABASE.md), [SimpleFIN](docs/SimpleFIN.md), and
[Deployment](docs/DEPLOYMENT.md).

## Development

Install development dependencies with uv or pip:

```sh
uv sync --group dev
# or, with the virtual environment active:
python -m pip install -e . pytest pytest-cov ruff
```

Run the project checks:

```sh
pytest          # configured with an 80% coverage minimum
ruff check .
npm ci
npm run build
```

The frontend build requires Node.js and writes the generated bundle to `static/`.
