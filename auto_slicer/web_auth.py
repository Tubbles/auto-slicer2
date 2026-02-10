"""Telegram Mini App initData HMAC-SHA256 validation.

Validates the initData string sent by the Telegram WebApp SDK to ensure
the request is authentic and not replayed. Pure functions, no I/O.

Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, unquote


def validate_init_data(
    init_data: str, bot_token: str, max_age_seconds: int = 3600,
) -> tuple[int | None, str]:
    """Validate Telegram initData and return (user_id, error).

    Returns (user_id, "") on success, or (None, error_message) on failure.
    """
    params = parse_init_data(init_data)

    received_hash = params.pop("hash", "")
    if not received_hash:
        return None, "missing hash"

    data_check_string = _build_data_check_string(params)
    expected_hash = _compute_hmac(bot_token, data_check_string)

    if not hmac.compare_digest(received_hash, expected_hash):
        return None, "invalid signature"

    auth_date_str = params.get("auth_date", "")
    if not auth_date_str:
        return None, "missing auth_date"

    try:
        auth_date = int(auth_date_str)
    except ValueError:
        return None, "invalid auth_date"

    if time.time() - auth_date > max_age_seconds:
        return None, "expired"

    user_json = params.get("user", "")
    if not user_json:
        return None, "missing user"

    try:
        user = json.loads(user_json)
    except (json.JSONDecodeError, TypeError):
        return None, "invalid user JSON"

    user_id = user.get("id")
    if not isinstance(user_id, int):
        return None, "missing user id"

    return user_id, ""


def parse_init_data(init_data: str) -> dict[str, str]:
    """Parse the initData query string into a flat dict."""
    result = {}
    for key, values in parse_qs(init_data, keep_blank_values=True).items():
        result[key] = values[0] if values else ""
    return result


def _build_data_check_string(params: dict[str, str]) -> str:
    """Build the sorted newline-separated data-check-string for HMAC."""
    return "\n".join(f"{k}={v}" for k, v in sorted(params.items()))


def _compute_hmac(bot_token: str, data_check_string: str) -> str:
    """Compute HMAC-SHA256 using the bot token as the secret key.

    The secret key is HMAC-SHA256("WebAppData", bot_token).
    """
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256,
    ).digest()
    return hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256,
    ).hexdigest()
