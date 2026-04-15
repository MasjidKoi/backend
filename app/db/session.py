from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Create async engine optimized for PgBouncer in transaction mode
# We use NullPool so SQLAlchemy doesn't hold connections; PgBouncer handles the pooling.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    poolclass=NullPool,
    # Disable prepared statement cache which is incompatible with PgBouncer transaction mode
    connect_args={
        "statement_cache_size": 0,
    }
)

# Create a sessionmaker
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

async def get_db():
    async with async_session_maker() as session:
        yield session
