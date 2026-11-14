from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Shared declarative base for InfraScan application persistence models.

    Application tables use this metadata exclusively and remain separate from
    Celery's SQLAlchemy result-backend tables (`celery_taskmeta` and
    `celery_tasksetmeta`). Schema changes are managed via Alembic migrations,
    never via startup `Base.metadata.create_all()`.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
