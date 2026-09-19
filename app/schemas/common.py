"""Shared schema base."""

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Response model read directly off a SQLAlchemy instance."""

    model_config = ConfigDict(from_attributes=True)
