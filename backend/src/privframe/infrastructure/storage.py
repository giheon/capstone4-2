import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import JSON, Integer, String, create_engine, event, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from privframe.domain.models import Contract

T = TypeVar("T", bound=Contract)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Artifact path must stay inside data directory")
        return path

    def relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root))

    def put_bytes(self, relative: str, data: bytes) -> str:
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return relative


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ConflictError(ValueError):
    pass


class Repository:
    """Short transactions, optimistic updates, JSON versioned domain snapshots."""

    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 15})

        @event.listens_for(self.engine, "connect")
        def setup(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        Base.metadata.create_all(self.engine)

    def insert(self, kind: str, value: T) -> T:
        with Session(self.engine) as session, session.begin():
            session.add(Record(kind=kind, id=value.id, payload=value.model_dump(mode="json")))
        return value

    def get(self, kind: str, record_id: str, cls: type[T]) -> T:
        with Session(self.engine) as session:
            row = session.get(Record, (kind, record_id))
            if row is None:
                raise KeyError(f"{kind} {record_id} not found")
            return cls.model_validate(row.payload)

    def list(self, kind: str, cls: type[T]) -> list[T]:
        with Session(self.engine) as session:
            rows = session.scalars(select(Record).where(Record.kind == kind)).all()
            values = [cls.model_validate(row.payload) for row in rows]
            return sorted(values, key=lambda value: value.created_at, reverse=True)

    def mutate(self, kind: str, record_id: str, cls: type[T], change: Callable[[T], None]) -> T:
        with Session(self.engine) as session, session.begin():
            row = session.get(Record, (kind, record_id))
            if row is None:
                raise KeyError(f"{kind} {record_id} not found")
            value = cls.model_validate(row.payload)
            change(value)
            result = session.execute(
                update(Record).where(Record.kind == kind, Record.id == record_id, Record.revision == row.revision)
                .values(payload=value.model_dump(mode="json"), revision=row.revision + 1)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise ConflictError("Record changed concurrently; reload and retry")
            return value
