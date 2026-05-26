from app.core.settings import DATABASE_PATH
from app.db.database import init_db


def main() -> None:
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()
        print(f"Deleted {DATABASE_PATH}")
    init_db()
    print(f"Initialized clean database at {DATABASE_PATH}")


if __name__ == "__main__":
    main()
