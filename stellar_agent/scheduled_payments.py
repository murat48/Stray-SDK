"""Scheduled Payments module for Stellar Agent.

Allows scheduling one-time or recurring XLM payments with persistent storage.
"""
import json
import uuid
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional

from .config import config
from .client import StellarClient
from .utils.validators import is_valid_stellar_address, is_valid_amount

# Default file path for persisted scheduled payments
_DEFAULT_STORAGE_PATH = os.path.join(
    os.path.expanduser("~"), ".stellar_agent", "scheduled_payments.json"
)

RECURRENCE_OPTIONS = ("none", "daily", "weekly", "monthly")
STATUS_PENDING = "pending"
STATUS_EXECUTED = "executed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


class ScheduledPayment:
    """Represents a single scheduled payment."""

    def __init__(
        self,
        destination: str,
        amount: float,
        scheduled_time: datetime,
        recurrence: str = "none",
        memo: str = "",
        payment_id: Optional[str] = None,
        status: str = STATUS_PENDING,
        last_executed_at: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ):
        if not is_valid_stellar_address(destination):
            raise ValueError(f"Invalid destination address: {destination}")
        if not is_valid_amount(amount):
            raise ValueError("Amount must be positive.")
        if recurrence not in RECURRENCE_OPTIONS:
            raise ValueError(
                f"Invalid recurrence '{recurrence}'. Choose from: {RECURRENCE_OPTIONS}"
            )

        self.id: str = payment_id or str(uuid.uuid4())
        self.destination: str = destination
        self.amount: float = amount
        self.scheduled_time: datetime = scheduled_time
        self.recurrence: str = recurrence
        self.memo: str = memo
        self.status: str = status
        self.last_executed_at: Optional[str] = last_executed_at
        self.failure_reason: Optional[str] = failure_reason

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "destination": self.destination,
            "amount": self.amount,
            "scheduled_time": self.scheduled_time.isoformat(),
            "recurrence": self.recurrence,
            "memo": self.memo,
            "status": self.status,
            "last_executed_at": self.last_executed_at,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScheduledPayment":
        return cls(
            destination=data["destination"],
            amount=data["amount"],
            scheduled_time=datetime.fromisoformat(data["scheduled_time"]),
            recurrence=data.get("recurrence", "none"),
            memo=data.get("memo", ""),
            payment_id=data["id"],
            status=data.get("status", STATUS_PENDING),
            last_executed_at=data.get("last_executed_at"),
            failure_reason=data.get("failure_reason"),
        )

    def __repr__(self) -> str:
        return (
            f"ScheduledPayment(id={self.id[:8]}…, dest={self.destination[:8]}…, "
            f"amount={self.amount} XLM, at={self.scheduled_time.isoformat()}, "
            f"recurrence={self.recurrence}, status={self.status})"
        )


