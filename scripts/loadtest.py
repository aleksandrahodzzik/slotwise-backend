"""Fire N concurrent bookings at one slot and count the rows Postgres kept.

Confirmed bookings come from the database. HTTP 201s are not the source of
truth: the naive strategy can write a legal booked_count and still insert
more confirmed rows than the slot can hold.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def pg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def run(args: argparse.Namespace) -> dict[str, object]:
    timeout = httpx.Timeout(120.0)
    limits = httpx.Limits(max_connections=args.requests, max_keepalive_connections=args.requests)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout, limits=limits) as client:
        health = await client.get("/health")
        health.raise_for_status()
        body = health.json()
        if body.get("strategy") != args.strategy:
            raise SystemExit(
                f"/health reports strategy {body.get('strategy')!r}, expected {args.strategy!r}"
            )
        resource = await client.post(
            "/api/v1/resources",
            json={"name": f"Bench {args.strategy}", "kind": "room"},
        )
        resource.raise_for_status()
        start = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
        created = await client.post(
            f"/api/v1/resources/{resource.json()['id']}/slots",
            json={
                "from": start.isoformat(),
                "to": (start + timedelta(minutes=30)).isoformat(),
                "duration_minutes": 30,
                "capacity": args.capacity,
            },
        )
        created.raise_for_status()
        slot_id = int(created.json()[0]["id"])

        async def once(index: int) -> tuple[int, float]:
            started = time.perf_counter()
            try:
                response = await client.post(
                    "/api/v1/bookings",
                    json={"slot_id": slot_id, "customer_email": f"bench{index}@example.com"},
                )
            except httpx.HTTPError:
                return 0, (time.perf_counter() - started) * 1000
            return response.status_code, (time.perf_counter() - started) * 1000

        print(f"{args.strategy}: sending {args.requests} concurrent bookings", file=sys.stderr)
        outcomes = await asyncio.gather(*(once(i) for i in range(args.requests)))

    codes = [code for code, _latency in outcomes]
    latencies = [latency for _code, latency in outcomes]
    errors = sum(1 for code in codes if code == 0 or code >= 500)
    connection = await asyncpg.connect(pg_dsn(args.database_url))
    try:
        confirmed = int(
            await connection.fetchval(
                "SELECT COUNT(*) FROM bookings WHERE slot_id = $1 AND status = 'confirmed'",
                slot_id,
            )
        )
        row = await connection.fetchrow(
            "SELECT capacity, booked_count FROM slots WHERE id = $1",
            slot_id,
        )
    finally:
        await connection.close()
    if row is None:
        raise SystemExit(f"slot {slot_id} disappeared before it could be counted")
    capacity = int(row["capacity"])
    booked_count = int(row["booked_count"])
    return {
        "strategy": args.strategy,
        "requests": args.requests,
        "capacity": capacity,
        "confirmed": confirmed,
        "booked_count": booked_count,
        "overbooked_by": max(0, confirmed - capacity),
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "errors": errors,
        "http_201": sum(1 for code in codes if code == 201),
        "http_409": sum(1 for code in codes if code == 409),
        "http_other": sum(1 for code in codes if code not in {0, 201, 409} and code < 500),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--capacity", type=int, default=5)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://slotwise:slotwise@localhost:5432/slotwise",
        ),
    )
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
