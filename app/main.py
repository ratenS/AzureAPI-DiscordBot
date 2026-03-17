from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

import structlog
import uvicorn
from fastapi import FastAPI

from app.config import get_settings
from app.db import Database
from app.discord_client import AzureDiscordBot
from app.logging import configure_logging
from app.repositories.memory_repository import MemoryRepository
from app.services.chat_service import ChatService
from app.services.image_service import ImageService
from app.services.memory_service import MemoryService
from app.services.rate_limit_service import RateLimitService

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)

database = Database(settings)
repository = MemoryRepository()
memory_service = MemoryService(
    repository=repository,
    retention_days=settings.default_raw_log_retention_days,
    sync_heuristics_enabled=settings.memory_sync_heuristics_enabled,
)
chat_service = ChatService(settings)
rate_limit_service = RateLimitService(settings.rate_limit_requests_per_minute)
image_service = ImageService(settings, repository)
discord_bot = AzureDiscordBot(
    settings=settings,
    database=database,
    chat_service=chat_service,
    image_service=image_service,
    memory_service=memory_service,
    rate_limit_service=rate_limit_service,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize_schema()
    bot_task = asyncio.create_task(discord_bot.start(settings.discord_bot_token))
    try:
        with database.session() as session:
            memory_service.cleanup_expired_messages(session)
        yield
    finally:
        await discord_bot.close()
        bot_task.cancel()
        with suppress(asyncio.CancelledError):
            await bot_task


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> dict[str, str]:
    with database.session() as session:
        from sqlalchemy import text

        session.execute(text("SELECT 1"))
    return {"status": "ready"}


def main() -> None:
    logger.info("starting_api", host=settings.host, port=settings.port)
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
