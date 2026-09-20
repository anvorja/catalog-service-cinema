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
    # Default = group_id histórico de producción, sin variable nueva en Render.
    # Local lo sobreescribe con sufijo "-local" — dev y prod comparten el
    # mismo cluster de Confluent Cloud, y sin distinguir el group_id ambos
    # entornos terminan en el MISMO grupo de consumidores.
    KAFKA_GROUP_ID: str = "catalog-service-group"
    CATALOG_EVENT_IDEMPOTENCY_TTL: int = 60 * 60 * 24 * 7

    # URL interna al booking-service (para consultar occupied-seats)
    BOOKING_SERVICE_URL: str = "http://localhost:8004"

    # Secreto compartido para llamar a rutas /internal/* de booking-service.
    # Debe coincidir con INTERNAL_SERVICE_TOKEN en booking-service-cinema —
    # ver ARCHITECTURE.md, "Aislamiento de base de datos por servicio".
    INTERNAL_SERVICE_TOKEN: str = ""

    # JWT — mismo secret que auth-service para validar tokens en ratings
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"

    DEBUG: bool = False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


settings = Settings()
