"""
User authentication endpoints — signup, login, profile management
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, status, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db_session
from app.database.models import RefreshToken, User
from app.auth.password import hash_password, verify_password
from app.auth.jwt import create_access_token, create_refresh_token, decode_token, new_jti, REFRESH_TOKEN_EXPIRE_DAYS
from app.auth.deps import get_current_user
from app.auth.rate_limit import enforce_rate_limit

logger = logging.getLogger("api")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Two tabs of one browser can refresh at the same moment with the same cookie. A used token presented again within this
# window is that race, not theft: it gets a fresh token in the same family instead of revoking the sign-in.
REUSE_GRACE_SECONDS = 30

# A bcrypt hash of a random string: checked when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = hash_password("not-a-real-password-0f9a8b7c")

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
    role: Optional[str] = Field("analyst", description="analyst | fund_manager. Admins are made by whoever runs the server: "
                                                       "`python scripts/dev.py make-admin EMAIL`.")


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

_SIGNUP_ROLES = {"analyst", "fund_manager"}


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/auth", secure=True, httponly=True, samesite="none")


def _unauthorized(detail: str) -> JSONResponse:
    """A 401 that also drops the refresh cookie, so a dead token is not sent again on every page load."""
    r = JSONResponse(status_code=401, content={"detail": detail})
    _clear_cookie(r)
    return r


async def _issue_tokens(db: AsyncSession, user: User, request: Request, response: Response, family: Optional[str] = None) -> TokenResponse:
    """Stores a new refresh token (a new family for a sign-in, the same family for a refresh), sets it as an httpOnly
    cookie, and returns the access token in the body. The access token is kept only in the page's memory; the refresh
    token is never readable by JavaScript."""
    now = datetime.utcnow()
    jti, family = new_jti(), family or new_jti()
    db.add(RefreshToken(jti=jti, user_id=user.id, family=family, created_at=now, expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
                        user_agent=(request.headers.get("user-agent") or "")[:255] or None))
    # Housekeeping: this user's tokens that can no longer be used.
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.expires_at < now))
    await db.commit()
    response.set_cookie(REFRESH_COOKIE_NAME, create_refresh_token(user.id, user.token_version, jti, family), **_COOKIE_KWARGS)
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

    if payload.role not in _SIGNUP_ROLES:
        raise HTTPException(status_code=400, detail="Choose Analyst or Fund Manager.")

    if (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    if (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    # Nobody can make themselves admin: admins are made by whoever runs the server (`dev.py make-admin EMAIL`).
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

    return await _issue_tokens(db, user, request, response)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db_session)):
    """Authenticate with email + password and return tokens."""
    enforce_rate_limit(request, payload.email)

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if not verify_password(payload.password, user.hashed_password if user else _DUMMY_HASH) or not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is turned off. Ask your administrator to turn it back on.")

    return await _issue_tokens(db, user, request, response)


@router.post("/refresh", response_model=TokenResponse, responses={204: {"description": "No sign-in in this browser"}})
async def refresh_token(request: Request, response: Response, db: AsyncSession = Depends(get_db_session)):
    """Exchange the httpOnly refresh cookie for a new access token and a new refresh token (rotation). Each refresh token
    works once; one presented again after its grace window revokes that whole sign-in, since it must have been copied."""
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        # Not signed in in this browser: a normal answer for every page load of a signed-out tab, not an error.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    data = decode_token(raw)
    if not data or data.get("type") != "refresh" or not data.get("jti"):
        return _unauthorized("Your session has ended. Please sign in again.")

    user = (await db.execute(select(User).where(User.id == int(data["sub"]), User.is_active == True))).scalar_one_or_none()  # noqa: E712
    row = await db.get(RefreshToken, data["jti"])
    now = datetime.utcnow()
    if not user or data.get("ver") != user.token_version or not row or row.user_id != user.id or row.revoked_at or row.expires_at < now:
        return _unauthorized("Your session has ended. Please sign in again.")

    if row.used_at:
        if (now - row.used_at).total_seconds() > REUSE_GRACE_SECONDS:
            await db.execute(update(RefreshToken).where(RefreshToken.family == row.family, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
            await db.commit()
            logger.warning("Refresh token reuse for user %s: sign-in %s revoked", user.id, row.family[:8])
            return _unauthorized("For your security this session was ended. Please sign in again.")
    else:
        row.used_at = now

    return await _issue_tokens(db, user, request, response, family=row.family)


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
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db_session)):
    """Sign out this browser: revokes its sign-in (the refresh-token family) and drops the cookie. Needs no access
    token, so it still works after the access token has expired. Other devices stay signed in."""
    data = decode_token(request.cookies.get(REFRESH_COOKIE_NAME) or "")
    if data and data.get("type") == "refresh" and data.get("fam"):
        await db.execute(update(RefreshToken).where(RefreshToken.family == data["fam"], RefreshToken.revoked_at.is_(None))
                         .values(revoked_at=datetime.utcnow()))
        await db.commit()
    _clear_cookie(response)
    return {"message": "Logged out successfully"}


async def _revoke_all(db: AsyncSession, user: User) -> None:
    """Every token the user holds stops working: access tokens by version, refresh tokens by revocation."""
    user.token_version += 1
    await db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.utcnow()))


@router.post("/logout-all", status_code=status.HTTP_200_OK)
async def logout_all(response: Response, db: AsyncSession = Depends(get_db_session), current_user: User = Depends(get_current_user)):
    """Sign out everywhere: every device and browser."""
    await _revoke_all(db, current_user)
    await db.commit()
    _clear_cookie(response)
    return {"message": "Signed out on every device"}


@router.put("/me", response_model=UserResponse)
async def update_me(
    payload: UpdateProfileRequest,
    request: Request,
    response: Response,
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
        # A new password signs out every device. This browser gets a fresh refresh cookie, so its next request
        # (whose access token is now stale) refreshes silently instead of signing the user out.
        await _revoke_all(db, current_user)
        await db.commit()
        await db.refresh(current_user)
        await _issue_tokens(db, current_user, request, response)

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
