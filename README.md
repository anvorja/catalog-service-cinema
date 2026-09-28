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

## Flujo de trabajo: Gitflow

| Rama        | Sale de   | Entra a (vía PR)         | Método en GitHub | Para |
| ----------- | --------- | ------------------------ | ---------------- | ---- |
| `main`      | —         | —                        | —                | Lo que está en producción. Cada merge es una versión. |
| `develop`   | `main`    | —                        | —                | Integración de lo próximo a publicar. Rama por defecto. |
| `feature/*` | `develop` | `develop`                | **Squash**       | Una funcionalidad o cambio: `feature/mi-cambio`. |
| `release/*` | `develop` | `main` y luego `develop` | **Merge** a `main`; **Squash** a `develop` | Preparar una versión: `release/1.0.0`. Solo ajustes finales. |
| `hotfix/*`  | `main`    | `main` y luego `develop` | **Merge** a `main`; **Squash** a `develop` | Corrección urgente en producción. |

- **Nadie hace push directo** a `main` ni a `develop`: todo entra por pull request, con los checks de CI en verde.
- **En `develop` se usa squash:** cada feature queda como un solo commit con el título del PR.
- **En `main` se usa merge commit:** cada release o hotfix queda visible como una unidad.
- **Todavía no hay releases:** la app no está completa, así que `main` se queda como está hasta el
  primer `release/*`. Desde entonces, cada versión se etiqueta en `main` (`git tag -a v1.0.0`) con
  [versionado semántico](https://semver.org/lang/es/).

```bash
git switch develop && git pull
git switch -c feature/mi-cambio
# ...commits...
git push -u origin feature/mi-cambio   # abrir PR hacia develop → Squash and merge
```

## CI/CD

GitHub Actions (`.github/workflows/`) corre en cada PR hacia `main` o `develop`. Los rulesets exigen
estos checks; si se renombra un job, hay que actualizar `.github/rulesets/*.json`.

| Check | Qué revisa |
| ----- | ---------- |
| `Lint` | Ruff con las reglas de `ruff.toml`. |
| `Calidad y build` | Instala las dependencias, compila todo el código y carga la app con configuración falsa (sin base de datos ni Kafka). |
| `Imagen Docker` | Construye la imagen y comprueba que la app carga dentro de ella, sin red. |

Con cada push a `develop` o `main` (es decir, al fusionar un PR), y solo si pasaron los checks, se
publica en Docker Hub **la misma imagen que se probó** (no se reconstruye):

- `develop` → `<usuario>/catalog-service-cinema:develop` y `:<sha>`
- `main` → `<usuario>/catalog-service-cinema:latest` y `:<sha>`

El flujo no despliega en ningún servicio (tampoco en Render): solo publica la imagen.

### Configuración en GitHub (una vez)

- **Rulesets:** `main` y `develop` se protegen importando `.github/rulesets/main.json` y
  `.github/rulesets/develop.json` en *Settings → Rules → Rulesets → Import a ruleset*. Exigen PR, los
  checks de la tabla de arriba, y no permiten borrar la rama ni forzar pushes. `main` solo acepta
  merge commit y `develop` solo squash.
- **Settings → General:** rama por defecto `develop`; permitir merge commits y squash (no rebase);
  activar *Automatically delete head branches*.
- **Secrets** (*Settings → Secrets and variables → Actions*): `DOCKER_USERNAME` y `DOCKER_TOKEN`
  (token de acceso de Docker Hub con permiso de escritura).
