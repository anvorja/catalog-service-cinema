# app/core/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Catalog Service"
    VERSION: str = "1.0.0"

    DATABASE_URL: str
    REDIS_URL: str = ""
    CACHE_HOME_TTL: int = 120
    CACHE_DEFAULT_TTL: int = 300

    # Kafka — consume payment.success para decrementar available_tickets
    KAFKA_ENABLED: bool = False
    KAFKA_BOOTSTRAP_SERVERS: str = ""
    KAFKA_API_KEY: str = ""
    KAFKA_API_SECRET: str = ""
    CATALOG_EVENT_IDEMPOTENCY_TTL: int = 60 * 60 * 24 * 7

    # URL interna al booking-service (para consultar occupied-seats)
    BOOKING_SERVICE_URL: str = "http://localhost:8004"

    # JWT — mismo secret que auth-service para validar tokens en ratings
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"

    DEBUG: bool = False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


settings = Settings()
