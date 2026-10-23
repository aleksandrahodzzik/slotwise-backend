from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from slotwise.booking.errors import conflict, not_found
from slotwise.booking.strategies import STRATEGIES, iter_slot_starts
from slotwise.config import get_settings
from slotwise.db.models import Booking, BookingStatus, Resource, Slot


def _constraint_name(exc: IntegrityError) -> str:
    orig = getattr(exc, "orig", None)
    name = getattr(orig, "constraint_name", None)
    return str(name or orig or "")


async def _run(
    session: AsyncSession, slot_id: int, email: str, key: str | None
) -> tuple[Booking, bool]:
    # Name the level in the transaction itself, not only in a comment.
    # PostgreSQL's default is already READ COMMITTED; this keeps a pooler
    # or a future session GUC from silently upgrading it under us.
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    strategy = STRATEGIES[get_settings().strategy]
    return await strategy(session, slot_id, email, key)


async def book(
    session: AsyncSession, slot_id: int, email: str, key: str | None
) -> tuple[Booking, bool]:
    email = email.lower()
    try:
        async with session.begin():
            return await _run(session, slot_id, email, key)
    except IntegrityError as exc:
        name = _constraint_name(exc)
        if key and "idempotency" in name:
            async with session.begin():
                existing = await session.scalar(
                    select(Booking).where(Booking.idempotency_key == key)
                )
            if existing is not None:
                return existing, False
        if "uq_booking_slot_email" in name:
            raise conflict("You already have a seat in this slot.") from exc
        if "ck_slots_booked_within_capacity" in name:
            raise conflict("This slot is full.") from exc
        raise


async def cancel(session: AsyncSession, booking_id: int) -> Booking:
    """Lock the slot, then the booking. See strategies.py for why that order is fixed."""
    async with session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        slot_id = await session.scalar(select(Booking.slot_id).where(Booking.id == booking_id))
        if slot_id is None:
            raise not_found("Booking does not exist.")
        slot = await session.scalar(select(Slot).where(Slot.id == slot_id).with_for_update())
        booking = await session.scalar(
            select(Booking).where(Booking.id == booking_id).with_for_update()
        )
        if slot is None or booking is None:
            raise not_found("Booking does not exist.")
        if booking.status == BookingStatus.cancelled:
            return booking
        booking.status = BookingStatus.cancelled
        slot.booked_count -= 1
        await session.flush()
        return booking


async def get_booking(session: AsyncSession, booking_id: int) -> Booking:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        raise not_found("Booking does not exist.")
    return booking


async def create_resource(session: AsyncSession, name: str, kind: str) -> Resource:
    async with session.begin():
        resource = Resource(name=name.strip(), kind=kind.strip())
        session.add(resource)
        await session.flush()
        return resource


async def list_resources(session: AsyncSession) -> list[Resource]:
    return list(await session.scalars(select(Resource).order_by(Resource.id)))


async def generate_slots(
    session: AsyncSession,
    resource_id: int,
    start: datetime,
    end: datetime,
    duration_minutes: int,
    capacity: int,
) -> list[Slot]:
    starts = iter_slot_starts(start, end, duration_minutes)
    if not starts:
        raise conflict("That range does not fit a single slot of the given duration.")
    duration = timedelta(minutes=duration_minutes)
    try:
        async with session.begin():
            resource = await session.get(Resource, resource_id)
            if resource is None:
                raise not_found("Resource does not exist.")
            created: list[Slot] = []
            for starts_at in starts:
                slot = Slot(
                    resource_id=resource_id,
                    starts_at=starts_at,
                    ends_at=starts_at + duration,
                    capacity=capacity,
                    booked_count=0,
                )
                session.add(slot)
                created.append(slot)
            await session.flush()
            return created
    except IntegrityError as exc:
        if "uq_slots_resource_start" in _constraint_name(exc):
            raise conflict("A slot already starts at one of those times.") from exc
        raise


async def availability(
    session: AsyncSession, resource_id: int, start: datetime, end: datetime
) -> list[Slot]:
    resource = await session.get(Resource, resource_id)
    if resource is None:
        raise not_found("Resource does not exist.")
    rows = await session.scalars(
        select(Slot)
        .where(
            Slot.resource_id == resource_id,
            Slot.starts_at < end,
            Slot.ends_at > start,
        )
        .order_by(Slot.starts_at)
    )
    return list(rows)
