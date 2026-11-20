from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings().database_url,
    pool_pre_ping=True,
    pool_timeout=5,
    connect_args={
        "connect_timeout": 5,
        "options": "-c statement_timeout=10000",
    },
)


def database() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
