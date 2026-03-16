from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self._engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False, autocommit=False, future=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
