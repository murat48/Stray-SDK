"""Tests for TransactionHistory module."""
import pytest
from unittest.mock import Mock, patch, MagicMock
from stellar_agent.history import TransactionHistory


MOCK_ACCOUNT_ID = "GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H"
OTHER_ACCOUNT_ID = "GDQJUTQYK2MQX2VGDR2FYWLIYAQIEGXTQVTFEMGH0BELWGYNR4GJKSV"


@pytest.fixture
def mock_config():
    with patch("stellar_agent.history.config") as mock_conf:
        mock_conf.horizon_url = "https://horizon-testnet.stellar.org"
        yield mock_conf


@pytest.fixture
def history_client(mock_config):
    with patch("stellar_agent.history.Server"):
        client = TransactionHistory()
        return client


# ---------------------------------------------------------------------------
# get_payments
# ---------------------------------------------------------------------------

def test_get_payments_success(mock_config):
    """get_payments returns formatted records and next_cursor."""
    sample_records = [
        {
            "id": "op1",
            "type": "payment",
            "paging_token": "token_op1",
            "from": OTHER_ACCOUNT_ID,
            "to": MOCK_ACCOUNT_ID,
            "asset_type": "native",
            "amount": "10.0000000",
            "transaction_hash": "abc123",
            "created_at": "2026-03-01T12:00:00Z",
        }
    ]

    with patch("stellar_agent.history.Server") as mock_server_class:
        mock_server = mock_server_class.return_value
        mock_payments_builder = MagicMock()
        mock_server.payments.return_value = mock_payments_builder
        mock_payments_builder.for_account.return_value = mock_payments_builder
        mock_payments_builder.order.return_value = mock_payments_builder
        mock_payments_builder.limit.return_value = mock_payments_builder
        mock_payments_builder.call.return_value = {
            "_embedded": {"records": sample_records}
        }

        client = TransactionHistory()
        result = client.get_payments(MOCK_ACCOUNT_ID, limit=10)

    assert result["count"] == 1
    assert result["records"] == sample_records
    assert result["next_cursor"] == "token_op1"


def test_get_payments_not_found(mock_config):
    """get_payments raises RuntimeError when account is not found."""
    from stellar_sdk.exceptions import NotFoundError

    with patch("stellar_agent.history.Server") as mock_server_class:
        mock_server = mock_server_class.return_value
        mock_payments_builder = MagicMock()
        mock_server.payments.return_value = mock_payments_builder
        mock_payments_builder.for_account.return_value = mock_payments_builder
        mock_payments_builder.order.return_value = mock_payments_builder
        mock_payments_builder.limit.return_value = mock_payments_builder
        mock_payments_builder.call.side_effect = NotFoundError(Mock())

        client = TransactionHistory()
        with pytest.raises(RuntimeError, match="not found on the Stellar network"):
            client.get_payments(MOCK_ACCOUNT_ID)


def test_get_payments_network_error(mock_config):
    """get_payments wraps unexpected errors in RuntimeError."""
    with patch("stellar_agent.history.Server") as mock_server_class:
        mock_server = mock_server_class.return_value
        mock_payments_builder = MagicMock()
        mock_server.payments.return_value = mock_payments_builder
        mock_payments_builder.for_account.return_value = mock_payments_builder
        mock_payments_builder.order.return_value = mock_payments_builder
        mock_payments_builder.limit.return_value = mock_payments_builder
        mock_payments_builder.call.side_effect = ConnectionError("timeout")

        client = TransactionHistory()
        with pytest.raises(RuntimeError, match="Failed to fetch payment history"):
            client.get_payments(MOCK_ACCOUNT_ID)


