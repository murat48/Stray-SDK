"""Transaction history module for Stellar Agent."""
from stellar_sdk import Server
from stellar_sdk.exceptions import NotFoundError
from typing import Dict, Any, List, Optional
from datetime import datetime
from decimal import Decimal
from .config import config


class TransactionHistory:
    """Fetches and formats Stellar account transaction history."""

    def __init__(self):
        self.server = Server(config.horizon_url)

    def get_payments(
        self,
        account_id: str,
        limit: int = 10,
        cursor: Optional[str] = None,
        order: str = "desc",
    ) -> Dict[str, Any]:
        """
        Fetch payment operations for a given account.

        Args:
            account_id: Public key of the account
            limit: Number of records to return (max 200)
            cursor: Paging token for pagination
            order: 'asc' or 'desc' (default: 'desc' = newest first)

        Returns:
            Dict with 'records' list and 'next_cursor' for pagination

        Raises:
            RuntimeError: If account not found or network issues occur
        """
        try:
            limit = max(1, min(limit, 200))
            builder = (
                self.server.payments()
                .for_account(account_id)
                .order(order)
                .limit(limit)
            )
            if cursor:
                builder = builder.cursor(cursor)

            response = builder.call()
            records = response.get("_embedded", {}).get("records", [])
            next_cursor = None
            if records:
                next_cursor = records[-1].get("paging_token")

            return {
                "records": records,
                "next_cursor": next_cursor,
                "count": len(records),
            }
        except NotFoundError:
            raise RuntimeError(
                f"Account {account_id} not found on the Stellar network."
            )
        except Exception as e:
            raise RuntimeError(f"Failed to fetch payment history: {e}")

    def format_payment(self, record: Dict[str, Any], account_id: str) -> Dict[str, Any]:
        """
        Format a raw payment record into a human-readable dict.

        Args:
            record: Raw payment record from Horizon
            account_id: The account whose perspective we use (to detect direction)

        Returns:
            Formatted payment dictionary
        """
        op_type = record.get("type", "unknown")

        if op_type == "payment":
            asset = (
                "XLM"
                if record.get("asset_type") == "native"
                else f"{record.get('asset_code', '?')}/{record.get('asset_issuer', '')[:8]}…"
            )
            amount = record.get("amount", "0")
            from_addr = record.get("from", "")
            to_addr = record.get("to", "")
            direction = "RECEIVED" if to_addr == account_id else "SENT"
            counterpart = from_addr if direction == "RECEIVED" else to_addr
        elif op_type == "create_account":
            asset = "XLM"
            amount = record.get("starting_balance", "0")
            from_addr = record.get("funder", "")
            to_addr = record.get("account", "")
            direction = "RECEIVED" if to_addr == account_id else "SENT"
            counterpart = from_addr if direction == "RECEIVED" else to_addr
        else:
            asset = "—"
            amount = "—"
            direction = "—"
            counterpart = "—"

        # Parse ISO8601 created_at
        created_at_raw = record.get("created_at", "")
        try:
            created_at = datetime.strptime(created_at_raw, "%Y-%m-%dT%H:%M:%SZ").strftime(
                "%Y-%m-%d %H:%M UTC"
            )
        except ValueError:
            created_at = created_at_raw

        return {
            "id": record.get("id", ""),
            "type": op_type,
            "direction": direction,
            "amount": amount,
            "asset": asset,
            "counterpart": counterpart,
            "transaction_hash": record.get("transaction_hash", ""),
            "paging_token": record.get("paging_token", ""),
            "created_at": created_at,
        }

    def get_formatted_history(
        self,
        account_id: str,
        limit: int = 10,
        cursor: Optional[str] = None,
        order: str = "desc",
    ) -> Dict[str, Any]:
        """
        Fetch and format payment history for an account.

        Returns:
            Dict with 'payments' (list of formatted records), 'next_cursor', 'count'
        """
        raw = self.get_payments(account_id, limit=limit, cursor=cursor, order=order)
        formatted = [self.format_payment(r, account_id) for r in raw["records"]]
        return {
            "payments": formatted,
            "next_cursor": raw["next_cursor"],
            "count": raw["count"],
        }
