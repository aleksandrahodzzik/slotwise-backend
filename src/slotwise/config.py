from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://slotwise:slotwise@localhost:5432/slotwise"
    booking_strategy: str = "row_lock"
    naive_delay_ms: int = 0
    db_pool_size: int = 10
    db_max_overflow: int = 40
    web_dir: str = ""
    web_concurrency: int = 4

    @property
    def strategy(self) -> str:
        name = self.booking_strategy
        if name not in {"row_lock", "atomic_update", "naive"}:
            raise RuntimeError(
                f"BOOKING_STRATEGY must be row_lock, atomic_update, or naive, not {name!r}"
            )
        return name


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Tests change env vars between cases. The process cache must follow."""
    global _settings
    _settings = None
