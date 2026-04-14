# app/api/movies.py
from typing import List, Optional
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.cache import cache
from app.core.config import settings
from app.core.auth import get_current_user_email
from app.services.movie_service import MovieService
from app.models.movie import MovieStatus
from app.schemas.catalog import MovieListResponse, MovieDetailResponse, ShowtimeResponse, TheaterResponse, HomeResponse, RatingResponse

router = APIRouter(prefix="/api/v1/movies", tags=["Movies"])


@router.get("", response_model=List[MovieListResponse])
async def get_movies(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    status: Optional[str] = Query(default=None),
    is_presale: Optional[bool] = Query(default=None),
    theater: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    if status is None:
        movies = MovieService.get_movies(
            db, skip=skip, limit=limit, theater_name=theater,
            is_presale=is_presale, available_only=True,
        )
    else:
        try:
            movie_status = MovieStatus(status)
        except ValueError:
            raise HTTPException(400, "Estado inválido")
        movies = MovieService.get_movies(
            db, skip=skip, limit=limit, status=movie_status,
            is_presale=is_presale, theater_name=theater, available_only=True,
        )
    return [MovieListResponse.from_orm(m) for m in movies]


@router.get("/search", response_model=List[MovieListResponse])
async def search_movies(
    q: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
    director: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    rating: Optional[str] = Query(None, pattern="^(G|PG|PG-13|R|NC-17)$"),
    status: Optional[str] = Query(None),
    theater: Optional[str] = Query(None),
    available_only: bool = Query(True),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    movie_status = None
    if status:
        try:
            movie_status = MovieStatus(status)
        except ValueError:
            pass
    movies = MovieService.get_movies(
        db, skip=skip, limit=limit, search=q, genre=genre, director=director,
        country=country, min_price=min_price, max_price=max_price, rating=rating,
        status=movie_status, theater_name=theater, available_only=available_only,
    )
    return [MovieListResponse.from_orm(m) for m in movies]


@router.get("/coming-soon", response_model=List[MovieListResponse])
async def get_coming_soon(
    limit: int = Query(default=10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    return [MovieListResponse.from_orm(m) for m in MovieService.get_movies_coming_soon(db, limit)]


@router.get("/presales", response_model=List[MovieListResponse])
async def get_presales(
    limit: int = Query(default=10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    return [MovieListResponse.from_orm(m) for m in MovieService.get_movies_in_presale(db, limit)]


@router.get("/home", response_model=HomeResponse)
async def get_home_data(
    cartelera_limit: int = Query(default=8, ge=1, le=20),
    coming_soon_limit: int = Query(default=4, ge=1, le=10),
    presales_limit: int = Query(default=4, ge=1, le=10),
    db: Session = Depends(get_db),
):
    cache_key = f"home:{cartelera_limit}:{coming_soon_limit}:{presales_limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = MovieService.get_home_data(db, cartelera_limit, coming_soon_limit, presales_limit)
    result = {
        "cartelera": [MovieListResponse.from_orm(m).model_dump(mode="json") for m in data["cartelera"]],
        "coming_soon": [MovieListResponse.from_orm(m).model_dump(mode="json") for m in data["coming_soon"]],
        "presales": [MovieListResponse.from_orm(m).model_dump(mode="json") for m in data["presales"]],
    }
    cache.set(cache_key, result, ttl=settings.CACHE_HOME_TTL)
    return result


@router.get("/{movie_id}", response_model=MovieDetailResponse)
async def get_movie(movie_id: int, db: Session = Depends(get_db)):
    from sqlalchemy import func as sa_func
    from app.models.rating import MovieRating

    movie = MovieService.get_movie_with_showtimes(db, movie_id)
    if not movie:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Película no encontrada")

    rating_agg = db.query(
        sa_func.avg(MovieRating.score).label("avg"),
        sa_func.count(MovieRating.id).label("cnt"),
    ).filter(MovieRating.movie_id == movie_id, MovieRating.is_active == True).one()

    avg_rating = round(float(rating_agg.avg), 1) if rating_agg.avg else None
    return MovieDetailResponse.from_orm(movie, average_rating=avg_rating, rating_count=rating_agg.cnt or 0)


@router.get("/{movie_id}/showtimes", response_model=List[ShowtimeResponse])
async def get_movie_showtimes(
    movie_id: int,
    theater_id: Optional[int] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    if not MovieService.get_movie_by_id(db, movie_id):
        raise HTTPException(404, "Película no encontrada")
    showtimes = MovieService.get_movie_showtimes(db, movie_id, theater_id, start_date, end_date)
    return [ShowtimeResponse.from_orm(st) for st in showtimes]


@router.get("/{movie_id}/showtimes/{showtime_id}", response_model=ShowtimeResponse)
async def get_showtime_by_id(
    movie_id: int,
    showtime_id: int,
    db: Session = Depends(get_db),
):
    """Retorna una función específica. Usado por booking-service para validar disponibilidad."""
    showtime = MovieService.get_showtime_by_id(db, movie_id, showtime_id)
    if not showtime:
        raise HTTPException(404, "Función no encontrada")
    return ShowtimeResponse.from_orm(showtime)


@router.get("/{movie_id}/theaters", response_model=List[TheaterResponse])
async def get_movie_theaters(movie_id: int, db: Session = Depends(get_db)):
    theaters = MovieService.get_theaters_for_movie(db, movie_id)
    if theaters is None:
        raise HTTPException(404, "Película no encontrada")
    return [TheaterResponse.from_orm(t) for t in theaters]


@router.get("/{movie_id}/availability")
async def get_movie_availability(movie_id: int, db: Session = Depends(get_db)):
    movie = MovieService.get_movie_with_showtimes(db, movie_id)
    if not movie:
        raise HTTPException(404, "Película no encontrada")

    today = date.today()
    cutoff = today + timedelta(days=3)
    upcoming = [
        st for st in movie.showtimes
        if st.is_active and today <= st.show_date <= cutoff
    ]
    by_date = {}
    for st in upcoming:
        key = st.show_date.isoformat()
        by_date.setdefault(key, []).append({
            "theater": st.theater.name, "time": st.show_time,
            "format": st.format.value, "available_tickets": st.available_tickets,
            "capacity": st.capacity,
        })

    return {
        "movie_id": movie.id,
        "title": movie.title,
        "status": movie.status.value,
        "is_presale": movie.is_presale,
        "price": movie.price,
        "theaters": movie.theaters,
        "total_available": movie.available_tickets,
        "is_available": movie.is_available,
        "occupancy_rate": movie.occupancy_rate,
        "upcoming_showtimes": by_date,
        "purchase_availability": movie.get_purchase_availability_info(),
    }


class RateMovieRequest(BaseModel):
    score: int = Field(..., ge=1, le=5, description="Calificación de 1 a 5 estrellas")
    review: Optional[str] = Field(None, max_length=500)


@router.post("/{movie_id}/rate", response_model=RatingResponse, status_code=status.HTTP_201_CREATED)
async def rate_movie(
    movie_id: int,
    body: RateMovieRequest,
    db: Session = Depends(get_db),
    user_email: str = Depends(get_current_user_email),
):
    """
    Califica una película (1-5 estrellas). Requiere autenticación.
    Un usuario solo puede calificar una película una vez; re-enviar actualiza la calificación.
    """
    from app.models.rating import MovieRating

    movie = MovieService.get_movie_by_id(db, movie_id)
    if not movie:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Película no encontrada")

    existing = (
        db.query(MovieRating)
        .filter(MovieRating.movie_id == movie_id, MovieRating.user_email == user_email)
        .first()
    )

    if existing:
        existing.score = body.score
        existing.review = body.review
        db.commit()
        db.refresh(existing)
        return RatingResponse.from_orm(existing)

    rating = MovieRating(
        movie_id=movie_id,
        user_email=user_email,
        score=body.score,
        review=body.review,
    )
    db.add(rating)
    db.commit()
    db.refresh(rating)
    return RatingResponse.from_orm(rating)
