from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self._engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False, autocommit=False, future=True)
        self._schema_files = [
            Path(__file__).resolve().parent.parent / "db" / "init" / "001_enable_pgvector.sql",
            Path(__file__).resolve().parent.parent / "db" / "init" / "002_schema.sql",
        ]

    def initialize_schema(self) -> None:
        with self._engine.begin() as connection:
            for schema_file in self._schema_files:
                connection.exec_driver_sql(schema_file.read_text(encoding="utf-8"))

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
