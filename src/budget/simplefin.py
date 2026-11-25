"""Small SimpleFIN Bridge client for claiming tokens and reading account data.

SimpleFIN's protocol specification does not prescribe a rate limit. The
SimpleFIN Bridge developer guide expects at most 24 all-account requests per
day per access token (with some leeway during initial setup); excess requests
can lead to warnings and eventual token disablement. See
https://beta-bridge.simplefin.org/info/developers#limits.
"""

import base64
import binascii
import logging
import os
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from budget.balances_db import reserve_simplefin_request
from budget.config import get_env, get_fetch_timeout, load_toml

_DEFAULT_DAILY_REQUEST_LIMIT = 24
logger = logging.getLogger(__name__)


def _daily_request_limit() -> int:
    """Return the configured per-database daily request cap."""
    raw_limit = os.environ.get("SIMPLEFIN_DAILY_REQUEST_LIMIT")
    if raw_limit is None:
        return _DEFAULT_DAILY_REQUEST_LIMIT
    try:
        limit = int(raw_limit)
    except ValueError:
        raise SimpleFINError(
            "SIMPLEFIN_DAILY_REQUEST_LIMIT must be a positive integer"
        ) from None
    if limit < 1:
        raise SimpleFINError(
            "SIMPLEFIN_DAILY_REQUEST_LIMIT must be a positive integer"
        )
    return limit


class SimpleFINError(Exception):
    """An error communicating with SimpleFIN, without credential-bearing URLs."""


def _request_error(action: str, exc: Exception) -> SimpleFINError:
    if isinstance(exc, requests.Timeout):
        return SimpleFINError(
            f"SimpleFIN {action} request timed out.\nIf your VPN is on, turn it off and try again."
        )
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        suffix = f" (HTTP {status})" if status is not None else ""
        return SimpleFINError(
            f"SimpleFIN {action} request returned an HTTP error{suffix}"
        )
    return SimpleFINError(f"SimpleFIN {action} request failed")


