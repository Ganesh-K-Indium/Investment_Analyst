"""Dashboard reads (portfolio pulse, cross-portfolio overview, a ticker's thesis timeline) and the opt-in re-run schedules."""
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.config import get_settings
from app.database.connection import get_db_session
from app.database.models import AnalysisSchedule, User
from app.services import analysis_runs, portfolio_pulse
from app.services.portfolio import PortfolioService

router = APIRouter(prefix="/analysis", tags=["Analysis Dashboard"])


async def _own_portfolio(db: AsyncSession, portfolio_id: int, user: User):
    portfolio = await PortfolioService.get_portfolio(db, portfolio_id)
    if not portfolio or str(portfolio.user_id) != str(user.id):
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


@router.get("/config")
async def dashboard_config(current_user: User = Depends(get_current_user)):
    cfg = get_settings()
    return {"schedules_allowed": cfg.analysis_schedules_allowed, "stale_days": cfg.analysis_stale_days,
            "insider_alert_usd": cfg.analysis_insider_alert_usd}


@router.get("/portfolios/{portfolio_id}/pulse")
async def get_pulse(portfolio_id: int, db: AsyncSession = Depends(get_db_session), current_user: User = Depends(get_current_user)):
    portfolio = await _own_portfolio(db, portfolio_id, current_user)
    return await portfolio_pulse.portfolio_pulse(db, str(current_user.id), portfolio)


@router.get("/overview")
async def get_overview(db: AsyncSession = Depends(get_db_session), current_user: User = Depends(get_current_user)):
    return await portfolio_pulse.overview(db, str(current_user.id))


@router.get("/timeline/{ticker}")
async def get_timeline(ticker: str, portfolio_id: Optional[int] = None, db: AsyncSession = Depends(get_db_session),
                       current_user: User = Depends(get_current_user)):
    if portfolio_id is not None:
        await _own_portfolio(db, portfolio_id, current_user)
    return await portfolio_pulse.timeline(db, str(current_user.id), ticker, portfolio_id)


# ── Schedules (every one starts switched off) ──

class ScheduleInput(BaseModel):
    trigger: Literal["daily", "weekly", "monthly", "new_filing"] = "weekly"
    hour_utc: int = Field(6, ge=0, le=23)
    weekday: Optional[int] = Field(0, ge=0, le=6)
    tickers: Optional[List[str]] = None
    enabled: bool = False


class SchedulePatch(BaseModel):
    trigger: Optional[Literal["daily", "weekly", "monthly", "new_filing"]] = None
    hour_utc: Optional[int] = Field(None, ge=0, le=23)
    weekday: Optional[int] = Field(None, ge=0, le=6)
    tickers: Optional[List[str]] = None
    enabled: Optional[bool] = None


def _refresh_next(s: AnalysisSchedule) -> None:
    s.next_run_at = analysis_runs.next_run(s.trigger, s.hour_utc, s.weekday, datetime.utcnow()) if s.enabled else None


def _check_allowed(enabling: bool) -> None:
    if enabling and not get_settings().analysis_schedules_allowed:
        raise HTTPException(status_code=403, detail="Scheduled re-runs are turned off for this deployment")


@router.get("/portfolios/{portfolio_id}/schedules")
async def list_schedules(portfolio_id: int, db: AsyncSession = Depends(get_db_session), current_user: User = Depends(get_current_user)):
    await _own_portfolio(db, portfolio_id, current_user)
    return [analysis_runs.schedule_dict(s) for s in await analysis_runs.schedules_for(db, portfolio_id)]


@router.post("/portfolios/{portfolio_id}/schedules")
async def create_schedule(portfolio_id: int, payload: ScheduleInput, db: AsyncSession = Depends(get_db_session),
                          current_user: User = Depends(get_current_user)):
    await _own_portfolio(db, portfolio_id, current_user)
    _check_allowed(payload.enabled)
    s = AnalysisSchedule(user_id=str(current_user.id), portfolio_id=portfolio_id, task_type="alpha", trigger=payload.trigger,
                         hour_utc=payload.hour_utc, weekday=payload.weekday,
                         tickers=[t.upper() for t in payload.tickers] if payload.tickers else None, enabled=payload.enabled,
                         created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    _refresh_next(s)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return analysis_runs.schedule_dict(s)


async def _own_schedule(db: AsyncSession, schedule_id: int, user: User) -> AnalysisSchedule:
    s = await db.get(AnalysisSchedule, schedule_id)
    if s is None or str(s.user_id) != str(user.id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return s


@router.patch("/schedules/{schedule_id}")
async def update_schedule(schedule_id: int, payload: SchedulePatch, db: AsyncSession = Depends(get_db_session),
                          current_user: User = Depends(get_current_user)):
    s = await _own_schedule(db, schedule_id, current_user)
    _check_allowed(bool(payload.enabled))
    data = payload.model_dump(exclude_unset=True)
    if "tickers" in data:
        data["tickers"] = [t.upper() for t in data["tickers"]] if data["tickers"] else None
    for key, value in data.items():
        setattr(s, key, value)
    if payload.enabled:
        s.last_skip_reason = None
        if s.trigger == "new_filing" and s.last_run_at is None:
            s.updated_at = datetime.utcnow()     # only filings that arrive from now on start a run
    s.updated_at = datetime.utcnow()
    _refresh_next(s)
    await db.commit()
    await db.refresh(s)
    return analysis_runs.schedule_dict(s)


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db_session), current_user: User = Depends(get_current_user)):
    s = await _own_schedule(db, schedule_id, current_user)
    await db.delete(s)
    await db.commit()
