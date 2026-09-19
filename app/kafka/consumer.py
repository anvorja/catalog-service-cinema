# app/kafka/consumer.py — catalog-service
#
# Dos familias de eventos:
#
# 1. payment.success / order.refunded — decrementan/restauran available_tickets
#    en cinema_catalog.movies y movie_showtimes. Idempotencia por order_id.
#
# 2. movie.* / theater.* / showtime.* — sincronizan la copia de lectura de
#    cinema_catalog con cinema_admin (dueña real, ver ARCHITECTURE.md,
#    "Aislamiento de base de datos por servicio", caso 1). Idempotencia por
#    ON CONFLICT DO NOTHING/UPDATE sobre el id de cada entidad (mismo id en
#    ambas bases — cinema_admin es la fuente, este consumer solo replica).
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
        # Invalidar cache de asientos ocupados para que el mapa refleje la venta
        if showtime_id:
            cache.delete(f"showtime:{showtime_id}:occupied_seats")
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
        # Invalidar cache de asientos ocupados para que el mapa refleje la devolución
        if showtime_id:
            cache.delete(f"showtime:{showtime_id}:occupied_seats")
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

    fields = {k: v for k, v in payload.items() if k not in ("movie_id", "theater_ids") and v is not None}
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


async def _handle_movie_created(payload: dict, db_factory) -> None:
    """
    Recibe movie.created publicado por admin-service (dueño de cinema_admin,
    ver ARCHITECTURE.md "Aislamiento de base de datos por servicio", caso 1).
    Inserta la película completa (misma fila, mismo id) en cinema_catalog —
    idempotente vía ON CONFLICT DO NOTHING. Los ids de teatro en
    `theater_ids` se materializan como filas de theater_movies aparte, en su
    propia transacción, para que un teatro que todavía no haya llegado (el
    orden entre eventos con distinta key no está garantizado) no eche para
    atrás la inserción de la película misma.
    """
    from sqlalchemy import text

    movie_id = payload.get("movie_id")
    if not movie_id:
        logger.warning("movie.created payload sin movie_id: %s", payload)
        return

    with db_factory() as db:
        db.execute(
            text("""
                INSERT INTO movies
                    (id, title, description, genre, duration, rating, price,
                     director, country, status, is_presale, release_date,
                     max_capacity, available_tickets,
                     poster_url, backdrop_url, detail_1_url, detail_2_url,
                     is_active, created_at, updated_at)
                VALUES
                    (:movie_id, :title, :description, :genre, :duration, :rating, :price,
                     :director, :country, :status, :is_presale, :release_date,
                     :max_capacity, :available_tickets,
                     :poster_url, :backdrop_url, :detail_1_url, :detail_2_url,
                     true, now(), now())
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "movie_id":          movie_id,
                "title":             payload.get("title", ""),
                "description":       payload.get("description", ""),
                "genre":             payload.get("genre"),
                "duration":          payload.get("duration"),
                "rating":            payload.get("rating"),
                "price":             payload.get("price", 0.0),
                "director":          payload.get("director", ""),
                "country":           payload.get("country", ""),
                "status":            payload.get("status", "IN_THEATERS"),
                "is_presale":        payload.get("is_presale", False),
                "release_date":      payload.get("release_date"),
                "max_capacity":      payload.get("max_capacity", 0),
                "available_tickets": payload.get("available_tickets", 0),
                "poster_url":        payload.get("poster_url"),
                "backdrop_url":      payload.get("backdrop_url"),
                "detail_1_url":      payload.get("detail_1_url"),
                "detail_2_url":      payload.get("detail_2_url"),
            },
        )
        db.commit()
    logger.info("Película insertada en cinema_catalog por movie.created | movie_id=%s", movie_id)

    for theater_id in payload.get("theater_ids") or []:
        try:
            with db_factory() as db:
                db.execute(
                    text("""
                        INSERT INTO theater_movies (theater_id, movie_id, capacity, available_tickets, is_active, created_at, updated_at)
                        VALUES (:theater_id, :movie_id, 100, 100, true, now(), now())
                        ON CONFLICT (theater_id, movie_id) DO NOTHING
                    """),
                    {"theater_id": theater_id, "movie_id": movie_id},
                )
                db.commit()
        except Exception as e:
            # Típicamente el teatro aún no llegó (distinta key de partición,
            # sin orden garantizado con movie.created) — el mensaje ya se
            # commiteó como leído; si hace falta reprocesar, es manual desde
            # el log. No aborta el resto de teatros de esta película.
            logger.error(
                "No se pudo asociar theater_id=%s a movie_id=%s (¿theater.created aún no llegó?): %s",
                theater_id, movie_id, e,
            )

    from app.core.cache import cache
    cache.delete_pattern("home:*")


async def _handle_movie_deactivated(payload: dict, db_factory) -> None:
    """Marca la película como inactiva en cinema_catalog.movies."""
    from sqlalchemy import text

    movie_id = payload.get("movie_id")
    if not movie_id:
        logger.warning("movie.deactivated payload sin movie_id: %s", payload)
        return

    with db_factory() as db:
        db.execute(
            text("UPDATE movies SET is_active = false, updated_at = now() WHERE id = :movie_id"),
            {"movie_id": movie_id},
        )
        db.commit()

    from app.core.cache import cache
    cache.delete_pattern("home:*")
    cache.delete_pattern(f"movie:{movie_id}:*")
    logger.info("Película desactivada en cinema_catalog por movie.deactivated | movie_id=%s", movie_id)


async def _handle_theater_created(payload: dict, db_factory) -> None:
    from sqlalchemy import text

    theater_id = payload.get("theater_id")
    if not theater_id:
        logger.warning("theater.created payload sin theater_id: %s", payload)
        return

    with db_factory() as db:
        db.execute(
            text("""
                INSERT INTO theaters (id, name, location, description, is_active, created_at, updated_at)
                VALUES (:theater_id, :name, :location, :description, true, now(), now())
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "theater_id": theater_id,
                "name": payload.get("name", ""),
                "location": payload.get("location", ""),
                "description": payload.get("description"),
            },
        )
        db.commit()
    logger.info("Teatro insertado en cinema_catalog por theater.created | theater_id=%s", theater_id)


