"""Aggregates every v1 resource router. Routers are added phase by phase."""

from fastapi import APIRouter

from app.api.v1 import (
    appointments,
    auth,
    availability,
    barbers,
    barbershops,
    dashboard,
    reviews,
    schedule,
    services,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(barbershops.router)
api_router.include_router(barbers.shop_scoped)
api_router.include_router(barbers.router)
api_router.include_router(availability.router)
api_router.include_router(schedule.router)
api_router.include_router(schedule.breaks_router)
api_router.include_router(services.shop_scoped)
api_router.include_router(services.router)
api_router.include_router(appointments.shop_scoped)
api_router.include_router(appointments.router)
api_router.include_router(reviews.appointment_scoped)
api_router.include_router(reviews.barber_scoped)
api_router.include_router(dashboard.router)
