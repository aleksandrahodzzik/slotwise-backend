"""Open nine days of the North studio on a desk that is already running."""

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
        opened = 0
        tomorrow = (datetime.now(WARSAW) + timedelta(days=1)).date()
        for offset in range(9):
            day = tomorrow + timedelta(days=offset)
            start_hour, end_hour, capacity = _hours(day)
            start = datetime(day.year, day.month, day.day, start_hour, 0, tzinfo=WARSAW)
            end = datetime(day.year, day.month, day.day, end_hour, 0, tzinfo=WARSAW)
            slots = client.post(
                f"/api/v1/resources/{created.json()['id']}/slots",
                json={
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "duration_minutes": 60,
                    "capacity": capacity,
                },
            )
            slots.raise_for_status()
            opened += len(slots.json())
        print(f"opened {opened} slots in the North studio")


def _hours(day) -> tuple[int, int, int]:
    if day.weekday() == 6:
        return 12, 15, 2
    if day.weekday() == 5:
        return 11, 15, 2
    return 10, 16, 3


if __name__ == "__main__":
    main()