class ScheduledPaymentManager:
    """Manages scheduling, persistence, and execution of scheduled payments."""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path: str = storage_path or _DEFAULT_STORAGE_PATH
        self._payments: Dict[str, ScheduledPayment] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule(
        self,
        destination: str,
        amount: float,
        scheduled_time: datetime,
        recurrence: str = "none",
        memo: str = "",
    ) -> ScheduledPayment:
        """
        Schedule a new payment.

        Args:
            destination: Destination Stellar public key.
            amount: Amount of XLM to send.
            scheduled_time: When the payment should be executed.
            recurrence: Repeat interval – 'none', 'daily', 'weekly', or 'monthly'.
            memo: Optional memo text (max 28 characters).

        Returns:
            The created ScheduledPayment instance.

        Raises:
            ValueError: If any parameter is invalid.
        """
        if len(memo) > 28:
            raise ValueError("Memo must be 28 characters or fewer.")
        if scheduled_time <= datetime.utcnow():
            raise ValueError("scheduled_time must be in the future.")

        payment = ScheduledPayment(
            destination=destination,
            amount=amount,
            scheduled_time=scheduled_time,
            recurrence=recurrence,
            memo=memo,
        )
        self._payments[payment.id] = payment
        self._save()
        return payment

    def list_payments(self, status_filter: Optional[str] = None) -> List[ScheduledPayment]:
        """
        Return all scheduled payments, optionally filtered by status.

        Args:
            status_filter: One of 'pending', 'executed', 'failed', 'cancelled', or None for all.

        Returns:
            List of ScheduledPayment objects sorted by scheduled_time.
        """
        payments = list(self._payments.values())
        if status_filter:
            payments = [p for p in payments if p.status == status_filter]
        return sorted(payments, key=lambda p: p.scheduled_time)

    def cancel(self, payment_id: str) -> ScheduledPayment:
        """
        Cancel a pending scheduled payment.

        Args:
            payment_id: Full or prefix of the payment UUID.

        Returns:
            The cancelled ScheduledPayment.

        Raises:
            KeyError: If not found.
            RuntimeError: If the payment is not in 'pending' state.
        """
        payment = self._find(payment_id)
        if payment.status != STATUS_PENDING:
            raise RuntimeError(
                f"Cannot cancel payment {payment.id[:8]}… – current status is '{payment.status}'."
            )
        payment.status = STATUS_CANCELLED
        self._save()
        return payment

    def execute_due(self, source_secret: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Execute all pending payments whose scheduled_time has passed.

        Args:
            source_secret: The Stellar secret key to fund payments. Falls back to
                           ``config.source_secret`` when not supplied.

        Returns:
            List of result dicts with keys 'payment_id', 'status', 'hash' (on success),
            and 'error' (on failure).
        """
        secret = source_secret or config.source_secret
        if not secret:
            raise RuntimeError(
                "No source secret provided. Set SOURCE_SECRET in your environment or pass it explicitly."
            )

        client = StellarClient()
        now = datetime.utcnow()
        results: List[Dict[str, Any]] = []

        for payment in self.list_payments(status_filter=STATUS_PENDING):
            if payment.scheduled_time > now:
                continue  # Not due yet

            result: Dict[str, Any] = {"payment_id": payment.id}
            try:
                response = client.send_payment(
                    source_secret=secret,
                    destination_public=payment.destination,
                    amount=payment.amount,
                )
                payment.status = STATUS_EXECUTED
                payment.last_executed_at = datetime.utcnow().isoformat()
                result["status"] = STATUS_EXECUTED
                result["hash"] = response.get("hash", "")

                # Reschedule if recurring
                if payment.recurrence != "none":
                    self._reschedule(payment)

            except Exception as e:
                payment.status = STATUS_FAILED
                payment.failure_reason = str(e)
                result["status"] = STATUS_FAILED
                result["error"] = str(e)

            results.append(result)

        self._save()
        return results

    def get(self, payment_id: str) -> ScheduledPayment:
        """Retrieve a payment by full or prefix ID."""
        return self._find(payment_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find(self, payment_id: str) -> ScheduledPayment:
        """Find a payment by exact or prefix match."""
        # Exact match first
        if payment_id in self._payments:
            return self._payments[payment_id]
        # Prefix match
        matches = [p for pid, p in self._payments.items() if pid.startswith(payment_id)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise KeyError(
                f"Ambiguous ID prefix '{payment_id}' matches {len(matches)} payments."
            )
        raise KeyError(f"Payment '{payment_id}' not found.")

    def _reschedule(self, payment: ScheduledPayment) -> None:
        """Create a new pending payment for the next recurrence cycle."""
        delta_map = {
            "daily": timedelta(days=1),
            "weekly": timedelta(weeks=1),
        }
        if payment.recurrence in delta_map:
            next_time = payment.scheduled_time + delta_map[payment.recurrence]
        elif payment.recurrence == "monthly":
            # Advance by ~1 month (same day next month)
            original = payment.scheduled_time
            month = original.month + 1
            year = original.year + month // 13
            month = month % 12 or 12
            import calendar
            max_day = calendar.monthrange(year, month)[1]
            next_time = original.replace(year=year, month=month, day=min(original.day, max_day))
        else:
            return  # 'none' – no reschedule

        new_payment = ScheduledPayment(
            destination=payment.destination,
            amount=payment.amount,
            scheduled_time=next_time,
            recurrence=payment.recurrence,
            memo=payment.memo,
        )
        self._payments[new_payment.id] = new_payment

    def _load(self) -> None:
        """Load payments from the JSON storage file."""
        if not os.path.exists(self._storage_path):
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for item in data:
                p = ScheduledPayment.from_dict(item)
                self._payments[p.id] = p
        except (json.JSONDecodeError, KeyError, ValueError):
            # Corrupt storage – start fresh rather than crash
            self._payments = {}

    def _save(self) -> None:
        """Persist payments to the JSON storage file."""
        os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
        with open(self._storage_path, "w", encoding="utf-8") as fh:
            json.dump(
                [p.to_dict() for p in self._payments.values()],
                fh,
                indent=2,
                ensure_ascii=False,
            )
