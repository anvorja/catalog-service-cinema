# app/main.py
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.movies import router as movies_router
from app.api.theaters import router as theaters_router
from app.api.calendar import router as calendar_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_consumer_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _consumer_task
    logger.info("Catalog Service starting...")

    from app.models.base import Base
    from app.core.database import engine
    # Importar todos los modelos para que create_all los registre
    import app.models.movie  # noqa: F401
    import app.models.theater  # noqa: F401
    import app.models.rating  # noqa: F401
    Base.metadata.create_all(bind=engine)

    from app.core.cache import cache
    if cache.is_healthy():
        logger.info("Redis cache: connected")
    else:
        logger.info("Redis cache: unavailable — operating in passthrough mode")

    from app.kafka.consumer import start_consumer
    from app.core.database import SessionLocal
    _consumer_task = asyncio.create_task(start_consumer(SessionLocal))

    yield

    if _consumer_task:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
    logger.info("Catalog Service shutting down")


app = FastAPI(
    title="Catalog Service",
    version="1.0.0",
    description="Movies, theaters and showtimes catalog",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(movies_router)
app.include_router(theaters_router)
app.include_router(calendar_router)


@app.get("/health")
async def health():
    from app.core.cache import cache
    consumer_running = _consumer_task is not None and not _consumer_task.done()
    return {
        "status": "healthy",
        "service": "catalog-service",
        "redis_cache": cache.is_healthy(),
        "kafka_consumer": "running" if consumer_running else "stopped",
    }
