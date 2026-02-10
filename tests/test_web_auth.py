"""Tests for Telegram Mini App initData HMAC validation."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from auto_slicer.web_auth import (
    validate_init_data,
    parse_init_data,
    _build_data_check_string,
    _compute_hmac,
)


BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _make_init_data(
    user_id: int = 12345,
    auth_date: int | None = None,
    extra_params: dict | None = None,
    bot_token: str = BOT_TOKEN,
    tamper_hash: str | None = None,
) -> str:
    """Build a valid initData string for testing."""
    if auth_date is None:
        auth_date = int(time.time())
    user = json.dumps({"id": user_id, "first_name": "Test", "username": "testuser"})
    params = {"user": user, "auth_date": str(auth_date)}
    if extra_params:
        params.update(extra_params)

    data_check_string = _build_data_check_string(params)
    hash_val = tamper_hash or _compute_hmac(bot_token, data_check_string)
    params["hash"] = hash_val
    return urlencode(params)


class TestValidateInitData:
    def test_valid(self):
        init_data = _make_init_data()
        user_id, error = validate_init_data(init_data, BOT_TOKEN)
        assert user_id == 12345
        assert error == ""

    def test_invalid_hmac(self):
        init_data = _make_init_data(tamper_hash="0" * 64)
        user_id, error = validate_init_data(init_data, BOT_TOKEN)
        assert user_id is None
        assert error == "invalid signature"

    def test_wrong_token(self):
        init_data = _make_init_data()
        user_id, error = validate_init_data(init_data, "wrong:token")
        assert user_id is None
        assert error == "invalid signature"

    def test_expired(self):
        old_date = int(time.time()) - 7200  # 2 hours ago
        init_data = _make_init_data(auth_date=old_date)
        user_id, error = validate_init_data(init_data, BOT_TOKEN, max_age_seconds=3600)
        assert user_id is None
        assert error == "expired"

    def test_custom_max_age(self):
        old_date = int(time.time()) - 100
        init_data = _make_init_data(auth_date=old_date)
        # With 50s max age, should be expired
        user_id, error = validate_init_data(init_data, BOT_TOKEN, max_age_seconds=50)
        assert user_id is None
        assert error == "expired"
        # With 200s max age, should be valid
        user_id, error = validate_init_data(init_data, BOT_TOKEN, max_age_seconds=200)
        assert user_id == 12345

    def test_missing_hash(self):
        params = {
            "user": json.dumps({"id": 1}),
            "auth_date": str(int(time.time())),
        }
        init_data = urlencode(params)
        user_id, error = validate_init_data(init_data, BOT_TOKEN)
        assert user_id is None
        assert error == "missing hash"

    def test_missing_user(self):
        auth_date = str(int(time.time()))
        params = {"auth_date": auth_date}
        dcs = _build_data_check_string(params)
        h = _compute_hmac(BOT_TOKEN, dcs)
        params["hash"] = h
        init_data = urlencode(params)
        user_id, error = validate_init_data(init_data, BOT_TOKEN)
        assert user_id is None
        assert error == "missing user"

    def test_invalid_user_json(self):
        auth_date = str(int(time.time()))
        params = {"auth_date": auth_date, "user": "not-json"}
        dcs = _build_data_check_string(params)
        h = _compute_hmac(BOT_TOKEN, dcs)
        params["hash"] = h
        init_data = urlencode(params)
        user_id, error = validate_init_data(init_data, BOT_TOKEN)
        assert user_id is None
        assert error == "invalid user JSON"

    def test_user_without_id(self):
        auth_date = str(int(time.time()))
        params = {"auth_date": auth_date, "user": json.dumps({"first_name": "No ID"})}
        dcs = _build_data_check_string(params)
        h = _compute_hmac(BOT_TOKEN, dcs)
        params["hash"] = h
        init_data = urlencode(params)
        user_id, error = validate_init_data(init_data, BOT_TOKEN)
        assert user_id is None
        assert error == "missing user id"


class TestParseInitData:
    def test_round_trip(self):
        init_data = _make_init_data(user_id=99)
        params = parse_init_data(init_data)
        assert "hash" in params
        assert "auth_date" in params
        user = json.loads(params["user"])
        assert user["id"] == 99

    def test_empty_string(self):
        assert parse_init_data("") == {}


class TestBuildDataCheckString:
    def test_sorted_order(self):
        params = {"b": "2", "a": "1", "c": "3"}
        result = _build_data_check_string(params)
        assert result == "a=1\nb=2\nc=3"

    def test_empty(self):
        assert _build_data_check_string({}) == ""


class TestComputeHmac:
    def test_deterministic(self):
        h1 = _compute_hmac(BOT_TOKEN, "test data")
        h2 = _compute_hmac(BOT_TOKEN, "test data")
        assert h1 == h2

    def test_different_data(self):
        h1 = _compute_hmac(BOT_TOKEN, "data1")
        h2 = _compute_hmac(BOT_TOKEN, "data2")
        assert h1 != h2

    def test_different_token(self):
        h1 = _compute_hmac("token1", "data")
        h2 = _compute_hmac("token2", "data")
        assert h1 != h2
