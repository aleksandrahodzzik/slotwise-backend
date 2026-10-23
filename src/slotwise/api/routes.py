from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from slotwise.booking.errors import BookingError
from slotwise.booking.service import (
    availability,
    book,
    cancel,
    create_resource,
    generate_slots,
    get_booking,
    list_resources,
)
from slotwise.config import get_settings
from slotwise.db.session import get_session
from slotwise.schemas import (
    AvailabilityOut,
    BookingIn,
    BookingOut,
    ResourceIn,
    ResourceOut,
    SlotGenerate,
    SlotOut,
)

router = APIRouter()


def problem(status: int, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


def _aware(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BookingError(
            422, "Unprocessable Entity", f"{field} is not an ISO-8601 datetime."
        ) from exc
    if parsed.tzinfo is None:
        raise BookingError(422, "Unprocessable Entity", f"{field} must include a timezone.")
    return parsed


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return problem(503, "Service Unavailable", "Database is not reachable.")
    return JSONResponse(
        {
            "status": "ok",
            "database": "ok",
            "strategy": get_settings().strategy,
        }
    )


@router.post("/resources", response_model=ResourceOut, status_code=201)
async def post_resource(
    body: ResourceIn, session: AsyncSession = Depends(get_session)
) -> ResourceOut:
    return ResourceOut.model_validate(await create_resource(session, body.name, body.kind))


@router.get("/resources", response_model=list[ResourceOut])
async def get_resources(session: AsyncSession = Depends(get_session)) -> list[ResourceOut]:
    return [ResourceOut.model_validate(row) for row in await list_resources(session)]


@router.post("/resources/{resource_id}/slots", response_model=list[SlotOut], status_code=201)
async def post_slots(
    resource_id: int,
    body: SlotGenerate,
    session: AsyncSession = Depends(get_session),
) -> list[SlotOut]:
    slots = await generate_slots(
        session,
        resource_id,
        body.from_,
        body.to,
        body.duration_minutes,
        body.capacity,
    )
    return [SlotOut.model_validate(slot) for slot in slots]


@router.get("/resources/{resource_id}/availability", response_model=list[AvailabilityOut])
async def get_availability(
    resource_id: int,
    from_value: str = Query(alias="from"),
    to_value: str = Query(alias="to"),
    session: AsyncSession = Depends(get_session),
) -> list[AvailabilityOut]:
    start = _aware(from_value, "from")
    end = _aware(to_value, "to")
    if end <= start:
        raise BookingError(422, "Unprocessable Entity", "to must be after from.")
    rows = await availability(session, resource_id, start, end)
    return [
        AvailabilityOut(
            id=row.id,
            resource_id=row.resource_id,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            capacity=row.capacity,
            booked_count=row.booked_count,
            remaining=row.capacity - row.booked_count,
        )
        for row in rows
    ]


@router.post("/bookings", response_model=BookingOut, status_code=201)
async def post_booking(
    body: BookingIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> BookingOut:
    key = idempotency_key.strip() if idempotency_key else None
    if key is not None and len(key) > 200:
        raise BookingError(
            422, "Unprocessable Entity", "Idempotency-Key must be at most 200 characters."
        )
    booking, created = await book(session, body.slot_id, str(body.customer_email), key)
    if not created:
        response.status_code = 200
    return BookingOut.model_validate(booking)


@router.get("/bookings/{booking_id}", response_model=BookingOut)
async def get_one_booking(
    booking_id: int, session: AsyncSession = Depends(get_session)
) -> BookingOut:
    return BookingOut.model_validate(await get_booking(session, booking_id))


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
async def post_cancel(booking_id: int, session: AsyncSession = Depends(get_session)) -> BookingOut:
    return BookingOut.model_validate(await cancel(session, booking_id))
