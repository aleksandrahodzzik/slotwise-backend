# Interview notes

Quote the table in [BENCHMARKS.md](BENCHMARKS.md). Those numbers came from
`make bench` on a specific machine. If the file still says "no numbers yet",
run it. Don't invent a latency.

## 60-second pitch

Slotwise is a booking API for a room that has a fixed number of seats in each
time slot. The race is the product: many requests can hit one slot together,
and PostgreSQL decides who gets a seat. The default path is one READ COMMITTED
transaction. It locks the slot with `SELECT … FOR UPDATE`, checks
`booked_count < capacity`, inserts the booking, and increments the counter.
The next transaction waits on that row, reads the committed count, and gets a
409 once the seats are gone. A second strategy does the same claim with one
conditional `UPDATE … WHERE booked_count < capacity`. A third strategy, named
`naive`, is a demo of the bug: it reads, sleeps, and writes a stale count.
Tests send 50 concurrent bookings at a 3-seat slot and assert exactly 3
succeed. The load test sends 200 at a 5-seat slot and counts confirmed rows
in Postgres, because the naive counter lies.

## Read the code in this order

About an hour.

1. `src/slotwise/booking/strategies.py` — the lock, the conditional update, and the broken demo.
2. `src/slotwise/booking/service.py` — isolation level, idempotency rollback, cancel order.
3. `src/slotwise/db/models.py` — why the CHECK constraint is not the lock.
4. `alembic/versions/0001_initial.py` — the same rules in DDL, including the partial unique index.
5. `src/slotwise/api/routes.py` — status codes and `Idempotency-Key`.
6. `tests/integration/test_concurrency.py` — the claim the tests actually make.
7. `scripts/bench.py` and `scripts/loadtest.py` — where the benchmark numbers come from.

## Questions

### What does `SELECT … FOR UPDATE` change here?

Two transactions can both see `booked_count = 2` on a 3-seat slot if they
only read. `FOR UPDATE` makes the second one wait. When the first commits,
the second reads 3 and returns 409. The client does not retry. The seat is
either taken or it isn't.

### What does READ COMMITTED guarantee in this transaction?

Each statement sees only data that was committed before that statement
started. It does not mean the count I read at the beginning is still true at
the insert. That is why the read of the slot and the decision to insert have
to be the same locked critical section, not two separate requests.

### Why is READ COMMITTED enough? Why not SERIALIZABLE?

The row lock closes the window READ COMMITTED would otherwise leave open.
The waiter observes the committed count and answers 409. SERIALIZABLE would
also let only one writer win, then fail the other with a serialization error
(`40001`). The API would have to retry that error and might still end on 409.
The lock turns the race into a queue and a normal conflict.

### How is the conditional UPDATE different?

`UPDATE slots SET booked_count = booked_count + 1 WHERE id = :id AND booked_count < capacity RETURNING id`
claims the seat in one statement. Zero rows means the slot is full, or it
never existed (the code checks existence first). Postgres still locks the
row for the update. The difference is where the condition lives: in Python
after an explicit lock, or in the `WHERE` clause. Both commit at most
`capacity` confirmed bookings. The default is the explicit lock because the
wait-then-read story is easier to point at in a review.

### Why not an optimistic version column?

`UPDATE … WHERE version = :seen` lets the loser get zero rows and retry.
That is a fine design when conflicts are rare and the work between read and
write is long. Here the whole decision is "is there a seat", and a retry
loop is extra client behavior. The row lock gives one answer.

### Why not an EXCLUDE constraint?

A range exclusion fits a resource that can take one person at a time, so two
bookings cannot overlap. These slots have a capacity. Three people at 09:00
is success, not a conflict. An exclusion constraint does not express "at most
three". The counter on the locked row does.

### Why isn't `CHECK (booked_count BETWEEN 0 AND capacity)` enough?

It rejects an illegal counter. It does not stop two transactions from both
deciding the counter is legal and both inserting a booking. Without the
lock, the loser often becomes an `IntegrityError` and a 500, not a clean
409. The check also cannot see the `bookings` table. A writer that stores a
stale count keeps `booked_count` legal while more than `capacity` rows are
confirmed. That is what `book_naive` does on purpose.

### What does the naive strategy prove?

It counts confirmed rows, sleeps (`NAIVE_DELAY_MS`), and inserts if that old
number was under capacity. Concurrent callers all read the same number.
`booked_count` is then set to `observed + 1`, not to a SQL increment, so the
CHECK stays quiet and the last writer leaves the counter low. The benchmark
counts `confirmed - capacity` from the bookings table. It is not the
default, and the process logs a warning when it is selected. CI does not run
it.

### How do book and cancel avoid a deadlock?

Both paths lock the slot row first. Booking never locks the booking row.
Cancel peeks `slot_id` without locking the booking, locks the slot, then
locks the booking. One order means they wait instead of deadlocking. A
second cancel sees `status = cancelled` and does not decrement again.
Compare that status with `==`. It is an enum value, not a guaranteed
singleton.

### How would you book three slots in one request?

One transaction. `SELECT … FROM slots WHERE id IN (…) ORDER BY id FOR UPDATE`,
so every caller locks in the same order. Check every slot, insert every
booking, increment every counter, commit once. Locking slot 2 then slot 1
in another request is how you build a deadlock. This repository does not
ship that endpoint yet.

### What does `Idempotency-Key` do?

The header is a unique nullable column. Postgres allows many nulls, so
ordinary bookings are unaffected. A replay with the same key returns the
original booking and HTTP 200, not a second row. Under concurrency the
unique index lets one insert commit. The loser rolls back, which undoes its
counter increment, then reads the row that won and returns 200. The recheck
sits after the row lock, and again after a conditional update that claimed
nothing, so a replay that lost the race is not turned into a 409.

### Why can't the same email take two seats, and why can it come back after cancel?

A partial unique index on `(slot_id, customer_email) WHERE status = 'confirmed'`
makes the second confirmed seat a 409. The email is lowercased before
insert. A cancelled row drops out of that index, and cancel decrements
`booked_count` under the same slot lock, so the person can book again.

### What if the insert loses a unique race after the counter moved?

The increment and the insert share a transaction. `IntegrityError` rolls
both back. The idempotency handler then returns the existing row. A duplicate
confirmed email becomes 409. A CHECK failure becomes 409 as well, which is
the "we lost the race badly" path, not the path the lock is supposed to take.

### How is the pool sized?

Default `pool_size` 10 and `max_overflow` 50 on the compose service, 4
workers. That is up to about 240 connections from one container. Postgres is
started with `max_connections=300` because the naive demo holds a connection
open during its sleep, and a burst of waiters on the row lock also holds
connections. Tests use overflow 60 so 50 concurrent bookings have a
connection each. `expire_on_commit` is false so the booking id is still on
the object after commit, which is what the 201 body needs.

### What changes with a second API instance?

Nothing in the lock. It lives in Postgres, so two hosts queue on the same
row. Each host brings its own pool. Two containers at this sizing can pass
300 connections. The next change is to size the pool from the number of
instances, not to move the lock into a process.

### What would you add before this took real money?

Authentication and a tenant on every row. Multi-slot booking with the
ordered lock above. An outbox row written in the same transaction as the
booking, so a confirmation email is not a second side effect that can
succeed when the booking rolls back, or the other way around. The desk is a
demo: it trusts whatever email the browser types.
