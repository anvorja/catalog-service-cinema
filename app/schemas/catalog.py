# app/schemas/catalog.py — response schemas for catalog-service
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime, date
from enum import Enum


class MovieStatus(str, Enum):
    IN_THEATERS = "in_theaters"
    COMING_SOON = "coming_soon"
    ENDED = "ended"


class ShowtimeFormat(str, Enum):
    TWO_D_DUBBED = "2d_dubbed"
    TWO_D_SUBTITLED = "2d_subtitled"
    THREE_D = "3d"
    IMAX = "imax"


class TheaterResponse(BaseModel):
    id: int
    name: str
    location: str
    description: Optional[str]
    is_active: bool
    created_at: datetime

    @classmethod
    def from_orm(cls, t):
        return cls(
            id=t.id, name=t.name, location=t.location,
            description=t.description, is_active=t.is_active, created_at=t.created_at,
        )


class ShowtimeResponse(BaseModel):
    id: int
    show_date: date
    show_time: str
    format: ShowtimeFormat
    capacity: int
    available_tickets: int
    theater_name: str

    @classmethod
    def from_orm(cls, st):
        return cls(
            id=st.id, show_date=st.show_date, show_time=st.show_time,
            format=st.format, capacity=st.capacity,
            available_tickets=st.available_tickets, theater_name=st.theater.name,
        )


class MovieListResponse(BaseModel):
    id: int
    title: str
    genre: str
    duration: int
    rating: str
    price: float
    director: str
    country: str
    status: str
    is_presale: bool
    formatted_release_date: str
    available_tickets: int
    is_available: bool
    occupancy_rate: float
    theaters: List[str]
    poster_url: str

    @classmethod
    def from_orm(cls, m):
        return cls(
            id=m.id, title=m.title, genre=m.genre, duration=m.duration,
            rating=m.rating, price=m.price, director=m.director, country=m.country,
            status=m.status.value, is_presale=m.is_presale,
            formatted_release_date=m.formatted_release_date,
            available_tickets=m.available_tickets, is_available=m.is_available,
            occupancy_rate=m.occupancy_rate, theaters=m.theaters,
            poster_url=m.poster_url,
        )


class RatingResponse(BaseModel):
    score: int
    review: Optional[str]
    created_at: datetime

    @classmethod
    def from_orm(cls, r):
        return cls(score=r.score, review=r.review, created_at=r.created_at)


class MovieDetailResponse(BaseModel):
    id: int
    title: str
    description: str
    genre: str
    duration: int
    rating: str
    price: float
    director: str
    country: str
    status: str
    is_presale: bool
    release_date: date
    formatted_release_date: str
    max_capacity: int
    available_tickets: int
    sold_tickets: int
    is_active: bool
    is_available: bool
    occupancy_rate: float
    theaters: List[str]
    is_in_theaters: bool
    is_coming_soon: bool
    poster_url: str
    backdrop_url: str
    detail_1_url: str
    detail_2_url: str
    detail_images: List[str]
    all_image_urls: List[str]
    created_at: datetime
    showtimes: List[ShowtimeResponse] = []
    average_rating: Optional[float] = None
    rating_count: int = 0

    @classmethod
    def from_orm(cls, m, average_rating: Optional[float] = None, rating_count: int = 0):
        return cls(
            id=m.id, title=m.title, description=m.description, genre=m.genre,
            duration=m.duration, rating=m.rating, price=m.price, director=m.director,
            country=m.country, status=m.status.value, is_presale=m.is_presale,
            release_date=m.release_date, formatted_release_date=m.formatted_release_date,
            max_capacity=m.max_capacity, available_tickets=m.available_tickets,
            sold_tickets=m.sold_tickets, is_active=m.is_active, is_available=m.is_available,
            occupancy_rate=m.occupancy_rate, theaters=m.theaters,
            is_in_theaters=m.is_in_theaters, is_coming_soon=m.is_coming_soon,
            poster_url=m.poster_url, backdrop_url=m.backdrop_url,
            detail_1_url=m.detail_1_url, detail_2_url=m.detail_2_url,
            detail_images=m.detail_images, all_image_urls=m.all_image_urls,
            created_at=m.created_at,
            showtimes=[ShowtimeResponse.from_orm(st) for st in m.showtimes if st.is_active]
            if hasattr(m, 'showtimes') else [],
            average_rating=average_rating,
            rating_count=rating_count,
        )


class HomeResponse(BaseModel):
    cartelera: List[MovieListResponse]
    coming_soon: List[MovieListResponse]
    presales: List[MovieListResponse]
