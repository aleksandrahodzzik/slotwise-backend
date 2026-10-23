class BookingError(Exception):
    def __init__(self, status: int, title: str, detail: str) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        super().__init__(detail)


def not_found(detail: str) -> BookingError:
    return BookingError(404, "Not Found", detail)


def conflict(detail: str) -> BookingError:
    return BookingError(409, "Conflict", detail)
