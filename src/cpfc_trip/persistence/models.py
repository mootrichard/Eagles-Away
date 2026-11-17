from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    submission_id: Mapped[str] = mapped_column(String(36), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    access_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workflow_input: Mapped[dict[str, Any]] = mapped_column(JSON)
    saved_itinerary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class SearchCacheRow(Base):
    __tablename__ = "search_cache"
    namespace: Mapped[str] = mapped_column(String(16), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
