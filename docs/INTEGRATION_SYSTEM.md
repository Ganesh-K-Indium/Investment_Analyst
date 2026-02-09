# Integration Management System

## Overview

The Integration Management System allows users to connect various data sources (SharePoint, Google Drive, Azure Blob, AWS S3, SFTP) to the RAG system. Users can browse files from these sources and import them directly into the vector database for question answering.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Integration System                       │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   API Routes │───>│   Services   │───>│  Connectors  │  │
│  │              │    │              │    │              │  │
│  │ - CRUD Ops   │    │ - Integration│    │ - SharePoint │  │
│  │ - Browse     │    │   Service    │    │ - GDrive     │  │
│  │ - Import     │    │ - File Import│    │ - Azure Blob │  │
│  └──────────────┘    │   Service    │    │ - AWS S3     │  │
│                      └──────────────┘    │ - SFTP       │  │
│                                          └──────────────┘  │
│                                                               │
│  ┌──────────────┐    ┌──────────────┐                       │
│  │   Database   │    │   Ingestion  │                       │
│  │              │    │   Pipeline   │                       │
│  │ - Integration│    │              │                       │
│  │   Model      │    │ - PDF Process│                       │
│  │              │    │ - Image Extract                      │
│  └──────────────┘    │ - Vector DB  │                       │
│                      └──────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

## Database Schema

### Integration Model

```python
class Integration(Base):
    id: int                      # Primary key
    user_id: str                 # User identifier
    vendor: str                  # Data source type
    name: str                    # User-friendly name
    url: str                     # Connection URL (optional)
    credentials: JSON            # Authentication credentials (encrypted)
    status: str                  # active, disconnected, error
    last_sync: datetime          # Last sync timestamp
    description: str             # Integration description
    created_at: datetime
    updated_at: datetime
```

## API Endpoints

### 1. Integration Management

#### Create Integration
```http
POST /integrations/
```

**Request Body:**
```json
{
  "user_id": "user123",
  "vendor": "sharepoint",
  "name": "Company SharePoint",
  "url": "https://company.sharepoint.com",
  "credentials": {
    "client_id": "sp-client-001",
    "client_secret": "••••••••",
    "user_id": "admin@company.com",
    "folder_path": "Documents"
  },
  "description": "Main SharePoint document library"
}
```

**Response:**
```json
{
  "id": 1,
  "user_id": "user123",
  "vendor": "sharepoint",
  "name": "Company SharePoint",
  "url": "https://company.sharepoint.com",
  "status": "active",
  "last_sync": null,
  "description": "Main SharePoint document library",
  "created_at": "2026-02-06T10:00:00",
  "updated_at": "2026-02-06T10:00:00",
  "credentials_summary": {
    "client_id": "sp-client-001",
    "client_secret": "••••••••",
    "user_id": "admin@company.com",
    "folder_path": "Documents"
  }
}
```

#### Get Integration
```http
GET /integrations/{integration_id}
```

#### List User Integrations
```http
GET /integrations/user/{user_id}?vendor=sharepoint
```

#### Update Integration
```http
PUT /integrations/{integration_id}
```

**Request Body:**
```json
{
  "name": "Updated Name",
  "description": "Updated description"
}
```

#### Delete Integration
```http
DELETE /integrations/{integration_id}
```

#### Disconnect Integration
```http
POST /integrations/{integration_id}/disconnect
```

#### Test Connection
```http
POST /integrations/{integration_id}/test
```

**Response:**
```json
{
  "success": true,
  "message": "SharePoint connection successful",
  "vendor": "sharepoint",
  "files_found": 15
}
```

### 2. File Browsing

#### Browse Files
```http
POST /integrations/browse
```

**Request Body:**
```json
{
  "integration_id": 1,
  "path": "/Documents",
  "search_query": "Q3"
}
```

**Response:**
```json
{
  "integration_id": 1,
  "vendor": "sharepoint",
  "path": "/Documents",
  "total_count": 3,
  "files": [
    {
      "name": "Q3_Report.pdf",
      "path": "/Documents/Q3_Report.pdf",
      "size": 2457600,
      "last_modified": "2024-10-15T14:30:00",
      "mime_type": "application/pdf",
      "is_directory": false
    },
    {
      "name": "Q3_Financial_Model.xlsx",
      "path": "/Documents/Q3_Financial_Model.xlsx",
      "size": 861184,
      "last_modified": "2024-02-05T16:15:00",
      "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "is_directory": false
    }
  ]
}
```

### 3. File Import

#### Import Files
```http
POST /integrations/import
```

**Request Body:**
```json
{
  "integration_id": 1,
  "file_paths": [
    "/Documents/Q3_Report.pdf",
    "/Documents/Annual_Report_2023.pdf"
  ],
  "portfolio_id": 5
}
```

**Response:**
```json
{
  "integration_id": 1,
  "total_files": 2,
  "successful": 1,
  "failed": 1,
  "file_results": [
    {
      "file_path": "/Documents/Q3_Report.pdf",
      "status": "completed",
      "success": true,
      "message": "Successfully ingested. Added 45 text chunks",
      "chunks_added": 45,
      "error": null
    },
    {
      "file_path": "/Documents/Annual_Report_2023.pdf",
      "status": "failed",
      "success": false,
      "message": "Failed to process file",
      "chunks_added": null,
      "error": "File download failed: timeout"
    }
  ]
}
```