def test_get_payments_with_cursor(mock_config):
    """get_payments passes the cursor to the builder."""
    with patch("stellar_agent.history.Server") as mock_server_class:
        mock_server = mock_server_class.return_value
        mock_payments_builder = MagicMock()
        mock_server.payments.return_value = mock_payments_builder
        mock_payments_builder.for_account.return_value = mock_payments_builder
        mock_payments_builder.order.return_value = mock_payments_builder
        mock_payments_builder.limit.return_value = mock_payments_builder
        mock_payments_builder.cursor.return_value = mock_payments_builder
        mock_payments_builder.call.return_value = {"_embedded": {"records": []}}

        client = TransactionHistory()
        client.get_payments(MOCK_ACCOUNT_ID, cursor="some_cursor")

        mock_payments_builder.cursor.assert_called_once_with("some_cursor")


def test_get_payments_limit_clamped(mock_config):
    """Limit values below 1 are clamped to 1; above 200 clamped to 200."""
    with patch("stellar_agent.history.Server") as mock_server_class:
        mock_server = mock_server_class.return_value
        mock_payments_builder = MagicMock()
        mock_server.payments.return_value = mock_payments_builder
        mock_payments_builder.for_account.return_value = mock_payments_builder
        mock_payments_builder.order.return_value = mock_payments_builder
        mock_payments_builder.limit.return_value = mock_payments_builder
        mock_payments_builder.call.return_value = {"_embedded": {"records": []}}

        client = TransactionHistory()

        client.get_payments(MOCK_ACCOUNT_ID, limit=0)
        mock_payments_builder.limit.assert_called_with(1)

        client.get_payments(MOCK_ACCOUNT_ID, limit=999)
        mock_payments_builder.limit.assert_called_with(200)


# ---------------------------------------------------------------------------
# format_payment
# ---------------------------------------------------------------------------

def test_format_payment_received_xlm(history_client):
    """Incoming XLM payment is formatted correctly."""
    record = {
        "id": "op1",
        "type": "payment",
        "paging_token": "token_op1",
        "from": OTHER_ACCOUNT_ID,
        "to": MOCK_ACCOUNT_ID,
        "asset_type": "native",
        "amount": "25.5000000",
        "transaction_hash": "hash_abc",
        "created_at": "2026-03-01T10:00:00Z",
    }

    result = history_client.format_payment(record, MOCK_ACCOUNT_ID)

    assert result["direction"] == "RECEIVED"
    assert result["amount"] == "25.5000000"
    assert result["asset"] == "XLM"
    assert result["counterpart"] == OTHER_ACCOUNT_ID
    assert result["created_at"] == "2026-03-01 10:00 UTC"


def test_format_payment_sent_xlm(history_client):
    """Outgoing XLM payment is formatted correctly."""
    record = {
        "id": "op2",
        "type": "payment",
        "paging_token": "token_op2",
        "from": MOCK_ACCOUNT_ID,
        "to": OTHER_ACCOUNT_ID,
        "asset_type": "native",
        "amount": "5.0000000",
        "transaction_hash": "hash_def",
        "created_at": "2026-03-02T15:30:00Z",
    }

    result = history_client.format_payment(record, MOCK_ACCOUNT_ID)

    assert result["direction"] == "SENT"
    assert result["counterpart"] == OTHER_ACCOUNT_ID
    assert result["asset"] == "XLM"


def test_format_payment_non_native_asset(history_client):
    """Non-native asset is formatted with code and truncated issuer."""
    record = {
        "id": "op3",
        "type": "payment",
        "paging_token": "token_op3",
        "from": OTHER_ACCOUNT_ID,
        "to": MOCK_ACCOUNT_ID,
        "asset_type": "credit_alphanum4",
        "asset_code": "USDC",
        "asset_issuer": "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN",
        "amount": "100.0000000",
        "transaction_hash": "hash_ghi",
        "created_at": "2026-03-03T08:00:00Z",
    }

    result = history_client.format_payment(record, MOCK_ACCOUNT_ID)

    assert result["asset"].startswith("USDC/")
    assert result["direction"] == "RECEIVED"


