import argparse
import json

from app.db.database import init_db
from app.services.mining import MiningError, request_faucet


def main() -> None:
    parser = argparse.ArgumentParser(description="Credit demo coins from the local testnet faucet.")
    parser.add_argument("account_id")
    parser.add_argument("--type", choices=["miner", "validator"], default="miner", dest="account_type")
    parser.add_argument("--amount", type=float, default=None)
    args = parser.parse_args()

    init_db()
    try:
        result = request_faucet(args.account_id, args.account_type, args.amount)
    except MiningError as exc:
        print(f"Faucet error: {exc.detail}")
        raise SystemExit(exc.status_code) from exc

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
