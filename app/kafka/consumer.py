# app/kafka/consumer.py — catalog-service
#
# Consume payment.success → decrementa available_tickets en cinema_catalog.movies
# y order.refunded → restaura el stock visible. Ambos handlers usan
# idempotencia por order_id para tolerar reenvíos de Kafka.
#
import asyncio
import json
import logging
import ssl
from datetime import datetime, timezone

from aiokafka import AIOKafkaConsumer
from app.core.config import settings

logger = logging.getLogger(__name__)


async def _send_to_dlq(topic: str, payload: dict, error: Exception) -> None:
    """Publica el mensaje fallido en el topic DLQ correspondiente antes de commitear el offset."""
    from app.kafka.producer import publish_event
    dlq_topic = f"{topic}.dlq"
    try:
        await publish_event(dlq_topic, {
            "original_topic": topic,
            "original_payload": payload,
            "error": str(error),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.warning("Mensaje enviado al DLQ | dlq_topic=%s", dlq_topic)
    except Exception as dlq_exc:
        logger.error("No se pudo publicar al DLQ | dlq_topic=%s | error=%s", dlq_topic, dlq_exc)

_RESTART_DELAY = 10
_PROCESSED_EVENT_PREFIX = "catalog:event:"


def _event_key(topic: str, order_id: int) -> str:
    return f"{_PROCESSED_EVENT_PREFIX}{topic}:{order_id}"


def _claim_event(topic: str, order_id: int) -> bool:
    from app.core.cache import cache

    claimed = cache.set_if_absent(
        _event_key(topic, order_id),
        {"claimed": True},
        ttl=settings.CATALOG_EVENT_IDEMPOTENCY_TTL,
    )
    if claimed:
        return True

    if not cache.is_healthy():
        logger.warning("Redis cache unavailable — processing without idempotency for %s order_id=%s", topic, order_id)
        return True

    logger.info("Evento duplicado ignorado | topic=%s | order_id=%s", topic, order_id)
    return False


async def _handle_payment_success(payload: dict, db_factory) -> None:
    order_id = payload.get("order_id")
    movie_id = payload.get("movie_id")
    quantity = payload.get("quantity", 0)
    showtime_id = payload.get("showtime_id")

    if not order_id or not movie_id or quantity <= 0:
        logger.warning("payment.success payload inválido: %s", payload)
        return

    if not _claim_event("payment.success", order_id):
        return

    from sqlalchemy import text
    with db_factory() as db:
        result = db.execute(
            text("""
                UPDATE movies
                SET available_tickets = GREATEST(available_tickets - :qty, 0),
                    updated_at        = now()
                WHERE id = :mid AND available_tickets > 0
            """),
            {"qty": quantity, "mid": movie_id},
        )

        # Si la compra estaba vinculada a una función específica, decrementar también
        # la disponibilidad de esa función en movie_showtimes.
        if showtime_id:
            db.execute(
                text("""
                    UPDATE movie_showtimes
                    SET available_tickets = GREATEST(available_tickets - :qty, 0),
                        updated_at        = now()
                    WHERE id = :sid AND available_tickets > 0
                """),
                {"qty": quantity, "sid": showtime_id},
            )

        db.commit()

    if result.rowcount:
        from app.core.cache import cache
        cache.delete_pattern("home:*")
        cache.delete_pattern(f"movie:{movie_id}:*")
        logger.info(
            "available_tickets decrementado en cinema_catalog | order_id=%s | movie_id=%s qty=%s showtime_id=%s",
            order_id, movie_id, quantity, showtime_id,
        )
    else:
        logger.warning(
            "payment.success sin efecto en cinema_catalog.movies | order_id=%s | movie_id=%s",
            order_id, movie_id,
        )


async def _handle_order_refunded(payload: dict, db_factory) -> None:
    """
    Restaura available_tickets en cinema_catalog.movies cuando una compra es reembolsada.
    Simétrico a _handle_payment_success, pero suma en lugar de restar.
    """
    order_id = payload.get("order_id")
    movie_id = payload.get("movie_id")
    quantity = payload.get("quantity", 0)
    showtime_id = payload.get("showtime_id")

    if not order_id or not movie_id or quantity <= 0:
        logger.warning("order.refunded payload inválido: %s", payload)
        return

    if not _claim_event("order.refunded", order_id):
        return

    from sqlalchemy import text
    with db_factory() as db:
        result = db.execute(
            text("""
                UPDATE movies
                SET available_tickets = LEAST(available_tickets + :qty, max_capacity),
                    updated_at        = now()
                WHERE id = :mid
            """),
            {"qty": quantity, "mid": movie_id},
        )

        # Restaurar disponibilidad en la función específica si aplica
        if showtime_id:
            db.execute(
                text("""
                    UPDATE movie_showtimes
                    SET available_tickets = LEAST(available_tickets + :qty, capacity),
                        updated_at        = now()
                    WHERE id = :sid
                """),
                {"qty": quantity, "sid": showtime_id},
            )

        db.commit()

    if result.rowcount:
        from app.core.cache import cache
        cache.delete_pattern("home:*")
        cache.delete_pattern(f"movie:{movie_id}:*")
        logger.info(
            "available_tickets restaurado en cinema_catalog | order_id=%s | movie_id=%s qty=%s showtime_id=%s",
            order_id, movie_id, quantity, showtime_id,
        )
    else:
        logger.warning(
            "order.refunded sin efecto en cinema_catalog | order_id=%s | movie_id=%s",
            order_id, movie_id,
        )


async def _handle_movie_updated(payload: dict, db_factory) -> None:
    """
    Recibe movie.updated publicado por admin-service.
    Actualiza precio, capacidad y available_tickets en cinema_catalog.movies.
    """
    movie_id = payload.get("movie_id")
    if not movie_id:
        return

    fields = {k: v for k, v in payload.items() if k != "movie_id" and v is not None}
    if not fields:
        return

    from sqlalchemy import text
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    with db_factory() as db:
        db.execute(
            text(f"UPDATE movies SET {set_clause}, updated_at = now() WHERE id = :movie_id"),
            {"movie_id": movie_id, **fields},
        )
        db.commit()

    from app.core.cache import cache
    cache.delete_pattern("home:*")
    cache.delete_pattern(f"movie:{movie_id}:*")
    logger.info("cinema_catalog.movies actualizado por movie.updated | movie_id=%s", movie_id)


_HANDLERS = {
    "payment.success": _handle_payment_success,
    "order.refunded":  _handle_order_refunded,
    "movie.updated":   _handle_movie_updated,
}


async def _run_consumer(db_factory) -> None:
    ssl_context = ssl.create_default_context()
    consumer = AIOKafkaConsumer(
        *_HANDLERS.keys(),
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        security_protocol="SASL_SSL",
        sasl_mechanism="PLAIN",
        sasl_plain_username=settings.KAFKA_API_KEY,
        sasl_plain_password=settings.KAFKA_API_SECRET,
        ssl_context=ssl_context,
        group_id="catalog-service-group",
        # payment.success y order.refunded no deben reaplicarse al arrancar un despliegue nuevo.
        auto_offset_reset="latest",
        enable_auto_commit=False,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )

    await consumer.start()
    logger.info("Catalog consumer iniciado | topics=%s", list(_HANDLERS.keys()))

    try:
        async for msg in consumer:
            handler = _HANDLERS.get(msg.topic)
            if handler:
                try:
                    await handler(msg.value, db_factory)
                except Exception as e:
                    logger.error("Error procesando %s: %s", msg.topic, e)
                    await _send_to_dlq(msg.topic, msg.value, e)
            await consumer.commit()
    except asyncio.CancelledError:
        raise
    finally:
        await consumer.stop()
        logger.info("Catalog consumer detenido")


async def start_consumer(db_factory) -> None:
    if not settings.KAFKA_ENABLED:
        logger.info("Kafka deshabilitado — catalog consumer no iniciado")
        return

    while True:
        try:
            await _run_consumer(db_factory)
            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Catalog consumer error: %s — reiniciando en %ds", e, _RESTART_DELAY)
            await asyncio.sleep(_RESTART_DELAY)
