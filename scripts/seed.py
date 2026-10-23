"""Open tomorrow's North studio on a desk that is already running."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

WARSAW = ZoneInfo("Europe/Warsaw")


def main() -> None:
    base = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
    with httpx.Client(base_url=base, timeout=30) as client:
        client.get("/health").raise_for_status()
        rooms = client.get("/api/v1/resources")
        rooms.raise_for_status()
        if any(room["name"] == "North studio" for room in rooms.json()):
            print("North studio is already on the desk")
            return
        created = client.post("/api/v1/resources", json={"name": "North studio", "kind": "room"})
        created.raise_for_status()
        tomorrow = (datetime.now(WARSAW) + timedelta(days=1)).date()
        start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 9, 0, tzinfo=WARSAW)
        end = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 17, 0, tzinfo=WARSAW)
        slots = client.post(
            f"/api/v1/resources/{created.json()['id']}/slots",
            json={
                "from": start.isoformat(),
                "to": end.isoformat(),
                "duration_minutes": 30,
                "capacity": 3,
            },
        )
        slots.raise_for_status()
        print(f"opened {len(slots.json())} slots in the North studio")


if __name__ == "__main__":
    main()
