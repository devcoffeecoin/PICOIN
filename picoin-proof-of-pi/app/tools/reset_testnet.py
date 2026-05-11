import argparse
import shutil
from pathlib import Path

from app.core.settings import DATA_DIR, DATABASE_PATH
from app.db.database import init_db


TESTNET_DIR = DATA_DIR / "testnet"


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset the local Picoin testnet database and demo files.")
    parser.add_argument("--yes", action="store_true", help="Confirm the reset.")
    parser.add_argument("--keep-identities", action="store_true", help="Keep files under data/testnet.")
    args = parser.parse_args()

    if not args.yes:
        print("Refusing to reset without --yes.")
        raise SystemExit(2)

    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()
        print(f"Deleted {DATABASE_PATH}")

    if not args.keep_identities and TESTNET_DIR.exists():
        shutil.rmtree(TESTNET_DIR)
        print(f"Deleted {TESTNET_DIR}")

    init_db()
    print(f"Initialized clean database at {DATABASE_PATH}")


if __name__ == "__main__":
    main()
