from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from slotwise.db.models import BookingStatus


class ResourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=80)


class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: str
    created_at: datetime


class SlotGenerate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime
    duration_minutes: int = Field(gt=0, le=24 * 60)
    capacity: int = Field(gt=0, le=10_000)

    @model_validator(mode="after")
    def range_fits(self) -> "SlotGenerate":
        span = (self.to - self.from_).total_seconds()
        if span <= 0:
            raise ValueError("to must be after from")
        if span / (self.duration_minutes * 60) > 500:
            raise ValueError("refusing to generate more than 500 slots in one request")
        return self


class SlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    resource_id: int
    starts_at: datetime
    ends_at: datetime
    capacity: int
    booked_count: int


class AvailabilityOut(SlotOut):
    remaining: int


class BookingIn(BaseModel):
    slot_id: int
    customer_email: EmailStr


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slot_id: int
    customer_email: str
    status: BookingStatus
    created_at: datetime


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
