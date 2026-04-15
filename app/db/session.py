import logging
from collections.abc import AsyncGenerator

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────────
#
# NullPool: SQLAlchemy holds zero connections between requests.
# Every session.execute() acquires a fresh connection from PgBouncer, which
# multiplexes it to one of its 20 server-side PostgreSQL connections.
# The connection is released back to PgBouncer as soon as the statement
# completes (transaction mode) — never sooner, never later.
#
# asyncpg + PgBouncer transaction mode requires:
#   statement_cache_size=0          — disables asyncpg's local statement cache
#   prepared_statement_cache_size=0 — disables the prepared-statement name cache
#
# Without these, asyncpg will try to re-use prepared statement names across
# different server connections, which PgBouncer cannot route correctly.

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    },
)

# ── Session factory ───────────────────────────────────────────────────────────
#
# expire_on_commit=False: ORM objects remain readable after commit without
#   triggering a second SELECT. Critical in async code where lazy loads raise
#   MissingGreenlet errors.
#
# autoflush=False: prevents SQLAlchemy from issuing implicit flushes during
#   queries, which would start a transaction outside our explicit control and
#   break PgBouncer transaction-mode semantics.

async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ── Dependency ────────────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a short-lived async database session.

    Lifecycle per request:
      1. A new AsyncSession is created (no connection acquired yet).
      2. The first await session.execute(...) acquires a connection from PgBouncer.
      3. On normal exit the session is closed; NullPool discards the connection
         immediately — it is NOT returned to any pool.
      4. On exception: explicit rollback fires first, then close.

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            return await item_repo.get_all(db)
    """
    async with async_session_maker() as session:
        try:
            yield session
        except SQLAlchemyError:
            await session.rollback()
            raise
        except Exception:
            # Non-SQLAlchemy errors (validation, auth, etc.) — still rollback
            # any open transaction so PgBouncer gets a clean connection back.
            await session.rollback()
            raise
        # session.close() is called automatically by async_session_maker's
        # __aexit__; with NullPool the underlying connection is discarded here.
