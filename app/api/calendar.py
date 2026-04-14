# app/api/calendar.py
from typing import Optional, Dict, Any
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models.theater import Theater, MovieShowtime

router = APIRouter(prefix="/api/v1/calendar", tags=["Calendar"])


@router.get("/calendar/week", response_model=Dict[str, Any])
async def get_week_calendar(
    start_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    if not start_date:
        today = date.today()
        start_date = today - timedelta(days=today.weekday())

    week_dates = []
    for i in range(7):
        d = start_date + timedelta(days=i)
        week_dates.append({
            "date": d,
            "day_number": d.day,
            "day_name_short": ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"][d.weekday()],
            "month_short": ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN",
                            "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"][d.month - 1],
            "is_today": d == date.today(),
        })

    theaters = db.query(Theater).filter(Theater.is_active == True).all()
    week_end = start_date + timedelta(days=6)
    showtimes = (
        db.query(MovieShowtime)
        .options(joinedload(MovieShowtime.movie), joinedload(MovieShowtime.theater))
        .filter(
            MovieShowtime.show_date >= start_date,
            MovieShowtime.show_date <= week_end,
            MovieShowtime.is_active == True,
        )
        .order_by(
            MovieShowtime.theater_id, MovieShowtime.movie_id,
            MovieShowtime.show_date, MovieShowtime.show_time,
        )
        .all()
    )

    theaters_data = []
    for theater in theaters:
        t_sts = [st for st in showtimes if st.theater_id == theater.id]
        movies_in_theater = {}
        for st in t_sts:
            mid = st.movie_id
            if mid not in movies_in_theater:
                movies_in_theater[mid] = {
                    "movie": {
                        "id": st.movie.id, "title": st.movie.title,
                        "director": st.movie.director, "duration": st.movie.duration,
                        "rating": st.movie.rating, "poster_url": st.movie.poster_url,
                        "genre": st.movie.genre, "price": st.movie.price,
                    },
                    "schedule": {d["date"].isoformat(): [] for d in week_dates},
                }
            day_key = st.show_date.isoformat()
            if day_key in movies_in_theater[mid]["schedule"]:
                movies_in_theater[mid]["schedule"][day_key].append({
                    "id": st.id, "time": st.show_time, "format": st.format.value,
                    "available": st.available_tickets, "capacity": st.capacity,
                })

        theaters_data.append({
            "theater": {"id": theater.id, "name": theater.name, "location": theater.location},
            "movies": [
                {"movie": v["movie"], "schedule": v["schedule"]}
                for v in movies_in_theater.values()
            ],
        })

    return {
        "week_dates": [
            {
                "date": d["date"].isoformat(),
                "day_number": d["day_number"],
                "day_name_short": d["day_name_short"],
                "month_short": d["month_short"],
                "is_today": d["is_today"],
            }
            for d in week_dates
        ],
        "theaters": theaters_data,
        "total_theaters": len(theaters),
    }
