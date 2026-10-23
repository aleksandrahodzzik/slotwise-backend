# Benchmarks

Measured 2026-10-23T17:01:13+02:00 by `make bench` on this machine. Confirmed bookings are
`COUNT(*)` of `status = 'confirmed'` for the slot under test. They are not
counted from HTTP 201 responses. The naive strategy writes `booked_count`
from a stale read, so that counter can stay inside its CHECK while confirmed
rows pass capacity. Overbooked by is `max(0, confirmed - capacity)`.

Do not type new numbers in here. Re-run `make bench`.

## Machine

- platform: Windows-11-10.0.26200-SP0
- processor: AMD64 Family 25 Model 68 Stepping 1, AuthenticAMD
- CPUs: 12
- Docker server: 29.8.2
- command: `make bench`

## Load

200 concurrent `POST /api/v1/bookings` calls, one slot of capacity 5,
unique emails, no idempotency key. API container: 4 uvicorn workers.
`NAIVE_DELAY_MS=40` for every strategy (only the naive path sleeps).
p50 and p95 use linear interpolation over every attempt, including 409s and
failed connections.

| strategy | confirmed bookings | overbooked by | p50 latency | p95 latency | errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| row_lock | 5 | 0 | 1747.7 ms | 3515.0 ms | 0 |
| atomic_update | 5 | 0 | 1732.9 ms | 3338.5 ms | 0 |
| naive | 11 | 6 | 1681.9 ms | 3203.5 ms | 0 |

- `row_lock`: booked_count 5, HTTP 201 5, HTTP 409 195, other HTTP 0
- `atomic_update`: booked_count 5, HTTP 201 5, HTTP 409 195, other HTTP 0
- `naive`: booked_count 5, HTTP 201 11, HTTP 409 189, other HTTP 0
