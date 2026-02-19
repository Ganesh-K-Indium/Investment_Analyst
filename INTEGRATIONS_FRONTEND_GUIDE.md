# Integrations — Frontend Developer Guide

This document describes every API endpoint used by the Integration Management UI so a frontend developer can build or replace `static/integrations.html` with a production UI.

---

## Base URL

```
http://<host>:8000
```

All endpoints are prefixed with `/integrations`.

---

## Supported Vendors

| Vendor key | Display name |
|---|---|
| `sharepoint` | SharePoint Online |
| `google_drive` | Google Drive |
| `onedrive` | Microsoft OneDrive |
| `confluence` | Atlassian Confluence |
| `azure_blob` | Azure Blob Storage |
| `aws_s3` | AWS S3 |
| `sftp` | SFTP Server |

---

## Endpoints

### 1. List user integrations

```
GET /integrations/user/{user_id}
```

Optional query param: `?vendor=google_drive` to filter by vendor.

**Response** — array of `IntegrationResponse`:

```json
[
  {
    "id": 1,
    "user_id": "alice",
    "vendor": "google_drive",
    "name": "Research Drive",
    "url": null,
    "status": "active",
    "last_sync": "2024-01-15T10:30:00",
    "description": "Google Drive for research PDFs",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00",
    "credentials_summary": {
      "service_account_json": "••••••••",
      "folder_path": "root"
    }
  }
]
```

> `credentials_summary` has secret fields replaced with `••••••••`.

---

### 2. Create integration

```
POST /integrations/
Content-Type: application/json
```

**Body**:

```json
{
  "user_id": "alice",
  "vendor": "google_drive",
  "name": "Research Drive",
  "url": null,
  "credentials": { ... },
  "description": "Optional"
}
```

#### Required credentials per vendor

##### SharePoint
```json
{
  "tenant_id": "your-tenant-id",
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "site_name": "mysite",
  "folder_path": "Documents"
}
```
- `folder_path` — path to start browsing from (e.g. `"Documents"`, `"Documents/Reports"`). Leave blank for drive root.
- `url` (top-level field, not in credentials) — the SharePoint site URL e.g. `https://company.sharepoint.com/sites/mysite`.

##### Google Drive
```json
{
  "service_account_json": "{\"type\":\"service_account\", ... }",
  "folder_path": ""
}
```
- `service_account_json` — full JSON string of the Google Service Account key file.
- `folder_path` — a **Google Drive folder ID** to start from (e.g. `"1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"`). Leave blank or omit to start at Drive root.
- Alternatively, provide OAuth2 credentials: `client_id`, `client_secret`, `refresh_token`.

