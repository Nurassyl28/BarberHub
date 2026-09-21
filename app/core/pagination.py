"""One pagination envelope for every list endpoint (see docs/SPEC.md §7)."""

from dataclasses import dataclass
from math import ceil
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class PageParams:
    page: int
    limit: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


def page_params(
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
) -> PageParams:
    return PageParams(page=page, limit=limit)


PageParamsDep = Annotated[PageParams, Depends(page_params)]


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    limit: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=params.page,
            limit=params.limit,
            pages=ceil(total / params.limit) if params.limit else 0,
        )
