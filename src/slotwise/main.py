import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from slotwise.api.routes import health, problem, router
from slotwise.booking.errors import BookingError
from slotwise.config import get_settings
from slotwise.db.session import dispose_engine

log = logging.getLogger("slotwise")


class DeskFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.endswith((".css", ".js")):
            response.headers["Cache-Control"] = "no-cache"
        return response


def web_dir() -> Path:
    configured = get_settings().web_dir
    if configured:
        return Path(configured)
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "web", Path.cwd() / "web"):
        if (candidate / "index.html").exists():
            return candidate
    return Path.cwd() / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if settings.strategy == "naive":
        log.warning(
            "BOOKING_STRATEGY=naive is a demo of a lost update. Do not use it for real bookings."
        )
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="Slotwise", version="0.1.0", lifespan=lifespan)
    app.include_router(router, prefix="/api/v1")
    # Docker and the desk poll the root. The same handler also lives under /api/v1.
    app.add_api_route("/health", health, methods=["GET"])

    @app.exception_handler(BookingError)
    async def on_booking_error(_request: Request, exc: BookingError):
        return problem(exc.status, exc.title, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def on_validation(_request: Request, exc: RequestValidationError):
        detail = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return problem(422, "Unprocessable Entity", detail or "Request validation failed.")

    desk = web_dir()

    @app.get("/", include_in_schema=False)
    async def desk_page() -> FileResponse:
        return FileResponse(desk / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/assets", DeskFiles(directory=desk / "assets"), name="assets")
    return app


app = create_app()
