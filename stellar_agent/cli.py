"""Command-line interface for Stellar Agent."""
from datetime import datetime
from .client import StellarClient
from .history import TransactionHistory
from .config import config
from .utils.validators import is_valid_stellar_address, is_valid_amount
from .scheduled_payments import ScheduledPaymentManager, RECURRENCE_OPTIONS, STATUS_PENDING

def show_history():
    """Interactive CLI for viewing Stellar transaction history."""
    history = TransactionHistory()

    account_id = input("Enter account public key to view history: ").strip()
    if not is_valid_stellar_address(account_id):
        print("❌ Invalid Stellar address. Must start with 'G' and be 56 characters long.")
        return

    limit_str = input("How many transactions to show? [default: 10]: ").strip()
    try:
        limit = int(limit_str) if limit_str else 10
        if limit < 1:
            raise ValueError
    except ValueError:
        print("❌ Invalid number. Using default of 10.")
        limit = 10

    print(f"\nFetching last {limit} transaction(s) for {account_id[:8]}…\n")
    try:
        result = history.get_formatted_history(account_id, limit=limit)
    except RuntimeError as e:
        print(f"❌ {e}")
        return

    if result["count"] == 0:
        print("ℹ️  No payment history found for this account.")
        return

    print(f"{'#':<4} {'Date':<20} {'Dir':<10} {'Amount':<16} {'Asset':<12} {'Counterpart':<58} {'Tx Hash'}")
    print("-" * 140)
    for i, p in enumerate(result["payments"], start=1):
        direction_icon = "⬇ IN " if p["direction"] == "RECEIVED" else "⬆ OUT"
        print(
            f"{i:<4} {p['created_at']:<20} {direction_icon:<10} "
            f"{p['amount']:<16} {p['asset']:<12} "
            f"{p['counterpart']:<58} {p['transaction_hash']}"
        )

    if result["next_cursor"]:
        print(f"\n💡 Next cursor for pagination: {result['next_cursor']}")


def prompt_and_send():
    """Interactive CLI for sending Stellar payments."""
    client = StellarClient()
    
    # Validate configuration
    try:
        config.validate()
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        print("Please set SOURCE_SECRET in your environment variables or .env file")
        return
    
    while True:
        print("\n--- Stellar Agent ---")
        print("Commands: send | history | exit")
        destination = input("Enter destination public key (or 'history' / 'exit'): ").strip()

        if destination.lower() == "exit":
            break

        if destination.lower() == "history":
            show_history()
            continue
        
        # Validate destination address
        if not is_valid_stellar_address(destination):
            print("❌ Invalid Stellar address. Must start with 'G' and be 56 characters long.")
            continue
        
        amount_str = input("Enter amount to send (in XLM): ").strip()
        
        try:
            amount = float(amount_str)
            if not is_valid_amount(amount):
                print("❌ Amount must be positive.")
                continue
        except ValueError:
            print("❌ Invalid amount. Please enter a number.")
            continue
        
        # Check balance before attempting payment (if enabled)
        if config.balance_check_enabled:
            try:
                source_public_key = config.get_source_public_key()
                sufficient, current_balance, error_msg = client.check_sufficient_balance(source_public_key, amount)
                
                if not sufficient:
                    print(f"❌ {error_msg}")
                    print(f"💡 Please check: account funding, network connectivity, or reduce payment amount.")
                    continue
                else:
                    print(f"💰 Current balance: {current_balance} XLM (sufficient for payment)")
            except Exception as e:
                print(f"⚠️  Warning: Could not verify balance: {e}")
                print("Proceeding with payment attempt...")
        
        print(f"Sending {amount} XLM to {destination}...")
        try:
            response = client.send_payment(config.source_secret, destination, amount)
            print("✅ Transaction Successful!")
            print("Transaction Hash:", response['hash'])
            if 'ledger' in response:
                print("Ledger:", response['ledger'])
        except RuntimeError as e:
            print(f"❌ Transaction Failed: {e}")
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            print("💡 Please check your network connectivity and configuration.")