async def _handle_theater_toggled(payload: dict, db_factory) -> None:
    from sqlalchemy import text

    theater_id = payload.get("theater_id")
    is_active = payload.get("is_active")
    if not theater_id or is_active is None:
        logger.warning("theater.toggled payload inválido: %s", payload)
        return

    with db_factory() as db:
        db.execute(
            text("UPDATE theaters SET is_active = :is_active, updated_at = now() WHERE id = :theater_id"),
            {"theater_id": theater_id, "is_active": is_active},
        )
        db.commit()
    logger.info("Teatro actualizado en cinema_catalog por theater.toggled | theater_id=%s is_active=%s", theater_id, is_active)


async def _handle_showtime_created(payload: dict, db_factory) -> None:
    from sqlalchemy import text

    showtime_id = payload.get("showtime_id")
    if not showtime_id:
        logger.warning("showtime.created payload sin showtime_id: %s", payload)
        return

    with db_factory() as db:
        db.execute(
            text("""
                INSERT INTO movie_showtimes
                    (id, movie_id, theater_id, show_date, show_time, format,
                     capacity, available_tickets, hall_number, hall_template_id,
                     is_active, created_at, updated_at)
                VALUES
                    (:showtime_id, :movie_id, :theater_id, :show_date, :show_time, :format,
                     :capacity, :available_tickets, :hall_number, :hall_template_id,
                     true, now(), now())
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "showtime_id":       showtime_id,
                "movie_id":          payload.get("movie_id"),
                "theater_id":        payload.get("theater_id"),
                "show_date":         payload.get("show_date"),
                "show_time":         payload.get("show_time"),
                "format":            payload.get("format"),
                "capacity":          payload.get("capacity", 100),
                "available_tickets": payload.get("available_tickets", 100),
                "hall_number":       payload.get("hall_number"),
                "hall_template_id":  payload.get("hall_template_id"),
            },
        )
        db.commit()

    from app.core.cache import cache
    cache.delete_pattern("home:*")
    logger.info("Función insertada en cinema_catalog por showtime.created | showtime_id=%s", showtime_id)


async def _handle_showtime_deleted(payload: dict, db_factory) -> None:
    from sqlalchemy import text

    showtime_id = payload.get("showtime_id")
    if not showtime_id:
        logger.warning("showtime.deleted payload sin showtime_id: %s", payload)
        return

    with db_factory() as db:
        db.execute(text("DELETE FROM movie_showtimes WHERE id = :showtime_id"), {"showtime_id": showtime_id})
        db.commit()

    from app.core.cache import cache
    cache.delete_pattern("home:*")
    logger.info("Función eliminada en cinema_catalog por showtime.deleted | showtime_id=%s", showtime_id)


_HANDLERS = {
    "payment.success":    _handle_payment_success,
    "order.refunded":     _handle_order_refunded,
    "movie.updated":      _handle_movie_updated,
    "movie.created":      _handle_movie_created,
    "movie.deactivated":  _handle_movie_deactivated,
    "theater.created":    _handle_theater_created,
    "theater.toggled":    _handle_theater_toggled,
    "showtime.created":   _handle_showtime_created,
    "showtime.deleted":   _handle_showtime_deleted,
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
