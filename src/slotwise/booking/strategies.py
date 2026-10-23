"""Booking strategies. The isolation level is READ COMMITTED.

READ COMMITTED alone does not stop two transactions from both observing
booked_count < capacity. SELECT … FOR UPDATE closes that window: the second
transaction waits, then reads the count the first one committed. That is
enough. SERIALIZABLE would also reject one writer, but with a serialization
error the client must retry; the row lock turns the same race into a queue
and an ordinary 409 once the seat is gone.

Lock order, whenever a transaction touches both tables: the slot row first,
then the booking row. Booking locks only the slot. Cancellation locks the
slot and then the booking. One order means those two paths wait on each
other instead of deadlocking.
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from slotwise.booking.errors import conflict, not_found
from slotwise.config import get_settings
from slotwise.db.models import Booking, BookingStatus, Slot


def iter_slot_starts(start: datetime, end: datetime, duration_minutes: int) -> list[datetime]:
    duration = timedelta(minutes=duration_minutes)
    cursor = start
    starts: list[datetime] = []
    while cursor + duration <= end:
        starts.append(cursor)
        cursor += duration
    return starts


async def _by_key(session: AsyncSession, key: str) -> Booking | None:
    return await session.scalar(select(Booking).where(Booking.idempotency_key == key))


async def _confirmed_seat(session: AsyncSession, slot_id: int, email: str) -> Booking | None:
    return await session.scalar(
        select(Booking).where(
            Booking.slot_id == slot_id,
            Booking.customer_email == email,
            Booking.status == BookingStatus.confirmed,
        )
    )


def _new_booking(slot_id: int, email: str, key: str | None) -> Booking:
    return Booking(
        slot_id=slot_id,
        customer_email=email,
        status=BookingStatus.confirmed,
        idempotency_key=key,
    )


async def book_row_lock(
    session: AsyncSession, slot_id: int, email: str, key: str | None
) -> tuple[Booking, bool]:
    """Lock the slot row, then insert. `bool` is True when this call created the row (201)."""
    slot = await session.scalar(select(Slot).where(Slot.id == slot_id).with_for_update())
    if slot is None:
        raise not_found("Slot does not exist.")
    if key is not None:
        # Recheck after the lock. A concurrent request with the same key may
        # have committed while we waited.
        existing = await _by_key(session, key)
        if existing is not None:
            return existing, False
    if await _confirmed_seat(session, slot_id, email) is not None:
        raise conflict("You already have a seat in this slot.")
    if slot.booked_count >= slot.capacity:
        raise conflict("This slot is full.")
    booking = _new_booking(slot_id, email, key)
    session.add(booking)
    slot.booked_count += 1
    await session.flush()
    return booking, True


async def book_atomic_update(
    session: AsyncSession, slot_id: int, email: str, key: str | None
) -> tuple[Booking, bool]:
    """One conditional UPDATE claims the seat. Zero rows updated means full, or gone."""
    exists = await session.scalar(select(Slot.id).where(Slot.id == slot_id))
    if exists is None:
        raise not_found("Slot does not exist.")
    if key is not None:
        existing = await _by_key(session, key)
        if existing is not None:
            return existing, False
    if await _confirmed_seat(session, slot_id, email) is not None:
        raise conflict("You already have a seat in this slot.")

    claimed = await session.scalar(
        update(Slot)
        .where(Slot.id == slot_id, Slot.booked_count < Slot.capacity)
        .values(booked_count=Slot.booked_count + 1)
        .returning(Slot.id)
        .execution_options(synchronize_session=False)
    )
    if claimed is None:
        if key is not None:
            existing = await _by_key(session, key)
            if existing is not None:
                return existing, False
        raise conflict("This slot is full.")
    booking = _new_booking(slot_id, email, key)
    session.add(booking)
    await session.flush()
    return booking, True


async def book_naive(
    session: AsyncSession, slot_id: int, email: str, key: str | None
) -> tuple[Booking, bool]:
    """DEMO ONLY. Never the default.

    Reads the number of confirmed bookings, waits, then inserts if that old
    number was under capacity. Concurrent callers all read the same number
    and all insert. booked_count is written from the stale read, so the CHECK
    constraint stays quiet while the bookings table oversells the slot.
    """
    slot = await session.get(Slot, slot_id)
    if slot is None:
        raise not_found("Slot does not exist.")
    if key is not None:
        existing = await _by_key(session, key)
        if existing is not None:
            return existing, False
    observed = await session.scalar(
        select(func.count())
        .select_from(Booking)
        .where(Booking.slot_id == slot_id, Booking.status == BookingStatus.confirmed)
    )
    observed = int(observed or 0)
    delay_ms = get_settings().naive_delay_ms
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000)
    if observed >= slot.capacity:
        raise conflict("This slot is full.")
    booking = _new_booking(slot_id, email, key)
    session.add(booking)
    slot.booked_count = observed + 1
    await session.flush()
    return booking, True


STRATEGIES = {
    "row_lock": book_row_lock,
    "atomic_update": book_atomic_update,
    "naive": book_naive,
}
