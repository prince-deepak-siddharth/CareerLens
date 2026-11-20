import re
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import database
from .models import User
from .observability import span
from .schemas import Strict

router = APIRouter(prefix="/auth", tags=["authentication"])
passwords = PasswordHash.recommended()
dummy = passwords.hash("invalid-account-password-verification")
DB = Annotated[Session, Depends(database)]
bearer = HTTPBearer(auto_error=False)


class Credentials(Strict):
    model_config = ConfigDict(str_strip_whitespace=False)

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value.casefold()


class Account(Strict):
    id: UUID
    email: str
    admin: bool


class Token(Strict):
    access_token: str
    token_type: str = "bearer"


def issue_token(user):
    now = datetime.now(UTC)
    return Token(
        access_token=jwt.encode(
            {"sub": str(user.id), "iat": now, "exp": now + timedelta(hours=8), "aud": "careerlens"},
            settings().jwt_secret.get_secret_value(),
            algorithm="HS256",
        )
    )


def current_user(db: DB, credential: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    with span("auth.verify"):
        try:
            if credential is None:
                raise ValueError
            payload = jwt.decode(
                credential.credentials,
                settings().jwt_secret.get_secret_value(),
                algorithms=["HS256"],
                audience="careerlens",
                options={"require": ["exp", "sub"]},
            )
            user = db.get(User, UUID(payload["sub"]))
            if user:
                return user
        except (jwt.InvalidTokenError, ValueError):
            pass
    raise HTTPException(401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"})


CurrentUser = Annotated[User, Depends(current_user)]


def account(user):
    return Account(id=user.id, email=user.email, admin=user.is_admin and user.email in settings().admins)


def administrator(user: CurrentUser):
    if not account(user).admin:
        raise HTTPException(403, "Administrator access required")
    return user


Admin = Annotated[User, Depends(administrator)]


def login_limit(request: Request):
    request.app.state.rate_limit(request, "auth", 12)


@router.post("/register", status_code=201, dependencies=[Depends(login_limit)])
def register(data: Credentials, db: DB) -> Token:
    if data.email in settings().admins:
        raise HTTPException(403, "This address is reserved for operator provisioning")
    user = User(email=data.email, password_hash=passwords.hash(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This account already exists") from None
    return issue_token(user)


@router.post("/login", dependencies=[Depends(login_limit)])
def login(data: Credentials, db: DB) -> Token:
    user = db.scalar(select(User).where(User.email == data.email))
    valid = passwords.verify(data.password, user.password_hash if user else dummy)
    if not valid or user is None:
        raise HTTPException(401, "Invalid email or password")
    return issue_token(user)


@router.get("/me")
def me(user: CurrentUser) -> Account:
    return account(user)
