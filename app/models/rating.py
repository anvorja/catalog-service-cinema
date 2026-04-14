# app/models/rating.py — catalog-service
#
# Tabla movie_ratings: una calificación por usuario por película.
# La nota es un entero de 1 a 5 (estrellas).
# review es opcional (hasta 500 caracteres).
# Identificamos el usuario por email (el JWT de auth-service usa email como sub).
#
from typing import Optional

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class MovieRating(BaseModel):
    __tablename__ = "movie_ratings"

    movie_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5
    review: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint("movie_id", "user_email", name="uq_rating_movie_user"),
    )
