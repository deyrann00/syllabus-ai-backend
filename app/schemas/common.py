from pydantic import BaseModel, ConfigDict


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](Schema):
    items: list[T]
    total: int
    limit: int
    offset: int
