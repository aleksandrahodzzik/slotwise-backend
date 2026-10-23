"""Initial schema. Reviewed by hand so the locks in strategies.py have something to lock."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "resource_id",
            sa.Integer(),
            sa.ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("booked_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("capacity > 0", name="ck_slots_capacity_positive"),
        sa.CheckConstraint(
            "booked_count BETWEEN 0 AND capacity",
            name="ck_slots_booked_within_capacity",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_slots_ends_after_start"),
        sa.UniqueConstraint("resource_id", "starts_at", name="uq_slots_resource_start"),
    )
    op.create_index("ix_slots_resource_start", "slots", ["resource_id", "starts_at"])

    booking_status = sa.Enum("confirmed", "cancelled", name="booking_status")
    booking_status.create(op.get_bind(), checkfirst=True)
    status_column = sa.Enum("confirmed", "cancelled", name="booking_status", create_type=False)
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "slot_id",
            sa.Integer(),
            sa.ForeignKey("slots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("customer_email", sa.String(length=320), nullable=False),
        sa.Column("status", status_column, nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_bookings_idempotency_key"),
    )
    op.create_index(
        "uq_booking_slot_email_confirmed",
        "bookings",
        ["slot_id", "customer_email"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_booking_slot_email_confirmed", table_name="bookings")
    op.drop_table("bookings")
    sa.Enum(name="booking_status").drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_slots_resource_start", table_name="slots")
    op.drop_table("slots")
    op.drop_table("resources")