def claim_setup_token(setup_token: str) -> str:
    """Claim a base64-encoded one-time setup token and return its access URL."""
    try:
        claim_url = base64.b64decode(setup_token, validate=True).decode("utf-8").strip()
        parsed = urlsplit(claim_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("not an HTTPS URL")
    except (ValueError, UnicodeDecodeError, binascii.Error, TypeError):
        raise SimpleFINError("Invalid SimpleFIN setup token") from None

    try:
        response = requests.post(
            claim_url, timeout=get_fetch_timeout(), allow_redirects=False
        )
        response.raise_for_status()
        access_url = response.text.strip()
        access_parts = urlsplit(access_url)
        if access_parts.scheme != "https" or not access_parts.netloc:
            raise SimpleFINError("SimpleFIN returned an invalid access URL")
        return access_url
    except SimpleFINError:
        raise
    except requests.RequestException as exc:
        raise _request_error("claim", exc) from None


def get_accounts(
    access_url: str | None = None,
    account_names: Mapping[str, str] | None = None,
    *,
    include_unmapped: bool = False,
) -> list[dict[str, Any]]:
    """Fetch account ids, names, balances, and balance timestamps.

    If ``access_url`` is omitted, it is read from ``SIMPLEFIN_ACCESS_URL``.
    Account ids are translated to app display names using ``[simplefin.accounts]``
    in config.toml. Unmapped accounts are warned about and skipped. ``account_names``
    can be supplied to override the config mapping.
    """
    if access_url is None:
        access_url = get_env("SIMPLEFIN_ACCESS_URL")
    if account_names is None:
        account_names = load_toml().get("simplefin", {}).get("accounts", {})
    try:
        parts = urlsplit(access_url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("invalid access URL")
        request_limit = _daily_request_limit()
        if not reserve_simplefin_request(limit=request_limit):
            raise SimpleFINError(
                f"SimpleFIN daily request limit reached ({request_limit} requests)"
            )
        endpoint = urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") + "/accounts", "", "")
        )
        response = requests.get(
            endpoint,
            params={"balances-only": 1},
            timeout=get_fetch_timeout(),
            allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        accounts = payload["accounts"]
        if not isinstance(accounts, list):
            raise TypeError("accounts is not a list")
        errors = payload.get("errlist", [])
        if errors:
            safe_errors = [
                re.sub(r"https?://\S+", "[redacted URL]", str(error)).replace(
                    access_url, "[redacted]"
                )
                for error in errors
            ]
            raise SimpleFINError(
                "SimpleFIN reported account errors: " + "; ".join(safe_errors)
            )
        normalized = []
        for account in accounts:
            account_id = account["id"]
            display_name = account_names.get(account_id)
            if display_name is None and not include_unmapped:
                logger.warning("Skipping unmapped SimpleFIN account id %s", account_id)
                continue
            balance_date = account.get("balance-date")
            if balance_date is not None:
                try:
                    balance_date = (
                        datetime.fromtimestamp(float(balance_date), UTC)
                        .date()
                        .isoformat()
                    )
                except (TypeError, ValueError, OverflowError, OSError):
                    balance_date = None
            if balance_date is None:
                logger.warning(
                    "SimpleFIN account %s has no valid balance date", account_id
                )
            elif (date.today() - date.fromisoformat(balance_date)).days > 1:
                logger.warning(
                    "SimpleFIN account %s balance is stale (as of %s)",
                    account_id,
                    balance_date,
                )
            normalized.append(
                {
                    "id": account_id,
                    "name": display_name or account.get("name") or account_id,
                    "balance": account["balance"],
                    "balance_timestamp": account.get("balance-date"),
                    "date": balance_date,
                }
            )
        return normalized
    except requests.exceptions.JSONDecodeError:
        raise SimpleFINError("SimpleFIN returned malformed account data") from None
    except requests.RequestException as exc:
        raise _request_error("accounts", exc) from None
    except (ValueError, KeyError, TypeError, AttributeError):
        # requests' JSONDecodeError may retain the response URL, so don't chain it.
        raise SimpleFINError("SimpleFIN returned malformed account data") from None


def get_transactions(
    access_url: str | None = None,
    account_names: Mapping[str, str] | None = None,
    *,
    account_owners: Mapping[str, str] | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> list[dict[str, Any]]:
    """Fetch mapped accounts and transactions in a SimpleFIN date window.

    The end date is exclusive as required by SimpleFIN. Longer windows are
    fetched in consecutive 90-day chunks to stay within Bridge's per-request
    limit.
    """
    if access_url is None:
        access_url = get_env("SIMPLEFIN_ACCESS_URL")
    if account_names is None or account_owners is None:
        simplefin_config = load_toml().get("simplefin", {})
        if account_names is None:
            account_names = simplefin_config.get("accounts", {})
        if account_owners is None:
            account_owners = simplefin_config.get("account_owners", {})
    try:
        today = date.today()
        end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
        start = (
            date.fromisoformat(start_date)
            if isinstance(start_date, str)
            else start_date
        )
        end = end or today + timedelta(days=1)
        start = start or end - timedelta(days=90)
        if start >= end:
            raise ValueError("invalid date window")
        parts = urlsplit(access_url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("invalid access URL")
        request_limit = _daily_request_limit()
        endpoint = urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") + "/accounts", "", "")
        )
        normalized_by_id: dict[str, dict[str, Any]] = {}
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=90), end)
            if not reserve_simplefin_request(limit=request_limit):
                raise SimpleFINError(
                    f"SimpleFIN daily request limit reached ({request_limit} requests)"
                )
            params = {
                "start-date": int(
                    datetime.combine(chunk_start, time.min, UTC).timestamp()
                ),
                "end-date": int(datetime.combine(chunk_end, time.min, UTC).timestamp()),
            }
            response = requests.get(
                endpoint, params=params, timeout=get_fetch_timeout(), allow_redirects=False
            )
            response.raise_for_status()
            payload = response.json()
            accounts = payload["accounts"]
            if not isinstance(accounts, list):
                raise TypeError("accounts is not a list")
            errors = payload.get("errlist", [])
            if errors:
                safe_errors = [
                    re.sub(r"https?://\S+", "[redacted URL]", str(error)).replace(
                        access_url, "[redacted]"
                    )
                    for error in errors
                ]
                raise SimpleFINError(
                    "SimpleFIN reported account errors: " + "; ".join(safe_errors)
                )
            for account in accounts:
                account_id = account["id"]
                display_name = account_names.get(account_id)
                if display_name is None:
                    logger.warning("Skipping unmapped SimpleFIN account id %s", account_id)
                    continue
                transactions = account.get("transactions", [])
                if not isinstance(transactions, list):
                    raise TypeError("transactions is not a list")
                normalized = normalized_by_id.setdefault(
                    account_id,
                    {
                        "id": account_id,
                        "name": display_name,
                        "owner": account_owners.get(account_id, account.get("owner")),
                        "transactions": [],
                    },
                )
                normalized["transactions"].extend(transactions)
            chunk_start = chunk_end
        return list(normalized_by_id.values())
    except SimpleFINError:
        raise
    except requests.exceptions.JSONDecodeError:
        raise SimpleFINError("SimpleFIN returned malformed transaction data") from None
    except requests.RequestException as exc:
        raise _request_error("transactions", exc) from None
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
        raise SimpleFINError(
            "SimpleFIN returned malformed transaction data or date range"
        ) from None
