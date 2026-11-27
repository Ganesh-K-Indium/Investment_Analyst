# Analysis Dashboard & History — Revamp Plan

**Status:** implemented (27 Nov 2026) · **Scope:** the per-portfolio *Overview* tab and the cross-portfolio *Analysis Dashboard* (`/dashboard`), backend and frontend.

> **As built.** All five phases shipped together. Where the build differs from this proposal:
> - **Stance and pillars** come from the ALPHA run itself (each pillar's score −2..+2 and verdict; stance from the mean in fixed bands, `rag/alpha/framework.py`), not from parsing report text. Parsing remains only for runs made before that.
> - **Spend** covers every model and embedding call for any provider (requests, ingestion, document processing, tool servers), not only background runs; see `docs/ARCHITECTURE.md` §4.
> - **Not built:** a sidebar badge for the review inbox (the header bell shows finished runs), TanStack Query (a small fetch hook per view instead), and a weekly digest.
> - **Current reference:** `docs/ARCHITECTURE.md` (§5 runs, schedules and dashboards; §6 ALPHA; §12 data model; §13 API). The phase estimates below are historical.

---

## 1. The idea in one paragraph

Today both screens are **job monitors**: they tell you a run is *Queued / Running / Ready / Failed* and give you a "View →" link. But every run produces analysis: a stance, five ALPHA pillar recommendations, tagged positive and negative facts, citations, a cost. The screens throw all of that away. The revamp turns them into **research cockpits**:

- **Per portfolio:** what do we currently think of each holding, what changed, what is stale, and what needs attention?
- **Across portfolios:** an inbox of finished work to review, alerts on downgrades, and a picture of conviction and spend across the whole book.

History stops being "the last 12 rows". It becomes a searchable, comparable record of how each thesis changed over time.

---

## 2. Where we are today (and why it feels outdated)

| Area | Current behaviour | Where |
|---|---|---|
| Per-portfolio Overview | 3 counters (Running / Ready / Failed) and the last **12** runs, each labelled only by type ("Secondary Research — Ask"). No question text, tickers, duration or result. | `frontend/src/components/project/analysis-dashboard/AnalysisDashboard.tsx` |
| Cross-portfolio dashboard | Portfolio list, status/agent filters, runs grouped by date. Rows show "*Portfolio* — Secondary Research". Filtering runs in the browser over at most 100 runs. | `frontend/src/pages/CrossPortfolioDashboard.tsx` |
| History depth | `list_for_portfolio` is hard-capped at **50**, with no paging. Older runs cannot be reached from the dashboard. | `backend/app/services/analysis_tasks.py:88` |
| Payload size | `_serialize()` sends the **whole** `result_metadata` (full answers, documents, progress snapshot) in every list call **and** every SSE event. Lists and live updates get heavier as history grows. | `analysis_tasks.py:36` |
| Run results | ALPHA reports already contain per-pillar **BUY/SELL/HOLD**, an overall **Bullish…Bearish** stance and `[[+|…]]` / `[[-|…]]` / `[[~|…]]` sentiment tags. All of it is buried in markdown and never surfaced. | `rag/prompts/prompts.py:1124` |
| Failures | Only a raw error string, with no retry button. The failed step is stored (`result_metadata.progress`) but not shown on the dashboard. | `rag_jobs.py:44` |
| Cost | `UsageEvent` rows exist but are linked only by a free-text `run_label`, not by task. Cost per run or per portfolio can't be shown. | `models.py:370` |
| Recurring work | `scheduled_at` supports one deferred run only. There is no recurring or filing-triggered re-analysis. | `api/analysis_tasks.py` |
| Compare runs | Not linked to a portfolio, so they never appear on the cross-portfolio dashboard. | `analysis_tasks.py:100` |

---

## 3. Feature pillars

Each pillar says what the user gets and what it needs underneath. Priority: **P0** = core of the vision, **P1** = makes it great, **P2** = differentiator.

### Pillar A — Run records that carry meaning (P0, foundation)
Every run row shows *what was asked, about which tickers, and what came out*.

- **Title:** the user's question (ask/quant), "AAPL vs MSFT" (compare), or "ALPHA · 8 tickers" (alpha).
- **Tickers** as chips, plus **duration**, **cost**, **sources used** (filings / web / notes), and an **outcome badge** (e.g. *Cautiously Bullish* or *3 BUY · 1 HOLD*).
- **One-line summary:** the first sentence of the answer, or the ALPHA summary's stance sentence.
- **Failed runs:** show *which step* failed (from the stored progress snapshot), a plain-language reason, and **Retry**.

### Pillar B — Portfolio Pulse (the new per-portfolio Overview) (P0)
The first screen of a portfolio answers "what do we think, and what needs me?"

1. **KPI strip:**
   - **Coverage:** tickers analysed in the last 30 days, out of the total.
   - **Last full ALPHA:** when it ran.
   - **Stance mix:** e.g. 4 Bullish · 2 Neutral · 1 Bearish.
   - **Spend this month.**
   - **Running now.**
2. **Signal matrix:**
   - **Rows** are tickers. **Columns** are the five ALPHA pillars (A · L · P · H · A) plus the overall stance.
   - **Each cell** is coloured BUY / HOLD / SELL and carries an arrow (▲▼) when it differs from the previous run.
   - **Click a cell** to open that pillar's text from the run that produced it.
   - **The ticker column** carries a "last analysed" age (*12d*) and turns amber once the analysis is stale.
3. **Needs attention:** a ranked list built from:
   - **Stance downgrades** since the last run.
   - **New filings ingested after the last analysis.** We already store `Document.filing_date` / `ingested_at`, so we can show e.g. "MSFT 10-Q filed 3 days after your last ALPHA".
   - **Tickers never analysed.**
   - **Tickers with no ingested filings:** coverage gaps.
   - **Failed runs.**
   - **Insider activity spikes** from the Form 4 table since the last run.

   Each item has a one-click action: **Re-run ALPHA for MSFT**, **Ingest filings**, **Retry**.
4. **Live runs:** in-flight runs with the existing `RunProgressPanel` inline (per-ticker lanes for ALPHA), queue position and an ETA from historical durations.
5. **History** (see Pillar C), embedded below.

### Pillar C — Real analysis history (P0)
- **Server-side paging:** cursor-based, so the full history can be reached, not 50 rows.
- **Filters**, kept in the URL so views can be shared and bookmarked:
  - **Run details:** status, agent, run type, date range.
  - **Ticker:** "everything we ever ran on NVDA".
  - **Outcome:** stance.
  - **Flags:** pinned, unreviewed.
- **Full-text search** over run titles and summaries (Postgres `tsvector`).
- **Run detail slide-over**, opened from any row without leaving the page:
  - summary, pillar breakdown, the step timeline (from the progress snapshot);
  - sources and citations count, cost and tokens by model role;
  - the analyst notes that were in scope.
  - **Actions:** *Open conversation*, *Re-run*, *Pin*, *Tag*, *Add to report* (writes a `ReportDraftItem`), *Delete* (with a named confirmation).
- **Day grouping** (Today / Yesterday / This week / Earlier) with sticky headers. Full keyboard navigation (j/k to move, Enter to open, `x` to select).

### Pillar D — Thesis timeline & run comparison (P1)
- **Stance timeline per ticker:** stance (Bearish = −2 … Bullish = +2) plotted over every ALPHA run, with each pillar as a thin band underneath, and filing dates and insider trades as markers on the time axis. This answers "when and why did our view on AAPL change?"
- **Compare two runs:** pick any two ALPHA runs of the same ticker and see, side by side:
  - each pillar's recommendation and text, with what changed highlighted;
  - new negative facts (from the `[[-|…]]` tags) that weren't there before;
  - the stance change, with its rationale.
- **"What changed since last time"** card shown automatically when a new ALPHA run completes.

### Pillar E — Cross-portfolio command centre (P1)
The `/dashboard` page becomes the analyst's home for review and oversight:

- **Review inbox:** finished runs the user hasn't opened yet (`reviewed_at` is null), with a count badge in the sidebar. Opening a run marks it reviewed, and there is a bulk *Mark all reviewed*.
- **Book-level conviction:** stance distribution across all portfolios, with tickers held in more than one portfolio deduplicated, and the top 5 downgrades and upgrades this week.
- **Alerts feed:** downgrades, new filings since the last analysis, and failed scheduled runs, across every portfolio.
- **Portfolio cards** in place of the plain list: stance-mix bar, coverage %, last-run age, unreviewed count and month spend.
- **Server-side aggregates:** one SQL query per widget, so the page stays fast with thousands of runs.
- **Compare runs included:** compare runs without a portfolio are shown under an "Ad-hoc" bucket instead of being hidden.

### Pillar F — Keep analysis fresh automatically (P2)
- **Schedules per portfolio:** "Re-run ALPHA every Monday 06:00" or "Refresh quant snapshot daily". These run as Arq cron jobs on the existing **batch** queue, so they never slow down interactive chat.
- **Event triggers:** when SEC ingestion lands a new 10-K/10-Q for a held ticker, queue a single-ticker ALPHA (optional, per portfolio, rate-limited).
- **Budget guard:** schedules respect the monthly usage budget that `usage_ledger` already tracks. A run that would exceed it is skipped, and an alert says why.
- **Digest:** an in-app weekly summary of stance changes ("2 downgrades, 1 upgrade, 3 new filings"). An email version can come later.

### Pillar G — Cost and run health (P2)
- **Cost per run and per portfolio**, with a monthly trend and the split by model role (e.g. the combiner LLM vs the retrieval LLM).
- **Reliability panel** (admin/fund_manager roles): failure rate by run type, failing steps, and p50/p90 duration.
- **Cancel** a queued or running run (Arq job abort, then a `CANCELLED` status).

---

## 4. Backend plan

### 4.1 Data model (one Alembic migration per phase)

**`analysis_tasks`: new columns.** Everything stays nullable, so old rows keep working.

| Column | Type | Purpose |
|---|---|---|
| `title` | `String(300)` | Display title (question / "ALPHA · 8 tickers") |
| `tickers` | `JSONB` (list) + GIN index | Ticker filter & "all runs on NVDA" |
| `summary` | `Text` | One-line outcome |
| `outcome` | `JSONB` | Small structured result, e.g. `{"stance":"Cautiously Bullish","counts":{"BUY":3,"HOLD":1}}` |
| `started_at`, `finished_at` | `DateTime` | Duration & ETA stats |
| `cost_usd`, `total_tokens` | `Float`, `Integer` | Per-run cost |
| `error_kind`, `failed_step` | `String` | Classified failure (timeout / provider / no_documents / …) and step id |
| `reviewed_at` | `DateTime` | Inbox |
| `pinned` | `Boolean` | Pin |
| `tags` | `JSONB` (list) | User tags |
| `parent_task_id` | `String` FK | Re-run/retry lineage |
| `schedule_id` | `Integer` FK | Which schedule created it |
| `search_vector` | `tsvector` (generated) + GIN | Full-text search over title + summary |

Also add `CANCELLED` to `TaskStatus`, and an index on `(user_id, portfolio_id, created_at DESC)`.

**New `ticker_signals` table:** one row per ticker per ALPHA run. This is what the matrix, the timeline and the alerts read.

```
id, task_id → analysis_tasks, user_id, portfolio_id, ticker,
stance            -- Bullish | Cautiously Bullish | Neutral | Cautiously Bearish | Bearish
stance_score      -- +2 … −2
pillars           -- JSONB {"alignment":"BUY","liquidity":"HOLD","performance":"SELL","horizon":"BUY","action":"HOLD"}
fact_counts       -- JSONB {"pos":14,"neg":6,"neutral":9}
key_risk          -- text (the summary's named main concern)
created_at
INDEX (user_id, ticker, created_at DESC), INDEX (portfolio_id, ticker, created_at DESC)
```

**New `analysis_schedules` table (Pillar F):** `id, user_id, portfolio_id, task_type, tickers (null = all), cron, trigger ('cron' | 'new_filing'), enabled, next_run_at, last_task_id, created_at`.

**`usage_events.task_id`:** a new nullable column, set from the run context. Cost per run and per portfolio then becomes a simple `SUM`.

### 4.2 Capturing structured results (no extra LLM calls)
- **ALPHA, at the source:** take each pillar's *Recommendation* label from the graph state as the pillar nodes produce it (`rag/graph/nodes.py`), and the final stance from the combiner output. Return them next to the `report` in `alpha_core`'s `results[]`.
- **ALPHA, parser fallback:** a deterministic parser (`app/services/run_insights.py`) that reads the markdown and extracts:
  - the five `Recommendation — BUY/SELL/HOLD` labels;
  - the final bold stance;
  - counts of the `[[+|`, `[[-|` and `[[~|` tags.

  It is used for older reports and as a check on the source values. An unparsable report is stored as `outcome = null`, never guessed.
- **ask/compare/quant:**
  - **title:** the question, trimmed;
  - **tickers:** from the `company_filter` / payload;
  - **summary:** the first sentence of the answer;
  - **sources:** document / web / notes counts from the response.
- **Where it runs:** the job wrappers (`rag_jobs.py`, `quant_jobs.py`) call `RunInsights.extract(task_type, result)` just before the `COMPLETED` update. They write the columns and the `ticker_signals` rows in the same transaction.
- **Backfill:** a `scripts/dev.py backfill-insights` command re-parses existing `result_metadata.response` for old tasks. It is idempotent and safe to re-run.

### 4.3 Slim payloads
- **`to_summary()` (new):** lists and **SSE events** send only the slim row (id, status, title, tickers, summary, outcome, timings, cost, flags). No full answer and no documents.
- **Progress during a run:** SSE carries `progress` only while the run is RUNNING.
- **Full payload:** `GET /analysis-tasks/{id}` keeps returning everything for the detail view and the chat hand-off. `extractSessionId` keeps working because `session_ref` and `thread_id` stay in the slim row.

### 4.4 API

| Method & path | Purpose |
|---|---|
| `GET /analysis-tasks` | Cursor-paged history. Query params: `portfolio_id?`, `status[]`, `agent`, `task_type`, `ticker`, `stance`, `q`, `from`, `to`, `pinned`, `unreviewed`, `cursor`, `limit`. Without `portfolio_id` it is user-wide, including ad-hoc compare runs. |
| `GET /analysis-tasks/{id}` | Full detail (unchanged) plus a cost breakdown by role and the parsed pillars. |
| `PATCH /analysis-tasks/{id}` | `pinned`, `tags`, `reviewed` |
| `POST /analysis-tasks/reviewed` | Bulk mark reviewed |
| `POST /analysis-tasks/{id}/retry` | Re-enqueue the same payload, linking `parent_task_id` |
| `POST /analysis-tasks/{id}/cancel` | Arq abort, then `CANCELLED` |
| `GET /analysis-tasks/compare?a=&b=&ticker=` | Run-vs-run diff by pillar and stance, plus new negative facts |
| `GET /portfolios/{id}/pulse` | KPI strip, signal matrix (latest plus previous signal per ticker), attention items, spend. **One call, all SQL aggregates.** |
| `GET /signals/timeline?ticker=&portfolio_id=` | Stance and pillar history, plus filing and Form 4 markers |
| `GET /dashboard/cross-portfolio` (v2) | Portfolio cards with aggregates, inbox count, conviction distribution, top movers, alerts |
| `GET/POST/PATCH/DELETE /portfolios/{id}/schedules` | Pillar F |

Every endpoint keeps the existing ownership check pattern (`portfolio.user_id == current_user.id`). The new tables carry `user_id`, so no query depends on a join alone for access control.

### 4.5 "Needs attention" rules (`app/services/portfolio_pulse.py`)
Pure functions over SQL results, each returning `{kind, ticker, severity, message, action}`:

- `stale_analysis`: no ALPHA in N days. N is a setting, default 30, in the existing config system.
- `filing_after_analysis`: `Document.filing_date > last ticker_signal.created_at`.
- `stance_downgrade`: `stance_score` fell compared with the previous signal.
- `never_analysed`: a ticker with no signal.
- `no_filings`: a ticker with no `Document` rows (a coverage gap).
- `insider_spike`: Form 4 net dollar flow since the last run is above a threshold.
- `failed_run`: a failure in the last 7 days with no successful re-run.

Each rule is unit-tested on its own.

### 4.6 Scheduler (Pillar F)
- **Cron job:** an Arq cron job (`app/worker.py`) wakes every minute and claims due schedules with `SELECT … FOR UPDATE SKIP LOCKED`, so it is safe to run more than one worker.
- **Enqueueing:** each due schedule goes onto the batch queue through the same `_enqueue` helper, with `schedule_id` set.
- **New-filing trigger:** SEC ingestion publishes a `filing_ingested` Redis event. A small consumer matches it against portfolios with that ticker and a `new_filing` schedule, and runs at most one per ticker per 24 h.
- **Budget check:** before enqueueing, read the usage summary from `usage_ledger`.

---

## 5. Frontend plan

### 5.1 Information architecture
```
Home › Portfolio › Overview  (Portfolio Pulse)
  ├─ KPI strip
  ├─ Signal matrix ───────────── click cell → Run detail (pillar anchored)
  ├─ Needs attention  |  Live runs
  └─ History (filters · search · paging) ─ click row → Run detail slide-over
                                          └─ "Compare with…" → Compare view
/dashboard  (Command centre)
  ├─ Review inbox (badge in Sidebar)
  ├─ Conviction distribution · Top movers · Alerts
  └─ Portfolio cards → Portfolio Pulse
/dashboard/ticker/:ticker   (Thesis timeline across portfolios)
```

### 5.2 Components (new folder `frontend/src/features/analysis/`)
| Component | Notes |
|---|---|
| `PortfolioPulse.tsx` | Replaces `AnalysisDashboard.tsx` in Home's Overview tab |
| `SignalMatrix.tsx` | Grid of tickers × pillars. Each cell is a button with an `aria-label` such as "AAPL Performance: SELL, down from HOLD". Colour plus an icon or arrow, never colour alone. |
| `AttentionList.tsx` | Ranked items with one action button each |
| `RunHistory.tsx` | Paged, filterable table with filters in the URL. Infinite scroll plus a "Load more" button for keyboard users. |
| `RunDetailDrawer.tsx` | Slide-over with focus trap and Esc to close. Reuses `RunProgressPanel` for the step timeline. |
| `RunCompare.tsx` | Two-column pillar diff |
| `StanceTimeline.tsx` | Small hand-written SVG chart (no new dependency) |
| `StanceBadge`, `TickerChips`, `CostTag` | Small shared UI pieces next to `StatTile` |

### 5.3 State
- **`analysisRunsStore`:** keeps owning *live* status, fed by SSE. History pages and pulse data come from the new endpoints through a small fetch-and-cache hook (`useQueryish`, or adopt TanStack Query once for the whole app; recommended).
- **Selectors:** must return stable references (see the earlier Settings break). Derive arrays with `useMemo` over the raw map, as `AnalysisDashboard` already does.
- **SSE events:** now slim, so an event patches the matching history row in place. When a run completes, the pulse refetches once (debounced) to pick up new signals.

### 5.4 UX rules (taken from the installed UX skills)
- **Loading:** skeletons for the matrix and history (not spinners).
- **Empty states with a clear action:** "No ALPHA run yet — Run ALPHA on 8 tickers".
- **Destructive actions:** confirmed by name.
- **Buttons:** verb-first labels ("Re-run ALPHA", "Mark reviewed").
- **Small screens:** usable at 375 px. The matrix scrolls sideways with a sticky ticker column, and the drawer goes full-screen.
- **Motion:** honour reduced motion.
- **Investment-advice disclaimer:** stays wherever a stance is shown.

---

## 6. Delivery phases

Each phase ships on its own and leaves the product better than before.

| Phase | Contents | Size | Outcome |
|---|---|---|---|
| **1. Foundations** | Migration (new columns, `ticker_signals`, `usage_events.task_id`). `RunInsights` extractor plus a backfill command. Slim `to_summary` for lists and SSE. Cursor-paged `GET /analysis-tasks`. | ~4–5 days | Rows show titles, tickers, stance, cost. History has no 50 cap. Lighter live traffic. |
| **2. Portfolio Pulse** | `/portfolios/{id}/pulse`, attention rules, SignalMatrix, AttentionList, RunHistory, RunDetailDrawer. Retry and cancel. Pin, tags, reviewed. | ~6–8 days | **The visible "wow" release.** The Overview tab is replaced. |
| **3. Thesis & compare** | Timeline endpoint and chart, run compare, "What changed" card, ticker page | ~4–5 days | History becomes insight |
| **4. Command centre** | Dashboard v2 aggregates, review inbox and sidebar badge, alerts, portfolio cards, ad-hoc compare bucket | ~4–5 days | Cross-portfolio oversight |
| **5. Automation & cost** | Schedules (cron and new-filing trigger), budget guard, weekly digest, cost and reliability panels | ~5–7 days | The analysis stays current without anyone starting runs |

**Recommended order:** 1 → 2 first; that is where most of the value is. 3 and 4 can run in parallel after that, and 5 comes last because it spends LLM budget automatically.

---

## 7. Testing (targeted, per phase)
- **`RunInsights`:** parser tests on stored real ALPHA and ask outputs, including a malformed report (expect `outcome = null`, never a guess).
- **Attention rules:** each rule tested in isolation on small fixtures.
- **API:** tests for paging, filters, ownership (another user's run returns 404) and the retry lineage.
- **Migration:** run upgrade/downgrade once on a copy of the dev DB, plus the backfill dry run.
- **Frontend:** `tsc`, `eslint`, `vite build`, and a jsdom smoke render for each new component, including a check that store selectors return stable references. The UI itself is checked by hand.

## 8. Risks and decisions to make
| Risk / decision | Recommendation |
|---|---|
| Parsing a stance out of LLM markdown is brittle | Capture values at the source (pillar nodes); keep the parser as fallback only; show "—" rather than a guessed stance |
| The stance is presented like advice | Keep the existing disclaimer on every stance surface. Label it "Model stance". |
| Schedules spend LLM budget unattended | Off by default; budget guard; at most 1 per ticker per 24 h for the filing trigger |
| SSE fan-out with many open tabs | Slim payloads (Phase 1) solve most of it. Revisit a shared user-channel connection if needed. |
| Chart library or hand-made SVG | Hand-made SVG for the timeline and matrix (two charts, no new dependency) |
| TanStack Query adoption | Worth it in Phase 2. It removes a lot of hand-written loading, error and caching code across the new views. |

## 9. How we'll know it worked
- **Faster to the answer:** under 5 seconds from opening a portfolio to seeing the current stance for every holding. Today it means opening each chat.
- **Fewer stale theses:** the share of held tickers with an ALPHA run younger than 30 days goes up.
- **Review inbox gets cleared:** median time from a run finishing to being reviewed.
- **Fewer abandoned failures:** the share of failed runs that get a successful retry within a day.
- **Faster screens:** dashboard p95 load time under 400 ms with 5k runs, and a smaller average SSE payload than today.
