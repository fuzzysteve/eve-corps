import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func, true,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


class Corporation(db.Model):
    """Public corporation info, cached from ESI /corporations/{id}."""

    __tablename__ = "corporation"

    # EVE IDs are assigned by CCP, so no autoincrement.
    corporation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    ticker: Mapped[str] = mapped_column(String(10))
    alliance_id: Mapped[int | None] = mapped_column(BigInteger)
    member_count: Mapped[int | None] = mapped_column(Integer)
    ceo_id: Mapped[int | None] = mapped_column(BigInteger)
    # Raw in-game description (EVE markup), offered as a starting point for adverts.
    description: Mapped[str | None] = mapped_column(Text)
    # When ESI says the cached data may be refreshed; never fetch before this.
    esi_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    characters: Mapped[list["Character"]] = relationship(back_populates="corporation")
    advert: Mapped["Advert | None"] = relationship(back_populates="corporation")


class Character(db.Model):
    """A character that has logged in through EVE SSO."""

    __tablename__ = "character"

    character_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    # SSO "owner" claim; changes when the character is sold to another account.
    owner_hash: Mapped[str | None] = mapped_column(String(64))
    corporation_id: Mapped[int | None] = mapped_column(
        ForeignKey("corporation.corporation_id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    corporation: Mapped[Corporation | None] = relationship(back_populates="characters")
    # Set when an SSO login with the roles scope showed Director/Personnel_Manager
    # (or CEO) in this corp. Re-checked once it is older than RECRUITER_TTL.
    recruiter_corp_id: Mapped[int | None] = mapped_column(BigInteger)
    recruiter_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Advert(db.Model):
    """One recruitment advert per corporation."""

    __tablename__ = "advert"
    __table_args__ = (
        Index("ix_advert_timezones", "timezones", postgresql_using="gin"),
        Index("ix_advert_languages", "languages", postgresql_using="gin"),
        Index("ix_advert_activities", "activities", postgresql_using="gin"),
        Index("ix_advert_space", "space", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[int] = mapped_column(
        ForeignKey("corporation.corporation_id"), unique=True
    )
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)

    timezones: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    activities: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    space: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)

    newbro_friendly: Mapped[bool] = mapped_column(Boolean, default=False)
    srp: Mapped[bool] = mapped_column(Boolean, default=False)
    # Minimum skill points required; whole SP, 0 = no requirement.
    min_sp: Mapped[int] = mapped_column(BigInteger, default=0)

    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("character.character_id"))

    corporation: Mapped[Corporation] = relationship(back_populates="advert")
    updated_by: Mapped[Character | None] = relationship()
