"""Run the three booking strategies and write only the numbers this machine produced.

The markdown is written after every strategy has returned. A failed run leaves
the previous file alone instead of inventing a row.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ("row_lock", "atomic_update", "naive")
NAIVE_DELAY_MS = 40
WORKERS = 4
REQUESTS = 200
START = "<!-- BENCH:START -->"
END = "<!-- BENCH:END -->"


def run(args: list[str], env: dict[str, str]) -> None:
    print("+", " ".join(args), file=sys.stderr)
    subprocess.run(["docker", "compose", *args], cwd=ROOT, env=env, check=True)


def strategy_env(strategy: str) -> dict[str, str]:
    env = os.environ.copy()
    env["BOOKING_STRATEGY"] = strategy
    env["NAIVE_DELAY_MS"] = str(NAIVE_DELAY_MS)
    env["WEB_CONCURRENCY"] = str(WORKERS)
    return env


def recreate(strategy: str, build: bool) -> None:
    args = ["up", "-d", "--force-recreate", "--wait", "--no-deps", "api"]
    if build:
        args.insert(2, "--build")
    run(args, strategy_env(strategy))


async def wait_for_strategy(strategy: str) -> None:
    deadline = time.monotonic() + 120
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get("http://localhost:8000/health")
                if response.status_code == 200 and response.json().get("strategy") == strategy:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise SystemExit(f"/health did not report strategy {strategy} within 120s")


def truncate(env: dict[str, str]) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "slotwise",
            "-d",
            "slotwise",
            "-c",
            "TRUNCATE bookings, slots, resources RESTART IDENTITY CASCADE",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def loadtest(strategy: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "loadtest.py"),
            "--strategy",
            strategy,
            "--requests",
            str(REQUESTS),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        raise SystemExit(f"load test failed for {strategy} (exit {completed.returncode})")
    line = completed.stdout.strip().splitlines()[-1]
    result = json.loads(line)
    if result.get("strategy") != strategy:
        raise SystemExit(f"load test returned {result.get('strategy')!r}, expected {strategy!r}")
    return result


def markdown_table(results: list[dict[str, object]]) -> str:
    lines = [
        "| strategy | confirmed bookings | overbooked by | p50 latency | p95 latency | errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in results:
        p50 = float(row["p50_ms"])
        p95 = float(row["p95_ms"])
        lines.append(
            f"| {row['strategy']} | {row['confirmed']} | {row['overbooked_by']} "
            f"| {p50:.1f} ms | {p95:.1f} ms | {row['errors']} |"
        )
    return "\n".join(lines)


def write_docs(results: list[dict[str, object]]) -> None:
    table = markdown_table(results)
    machine = platform.platform()
    cpu = platform.processor() or "not reported"
    stamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    docker = "not collected"
    try:
        docker = subprocess.check_output(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            text=True,
            cwd=ROOT,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        docker = "not collected"
    details = "\n".join(
        (
            f"- `{row['strategy']}`: booked_count {row['booked_count']}, "
            f"HTTP 201 {row['http_201']}, HTTP 409 {row['http_409']}, "
            f"other HTTP {row['http_other']}"
        )
        for row in results
    )
    document = f"""# Benchmarks

Measured {stamp} by `make bench` on this machine. Confirmed bookings are
`COUNT(*)` of `status = 'confirmed'` for the slot under test. They are not
counted from HTTP 201 responses. The naive strategy writes `booked_count`
from a stale read, so that counter can stay inside its CHECK while confirmed
rows pass capacity. Overbooked by is `max(0, confirmed - capacity)`.

Do not type new numbers in here. Re-run `make bench`.

## Machine

- platform: {machine}
- processor: {cpu}
- CPUs: {os.cpu_count()}
- Docker server: {docker}
- command: `make bench`

## Load

{REQUESTS} concurrent `POST /api/v1/bookings` calls, one slot of capacity 5,
unique emails, no idempotency key. API container: {WORKERS} uvicorn workers.
`NAIVE_DELAY_MS={NAIVE_DELAY_MS}` for every strategy (only the naive path sleeps).
p50 and p95 use linear interpolation over every attempt, including 409s and
failed connections.

{table}

{details}
"""
    (ROOT / "docs" / "BENCHMARKS.md").write_text(document, encoding="utf-8", newline="\n")
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit("README.md is missing the BENCH markers")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    readme.write_text(
        f"{before}{START}\n{table}\n{END}{after}",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    run(["up", "-d", "--wait", "db"], strategy_env("row_lock"))
    results: list[dict[str, object]] = []
    try:
        for index, strategy in enumerate(STRATEGIES):
            recreate(strategy, build=index == 0)
            asyncio.run(wait_for_strategy(strategy))
            truncate(strategy_env(strategy))
            results.append(loadtest(strategy))
        write_docs(results)
        print(markdown_table(results))
    finally:
        try:
            recreate("row_lock", build=False)
        except (subprocess.CalledProcessError, SystemExit) as exc:
            print(f"could not restore BOOKING_STRATEGY=row_lock: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
