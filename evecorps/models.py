import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Computed, DateTime, Float, ForeignKey, Index, Integer, String, Text, Uuid,
    false, func, text, true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship


from .extensions import db


class Alliance(db.Model):
    """Public alliance info, cached from ESI /alliances/{id}."""

    __tablename__ = "alliance"

    alliance_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(100))
    ticker: Mapped[str] = mapped_column(String(10))
    esi_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    # From ESI; used as verified filters.
    war_eligible: Mapped[bool | None] = mapped_column(Boolean)
    tax_rate: Mapped[float | None] = mapped_column(Float)  # ISK tax, percent 0-100
    date_founded: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enlisted_faction_id: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str | None] = mapped_column(String(10))  # "active" / "closed"
    # Raw in-game description (EVE markup), offered as a starting point for adverts.
    description: Mapped[str | None] = mapped_column(Text)
    # When ESI says the cached data may be refreshed; never fetch before this.
    esi_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    characters: Mapped[list["Character"]] = relationship(back_populates="corporation")
    # No DB foreign key: corp rows can arrive before their alliance row is fetched.
    alliance: Mapped[Alliance | None] = relationship(
        primaryjoin=lambda: foreign(Corporation.alliance_id) == Alliance.alliance_id,
        viewonly=True, lazy="joined",
    )
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
    # When ESI last confirmed corporation_id (affiliation lookup at login succeeded).
    corp_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
        Index("ix_advert_search", "search_vector", postgresql_using="gin"),
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
    alpha_friendly: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    commitment: Mapped[str | None] = mapped_column(String(10))  # key of choices.COMMITMENT
    srp: Mapped[bool] = mapped_column(Boolean, default=False)
    # Minimum skill points required; whole SP, 0 = no requirement.
    min_sp: Mapped[int] = mapped_column(BigInteger, default=0)

    # Structured contact details. contacts is [{"id": <character_id>, "name": str}, ...],
    # resolved through ESI when the advert is saved.
    discord_url: Mapped[str | None] = mapped_column(String(200))
    website_url: Mapped[str | None] = mapped_column(String(300))
    ingame_channel: Mapped[str | None] = mapped_column(String(60))
    contacts: Mapped[list[dict]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))

    # Set by the corp's recruiters.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # Set by moderators; recruiters can't override it.
    moderation_hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    moderation_reason: Mapped[str | None] = mapped_column(Text)
    # When the first report on this advert was posted to the admin Discord channel.
    # Only one post is ever made per advert.
    report_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped whenever a recruiter of this corp saves the advert or logs in as recruiter,
    # so readers can see the corp is still around. Shown as "Last updated".
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("character.character_id"))

    corporation: Mapped[Corporation] = relationship(back_populates="advert")
    updated_by: Mapped[Character | None] = relationship()

    # Full-text index over title and body, maintained by Postgres.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, ''))", persisted=True),
        deferred=True,
    )

    reports: Mapped[list["Report"]] = relationship(back_populates="advert", cascade="all, delete-orphan")
    revisions: Mapped[list["AdvertRevision"]] = relationship(
        back_populates="advert", cascade="all, delete-orphan", order_by="AdvertRevision.created_at"
    )

    @property
    def public(self) -> bool:
        return self.active and not self.moderation_hidden


class Report(db.Model):
    """A reader flagging an advert for moderators."""

    __tablename__ = "report"
    __table_args__ = (
        # One open report per reader per advert.
        Index("uq_report_open", "advert_id", "reporter_id", unique=True,
              postgresql_where=text("resolved_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    advert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("advert.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("character.character_id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("character.character_id"))

    advert: Mapped[Advert] = relationship(back_populates="reports")
    reporter: Mapped[Character] = relationship(foreign_keys=[reporter_id])


class ModerationLog(db.Model):
    """Audit trail of moderator actions."""

    __tablename__ = "moderation_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    advert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("advert.id", ondelete="CASCADE"), index=True)
    moderator_id: Mapped[int] = mapped_column(ForeignKey("character.character_id"))
    action: Mapped[str] = mapped_column(String(20))  # hide / unhide / dismiss
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    advert: Mapped[Advert] = relationship()
    moderator: Mapped[Character] = relationship()


class AdvertRevision(db.Model):
    """A saved version of an advert: full snapshot plus which fields changed."""

    __tablename__ = "advert_revision"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    advert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("advert.id", ondelete="CASCADE"), index=True)
    editor_id: Mapped[int] = mapped_column(ForeignKey("character.character_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    changed: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=list)
    snapshot: Mapped[dict] = mapped_column(JSONB)

    advert: Mapped[Advert] = relationship(back_populates="revisions")
    editor: Mapped[Character] = relationship()
