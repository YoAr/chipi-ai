"""FastAPI dependencies shared by the route modules in app/routes/.

create_app() puts the store, settings and a rate limiter on app.state; routes
read them through these functions so they never import main.py.
"""

from fastapi import Request
from starlette.exceptions import HTTPException

from .config import Settings
from .guards import client_ip


def get_store(request: Request):
    return request.app.state.store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def require_real(request: Request) -> None:
    """AI actions only run on real data; in sample-data mode they answer 409."""
    if request.app.state.settings.mock_mode:
        raise HTTPException(409, "Not available with sample data (MOCK_MODE=1)")


def limit_actions(request: Request) -> None:
    """Shared limit for expensive model actions: 10 per minute per client."""
    if not request.app.state.action_limiter.check(client_ip(request)):
        raise HTTPException(429, "Too many requests. Wait a minute and try again.")
