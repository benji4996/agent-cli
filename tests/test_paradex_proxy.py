from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from parent.paradex_proxy import ParadexProxy


TEST_L1_KEY = "0x59c6995e998f97a5a0044966f0945382d7d6f4858cc5b64cf68545ce7f0d3f4d"
EXPECTED_L1_ADDRESS = "0x5776cC4ee9b26B615a6c799db7f9d5EE827c595b"
TEST_L2_KEY = "0x1234"
TEST_L2_ADDRESS = "0x" + "1" * 62


@pytest.fixture(autouse=True)
def no_keystore_resolution():
    with patch("parent.paradex_proxy.resolve_private_key", return_value=TEST_L2_KEY):
        yield


def test_paradex_proxy_accepts_matching_l1_signer_and_address():
    proxy = ParadexProxy(
        l1_private_key=TEST_L1_KEY,
        l1_address=EXPECTED_L1_ADDRESS,
        l2_private_key=TEST_L2_KEY,
        l2_address=TEST_L2_ADDRESS,
        testnet=False,
    )
    assert proxy.l1_address.lower() == EXPECTED_L1_ADDRESS.lower()


def test_paradex_proxy_rejects_mismatched_explicit_l1_address():
    with pytest.raises(ValueError, match="Paradex L1 signer mismatch"):
        ParadexProxy(
            l1_private_key=TEST_L1_KEY,
            l1_address="0x" + "2" * 40,
            l2_private_key=TEST_L2_KEY,
            l2_address=TEST_L2_ADDRESS,
        )


def test_paradex_proxy_rejects_mismatched_env_l1_address(monkeypatch):
    monkeypatch.setenv("PARADEX_L1_ADDRESS", "0x" + "3" * 40)
    with pytest.raises(ValueError, match="Paradex L1 signer mismatch"):
        ParadexProxy(
            l1_private_key=TEST_L1_KEY,
            l2_private_key=TEST_L2_KEY,
            l2_address=TEST_L2_ADDRESS,
        )


def test_configure_http_resilience_sets_timeout_and_retry_strategy():
    proxy = ParadexProxy(
        l2_private_key=TEST_L2_KEY,
        l2_address=TEST_L2_ADDRESS,
        http_timeout_s=17.0,
        http_retry_max_retries=5,
        http_retry_base_delay_s=0.25,
        http_retry_max_delay_s=3.5,
    )
    api_client = SimpleNamespace(default_timeout=None, retry_strategy=None)
    proxy._api_client = api_client
    proxy._client = SimpleNamespace(api_client=api_client)

    proxy._configure_http_resilience()

    assert api_client.default_timeout == 17.0
    assert api_client.retry_strategy is not None
    assert api_client.retry_strategy.max_retries == 5
    assert api_client.retry_strategy.base_delay == 0.25
    assert api_client.retry_strategy.max_delay == 3.5
