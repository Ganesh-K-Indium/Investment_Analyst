from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.connection import get_db_session
from app.database.models import User
from app.auth.jwt import decode_token

_bearer = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    token = credentials.credentials
    payload = decode_token(token)

    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user_id = int(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


def require_role(*roles: str):
    """Dependency factory that enforces one of the given roles."""
    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not permitted for this action",
            )
        return current_user
    return _check


def verify_user_id_matches(user_id, current_user: User) -> None:
    """
    Raise 403 if a client-supplied user_id (path/query/body field) doesn't
    match the authenticated token's user. Every route that accepts a user_id
    must call this right after resolving `current_user` via get_current_user —
    it's what actually closes the "anyone can pass any user_id" hole; requiring
    a valid token alone isn't sufficient if the token's owner and the acted-on
    user_id are never cross-checked.

    Kept as a plain string comparison (not an int cast) since user_id is
    stored as a loose string column everywhere outside `users.id` itself.
    """
    if str(current_user.id) != str(user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user_id does not match the authenticated user",
        )


def verify_owner(owner_user_id, current_user: User) -> None:
    """
    Raise 403 if a fetched resource's stored owner (e.g. portfolio.user_id,
    chat_session.user_id) doesn't belong to the authenticated user. Use this
    for resource-id-only routes (GET/PUT/DELETE /thing/{id}) that don't carry
    an explicit user_id in the request — check ownership AFTER fetching the
    resource, treating a mismatch as 404 (not 403) so existence of another
    user's resource isn't leaked.
    """
    if str(current_user.id) != str(owner_user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
