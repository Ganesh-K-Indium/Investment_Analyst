# Report Repository — Complete Documentation

> Covers: User Auth (Phase 0), Draft Clipboard (Phase A), Report CRUD + PDF Export (Phase B), Repository & Search (Phase C)

---

## Table of Contents

1. [Overview & Architecture](#1-overview--architecture)
2. [Database Models](#2-database-models)
3. [Authentication](#3-authentication)
4. [Draft Clipboard API](#4-draft-clipboard-api)
5. [Report CRUD API](#5-report-crud-api)
6. [Repository & Search API](#6-repository--search-api)
7. [PDF Export](#7-pdf-export)
8. [Complete UI Flow](#8-complete-ui-flow)
9. [Environment Variables](#9-environment-variables)
10. [File Index](#10-file-index)

---

## 1. Overview & Architecture

### What was built

Investment Analysts use RAG and Quant agents to generate text analysis and chart images. Previously, outputs were stored in **localStorage** and manually assembled in a creation tab. This feature replaces that with a proper server-side system:

```
Agent output (text / chart image)
        │
        ▼
POST /reports/draft/items        ← persists to DB (replaces localStorage)
        │
        ▼
Creation Tab  ──── GET /reports/draft/items/{user_id}
   Edit / Reorder ── PUT, POST /reorder
        │
        ▼
POST /reports/from-draft/{user_id}   ← assembles + saves report in one call
        │
        ├── GET /reports/{id}/export/pdf   ← download PDF
        │
        └── POST /reports/{id}/publish     ← push to Fund Manager repository
                    │
                    ▼
          GET /reports                     ← FM browses repository
          GET /reports/search?q=           ← FM searches with FTS5
          GET /reports/repository/stats    ← FM dashboard
```

### Tech stack additions

| Package | Version | Purpose |
|---------|---------|---------|
| `python-jose[cryptography]` | ≥3.3.0 | JWT tokens |
| `bcrypt` | ≥4.0.0 | Password hashing |
| `fpdf2` | ≥2.7.0 | Server-side PDF generation |

### Database additions (via Alembic)

| Migration | Table(s) |
|-----------|----------|
| `008_add_users` | `users` |
| `009_add_report_draft_items` | `report_draft_items` |
| `010_add_analyst_reports` | `analyst_reports`, `analyst_reports_fts` (FTS5), 3 sync triggers |

---

## 2. Database Models

### `users`

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `email` | String UNIQUE | Login identifier |
| `username` | String UNIQUE | Display name |
| `full_name` | String | Optional |
| `role` | String | `analyst` \| `fund_manager` \| `admin` |
| `hashed_password` | String | bcrypt hash |
| `is_active` | Boolean | Soft-disable without delete |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

### `report_draft_items`

Staging clipboard — one row per clipped item. Cleared after report creation.

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `user_id` | String | Author (FK intent — matches `users.username` or `users.id`) |
| `item_type` | String | `text` \| `image` \| `summary` |
| `content` | Text | Markdown body for text/summary items |
| `image_url` | String | Cloudinary URL for image items |
| `source` | String | `rag` \| `quant` \| `summary` |
| `session_id` | String | Originating chat session |
| `label` | String | Section heading shown in creation tab |
| `sort_order` | Integer | Display order (0 = top) |
| `created_at` | DateTime | |

### `analyst_reports`

Permanent report record.

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | |
| `user_id` | String | Author |
| `title` | String | Report headline |
| `company_name` | String | Primary company analysed |
| `ticker` | String | Optional ticker symbol |
| `description` | Text | One-line analyst note (used in repository cards) |
| `recommendation` | Enum | `buy` \| `sell` \| `hold` |
| `content_markdown` | Text | Full report body in Markdown |
| `image_urls` | JSON | List of Cloudinary chart URLs |
| `source_session_ids` | JSON | Chat session IDs used to build this report |
| `portfolio_id` | Integer FK | Optional link to a portfolio |
| `status` | Enum | `draft` \| `published` |
| `tags` | JSON | Free-form string tags |
| `created_at` | DateTime | Indexed |
| `updated_at` | DateTime | |

**FTS5 virtual table** `analyst_reports_fts` — automatically synced via triggers on `analyst_reports`. Indexes: `title`, `company_name`, `description`, `content_markdown`.

---

## 3. Authentication

All auth endpoints are under `/auth`.

### POST `/auth/signup`

Register a new user. Returns tokens immediately.

**Request body:**
```json
{
  "email": "ganesh@indium.com",
  "username": "ganesh",
  "password": "mypassword",
  "full_name": "Ganesh K",
  "role": "analyst"
}
```

**Valid roles:** `analyst` | `fund_manager` | `admin`

**Response:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user_id": 1,
  "email": "ganesh@indium.com",
  "username": "ganesh",
  "role": "analyst"
}
```

**Error codes:** `409` email already registered | `409` username taken | `400` invalid role

---

### POST `/auth/login`

**Request body:**
```json
{
  "email": "ganesh@indium.com",
  "password": "mypassword"
}
```

**Response:** same as signup.

**Error codes:** `401` invalid credentials | `403` account inactive

---

### POST `/auth/refresh`

Exchange a refresh token for a new token pair.

**Request body:**
```json
{ "refresh_token": "eyJ..." }
```

---

### GET `/auth/me`

Returns the authenticated user's profile.

**Header:** `Authorization: Bearer <access_token>`

**Response:**
```json
{
  "id": 1,
  "email": "ganesh@indium.com",
  "username": "ganesh",
  "full_name": "Ganesh K",
  "role": "analyst",
  "is_active": true,
  "created_at": "2026-04-23T10:00:00"
}
```

---

### PUT `/auth/me`

Update full_name or change password.

**Request body:**
```json
{
  "full_name": "Ganesh Kumar",
  "current_password": "mypassword",
  "new_password": "newpassword"
}
```

**Notes:**
- Access token lifetime: 30 min (env: `ACCESS_TOKEN_EXPIRE_MINUTES`)
- Refresh token lifetime: 7 days (env: `REFRESH_TOKEN_EXPIRE_DAYS`)
- Secret key: env `JWT_SECRET_KEY` — **must be set in production**

---

## 4. Draft Clipboard API

Endpoints under `/reports/draft`. These replace localStorage for the creation tab.

### POST `/reports/draft/items`

Clip a generated output to the user's staging clipboard.

**Request body:**
```json
{
  "user_id": "ganesh",
  "item_type": "text",
  "content": "## Revenue Analysis\nApple posted record $97B...",
  "source": "rag",
  "session_id": "sess-abc123",
  "label": "Revenue Analysis",
  "sort_order": 0
}
```

| Field | Required | Values |
|-------|----------|--------|
| `user_id` | ✓ | any string |
| `item_type` | ✓ | `text` \| `image` \| `summary` |
| `content` | for text/summary | Markdown string |
| `image_url` | for image | Cloudinary URL |
| `source` | optional | `rag` \| `quant` \| `summary` |
| `session_id` | optional | originating session |
| `label` | optional | section heading in creation tab |
| `sort_order` | optional | integer, default 0 |

**Response:** `201` with the created `DraftItemResponse`

---

### GET `/reports/draft/items/{user_id}`

Load all clipboard items for the creation tab, ordered by `sort_order` then `created_at`.

**Response:**
```json
[
  {
    "id": 1,
    "user_id": "ganesh",
    "item_type": "text",
    "content": "## Revenue Analysis\n...",
    "image_url": null,
    "source": "rag",
    "session_id": "sess-abc123",
    "label": "Revenue Analysis",
    "sort_order": 0,
    "created_at": "2026-04-23T10:05:00"
  }
]
```

---

### PUT `/reports/draft/items/{item_id}?user_id=`

Edit a clipboard item's label, text content, or sort position.

**Request body** (all fields optional):
```json
{
  "label": "Updated Section Name",
  "content": "Updated markdown...",
  "sort_order": 2
}
```

---

### POST `/reports/draft/items/reorder?user_id=`

Reorder items by submitting all IDs in the desired display sequence.

**Request body:**
```json
{ "ordered_ids": [3, 1, 2] }
```

Returns updated list in new order.

---

### DELETE `/reports/draft/items/{item_id}?user_id=`

Remove a single clipboard item.

---

### DELETE `/reports/draft/items/user/{user_id}`

Clear all clipboard items for a user. Called automatically after `POST /reports/from-draft`.

**Response:**
```json
{ "message": "Cleared 3 item(s)", "user_id": "ganesh", "deleted": 3 }
```

---

## 5. Report CRUD API

### POST `/reports/from-draft/{user_id}` ← **Primary creation endpoint**

One-shot endpoint: reads all staged clipboard items, assembles them into a report, clears the clipboard.

- **text/summary** items → concatenated into `content_markdown` with their `label` as `## Section Heading`
- **image** items → collected into `image_urls`
- Session IDs from clipboard items are merged into `source_session_ids`

**Request body:**
```json
{
  "title": "Apple Inc — Initiating Coverage: BUY",
  "company_name": "Apple Inc",
  "ticker": "AAPL",
  "description": "Record services revenue supports premium valuation.",
  "recommendation": "buy",
  "tags": ["tech", "megacap"],
  "portfolio_id": null,
  "source_session_ids": [],
  "clear_draft": true
}
```

**Response:** `201` with full `ReportResponse` including `id`.

**Error:** `422` if the clipboard is empty.

---

### POST `/reports`

Create a report manually with pre-assembled content (use when you don't want to go through the clipboard flow).

**Request body:**
```json
{
  "user_id": "ganesh",
  "title": "Microsoft Azure — BUY",
  "company_name": "Microsoft Corp",
  "ticker": "MSFT",
  "description": "Azure momentum continues at 28% YoY growth.",
  "recommendation": "buy",
  "content_markdown": "## Overview\nAzure revenue...",
  "image_urls": ["https://res.cloudinary.com/.../chart.png"],
  "source_session_ids": ["sess-001"],
  "portfolio_id": 3,
  "tags": ["cloud", "enterprise"]
}
```

---

### GET `/reports/{report_id}`

Fetch a single report by ID (any status).

---

### PUT `/reports/{report_id}?user_id=`

Update a report. Only the author can edit. All fields optional.

**Request body:**
```json
{
  "title": "Updated Title",
  "recommendation": "hold",
  "content_markdown": "## Updated Analysis\n..."
}
```

---

### POST `/reports/{report_id}/publish?user_id=`

Move a draft report to `published` status so Fund Managers can see it.

---

### POST `/reports/{report_id}/unpublish?user_id=`

Revert a published report back to `draft`.

---

### DELETE `/reports/{report_id}?user_id=`

Permanently delete a report. Author only.

---

### ReportResponse shape

```json
{
  "id": 5,
  "user_id": "ganesh",
  "title": "Apple Inc — BUY",
  "company_name": "Apple Inc",
  "ticker": "AAPL",
  "description": "Record services revenue...",
  "recommendation": "buy",
  "content_markdown": "## Revenue Analysis\n...\n\n---\n\n## Investment Summary\n...",
  "image_urls": ["https://res.cloudinary.com/.../chart.png"],
  "source_session_ids": ["sess-abc"],
  "portfolio_id": null,
  "status": "published",
  "tags": ["tech"],
  "created_at": "2026-04-23T10:10:00",
  "updated_at": "2026-04-23T10:15:00"
}
```

---

## 6. Repository & Search API

### GET `/reports/repository/stats`

Fund Manager dashboard — aggregate data across all published reports.

**Response:**
```json
{
  "total_published": 24,
  "by_recommendation": {
    "buy": 12,
    "sell": 5,
    "hold": 6,
    "unrated": 1
  },
  "top_companies": [
    { "company": "Apple Inc", "count": 4 },
    { "company": "Microsoft Corp", "count": 3 }
  ],
  "top_analysts": [
    { "analyst": "ganesh", "count": 8 },
    { "analyst": "priya", "count": 6 }
  ],
  "recent_reports": [
    {
      "id": 24,
      "title": "Apple Inc — BUY",
      "company_name": "Apple Inc",
      "ticker": "AAPL",
      "recommendation": "buy",
      "author": "ganesh",
      "description": "Record services revenue...",
      "created_at": "2026-04-23T10:10:00"
    }
  ]
}
```

---

### GET `/reports` — Fund Manager repository

List all published reports with filters.

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `company` | string | Partial match on company_name |
| `ticker` | string | Partial match on ticker |
| `recommendation` | string | `buy` \| `sell` \| `hold` |
| `author` | string | Filter by analyst user_id |
| `portfolio_id` | int | Filter by portfolio |
| `from_date` | ISO datetime | Earliest created_at |
| `to_date` | ISO datetime | Latest created_at |
| `page` | int | Default 1 |
| `page_size` | int | Default 20, max 100 |

**Example:**
```
GET /reports?recommendation=buy&ticker=AAPL&from_date=2026-01-01T00:00:00
```

**Response:**
```json
{
  "total": 3,
  "page": 1,
  "page_size": 20,
  "items": [ ...ReportResponse... ]
}
```

---

### GET `/reports/user/{user_id}` — Analyst's own reports

Same filters as above, plus `status` (`draft` | `published`). Shows both draft and published.

**Example:**
```
GET /reports/user/ganesh?status=draft
```

---

### GET `/reports/search` — Full-text search

FTS5 search across `title`, `company_name`, `description`, and `content_markdown`. Combine with any metadata filter.

**Query parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `q` | string | ✓ (min 2 chars) | Search query |
| `status` | string | default `published` | `draft` \| `published` |
| `company` | string | | Post-filter |
| `ticker` | string | | Post-filter |
| `recommendation` | string | | Post-filter |
| `author` | string | | Post-filter |
| `from_date` | ISO datetime | | Post-filter |
| `to_date` | ISO datetime | | Post-filter |
| `page` | int | | Default 1 |
| `page_size` | int | | Default 20, max 100 |

**Examples:**
```
GET /reports/search?q=rising+interest+rates
GET /reports/search?q=Apple+services&recommendation=buy
GET /reports/search?q=margin+compression&from_date=2026-01-01T00:00:00
GET /reports/search?q=EV+competition&ticker=TSLA&author=ganesh
```

**How FTS5 works:** SQLite's FTS5 matches against all indexed columns (`title`, `company_name`, `description`, `content_markdown`). Results are ranked by relevance. Metadata filters are applied after the FTS match.

**FTS query syntax tips:**
- Simple keyword: `rising rates`
- Phrase: `"interest rate risk"`
- Prefix: `inflat*`
- Column-specific: `company_name: Apple`

---

## 7. PDF Export

### GET `/reports/{report_id}/export/pdf`

Downloads the report as a PDF file.

**PDF structure:**
1. **Header** — Title (large), colour-coded recommendation badge (green=BUY, red=SELL, grey=HOLD/N/A), company + ticker, author, date, description
2. **Divider line**
3. **Body** — Markdown parsed to PDF: `#`, `##`, `###` headings at different sizes; `- / *` bullets; blank lines as spacing; all other text as paragraphs
4. **Charts page** (if image_urls present) — each Cloudinary URL downloaded and embedded at full page width

**Filename:** `report_{id}_{company_name}.pdf`

**Notes:**
- Text is sanitised to latin-1 range (em-dash → `--`, smart quotes → `"'`, bullet → `*`, etc.)
- Images that fail to download show a placeholder line instead of crashing
- Requires `fpdf2` (included in `requirements.txt`)

---

## 8. Complete UI Flow

### Analyst flow (creating a report)

```
1. Run RAG or Quant agent
   → Agent generates text block
   → POST /reports/draft/items
     { user_id, item_type: "text", content: "...", source: "rag", label: "Revenue Analysis" }

2. Agent generates a chart
   → Cloudinary URL returned from quant agent
   → POST /reports/draft/items
     { user_id, item_type: "image", image_url: "https://res.cloudinary.com/...", source: "quant", label: "Price Chart" }

3. Generate consolidated summary
   → POST /chats/sessions/consolidated-summary  (existing endpoint)
   → POST /reports/draft/items
     { user_id, item_type: "summary", content: "...", source: "summary", label: "Investment Summary" }

4. Open creation tab
   → GET /reports/draft/items/{user_id}
   → Render sections in order; allow drag-to-reorder, label edits, item removal

5. (Optional) Reorder
   → POST /reports/draft/items/reorder?user_id=ganesh
     { "ordered_ids": [3, 1, 2] }

6. (Optional) Edit a section label
   → PUT /reports/draft/items/2?user_id=ganesh
     { "label": "Valuation Summary" }

7. Fill report metadata (title, company, recommendation) + click "Create Report"
   → POST /reports/from-draft/ganesh
     { title, company_name, ticker, recommendation, description, tags }
   ← Response: { id: 7, status: "draft", ... }

8. Click "Download PDF"
   → GET /reports/7/export/pdf
   ← Browser downloads PDF

9. (Optional) Publish to repository so Fund Managers can see it
   → POST /reports/7/publish?user_id=ganesh
```

### Fund Manager flow (browsing the repository)

```
1. Dashboard card
   → GET /reports/repository/stats
   ← { total_published, by_recommendation, top_companies, top_analysts, recent_reports }

2. Browse all published
   → GET /reports
   → GET /reports?recommendation=buy&from_date=2026-01-01T00:00:00

3. Search bar
   → GET /reports/search?q=rising+interest+rates
   → GET /reports/search?q=Apple&recommendation=buy

4. Open a report
   → GET /reports/7

5. Download PDF
   → GET /reports/7/export/pdf
```

---

## 9. Environment Variables

Add these to your `.env` file:

```bash
# Auth (REQUIRED — change in production)
JWT_SECRET_KEY=use-a-long-random-string-here
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
```

Generate a secure secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Existing variables (unchanged):
```bash
DATABASE_URL=sqlite:///./data/portfolios.db
CLOUDINARY_CLOUD_NAME=...
CLOUDINARY_API_KEY=...
CLOUDINARY_API_SECRET=...
```

---

## 10. File Index

### New files

| File | Purpose |
|------|---------|
| `app/auth/__init__.py` | Package init |
| `app/auth/password.py` | `hash_password` / `verify_password` via bcrypt |
| `app/auth/jwt.py` | `create_access_token`, `create_refresh_token`, `decode_token` |
| `app/auth/deps.py` | `get_current_user` FastAPI dependency, `require_role()` factory |
| `app/api/auth.py` | Auth router — signup, login, refresh, me, update |
| `app/api/reports.py` | Reports router — all clipboard + report + repository endpoints |
| `app/services/report.py` | Report service — all business logic for both draft and reports |
| `alembic/versions/008_add_users.py` | Migration — `users` table |
| `alembic/versions/009_add_report_draft_items.py` | Migration — `report_draft_items` table |
| `alembic/versions/010_add_analyst_reports.py` | Migration — `analyst_reports` + FTS5 + triggers |

### Modified files

| File | Change |
|------|--------|
| `app/database/models.py` | Added `User`, `ReportDraftItem`, `AnalystReport`, `RecommendationType`, `ReportStatus` |
| `app/main.py` | Registered `auth_router` and `reports_router` |
| `requirements.txt` | Added `python-jose[cryptography]`, `bcrypt`, `fpdf2` |

---

## Quick Reference — All New Endpoints

```
AUTH
  POST   /auth/signup
  POST   /auth/login
  POST   /auth/refresh
  GET    /auth/me
  PUT    /auth/me

CLIPBOARD
  POST   /reports/draft/items
  GET    /reports/draft/items/{user_id}
  PUT    /reports/draft/items/{item_id}?user_id=
  POST   /reports/draft/items/reorder?user_id=
  DELETE /reports/draft/items/{item_id}?user_id=
  DELETE /reports/draft/items/user/{user_id}

REPORTS
  POST   /reports/from-draft/{user_id}          ← main creation endpoint
  POST   /reports
  GET    /reports/{report_id}
  PUT    /reports/{report_id}?user_id=
  DELETE /reports/{report_id}?user_id=
  POST   /reports/{report_id}/publish?user_id=
  POST   /reports/{report_id}/unpublish?user_id=
  GET    /reports/{report_id}/export/pdf

REPOSITORY
  GET    /reports/repository/stats
  GET    /reports                                ← FM view, published only
  GET    /reports/user/{user_id}                 ← analyst's own reports
  GET    /reports/search?q=                      ← FTS5 search
```
