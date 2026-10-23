from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


async def create_resource(client: AsyncClient, name: str = "North studio") -> int:
    response = await client.post("/api/v1/resources", json={"name": name, "kind": "room"})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def create_slot(client: AsyncClient, resource_id: int, capacity: int = 3) -> int:
    end = START + timedelta(minutes=30)
    response = await client.post(
        f"/api/v1/resources/{resource_id}/slots",
        json={
            "from": START.isoformat(),
            "to": end.isoformat(),
            "duration_minutes": 30,
            "capacity": capacity,
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()[0]["id"])
