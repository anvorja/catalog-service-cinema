# app/api/theaters.py
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload
from datetime import date, datetime

from app.core.database import get_db
from app.models.theater import Theater, TheaterMovie, MovieShowtime
from app.models.movie import Movie, MovieStatus
from app.schemas.catalog import TheaterResponse, MovieListResponse

router = APIRouter(prefix="/api/v1/theaters", tags=["Theaters"])


@router.get("", response_model=List[TheaterResponse])
async def get_theaters(db: Session = Depends(get_db)):
    theaters = db.query(Theater).filter(Theater.is_active == True).all()
    return [TheaterResponse.from_orm(t) for t in theaters]


@router.get("/{theater_id}", response_model=TheaterResponse)
async def get_theater(theater_id: int, db: Session = Depends(get_db)):
    t = db.query(Theater).filter(Theater.id == theater_id, Theater.is_active == True).first()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Teatro no encontrado")
    return TheaterResponse.from_orm(t)


@router.get("/{theater_id}/movies", response_model=List[MovieListResponse])
async def get_theater_movies(
    theater_id: int,
    movie_status: Optional[str] = Query(default="in_theaters", alias="status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    t = db.query(Theater).filter(Theater.id == theater_id, Theater.is_active == True).first()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Teatro no encontrado")

    q = (
        db.query(Movie)
        .join(TheaterMovie)
        .filter(TheaterMovie.theater_id == theater_id, TheaterMovie.is_active == True, Movie.is_active == True)
    )
    if movie_status:
        try:
            q = q.filter(Movie.status == MovieStatus(movie_status))
        except ValueError:
            raise HTTPException(400, f"Estado inválido: {movie_status}")

    movies = q.order_by(Movie.created_at.desc()).offset(skip).limit(limit).all()
    return [MovieListResponse.from_orm(m) for m in movies]


@router.get("/{theater_id}/schedule")
async def get_theater_schedule(
    theater_id: int,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    from datetime import date as date_type

    t = db.query(Theater).filter(Theater.id == theater_id, Theater.is_active == True).first()
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Teatro no encontrado")

    target = date_type.today()
    if date:
        try:
            target = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "Formato de fecha inválido. Use YYYY-MM-DD")

    showtimes = (
        db.query(MovieShowtime)
        .options(joinedload(MovieShowtime.movie))
        .filter(
            MovieShowtime.theater_id == theater_id,
            MovieShowtime.show_date == target,
            MovieShowtime.is_active == True,
        )
        .order_by(MovieShowtime.show_time)
        .all()
    )

    by_movie = {}
    for st in showtimes:
        mid = st.movie_id
        if mid not in by_movie:
            by_movie[mid] = {
                "movie": {
                    "id": st.movie.id, "title": st.movie.title,
                    "director": st.movie.director, "duration": st.movie.duration,
                    "rating": st.movie.rating, "poster_url": st.movie.poster_url,
                    "price": st.movie.price,
                },
                "showtimes": [],
            }
        by_movie[mid]["showtimes"].append({
            "id": st.id, "time": st.show_time, "format": st.format.value,
            "available_tickets": st.available_tickets, "capacity": st.capacity,
            "occupancy_percent": round((1 - st.available_tickets / st.capacity) * 100, 1),
        })

    return {
        "theater": {"id": t.id, "name": t.name, "location": t.location},
        "date": target.isoformat(),
        "formatted_date": target.strftime("%d-%b-%Y"),
        "movies": list(by_movie.values()),
        "total_showtimes": len(showtimes),
    }
