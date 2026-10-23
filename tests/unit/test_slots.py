from datetime import UTC, datetime, timedelta

from slotwise.booking.strategies import iter_slot_starts


def test_slot_starts_stop_at_the_end_of_the_range() -> None:
    start = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    starts = iter_slot_starts(start, start + timedelta(hours=2), 30)
    assert starts == [
        start,
        start + timedelta(minutes=30),
        start + timedelta(minutes=60),
        start + timedelta(minutes=90),
    ]


def test_short_range_yields_nothing() -> None:
    start = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    assert iter_slot_starts(start, start + timedelta(minutes=20), 30) == []
