"""Request-scoped dependencies: settings, a database session and the signed-in user."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import TokenError, decode_token
from app.models import User


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request: committed if the handler returns, rolled back if it
    raises."""
    async with request.app.state.sessions.begin() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def current_user(
    session: SessionDep,
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        claims = decode_token(creds.credentials, settings, "access")
    except TokenError as e:
        raise unauthorized("invalid token") from e
    user = await session.get(User, claims["sub"])
    if user is None:
        raise unauthorized("invalid token")
    return user


UserDep = Annotated[User, Depends(current_user)]
