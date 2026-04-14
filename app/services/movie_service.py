# app/services/movie_service.py — read-only catalog queries
from typing import List, Optional, Dict, Any
from datetime import date
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import desc, or_, and_

from app.models.movie import Movie, MovieStatus
from app.models.theater import Theater, TheaterMovie, MovieShowtime
from app.core.cache import cache


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
