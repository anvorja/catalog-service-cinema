# app/services/movie_service.py — read-only catalog queries
import logging
from typing import List, Optional, Dict, Any
from datetime import date
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import desc, or_, and_

import httpx

from app.models.movie import Movie, MovieStatus
from app.models.theater import Theater, TheaterMovie, MovieShowtime, HallTemplate, HallSeat
from app.core.cache import cache
from app.core.config import settings

logger = logging.getLogger(__name__)

_HALL_SEATS_TTL = 3600       # layout nunca cambia — cache 1 hora
_OCCUPIED_SEATS_TTL = 5      # ocupados: TTL muy corto para minimizar ventana de doble venta


class MovieService:

    @staticmethod
    def get_movie_by_id(db: Session, movie_id: int, include_inactive: bool = False) -> Optional[Movie]:
        q = db.query(Movie).filter(Movie.id == movie_id)
        if not include_inactive:
            q = q.filter(Movie.is_active == True)
        return q.first()

    @staticmethod
    def get_movies(
        db: Session,
        skip: int = 0,
        limit: int = 10,
        include_inactive: bool = False,
        search: Optional[str] = None,
        genre: Optional[str] = None,
        director: Optional[str] = None,
        country: Optional[str] = None,
        status: Optional[MovieStatus] = None,
        is_presale: Optional[bool] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        rating: Optional[str] = None,
        available_only: bool = True,
        theater_name: Optional[str] = None,
    ) -> List[Movie]:
        q = db.query(Movie).options(
            selectinload(Movie.theater_movies).selectinload(TheaterMovie.theater)
        )
        if not include_inactive:
            q = q.filter(Movie.is_active == True)
        if search:
            t = f"%{search.strip()}%"
            q = q.filter(or_(
                Movie.title.ilike(t),
                Movie.description.ilike(t),
                Movie.director.ilike(t),
            ))
        if genre:
            q = q.filter(Movie.genre.ilike(f"%{genre.strip()}%"))
        if director:
            q = q.filter(Movie.director.ilike(f"%{director.strip()}%"))
        if country:
            q = q.filter(Movie.country.ilike(f"%{country.strip()}%"))
        if status:
            q = q.filter(Movie.status == status)
        if is_presale is not None:
            q = q.filter(Movie.is_presale == is_presale)
        if min_price is not None:
            q = q.filter(Movie.price >= min_price)
        if max_price is not None:
            q = q.filter(Movie.price <= max_price)
        if rating:
            q = q.filter(Movie.rating == rating.upper())
        if available_only:
            q = q.filter(Movie.available_tickets > 0).filter(or_(
                Movie.status == MovieStatus.IN_THEATERS,
                and_(Movie.status == MovieStatus.COMING_SOON, Movie.is_presale == True),
            ))
        if theater_name:
            q = q.join(Movie.theater_movies).join(TheaterMovie.theater).filter(
                Theater.name.ilike(f"%{theater_name}%")
            )
        return q.order_by(desc(Movie.created_at)).offset(skip).limit(limit).all()

    @staticmethod
    def get_movie_with_showtimes(
        db: Session, movie_id: int, include_inactive: bool = False
    ) -> Optional[Movie]:
        q = db.query(Movie).options(
            joinedload(Movie.showtimes).joinedload(MovieShowtime.theater),
            joinedload(Movie.theater_movies).joinedload(TheaterMovie.theater),
        ).filter(Movie.id == movie_id)
        if not include_inactive:
            q = q.filter(Movie.is_active == True)
        return q.first()

    @staticmethod
    def get_movie_showtimes(
        db: Session,
        movie_id: int,
        theater_id: Optional[int] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[MovieShowtime]:
        q = db.query(MovieShowtime).options(
            joinedload(MovieShowtime.theater)
        ).filter(MovieShowtime.movie_id == movie_id, MovieShowtime.is_active == True)
        if theater_id:
            q = q.filter(MovieShowtime.theater_id == theater_id)
        q = q.filter(MovieShowtime.show_date >= (start_date or date.today()))
        if end_date:
            q = q.filter(MovieShowtime.show_date <= end_date)
        return q.order_by(MovieShowtime.show_date, MovieShowtime.show_time).all()

    @staticmethod
    def get_showtime_by_id(
        db: Session,
        movie_id: int,
        showtime_id: int,
    ) -> Optional[MovieShowtime]:
        return (
            db.query(MovieShowtime)
            .options(joinedload(MovieShowtime.theater))
            .filter(
                MovieShowtime.id == showtime_id,
                MovieShowtime.movie_id == movie_id,
                MovieShowtime.is_active == True,
            )
            .first()
        )

    @staticmethod
    def get_theaters_for_movie(db: Session, movie_id: int) -> Optional[List[Theater]]:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            return None
        return db.query(Theater).join(TheaterMovie).filter(
            TheaterMovie.movie_id == movie_id,
            TheaterMovie.is_active == True,
            Theater.is_active == True,
        ).all()

    @staticmethod
    def get_movies_coming_soon(db: Session, limit: int = 10) -> List[Movie]:
        return (
            db.query(Movie)
            .options(selectinload(Movie.theater_movies).selectinload(TheaterMovie.theater))
            .filter(Movie.status == MovieStatus.COMING_SOON, Movie.is_active == True)
            .order_by(Movie.release_date)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_movies_in_presale(db: Session, limit: int = 10) -> List[Movie]:
        return (
            db.query(Movie)
            .options(selectinload(Movie.theater_movies).selectinload(TheaterMovie.theater))
            .filter(Movie.is_presale == True, Movie.is_active == True)
            .order_by(Movie.release_date)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_hall_seats(db: Session, hall_template_id: int) -> List[HallSeat]:
        """Carga los asientos del template con cache TTL 1h."""
        cache_key = f"hall_template:{hall_template_id}:seats"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached  # devuelve lista de dicts serializada

        seats = (
            db.query(HallSeat)
            .filter(HallSeat.hall_template_id == hall_template_id, HallSeat.is_active == True)
            .order_by(HallSeat.row_label, HallSeat.seat_number)
            .all()
        )
        # Serializar para cache
        seat_data = [
            {"code": s.seat_code, "number": s.seat_number, "row": s.row_label, "type": s.seat_type}
            for s in seats
        ]
        cache.set(cache_key, seat_data, ttl=_HALL_SEATS_TTL)
        return seat_data  # type: ignore[return-value]

    @staticmethod
    async def get_occupied_seats(showtime_id: int) -> List[str]:
        """Consulta booking-service para obtener asientos ocupados. TTL 30s."""
        cache_key = f"showtime:{showtime_id}:occupied_seats"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{settings.BOOKING_SERVICE_URL}/api/v1/purchases/showtimes/{showtime_id}/occupied-seats"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                seats = resp.json().get("seats", [])
                cache.set(cache_key, seats, ttl=_OCCUPIED_SEATS_TTL)
                return seats
            logger.warning("occupied-seats endpoint returned %s for showtime %s", resp.status_code, showtime_id)
            return []
        except Exception as exc:
            logger.warning("No se pudo consultar occupied-seats | showtime_id=%s | error=%s", showtime_id, exc)
            return []

    @staticmethod
    def get_held_seats(showtime_id: int) -> List[str]:
        """Lee directamente de Redis los holds activos para un showtime."""
        from app.core.cache import cache as _cache
        client = _cache._get_client()
        if client is None:
            return []
        try:
            pattern = f"seat:hold:{showtime_id}:*"
            keys = client.keys(pattern)
            # La parte final de la clave es el seat_code
            return [k.split(":")[-1] for k in keys]
        except Exception as exc:
            logger.warning("Error leyendo holds Redis | showtime_id=%s | error=%s", showtime_id, exc)
            return []

    @staticmethod
    async def get_seat_map(db: Session, movie_id: int, showtime_id: int) -> Optional[Dict[str, Any]]:
        """
        Ensambla el mapa completo de asientos para una función.
        Retorna None si el showtime no existe o no tiene hall_template_id.
        """
        showtime = (
            db.query(MovieShowtime)
            .options(joinedload(MovieShowtime.theater), joinedload(MovieShowtime.movie))
            .filter(
                MovieShowtime.id == showtime_id,
                MovieShowtime.movie_id == movie_id,
                MovieShowtime.is_active == True,
            )
            .first()
        )
        if not showtime:
            return None
        if not showtime.hall_template_id:
            return {"no_template": True, "showtime": showtime}

        # Cargar asientos del template (con cache)
        seat_data = MovieService.get_hall_seats(db, showtime.hall_template_id)

        # Consultar ocupados y retenidos en paralelo
        occupied_codes = set(await MovieService.get_occupied_seats(showtime_id))
        held_codes = set(MovieService.get_held_seats(showtime_id))

        # Agrupar por fila
        rows_dict: Dict[str, List[Dict]] = {}
        for s in seat_data:
            row = s["row"]
            if s["code"] in occupied_codes:
                status = "occupied"
            elif s["code"] in held_codes:
                status = "held"
            else:
                status = "available"
            rows_dict.setdefault(row, []).append({
                "code": s["code"],
                "number": s["number"],
                "type": s["type"],
                "status": status,
            })

        rows = [{"row": row, "seats": seats} for row, seats in sorted(rows_dict.items())]
        total = len(seat_data)
        occ_count = sum(1 for s in seat_data if s["code"] in occupied_codes)
        held_count = sum(1 for s in seat_data if s["code"] in held_codes)
        avail_count = total - occ_count - held_count

        return {
            "showtime_id": showtime.id,
            "movie_title": showtime.movie.title,
            "theater": showtime.theater.name,
            "hall_number": showtime.hall_number,
            "show_date": showtime.show_date,
            "show_time": showtime.show_time,
            "format": showtime.format.value,
            "total_seats": total,
            "available": avail_count,
            "occupied": occ_count,
            "held": held_count,
            "rows": rows,
        }

    @staticmethod
    def get_home_data(
        db: Session,
        cartelera_limit: int = 8,
        coming_soon_limit: int = 4,
        presales_limit: int = 4,
    ) -> Dict[str, List[Movie]]:
        eager = selectinload(Movie.theater_movies).selectinload(TheaterMovie.theater)
        cartelera = (
            db.query(Movie).options(eager)
            .filter(Movie.is_active == True, Movie.status == MovieStatus.IN_THEATERS, Movie.available_tickets > 0)
            .order_by(desc(Movie.created_at)).limit(cartelera_limit).all()
        )
        coming_soon = (
            db.query(Movie).options(eager)
            .filter(Movie.is_active == True, Movie.status == MovieStatus.COMING_SOON)
            .order_by(Movie.release_date).limit(coming_soon_limit).all()
        )
        presales = (
            db.query(Movie).options(eager)
            .filter(Movie.is_active == True, Movie.is_presale == True)
            .order_by(Movie.release_date).limit(presales_limit).all()
        )
        return {"cartelera": cartelera, "coming_soon": coming_soon, "presales": presales}