## Supported Data Sources

### 1. SharePoint Online

**Required Credentials:**
- `client_id`: Application Client ID
- `client_secret`: Application Client Secret
- `user_id`: User email
- `folder_path`: Document library path (default: "Documents")

**URL:** SharePoint site URL (e.g., https://company.sharepoint.com)

### 2. Google Drive

**Required Credentials:**
- `client_id`: OAuth Client ID
- `client_secret`: OAuth Client Secret
- `user_id`: Google account email
- `folder_path`: Folder ID to access

### 3. Azure Blob Storage

**Required Credentials:**
- `account_name`: Storage account name
- `account_key` or `sas_token`: Authentication key
- `folder_path`: Container name (default: "documents")

**URL:** Account URL (e.g., https://account.blob.core.windows.net)

### 4. AWS S3

**Required Credentials:**
- `bucket_name`: S3 bucket name
- `access_key_id`: AWS Access Key ID
- `secret_access_key`: AWS Secret Access Key
- `region`: AWS region (default: "us-east-1")
- `folder_path`: Prefix/folder path

### 5. SFTP Server

**Required Credentials:**
- `username`: SFTP username
- `password` or `private_key`: Authentication method
- `folder_path`: Remote directory path (default: "/")

**URL:** SFTP host (e.g., sftp.company.com)

## Usage Flow

### Step 1: Create Integration

```bash
curl -X POST http://localhost:8000/integrations/ \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user123",
    "vendor": "sharepoint",
    "name": "Company SharePoint",
    "url": "https://company.sharepoint.com",
    "credentials": {
      "client_id": "sp-client-001",
      "client_secret": "secret123",
      "user_id": "admin@company.com",
      "folder_path": "Documents"
    }
  }'
```

### Step 2: Test Connection

```bash
curl -X POST http://localhost:8000/integrations/1/test
```

### Step 3: Browse Files

```bash
curl -X POST http://localhost:8000/integrations/browse \
  -H "Content-Type: application/json" \
  -d '{
    "integration_id": 1,
    "path": "/Documents",
    "search_query": "report"
  }'
```

### Step 4: Import Files

```bash
curl -X POST http://localhost:8000/integrations/import \
  -H "Content-Type: application/json" \
  -d '{
    "integration_id": 1,
    "file_paths": [
      "/Documents/Q3_Report.pdf",
      "/Documents/Annual_Report_2023.pdf"
    ]
  }'
```

### Step 5: Use in RAG Queries

Once imported, files are automatically available in RAG queries:

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What were the Q3 revenue figures?",
    "thread_id": "session123"
  }'
```

## Frontend Integration

### Create Integration Modal

```javascript
// POST /integrations/
const createIntegration = async (data) => {
  const response = await fetch('/integrations/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: currentUser.id,
      vendor: data.vendor,
      name: data.name,
      url: data.url,
      credentials: {
        client_id: data.clientId,
        client_secret: data.clientSecret,
        user_id: data.userId,
        folder_path: data.folderPath
      },
      description: data.description
    })
  });
  return response.json();
};
```

### Browse Files Interface

```javascript
// POST /integrations/browse
const browseFiles = async (integrationId, path = null, searchQuery = null) => {
  const response = await fetch('/integrations/browse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      integration_id: integrationId,
      path: path,
      search_query: searchQuery
    })
  });
  const data = await response.json();
  return data.files;
};
```

### Import Files

```javascript
// POST /integrations/import
const importFiles = async (integrationId, selectedFiles) => {
  const response = await fetch('/integrations/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      integration_id: integrationId,
      file_paths: selectedFiles.map(f => f.path)
    })
  });
  return response.json();
};
```

## Security Considerations

1. **Credential Storage**: Credentials are stored in the database. Consider encrypting them at rest.

2. **Access Control**: Implement proper user authentication and authorization to ensure users can only access their own integrations.

3. **Secrets Management**: For production, use a secrets manager (AWS Secrets Manager, Azure Key Vault, HashiCorp Vault) instead of storing credentials in the database.

4. **HTTPS**: Always use HTTPS for API communication to protect credentials in transit.

5. **Token Rotation**: Implement token rotation for OAuth-based integrations (Google Drive, SharePoint).

## Future Enhancements

1. **Scheduled Sync**: Add background jobs to automatically sync files from integrations
2. **Webhooks**: Support webhooks for real-time file updates
3. **More Formats**: Support additional file formats beyond PDF (Word, Excel, PowerPoint)
4. **Batch Import**: Optimize for importing large numbers of files
5. **File Filtering**: Add rules for automatic file filtering and categorization
6. **Encryption**: Add encryption for credentials at rest
7. **Activity Logs**: Track all integration activities for audit purposes

## Troubleshooting

### Connection Test Fails

1. Verify credentials are correct
2. Check network connectivity
3. Ensure proper permissions are granted
4. Review firewall rules

### File Import Fails

1. Check file format (currently only PDF supported)
2. Verify file exists and is accessible
3. Check file size limits
4. Review error messages in the response

### Missing Files in Browse

1. Verify the path is correct
2. Check folder permissions
3. Ensure the integration is not disconnected
4. Test the connection first

## API Documentation

Full interactive API documentation is available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
