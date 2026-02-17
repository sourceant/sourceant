"""
Polymorphic key-value configuration model.

Stores per-entity settings (repository, organization, user, etc.)
with typed values and resolution helpers.
"""

import json
from enum import Enum
from typing import Any, Dict, Optional

from sqlmodel import Field, select, UniqueConstraint

from src.config.db import get_session
from src.models.base_model import BaseModel


class ConfigType(str, Enum):
    STRING = "string"
    INT = "int"
    BOOL = "bool"
    FLOAT = "float"
    JSON = "json"


class Config(BaseModel, table=True):
    __tablename__ = "configs"
    __table_args__ = (
        UniqueConstraint(
            "configurable_type",
            "configurable_id",
            "key",
            name="uq_config_entry",
        ),
    )

    configurable_type: str = Field(index=True)
    configurable_id: str = Field(index=True)
    key: str = Field(index=True)
    value: str
    type: str = Field(default=ConfigType.STRING)

    def cast_value(self) -> Any:
        """Cast self.value based on self.type. Returns raw string on error."""
        try:
            if self.type == ConfigType.BOOL:
                return self.value.lower() in ("true", "1", "yes")
            if self.type == ConfigType.INT:
                return int(self.value)
            if self.type == ConfigType.FLOAT:
                return float(self.value)
            if self.type == ConfigType.JSON:
                return json.loads(self.value)
        except (ValueError, json.JSONDecodeError):
            return self.value
        return self.value

    @classmethod
    def get_value(
        cls,
        configurable_type: str,
        configurable_id: str,
        key: str,
        default: Any = None,
    ) -> Any:
        """Fetch a single config value, cast to the correct Python type."""
        with next(get_session()) as session:
            entry = session.exec(
                select(cls).where(
                    cls.configurable_type == configurable_type,
                    cls.configurable_id == configurable_id,
                    cls.key == key,
                )
            ).first()
        if entry is None:
            return default
        return entry.cast_value()

    @classmethod
    def get_all_for(
        cls, configurable_type: str, configurable_id: str
    ) -> Dict[str, Any]:
        """Fetch all config entries for an entity as a {key: typed_value} dict."""
        with next(get_session()) as session:
            entries = session.exec(
                select(cls).where(
                    cls.configurable_type == configurable_type,
                    cls.configurable_id == configurable_id,
                )
            ).all()
        return {entry.key: entry.cast_value() for entry in entries}

    @classmethod
    def set_value(
        cls,
        configurable_type: str,
        configurable_id: str,
        key: str,
        value: Any,
        type: str = ConfigType.STRING,
    ) -> "Config":
        """Upsert a config entry."""
        str_value = cls._serialize_value(value, type)
        with next(get_session()) as session:
            entry = session.exec(
                select(cls).where(
                    cls.configurable_type == configurable_type,
                    cls.configurable_id == configurable_id,
                    cls.key == key,
                )
            ).first()
            if entry:
                entry.value = str_value
                entry.type = type
            else:
                entry = cls(
                    configurable_type=configurable_type,
                    configurable_id=configurable_id,
                    key=key,
                    value=str_value,
                    type=type,
                )
            session.add(entry)
            session.commit()
            session.refresh(entry)
        return entry

    @staticmethod
    def _serialize_value(value: Any, type: str) -> str:
        """Convert a Python value to its string representation for storage."""
        if type == ConfigType.JSON:
            return json.dumps(value)
        if type == ConfigType.BOOL:
            return "true" if value else "false"
        return str(value)
