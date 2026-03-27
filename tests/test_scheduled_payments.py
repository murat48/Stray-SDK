"""Unit tests for ScheduledPayment and ScheduledPaymentManager."""
import json
import os
import pytest
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from stellar_agent.scheduled_payments import (
    ScheduledPayment,
    ScheduledPaymentManager,
    STATUS_PENDING,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    RECURRENCE_OPTIONS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_DEST = "GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H"
FUTURE_TIME = datetime.utcnow() + timedelta(hours=2)
PAST_TIME = datetime.utcnow() - timedelta(hours=1)


def make_manager(tmp_path):
    """Return a ScheduledPaymentManager backed by a temp file."""
    storage = os.path.join(str(tmp_path), "scheduled.json")
    return ScheduledPaymentManager(storage_path=storage)


# ---------------------------------------------------------------------------
# ScheduledPayment unit tests
# ---------------------------------------------------------------------------

class TestScheduledPayment:
    def test_valid_creation(self):
        p = ScheduledPayment(destination=VALID_DEST, amount=10.0, scheduled_time=FUTURE_TIME)
        assert p.destination == VALID_DEST
        assert p.amount == 10.0
        assert p.status == STATUS_PENDING
        assert p.recurrence == "none"
        assert len(p.id) == 36  # UUID4

    def test_invalid_destination_raises(self):
        with pytest.raises(ValueError, match="Invalid destination address"):
            ScheduledPayment(destination="INVALID", amount=10.0, scheduled_time=FUTURE_TIME)

    def test_invalid_amount_raises(self):
        with pytest.raises(ValueError, match="Amount must be positive"):
            ScheduledPayment(destination=VALID_DEST, amount=0, scheduled_time=FUTURE_TIME)

    def test_invalid_recurrence_raises(self):
        with pytest.raises(ValueError, match="Invalid recurrence"):
            ScheduledPayment(
                destination=VALID_DEST, amount=5.0,
                scheduled_time=FUTURE_TIME, recurrence="hourly"
            )

    def test_to_dict_and_from_dict_roundtrip(self):
        original = ScheduledPayment(
            destination=VALID_DEST,
            amount=25.5,
            scheduled_time=FUTURE_TIME,
            recurrence="weekly",
            memo="Test memo",
        )
        data = original.to_dict()
        restored = ScheduledPayment.from_dict(data)

        assert restored.id == original.id
        assert restored.destination == original.destination
        assert restored.amount == original.amount
        assert restored.recurrence == original.recurrence
        assert restored.memo == original.memo
        assert restored.status == original.status

    def test_repr_contains_id_prefix(self):
        p = ScheduledPayment(destination=VALID_DEST, amount=1.0, scheduled_time=FUTURE_TIME)
        assert p.id[:8] in repr(p)


# ---------------------------------------------------------------------------
# ScheduledPaymentManager unit tests
# ---------------------------------------------------------------------------

class TestScheduledPaymentManager:
    def test_schedule_creates_payment(self, tmp_path):
        mgr = make_manager(tmp_path)
        p = mgr.schedule(VALID_DEST, 10.0, FUTURE_TIME)
        assert p.status == STATUS_PENDING
        assert len(mgr.list_payments()) == 1

    def test_schedule_persists_to_disk(self, tmp_path):
        storage = os.path.join(str(tmp_path), "scheduled.json")
        mgr = ScheduledPaymentManager(storage_path=storage)
        mgr.schedule(VALID_DEST, 5.0, FUTURE_TIME)

        # Reload from disk
        mgr2 = ScheduledPaymentManager(storage_path=storage)
        assert len(mgr2.list_payments()) == 1

    def test_schedule_past_time_raises(self, tmp_path):
        mgr = make_manager(tmp_path)
        with pytest.raises(ValueError, match="scheduled_time must be in the future"):
            mgr.schedule(VALID_DEST, 5.0, PAST_TIME)

    def test_schedule_long_memo_raises(self, tmp_path):
        mgr = make_manager(tmp_path)
        with pytest.raises(ValueError, match="Memo must be 28 characters or fewer"):
            mgr.schedule(VALID_DEST, 5.0, FUTURE_TIME, memo="x" * 29)

    def test_list_payments_filter_by_status(self, tmp_path):
        mgr = make_manager(tmp_path)
        p = mgr.schedule(VALID_DEST, 5.0, FUTURE_TIME)
        # Manually flip to executed
        p.status = STATUS_EXECUTED
        mgr._save()

        pending = mgr.list_payments(status_filter=STATUS_PENDING)
        executed = mgr.list_payments(status_filter=STATUS_EXECUTED)
        assert len(pending) == 0
        assert len(executed) == 1

    def test_list_payments_sorted_by_time(self, tmp_path):
        mgr = make_manager(tmp_path)
        t1 = datetime.utcnow() + timedelta(hours=3)
        t2 = datetime.utcnow() + timedelta(hours=1)
        t3 = datetime.utcnow() + timedelta(hours=2)
        mgr.schedule(VALID_DEST, 1.0, t1)
        mgr.schedule(VALID_DEST, 2.0, t2)
        mgr.schedule(VALID_DEST, 3.0, t3)

        payments = mgr.list_payments()
        times = [p.scheduled_time for p in payments]
        assert times == sorted(times)

    def test_cancel_pending_payment(self, tmp_path):
        mgr = make_manager(tmp_path)
        p = mgr.schedule(VALID_DEST, 10.0, FUTURE_TIME)
        cancelled = mgr.cancel(p.id)
        assert cancelled.status == STATUS_CANCELLED

    def test_cancel_non_pending_raises(self, tmp_path):
        mgr = make_manager(tmp_path)
        p = mgr.schedule(VALID_DEST, 10.0, FUTURE_TIME)
        p.status = STATUS_EXECUTED
        mgr._save()
        with pytest.raises(RuntimeError, match="Cannot cancel"):
            mgr.cancel(p.id)

    def test_cancel_unknown_id_raises(self, tmp_path):
        mgr = make_manager(tmp_path)
        with pytest.raises(KeyError):
            mgr.cancel("nonexistent-id")

    def test_cancel_by_prefix(self, tmp_path):
        mgr = make_manager(tmp_path)
        p = mgr.schedule(VALID_DEST, 10.0, FUTURE_TIME)
        cancelled = mgr.cancel(p.id[:8])
        assert cancelled.status == STATUS_CANCELLED

    def test_get_payment_by_full_id(self, tmp_path):
        mgr = make_manager(tmp_path)
        p = mgr.schedule(VALID_DEST, 10.0, FUTURE_TIME)
        fetched = mgr.get(p.id)
        assert fetched.id == p.id

    # ------------------------------------------------------------------
    # execute_due tests
    # ------------------------------------------------------------------

    def test_execute_due_skips_future_payments(self, tmp_path):
        mgr = make_manager(tmp_path)
        mgr.schedule(VALID_DEST, 5.0, FUTURE_TIME)
        results = mgr.execute_due(source_secret="SDUMMY")
        assert results == []

    def test_execute_due_processes_past_payments(self, tmp_path):
        mgr = make_manager(tmp_path)
        # Bypass future-time validation: inject directly
        p = ScheduledPayment(
            destination=VALID_DEST,
            amount=1.0,
            scheduled_time=PAST_TIME,
            payment_id="test-past-id",
        )
        mgr._payments[p.id] = p
        mgr._save()

        mock_response = {"hash": "abc123"}
        with patch("stellar_agent.scheduled_payments.StellarClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.send_payment.return_value = mock_response

            results = mgr.execute_due(source_secret="SDUMMYSECRET1234567890123456789012345678901234567")

        assert len(results) == 1
        assert results[0]["status"] == STATUS_EXECUTED
        assert results[0]["hash"] == "abc123"

    def test_execute_due_marks_failed_on_error(self, tmp_path):
        mgr = make_manager(tmp_path)
        p = ScheduledPayment(
            destination=VALID_DEST,
            amount=1.0,
            scheduled_time=PAST_TIME,
            payment_id="test-fail-id",
        )
        mgr._payments[p.id] = p
        mgr._save()

        with patch("stellar_agent.scheduled_payments.StellarClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.send_payment.side_effect = RuntimeError("Network error")

            results = mgr.execute_due(source_secret="SDUMMYSECRET1234567890123456789012345678901234567")

        assert results[0]["status"] == STATUS_FAILED
        assert "Network error" in results[0]["error"]

    def test_execute_due_no_secret_raises(self, tmp_path):
        mgr = make_manager(tmp_path)
        with patch("stellar_agent.scheduled_payments.config") as mock_conf:
            mock_conf.source_secret = ""
            with pytest.raises(RuntimeError, match="No source secret"):
                mgr.execute_due()

    def test_execute_due_reschedules_daily(self, tmp_path):
        mgr = make_manager(tmp_path)
        p = ScheduledPayment(
            destination=VALID_DEST,
            amount=1.0,
            scheduled_time=PAST_TIME,
            recurrence="daily",
            payment_id="test-daily-id",
        )
        mgr._payments[p.id] = p
        mgr._save()

        with patch("stellar_agent.scheduled_payments.StellarClient") as MockClient:
            mock_client = MockClient.return_value
            mock_client.send_payment.return_value = {"hash": "xyz"}
            mgr.execute_due(source_secret="SDUMMY")

        # Original executed + 1 new rescheduled payment
        all_payments = mgr.list_payments()
        assert len(all_payments) == 2
        new_p = [pa for pa in all_payments if pa.id != p.id][0]
        assert new_p.status == STATUS_PENDING
        assert new_p.recurrence == "daily"

    def test_corrupt_storage_loads_empty(self, tmp_path):
        storage = os.path.join(str(tmp_path), "scheduled.json")
        with open(storage, "w") as f:
            f.write("{corrupt json[")
        mgr = ScheduledPaymentManager(storage_path=storage)
        assert mgr.list_payments() == []
