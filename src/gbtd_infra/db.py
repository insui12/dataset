from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import AppConfig
from .models import Base


def get_engine(database_url: str):
    return create_engine(
        database_url,
        pool_pre_ping=True,
        future=True,
        connect_args={"options": "-c timezone=UTC"},
    )


def get_session_factory(database_url: str):
    engine = get_engine(database_url)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)


def init_db(database_url: str) -> None:
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(session_factory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def build_session_factory(config: AppConfig):
    return get_session_factory(config.database_url)