def schedule_payment():
    """Interactive CLI for scheduling a future Stellar payment."""
    manager = ScheduledPaymentManager()

    destination = input("Enter destination public key: ").strip()
    if not is_valid_stellar_address(destination):
        print("❌ Invalid Stellar address.")
        return

    amount_str = input("Enter amount to send (in XLM): ").strip()
    try:
        amount = float(amount_str)
        if not is_valid_amount(amount):
            print("❌ Amount must be positive.")
            return
    except ValueError:
        print("❌ Invalid amount.")
        return

    date_str = input("Enter scheduled date/time (YYYY-MM-DD HH:MM, UTC): ").strip()
    try:
        scheduled_time = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
    except ValueError:
        print("❌ Invalid date format. Use YYYY-MM-DD HH:MM.")
        return

    print(f"Recurrence options: {', '.join(RECURRENCE_OPTIONS)}")
    recurrence = input("Recurrence [default: none]: ").strip().lower() or "none"
    if recurrence not in RECURRENCE_OPTIONS:
        print(f"❌ Invalid recurrence. Choose from: {RECURRENCE_OPTIONS}")
        return

    memo = input("Memo (optional, max 28 chars): ").strip()

    try:
        payment = manager.schedule(
            destination=destination,
            amount=amount,
            scheduled_time=scheduled_time,
            recurrence=recurrence,
            memo=memo,
        )
        print(f"\n✅ Payment scheduled successfully!")
        print(f"   ID        : {payment.id}")
        print(f"   To        : {payment.destination}")
        print(f"   Amount    : {payment.amount} XLM")
        print(f"   At (UTC)  : {payment.scheduled_time.isoformat()}")
        print(f"   Recurrence: {payment.recurrence}")
        if payment.memo:
            print(f"   Memo      : {payment.memo}")
    except ValueError as e:
        print(f"❌ {e}")


def list_scheduled_payments():
    """List all scheduled payments."""
    manager = ScheduledPaymentManager()
    payments = manager.list_payments()

    if not payments:
        print("ℹ️  No scheduled payments found.")
        return

    print(f"\n{'ID':10} {'Status':12} {'Scheduled (UTC)':20} {'Amount':10} {'Recurrence':10} {'Destination'}")
    print("-" * 110)
    for p in payments:
        print(
            f"{p.id[:8]:<10} {p.status:<12} {p.scheduled_time.strftime('%Y-%m-%d %H:%M'):<20} "
            f"{p.amount:<10} {p.recurrence:<10} {p.destination}"
        )


def cancel_scheduled_payment():
    """Cancel a pending scheduled payment by ID."""
    manager = ScheduledPaymentManager()
    payment_id = input("Enter payment ID (or prefix) to cancel: ").strip()
    try:
        payment = manager.cancel(payment_id)
        print(f"✅ Payment {payment.id[:8]}… cancelled.")
    except (KeyError, RuntimeError) as e:
        print(f"❌ {e}")


def execute_due_payments():
    """Execute all pending payments that are now due."""
    try:
        config.validate()
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        return

    manager = ScheduledPaymentManager()
    print("Executing due payments…")
    try:
        results = manager.execute_due(source_secret=config.source_secret)
    except RuntimeError as e:
        print(f"❌ {e}")
        return

    if not results:
        print("ℹ️  No payments were due.")
        return

    for r in results:
        if r["status"] == "executed":
            print(f"✅ Payment {r['payment_id'][:8]}… executed. Hash: {r.get('hash', 'N/A')}")
        else:
            print(f"❌ Payment {r['payment_id'][:8]}… FAILED: {r.get('error')}")


def run():
    """Entry point for the CLI."""
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "history":
        show_history()
    elif len(sys.argv) > 1 and sys.argv[1] == "schedule":
        schedule_payment()
    elif len(sys.argv) > 1 and sys.argv[1] == "list-scheduled":
        list_scheduled_payments()
    elif len(sys.argv) > 1 and sys.argv[1] == "cancel-scheduled":
        cancel_scheduled_payment()
    elif len(sys.argv) > 1 and sys.argv[1] == "execute-due":
        execute_due_payments()
    else:
        prompt_and_send()
