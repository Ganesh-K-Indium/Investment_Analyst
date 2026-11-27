"""Sign-in sessions: refresh rotation, reuse detection, logout per browser, sign out everywhere, roles at sign-up."""
import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.auth import rate_limit
from app.database.connection import get_db_session
from app.database.models import Base, RefreshToken, User

COOKIE = auth_api.REFRESH_COOKIE_NAME


@pytest.fixture()
def env():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(setup())

    async def db():
        async with Session() as s:
            yield s

    app = FastAPI()
    app.include_router(auth_api.router)
    app.dependency_overrides[get_db_session] = db
    rate_limit._attempts.clear()

    def query(stmt):
        async def go():
            async with Session() as s:
                res = await s.execute(stmt)
                if stmt.is_select:
                    return res.scalars().all()
                await s.commit()
        return asyncio.run(go())

    def client():
        return TestClient(app, base_url="https://testserver")      # the refresh cookie is Secure

    yield client, query
    asyncio.run(engine.dispose())


def _signup(c, email, role="analyst"):
    r = c.post("/auth/signup", json={"email": email, "username": "user_" + email.split("@")[0], "password": "password123", "role": role})
    assert r.status_code == 201, r.text
    return r.json()


def test_admin_cannot_be_chosen_at_sign_up(env):
    client, _ = env
    assert _signup(client(), "first@x.com")["role"] == "analyst"          # not even the first account on a new install
    assert _signup(client(), "second@x.com", "fund_manager")["role"] == "fund_manager"
    r = client().post("/auth/signup", json={"email": "evil@x.com", "username": "evil", "password": "password123", "role": "admin"})
    assert r.status_code == 400


def test_refresh_rotates_and_reuse_revokes_the_sign_in(env):
    client, query = env
    c = client()
    _signup(c, "a@x.com")
    old = c.cookies.get(COOKIE)

    r = c.post("/auth/refresh")
    assert r.status_code == 200 and r.json()["access_token"]
    new = c.cookies.get(COOKIE)
    assert new and new != old

    def replay():
        t = client()                                                  # a separate jar: send the old cookie by hand
        return t.post("/auth/refresh", headers={"Cookie": f"{COOKIE}={old}"})

    # The old cookie presented again inside the grace window (two tabs racing): still works, same sign-in.
    assert replay().status_code == 200

    # Outside the window it is reuse: the whole sign-in is revoked, including the legitimate browser's newer token.
    first = next(r for r in query(select(RefreshToken)) if r.used_at is not None and r.created_at == min(x.created_at for x in query(select(RefreshToken))))
    query(update(RefreshToken).where(RefreshToken.jti == first.jti).values(used_at=datetime.utcnow() - timedelta(minutes=5)))

    r = replay()
    assert r.status_code == 401 and "security" in r.json()["detail"]
    assert c.post("/auth/refresh").status_code == 401
    assert all(r.revoked_at for r in query(select(RefreshToken)))


def test_logout_needs_no_access_token_and_only_ends_this_browser(env):
    client, query = env
    laptop, phone = client(), client()
    _signup(laptop, "b@x.com")
    assert phone.post("/auth/login", json={"email": "b@x.com", "password": "password123"}).status_code == 200

    r = laptop.post("/auth/logout")                                    # no bearer header at all
    assert r.status_code == 200 and not laptop.cookies.get(COOKIE)
    laptop.cookies.clear()
    assert laptop.post("/auth/refresh").status_code == 204          # signed out: "no session", not an error
    assert phone.post("/auth/refresh").status_code == 200             # the other device is still signed in


def test_logout_everywhere_and_password_change(env):
    client, query = env
    laptop, phone = client(), client()
    _signup(laptop, "c@x.com")
    tok = phone.post("/auth/login", json={"email": "c@x.com", "password": "password123"}).json()["access_token"]

    r = laptop.put("/auth/me", json={"current_password": "password123", "new_password": "newpassword1"},
                   headers={"Authorization": f"Bearer {laptop.post('/auth/refresh').json()['access_token']}"})
    assert r.status_code == 200
    assert phone.post("/auth/refresh").status_code == 401             # other devices are signed out
    assert phone.get("/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    fresh = laptop.post("/auth/refresh")                              # this browser carries on with its new cookie
    assert fresh.status_code == 200

    assert laptop.post("/auth/logout-all", headers={"Authorization": f"Bearer {fresh.json()['access_token']}"}).status_code == 200
    laptop.cookies.clear()
    assert all(r.revoked_at for r in query(select(RefreshToken)))
    assert query(select(User))[0].token_version == 2


def test_login_errors_do_not_reveal_which_part_was_wrong(env):
    client, _ = env
    c = client()
    _signup(c, "d@x.com")
    a = client().post("/auth/login", json={"email": "d@x.com", "password": "wrongpass1"})
    b = client().post("/auth/login", json={"email": "nobody@x.com", "password": "wrongpass1"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_tokens_from_before_rotation_are_refused(env):
    client, _ = env
    c = client()
    u = _signup(c, "e@x.com")
    from app.auth.jwt import _create_token
    legacy = _create_token({"sub": str(u["user_id"]), "type": "refresh", "ver": 0}, timedelta(days=1))   # no jti
    r = client().post("/auth/refresh", headers={"Cookie": f"{COOKIE}={legacy}"})
    assert r.status_code == 401 and COOKIE in r.headers.get("set-cookie", "")