##### OneDrive
```json
{
  "tenant_id": "your-tenant-id",
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "folder_path": "Documents"
}
```
- `tenant_id` — Azure AD tenant ID.
- `client_id` — Azure AD application (client) ID.
- `client_secret` — Azure AD application client secret.
- `folder_path` — optional path to start browsing from (e.g. `"Documents"`, `"Documents/Reports"`). Leave blank for drive root.
- No `url` field needed (always uses user's OneDrive).

##### Confluence
```json
{
  "username": "user@company.com",
  "api_token": "your-api-token",
  "folder_path": ""
}
```
- `username` — Atlassian Cloud email address.
- `api_token` — Personal API token (generate in Atlassian Account Settings).
- `folder_path` — optional space key to start from (e.g., `"PROJ"` for a space with key PROJ). Leave blank to list all spaces.
- `url` (top-level field, not in credentials) — the Confluence Cloud URL e.g. `https://company.atlassian.net/wiki`.

##### Azure Blob Storage
```json
{
  "account_name": "mystorageaccount",
  "account_key": "base64-encoded-key",
  "folder_path": "documents"
}
```
- `folder_path` — the container name. Required.

##### AWS S3
```json
{
  "bucket_name": "my-bucket",
  "access_key_id": "AKIA...",
  "secret_access_key": "secret",
  "region": "us-east-1",
  "folder_path": "optional/prefix/"
}
```
- `folder_path` — optional S3 key prefix to start browsing from.

##### SFTP
```json
{
  "username": "user",
  "password": "pass",
  "folder_path": "/"
}
```
- `url` (top-level) — the SFTP host, e.g. `sftp.example.com`.
- `folder_path` — remote Unix path to start from. Required.

**Response**: `IntegrationResponse` (same shape as list endpoint).

---

### 3. Test connection

```
POST /integrations/{integration_id}/test
```

**Response**:

```json
{
  "success": true,
  "message": "Google Drive connection successful.",
  "vendor": "google_drive",
  "files_found": 12,
  "error": null
}
```

---

### 4. Delete integration

```
DELETE /integrations/{integration_id}
```

**Response**: `{ "message": "Integration deleted successfully" }`

---

### 5. Browse files (with folder navigation)

```
POST /integrations/browse
Content-Type: application/json
```

**Body**:

```json
{
  "integration_id": 1,
  "path": null,
  "search_query": null,
  "portfolio_id": 7,
  "user_id": null
}
```

| Field | Type | Description |
|---|---|---|
| `integration_id` | int | Required. |
| `path` | string \| null | Folder to list. `null` = root of the integration. See path format below. |
| `search_query` | string \| null | Filter results by filename substring. |
| `portfolio_id` | int \| null | If supplied, response includes `available_tickers` from that portfolio. |
| `user_id` | string \| null | Alternative to `portfolio_id` — returns all tickers across all user portfolios. |

#### Path formats per vendor

| Vendor | Root | Subfolder |
|---|---|---|
| **SharePoint** | `null` or `""` | Human-readable path string: `"Documents"`, `"Documents/Reports"` |
| **Google Drive** | `null` or `""` | Google Drive **folder ID** (opaque string): `"1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"` |
| **OneDrive** | `null` or `""` | Folder path string: `"Documents"`, `"Documents/Reports"` |
| **Confluence** | `null` or `""` | Space key: `"PROJ"`, or page path: `"PROJ/123456"` (space_key/page_id) |
| **Azure Blob** | `null` | Blob prefix with trailing slash: `"reports/"`, `"reports/2024/"` |
| **AWS S3** | `null` | S3 key prefix with trailing slash: `"reports/"`, `"archive/2024/"` |
| **SFTP** | `null` | Unix path: `"/home/user/reports"` |

> **Important**: When the API returns a folder item, its `path` field is already in the correct format for the next browse call. The UI should pass `file.path` directly as the `path` parameter — no transformation needed.

**Response**:

```json
{
  "integration_id": 1,
  "vendor": "google_drive",
  "path": "/",
  "files": [
    {
      "name": "Reports",
      "path": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
      "size": null,
      "last_modified": null,
      "mime_type": "application/vnd.google-apps.folder",
      "is_directory": true
    },
    {
      "name": "annual_report_2023.pdf",
      "path": "1abc123def456",
      "size": 2048576,
      "last_modified": "2024-01-10T08:30:00+00:00",
      "mime_type": "application/pdf",
      "is_directory": false
    }
  ],
  "total_count": 2,
  "available_tickers": ["AMZN", "GOOGL", "AAPL"],
  "portfolio_id": 7,
  "portfolio_name": "Tech Portfolio"
}
```

---

### 6. Import files

```
POST /integrations/import
Content-Type: application/json
```

**Body**:

```json
{
  "integration_id": 1,
  "file_paths": ["1abc123def456", "1xyz789ghi012"],
  "ticker": "AMZN"
}
```

- `file_paths` — the `path` values from the browse response for the files to import (not folders).
- `ticker` — uppercase ticker symbol. Files are ingested into the `ticker_{ticker.lower()}` vector collection.

**Response**:

```json
{
  "integration_id": 1,
  "total_files": 2,
  "successful": 2,
  "failed": 0,
  "file_results": [
    {
      "file_path": "1abc123def456",
      "status": "completed",
      "success": true,
      "message": "Successfully ingested to ticker_amzn collection. Added 47 text chunks",
      "chunks_added": 47,
      "ticker": "AMZN",
      "error": null
    },
    {
      "file_path": "1xyz789ghi012",
      "status": "failed",
      "success": false,
      "message": "File type not supported",
      "chunks_added": null,
      "ticker": null,
      "error": "Only PDF files are currently supported"
    }
  ]
}
```

> Currently only **PDF** files are supported for ingestion. Non-PDF files will be marked as failed.

---

## Folder Navigation — UI Implementation Guide

### Concept

The API is stateless. The UI is responsible for maintaining the navigation stack (breadcrumb trail). On every folder click, re-call the browse endpoint with the new path.

### State to maintain

```typescript
let currentIntegrationId: number | null = null;
let currentPath: string | null = null;        // null = root
let currentFolderName: string = 'Root';
let navigationStack: Array<{ path: string | null; name: string }> = [];
```

### Flow

```
User clicks "Browse Files" on an integration card
    → Reset navigationStack = []
    → currentPath = null, currentFolderName = 'Root'
    → POST /integrations/browse { integration_id, path: null, portfolio_id }
    → Render files + breadcrumb ("Root")

User clicks a folder item (is_directory = true)
    → Push { path: currentPath, name: currentFolderName } onto navigationStack
    → currentPath = folder.path, currentFolderName = folder.name
    → POST /integrations/browse { integration_id, path: folder.path }
    → Re-render files + breadcrumb ("Root › FolderName")

User clicks a breadcrumb link at index i
    → fullHistory = [...navigationStack, { path: currentPath, name: currentFolderName }]
    → target = fullHistory[i]
    → navigationStack = fullHistory.slice(0, i)
    → currentPath = target.path, currentFolderName = target.name
    → POST /integrations/browse { integration_id, path: target.path }
    → Re-render files + breadcrumb
```

### Rendering rules

| `is_directory` | Behaviour |
|---|---|
| `true` | Show folder icon. Clicking navigates into it (calls browse with `file.path`). No checkbox. |
| `false` | Show file icon. Clicking toggles checkbox. `file.path` is used in the import payload. |

### Ticker / collection selection

- On **first open** (root browse), pass `portfolio_id` or `user_id` to get `available_tickers`.
- Show a dropdown so the user can pick which ticker collection to import into.
- When navigating subfolders, **do not** re-fetch portfolio data — keep the dropdown intact.
- Pass the selected ticker in the import request body as `ticker`.

---

## Error handling

All error responses follow FastAPI's default format:

```json
{ "detail": "Human-readable error message" }
```

HTTP status codes used:
- `400` — bad request (e.g. missing ticker)
- `404` — integration or portfolio not found
- `500` — connector or ingestion failure

---

## Google Drive — Service Account setup

To authenticate via a Service Account:

1. Create a GCP project and enable the Drive API.
2. Create a Service Account and download the JSON key file.
3. Share the target Drive folder (or entire Drive) with the service account email.
4. Paste the full JSON content into the "Service Account JSON" field when creating the integration.
5. Optionally, copy the **folder ID** from the Drive URL (`https://drive.google.com/drive/folders/<folder_id>`) and enter it as the "Starting Folder ID".

---

## OneDrive — OAuth2 Application setup

To authenticate with Microsoft OneDrive:

1. Sign in to [Azure Portal](https://portal.azure.com).
2. Navigate to **Azure Active Directory > App registrations > New registration**.
3. Register an application (e.g., "Investment Analyst OneDrive Integration").
4. Once created, copy the **Application (client) ID** and **Directory (tenant) ID**.
5. Under **Certificates & secrets**, create a new Client Secret and copy its value.
6. Under **API permissions**, add **Microsoft Graph > Files.Read** (or **Files.Read.All** for broader access).
7. Use the `tenant_id`, `client_id`, and `client_secret` when creating the OneDrive integration.
8. Optionally, specify a starting folder path (e.g., `"Documents"`) in the credentials.

---

## Confluence — API Token setup

To authenticate with Confluence Cloud:

1. Sign in to your [Atlassian Account](https://id.atlassian.com/manage/api-tokens).
2. Click **Create API token** and give it a name (e.g., "Investment Analyst").
3. Copy the generated API token (you'll only see it once).
4. Your username is your Atlassian Cloud email address.
5. Your Confluence URL is typically `https://company.atlassian.net/wiki` (visible in your browser when logged into Confluence).
6. Use the `username`, `api_token`, and URL when creating the Confluence integration.
7. Optionally, specify a starting space key (e.g., `"PROJ"`) in the credentials to start browsing from that space instead of listing all spaces.

**Note**: The connector exports Confluence pages as PDFs. Pages with child pages are shown as "folders" for navigation; leaf pages are downloadable as PDFs for ingestion.

---

## Notes

- The `credentials_summary` field in `IntegrationResponse` always has secrets redacted. Never store or display the raw `credentials` dict.
- `last_sync` is updated automatically after every successful import.
- The `status` field transitions: `active` → `disconnected` (via `/disconnect` endpoint). The UI can display a coloured dot accordingly.
