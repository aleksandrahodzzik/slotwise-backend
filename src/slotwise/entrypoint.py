"""Process entrypoint used by the container.

Migrations run once, in this process, before uvicorn forks its workers.
Running them inside each worker races on DDL.
"""

import os
import subprocess
import sys


def main() -> None:
    subprocess.check_call([sys.executable, "-m", "alembic", "upgrade", "head"])
    workers = os.environ.get("WEB_CONCURRENCY", "4")
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "slotwise.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--workers",
            workers,
        ],
    )


if __name__ == "__main__":
    main()
