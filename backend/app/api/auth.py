"""
User authentication endpoints — signup, login, profile management
"""
from fastapi import APIRouter, HTTPException, Depends, status, Request, Response
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db_session
from app.database.models import User
from app.auth.password import hash_password, verify_password
from app.auth.jwt import create_access_token, create_refresh_token, decode_token, REFRESH_TOKEN_EXPIRE_DAYS
from app.auth.deps import get_current_user
from app.auth.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/auth", tags=["Authentication"])

REFRESH_COOKIE_NAME = "ia_refresh_token"

# SameSite=None is required because the frontend and API are served from
# different origins (different ports in dev, different domains in prod) —
# without it the browser won't attach the cookie to cross-origin requests at
# all. `Secure` is mandatory alongside SameSite=None; both http://localhost
# and https:// production origins satisfy browsers' "potentially trustworthy
# origin" check for Secure cookies, so this works in dev without HTTPS.
# Scoped to /auth so no other route ever receives this cookie.
_COOKIE_KWARGS = dict(
    httponly=True,
    secure=True,
    samesite="none",
    path="/auth",
    max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None
    role: Optional[str] = Field("analyst", description="analyst | fund_manager | admin")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str
    username: str
    role: str


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    full_name: Optional[str]
    role: str
    is_active: bool
    created_at: str


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = Field(None, min_length=8)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ROLES = {"analyst", "fund_manager", "admin"}


def _issue_tokens(user: User, response: Response) -> TokenResponse:
    """Sets the refresh token as an httpOnly cookie and returns the access
    token in the body — the access token is short-lived and kept in memory
    on the client, never persisted, while the longer-lived refresh token is
    never exposed to JS at all."""
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        create_refresh_token(user.id, user.token_version),
        **_COOKIE_KWARGS,
    )
    return TokenResponse(
        access_token=create_access_token(user.id, user.email, user.role, user.token_version),
        user_id=user.id,
        email=user.email,
        username=user.username,
        role=user.role,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db_session)):
    """Register a new user account and return tokens."""
    enforce_rate_limit(request, payload.email)

    if payload.role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Choose from: {_VALID_ROLES}")

    if (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    if (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(
        email=payload.email,
        username=payload.username,
        full_name=payload.full_name,
        role=payload.role,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return _issue_tokens(user, response)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db_session)):
    """Authenticate with email + password and return tokens."""
    enforce_rate_limit(request, payload.email)

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive")

    return _issue_tokens(user, response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: Request, response: Response, db: AsyncSession = Depends(get_db_session)):
    """Exchange the httpOnly refresh cookie for a new access + refresh token pair."""
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail="No refresh token")

    data = decode_token(raw)
    if not data or data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    result = await db.execute(select(User).where(User.id == int(data["sub"]), User.is_active == True))
    user = result.scalar_one_or_none()
    if not user or data.get("ver") != user.token_version:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    return _issue_tokens(user, response)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat(),
    )


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Invalidate the current session for real: bumps token_version so every
    access/refresh token issued before this point is rejected on its next
    use, even though it hasn't naturally expired yet."""
    current_user.token_version += 1
    await db.commit()
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/auth")
    return {"message": "Logged out successfully"}


@router.put("/me", response_model=UserResponse)
async def update_me(
    payload: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Update profile — full_name or password."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name

    if payload.new_password:
        if not payload.current_password:
            raise HTTPException(status_code=400, detail="current_password is required to set a new password")
        if not verify_password(payload.current_password, current_user.hashed_password):
            raise HTTPException(status_code=401, detail="current_password is incorrect")
        current_user.hashed_password = hash_password(payload.new_password)
        # Changing the password invalidates every other session/device.
        current_user.token_version += 1

    await db.commit()
    await db.refresh(current_user)

    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        full_name=current_user.full_name,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat(),
    )
