# catalog-service-cinema

Catálogo público de películas, teatros, funciones y calificaciones.

## Responsabilidad

Dueño de `cinema_catalog`: películas, teatros, plantillas/mapas de asientos
y calificaciones de usuarios. El esquema se crea con `Base.metadata.create_all`
al arrancar (no usa Alembic pese a tenerlo en `requirements.txt`) — ver
`../db_asuntos/docDBcambios.md`.

No gestiona altas/bajas de películas, teatros ni funciones — eso es de
`admin-service`, dueño de `cinema_admin`. Hasta 2026-09-19 `catalog-service`
compartía físicamente `cinema_catalog` con `admin-service` (comparte-por-BD);
desde esa fecha `cinema_catalog` es una **copia de lectura propia**, sincronizada
por eventos Kafka en vez de una conexión compartida (ver `../ARCHITECTURE.md`,
"Aislamiento de base de datos por servicio", caso 1). `hall_templates`,
`hall_seats` y `movie_ratings` sí son dominio 100% propio (`admin-service`
nunca los tocó, antes ni ahora), igual que mantener `available_tickets` al
día vía los eventos de compra/reembolso.

## Stack

FastAPI + SQLAlchemy + Redis (cache opcional, passthrough si no está
disponible) + aiokafka. Puerto `8006`.

## API

| Método | Ruta | Propósito |
|---|---|---|
| GET | `/movies` | Listar películas |
| GET | `/movies/search` | Buscar películas |
| GET | `/movies/coming-soon` | Próximos estrenos |
| GET | `/movies/presales` | Películas en preventa |
| GET | `/movies/home` | Datos agregados para la home |
| GET | `/movies/{movie_id}` | Detalle de película |
| GET | `/movies/{movie_id}/showtimes` | Funciones de una película |
| GET | `/movies/{movie_id}/showtimes/{showtime_id}` | Detalle de una función |
| GET | `/movies/{movie_id}/showtimes/{showtime_id}/seats` | Mapa de asientos de una función |
| GET | `/movies/{movie_id}/theaters` | Teatros donde se exhibe |
| GET | `/movies/{movie_id}/availability` | Disponibilidad de tickets |
| GET | `/movies/{movie_id}/ratings` | Calificaciones de una película |
| GET | `/movies/{movie_id}/my-rating` | Calificación del usuario autenticado |
| POST | `/movies/{movie_id}/rate` | Calificar una película (requiere JWT) |
| GET | `/theaters` | Listar teatros |
| GET | `/theaters/{theater_id}` | Detalle de teatro |
| GET | `/theaters/{theater_id}/movies` | Cartelera de un teatro |
| GET | `/theaters/{theater_id}/schedule` | Horario de un teatro |
| GET | `/calendar/week` | Calendario semanal de funciones |
| GET | `/health` | Estado del servicio (incluye cache Redis y consumer Kafka) |

## Eventos Kafka

Solo consume — no publica nada. Payloads completos en
`../kafka-schemas-cinema/event_contracts_operativos.md`.

| Topic | Efecto |
|---|---|
| `payment.success` | Descuenta `available_tickets` (idempotente por `order_id`) |
| `order.refunded` | Restaura `available_tickets` |
| `movie.created` | Inserta la película completa (mismo `id` que en `cinema_admin`) y materializa `theater_movies` para cada id en `theater_ids` |
| `movie.updated` | Sincroniza los campos que cambiaron, invalida cache |
| `movie.deactivated` | Marca la película como inactiva |
| `theater.created` | Inserta el teatro (mismo `id`) |
| `theater.toggled` | Sincroniza `is_active` del teatro |
| `showtime.created` | Inserta la función (mismo `id`) |
| `showtime.deleted` | Elimina la función |

Los 6 últimos son nuevos desde 2026-09-19 — antes `catalog-service` veía esos
cambios directo porque compartía la BD con `admin-service` (ver
`../ARCHITECTURE.md`, Decisión 6.2, para el detalle del diseño: por qué el
payload de `movie.created`/`movie.updated` se amplió al `Movie` completo,
por qué los eventos van con `key` = id de la entidad, y la trampa de que
Postgres guarda el *nombre* del enum, no el `.value`).

## Variables de entorno clave

| Variable | Propósito | Requerida |
|---|---|---|
| `DATABASE_URL` | Conexión a `cinema_catalog` | Sí, sin default |
| `REDIS_URL` | Cache de lecturas (opcional, passthrough si falla) | No (`""`) |
| `KAFKA_ENABLED` | Activa el consumer de Kafka | No (`false`) |
| `KAFKA_BOOTSTRAP_SERVERS` / `KAFKA_API_KEY` / `KAFKA_API_SECRET` | Credenciales Confluent Cloud | Si `KAFKA_ENABLED=true` |
| `BOOKING_SERVICE_URL` | Consultar asientos ocupados / marcar ticket usado en booking-service | No (default `http://localhost:8004`, en Docker usar `http://booking-service:8004`) |
| `INTERNAL_SERVICE_TOKEN` | Header `X-Internal-Token` enviado al llamar a `/internal/*` de booking-service — debe coincidir con el mismo valor allá | No (`""`, pero sin esto booking-service responde 401) |
| `JWT_SECRET` / `JWT_ALGORITHM` | Validar el JWT de `auth-service` en `/movies/{id}/rate` (mismo secret, no emite tokens propios) | No (`""` / `HS256`) |
| `CACHE_HOME_TTL` / `CACHE_DEFAULT_TTL` / `CATALOG_EVENT_IDEMPOTENCY_TTL` | TTLs de cache y de deduplicación de eventos | No |

Ver `.env.example`, y `../IMPLEMENTATION-GUIDE.md` Fase 4 para el flujo de
configuración completo (local vs Render). `Settings` usa
`extra: "ignore"` — variables no declaradas aquí (ej. `PYTHON_VERSION` de
Render) se ignoran en vez de romper el arranque.

## Dependencias

- **admin-service**: Kafka (`movie.*`/`theater.*`/`showtime.*`) — dueño real de este dominio, `catalog-service` solo mantiene la copia de lectura. Ya no comparte base de datos con él.
- **booking-service**: HTTP, para asientos ocupados y marcar tickets usados.
- **auth-service**: indirecta, vía JWT compartido (no hay llamada HTTP).

## Correr en local

```bash
uvicorn app.main:app --reload --port 8006
```

En la práctica se levanta junto con el resto del stack vía
`../infra-cinema/docker-compose.dev.yml` (ver `../IMPLEMENTATION-GUIDE.md`
Fase 5).
