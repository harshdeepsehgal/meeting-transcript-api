from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

SCHEMA_NAME = "meeting_transcript"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for future domain models and Alembic metadata discovery."""

    metadata = MetaData(schema=SCHEMA_NAME, naming_convention=NAMING_CONVENTION)
