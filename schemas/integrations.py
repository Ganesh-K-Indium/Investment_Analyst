"""
Pydantic schemas for integration management
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Literal
from datetime import datetime


# Integration CRUD Schemas
class IntegrationCredentials(BaseModel):
    """Base credentials structure (flexible for different vendors)"""
    client_id: Optional[str] = Field(None, description="Client ID for OAuth")
    client_secret: Optional[str] = Field(None, description="Client Secret for OAuth")
    user_id: Optional[str] = Field(None, description="User ID or email for authentication")
    folder_path: Optional[str] = Field(None, description="Folder path or container name")
    
    # Additional vendor-specific fields can be added dynamically
    extra_fields: Optional[Dict[str, str]] = Field(None, description="Additional vendor-specific fields")


class IntegrationCreate(BaseModel):
    """Schema for creating a new integration"""
    user_id: str = Field(..., description="User identifier")
    vendor: Literal["sharepoint", "google_drive", "onedrive", "confluence", "azure_blob", "aws_s3", "sftp"] = Field(
        ...,
        description="Data source vendor type"
    )
    name: str = Field(..., description="User-friendly name for this integration")
    url: Optional[str] = Field(None, description="Connection URL (required for SharePoint, Azure, etc.)")
    credentials: Dict[str, str] = Field(
        ..., 
        description="Authentication credentials (client_id, client_secret, etc.)"
    )
    description: Optional[str] = Field(None, description="Integration description")


class IntegrationUpdate(BaseModel):
    """Schema for updating an existing integration"""
    name: Optional[str] = Field(None, description="Updated integration name")
    url: Optional[str] = Field(None, description="Updated connection URL")
    credentials: Optional[Dict[str, str]] = Field(None, description="Updated credentials")
    description: Optional[str] = Field(None, description="Updated description")
    status: Optional[Literal["active", "disconnected", "error"]] = Field(
        None, 
        description="Updated connection status"
    )


class IntegrationResponse(BaseModel):
    """Schema for integration response (masks sensitive data)"""
    id: int
    user_id: str
    vendor: str
    name: str
    url: Optional[str]
    status: str
    last_sync: Optional[datetime]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    # Masked credentials (only show client_id and user_id, hide secrets)
    credentials_summary: Dict[str, str] = Field(
        ..., 
        description="Summary of credentials with secrets masked"
    )
    
    class Config:
        from_attributes = True


# File browsing schemas
class RemoteFile(BaseModel):
    """Schema for a file from a remote data source"""
    name: str = Field(..., description="File name")
    path: str = Field(..., description="Full path to the file")
    size: Optional[int] = Field(None, description="File size in bytes")
    last_modified: Optional[datetime] = Field(None, description="Last modification timestamp")
    mime_type: Optional[str] = Field(None, description="MIME type of the file")
    is_directory: bool = Field(False, description="Whether this is a directory")


class BrowseFilesRequest(BaseModel):
    """Schema for browsing files from an integration"""
    integration_id: int = Field(..., description="Integration ID to browse files from")
    path: Optional[str] = Field(None, description="Path to browse (default: root)")
    search_query: Optional[str] = Field(None, description="Search query to filter files")
    portfolio_id: Optional[int] = Field(None, description="Portfolio ID to fetch available tickers")
    user_id: Optional[str] = Field(None, description="User ID to fetch portfolios and tickers")


class BrowseFilesResponse(BaseModel):
    """Schema for file browsing response"""
    integration_id: int
    vendor: str
    path: str
    files: List[RemoteFile]
    total_count: int
    available_tickers: Optional[List[str]] = Field(None, description="Available tickers from portfolio")
    portfolio_id: Optional[int] = Field(None, description="Portfolio ID if provided")
    portfolio_name: Optional[str] = Field(None, description="Portfolio name if available")


# File import schemas
class FileImportRequest(BaseModel):
    """Schema for importing files from an integration"""
    integration_id: int = Field(..., description="Integration ID to import from")
    file_paths: List[str] = Field(..., description="List of file paths to import")
    ticker: str = Field(..., description="Ticker symbol for these files (e.g., AAPL, GOOGL)")
    portfolio_id: Optional[int] = Field(None, description="Optional portfolio to associate files with")


class FileImportStatus(BaseModel):
    """Status of a single file import"""
    file_path: str
    status: Literal["pending", "downloading", "processing", "completed", "failed"]
    success: bool
    message: str
    chunks_added: Optional[int] = None
    ticker: Optional[str] = None
    error: Optional[str] = None


class FileImportResponse(BaseModel):
    """Schema for file import response"""
    integration_id: int
    total_files: int
    successful: int
    failed: int
    file_results: List[FileImportStatus]


# Connection test schema
class ConnectionTestResponse(BaseModel):
    """Schema for connection test response"""
    success: bool
    message: str
    vendor: str
    files_found: Optional[int] = None
    error: Optional[str] = None
