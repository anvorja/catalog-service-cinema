# catalog-service-cinema

Catálogo público de películas, teatros, funciones y calificaciones.

## Responsabilidad

Dueño de `cinema_catalog`: películas, teatros, plantillas/mapas de asientos
y calificaciones de usuarios. El esquema se crea con `Base.metadata.create_all`
al arrancar (no usa Alembic pese a tenerlo en `requirements.txt`) — ver
`../db_asuntos/docDBcambios.md`.

No gestiona altas/bajas de películas ni CORS de administración — eso es de
`admin-service`, que **comparte esta misma base de datos** (`cinema_catalog`)
como dueño de escritura para el ciclo de vida de películas. catalog-service
solo lee ese estado y lo sirve al público, además de mantener por su cuenta
`available_tickets` (vía eventos) y las calificaciones.

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
| `movie.updated` | Sincroniza cambios de película hechos desde `admin-service`, invalida cache |

`movie.created` y `movie.deactivated` (también publicados por `admin-service`)
**no** los consume: al compartir la misma BD, ya ve esos cambios directamente.

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

- **admin-service**: dueño de escritura de `cinema_catalog` (comparte BD).
- **booking-service**: HTTP, para asientos ocupados y marcar tickets usados.
- **auth-service**: indirecta, vía JWT compartido (no hay llamada HTTP).

## Correr en local

```bash
uvicorn app.main:app --reload --port 8006
```

En la práctica se levanta junto con el resto del stack vía
`../infra-cinema/docker-compose.dev.yml` (ver `../IMPLEMENTATION-GUIDE.md`
Fase 5).
