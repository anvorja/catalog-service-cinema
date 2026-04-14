# app/models/movie.py
from datetime import date
from typing import List, TYPE_CHECKING
from urllib.parse import urlparse
import enum

from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy import String, Integer, Float, Boolean, Date, Enum

from .base import BaseModel

if TYPE_CHECKING:
    from .theater import TheaterMovie, MovieShowtime


class MovieStatus(str, enum.Enum):
    IN_THEATERS = "in_theaters"
    COMING_SOON = "coming_soon"
    ENDED = "ended"


class Movie(BaseModel):
    __tablename__ = "movies"

    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    genre: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    rating: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    director: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[MovieStatus] = mapped_column(
        Enum(MovieStatus), default=MovieStatus.IN_THEATERS, nullable=False, index=True
    )
    is_presale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    release_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    max_capacity: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    available_tickets: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    poster_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    backdrop_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    detail_1_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    detail_2_url: Mapped[str] = mapped_column(String(1000), nullable=False)

    theater_movies: Mapped[List["TheaterMovie"]] = relationship(back_populates="movie")
    showtimes: Mapped[List["MovieShowtime"]] = relationship(back_populates="movie")

    @property
    def sold_tickets(self) -> int:
        return self.max_capacity - self.available_tickets

    @property
    def occupancy_rate(self) -> float:
        if self.max_capacity == 0:
            return 0.0
        return round((self.sold_tickets / self.max_capacity) * 100, 2)

    @property
    def theaters(self) -> List[str]:
        return [tm.theater.name for tm in self.theater_movies if tm.theater.is_active]

    @property
    def is_available(self) -> bool:
        return self.is_active and self.available_tickets > 0

    @property
    def formatted_release_date(self) -> str:
        months = {
            1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
            7: "Jul", 8: "Ago", 9: "Sept", 10: "Oct", 11: "Nov", 12: "Dic",
        }
        return f"{self.release_date.day:02d}-{months[self.release_date.month]}-{self.release_date.year}"

    @property
    def detail_images(self) -> list:
        return [self.detail_1_url, self.detail_2_url]

    @property
    def all_image_urls(self) -> list:
        return [self.poster_url, self.backdrop_url, self.detail_1_url, self.detail_2_url]

    @property
    def is_in_theaters(self) -> bool:
        return self.status == MovieStatus.IN_THEATERS

    @property
    def is_coming_soon(self) -> bool:
        return self.status == MovieStatus.COMING_SOON

    def can_purchase(self, quantity: int = 1) -> bool:
        return self.is_available and self.available_tickets >= quantity

    def get_purchase_availability_info(self) -> dict:
        return {
            "can_purchase": self.can_purchase(),
            "available_tickets": self.available_tickets,
            "status": self.status.value,
            "is_presale": self.is_presale,
        }

    @property
    def images(self) -> dict:
        return {
            "poster": self.poster_url,
            "backdrop": self.backdrop_url,
            "detail_1": self.detail_1_url,
            "detail_2": self.detail_2_url,
        }
