"""Dashboard payloads."""

import uuid
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class PopularService(BaseModel):
    id: uuid.UUID
    name: str
    count: int


class TopBarber(BaseModel):
    id: uuid.UUID
    name: str
    completed: int
    revenue: Decimal


class Period(BaseModel):
    date_from: datetime
    date_to: datetime
    timezone: str


class ShopDashboard(BaseModel):
    shop_id: uuid.UUID
    period: Period
    today: date_type
    total_appointments: int
    today_appointments: int
    completed_appointments: int
    cancelled_appointments: int
    no_show_appointments: int
    monthly_revenue: Decimal
    average_ticket: Decimal
    most_popular_service: PopularService | None = None
    top_barber: TopBarber | None = None


class BarberDashboard(BaseModel):
    barber_id: uuid.UUID
    period: Period
    today: date_type
    total_appointments: int
    today_appointments: int
    completed_appointments: int
    cancelled_appointments: int
    no_show_appointments: int
    revenue: Decimal
    average_ticket: Decimal
    rating: Decimal
    reviews_count: int
    most_popular_service: PopularService | None = None
