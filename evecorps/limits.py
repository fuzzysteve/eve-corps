"""Fixed-window rate limits, stored in Postgres so they hold across gunicorn workers."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import insert

from .extensions import db
from .models import RateLimit

# action -> (max requests, window in seconds), per character.
LIMITS = {
    "report": (5, 3600),
    "preview": (60, 600),
    "convert": (20, 600),
    "import": (20, 600),
}


def allow(action: str, who: int | str) -> bool:
    """Count one request for `who` doing `action`; False once over the limit."""
    limit, window = LIMITS[action]
    now = datetime.now(timezone.utc)
    start = datetime.fromtimestamp(int(now.timestamp()) // window * window, timezone.utc)
    stmt = (
        insert(RateLimit)
        .values(key=f"{action}:{who}", window_start=start, count=1)
        .on_conflict_do_update(
            index_elements=[RateLimit.key, RateLimit.window_start],
            set_={"count": RateLimit.count + 1},
        )
        .returning(RateLimit.count)
    )
    count = db.session.execute(stmt).scalar_one()
    db.session.commit()
    return count <= limit


def prune(older_than: timedelta = timedelta(days=1)) -> int:
    cutoff = datetime.now(timezone.utc) - older_than
    deleted = db.session.query(RateLimit).filter(RateLimit.window_start < cutoff).delete()
    db.session.commit()
    return deleted