def test_format_payment_create_account(history_client):
    """create_account operation is formatted correctly."""
    record = {
        "id": "op4",
        "type": "create_account",
        "paging_token": "token_op4",
        "funder": OTHER_ACCOUNT_ID,
        "account": MOCK_ACCOUNT_ID,
        "starting_balance": "1.0000000",
        "transaction_hash": "hash_jkl",
        "created_at": "2026-01-01T00:00:00Z",
    }

    result = history_client.format_payment(record, MOCK_ACCOUNT_ID)

    assert result["direction"] == "RECEIVED"
    assert result["amount"] == "1.0000000"
    assert result["type"] == "create_account"


def test_format_payment_unknown_type(history_client):
    """Unknown operation types are handled without crashing."""
    record = {
        "id": "op5",
        "type": "change_trust",
        "paging_token": "token_op5",
        "transaction_hash": "hash_mno",
        "created_at": "2026-03-04T12:00:00Z",
    }

    result = history_client.format_payment(record, MOCK_ACCOUNT_ID)

    assert result["direction"] == "—"
    assert result["asset"] == "—"


def test_format_payment_invalid_date(history_client):
    """Malformed created_at is returned as-is without raising."""
    record = {
        "id": "op6",
        "type": "payment",
        "from": OTHER_ACCOUNT_ID,
        "to": MOCK_ACCOUNT_ID,
        "asset_type": "native",
        "amount": "1.0",
        "transaction_hash": "hash_pqr",
        "created_at": "not-a-date",
        "paging_token": "tok",
    }

    result = history_client.format_payment(record, MOCK_ACCOUNT_ID)
    assert result["created_at"] == "not-a-date"


# ---------------------------------------------------------------------------
# get_formatted_history
# ---------------------------------------------------------------------------

def test_get_formatted_history_empty(mock_config):
    """get_formatted_history returns empty list when no payments found."""
    with patch("stellar_agent.history.Server") as mock_server_class:
        mock_server = mock_server_class.return_value
        mock_payments_builder = MagicMock()
        mock_server.payments.return_value = mock_payments_builder
        mock_payments_builder.for_account.return_value = mock_payments_builder
        mock_payments_builder.order.return_value = mock_payments_builder
        mock_payments_builder.limit.return_value = mock_payments_builder
        mock_payments_builder.call.return_value = {"_embedded": {"records": []}}

        client = TransactionHistory()
        result = client.get_formatted_history(MOCK_ACCOUNT_ID)

    assert result["payments"] == []
    assert result["count"] == 0
    assert result["next_cursor"] is None


def test_get_formatted_history_multiple(mock_config):
    """get_formatted_history formats all records returned."""
    records = [
        {
            "id": f"op{i}",
            "type": "payment",
            "paging_token": f"tok{i}",
            "from": OTHER_ACCOUNT_ID,
            "to": MOCK_ACCOUNT_ID,
            "asset_type": "native",
            "amount": f"{i}.0",
            "transaction_hash": f"hash{i}",
            "created_at": "2026-03-01T12:00:00Z",
        }
        for i in range(1, 4)
    ]

    with patch("stellar_agent.history.Server") as mock_server_class:
        mock_server = mock_server_class.return_value
        mock_payments_builder = MagicMock()
        mock_server.payments.return_value = mock_payments_builder
        mock_payments_builder.for_account.return_value = mock_payments_builder
        mock_payments_builder.order.return_value = mock_payments_builder
        mock_payments_builder.limit.return_value = mock_payments_builder
        mock_payments_builder.call.return_value = {"_embedded": {"records": records}}

        client = TransactionHistory()
        result = client.get_formatted_history(MOCK_ACCOUNT_ID, limit=3)

    assert result["count"] == 3
    assert len(result["payments"]) == 3
    for p in result["payments"]:
        assert p["direction"] == "RECEIVED"
        assert p["asset"] == "XLM"
