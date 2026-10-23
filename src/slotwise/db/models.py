import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from slotwise.db.base import Base


class BookingStatus(enum.StrEnum):
    confirmed = "confirmed"
    cancelled = "cancelled"


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Slot(Base):
    """Capacity lives on the row that the booking transaction locks.

    ck_slots_booked_within_capacity is defense in depth, not the concurrency
    strategy. Without a row lock, two transactions can both insert a booking
    and only then trip this check, which surfaces as IntegrityError (500)
    rather than a clean 409. The check also cannot see the bookings table:
    a writer that stores a stale count can keep booked_count legal while
    more than `capacity` rows are confirmed. See book_naive.
    """

    __tablename__ = "slots"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="ck_slots_capacity_positive"),
        CheckConstraint(
            "booked_count BETWEEN 0 AND capacity",
            name="ck_slots_booked_within_capacity",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_slots_ends_after_start"),
        UniqueConstraint("resource_id", "starts_at", name="uq_slots_resource_start"),
        Index("ix_slots_resource_start", "resource_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id", ondelete="CASCADE"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(Integer)
    booked_count: Mapped[int] = mapped_column(Integer, server_default="0")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_bookings_idempotency_key"),
        Index(
            "uq_booking_slot_email_confirmed",
            "slot_id",
            "customer_email",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id", ondelete="CASCADE"))
    customer_email: Mapped[str] = mapped_column(String(320))
    status: Mapped[BookingStatus] = mapped_column(
        Enum(
            BookingStatus,
            name="booking_status",
            native_enum=True,
            values_callable=lambda items: [item.value for item in items],
        )
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
