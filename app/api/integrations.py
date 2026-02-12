"""
Integration management and file import endpoints
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database.connection import get_db_session
from app.services.integration import IntegrationService
from app.services.connectors.base import BaseConnector
from app.services.file_import import FileImportService
from app.services.portfolio import PortfolioService
from schemas.integrations import (
    IntegrationCreate,
    IntegrationUpdate,
    IntegrationResponse,
    BrowseFilesRequest,
    BrowseFilesResponse,
    FileImportRequest,
    FileImportResponse,
    ConnectionTestResponse,
    RemoteFile
)

router = APIRouter(prefix="/integrations", tags=["Integrations"])


# ========== Integration Management Endpoints ==========

@router.post("/", response_model=IntegrationResponse)
def create_integration(
    payload: IntegrationCreate,
    db: Session = Depends(get_db_session)
):
    """
    Create a new data source integration
    
    Supported vendors:
    - sharepoint: SharePoint Online
    - google_drive: Google Drive
    - azure_blob: Azure Blob Storage
    - aws_s3: AWS S3
    - sftp: SFTP Server
    """
    try:
        integration = IntegrationService.create_integration(
            db=db,
            user_id=payload.user_id,
            vendor=payload.vendor,
            name=payload.name,
            credentials=payload.credentials,
            url=payload.url,
            description=payload.description
        )
        
        # Mask credentials for response
        credentials_summary = IntegrationService.mask_credentials(integration.credentials)
        
        return IntegrationResponse(
            id=integration.id,
            user_id=integration.user_id,
            vendor=integration.vendor,
            name=integration.name,
            url=integration.url,
            status=integration.status,
            last_sync=integration.last_sync,
            description=integration.description,
            created_at=integration.created_at,
            updated_at=integration.updated_at,
            credentials_summary=credentials_summary
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create integration: {str(e)}")


@router.get("/{integration_id}", response_model=IntegrationResponse)
def get_integration(
    integration_id: int,
    db: Session = Depends(get_db_session)
):
    """Get integration by ID"""
    integration = IntegrationService.get_integration(db, integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    credentials_summary = IntegrationService.mask_credentials(integration.credentials)
    
    return IntegrationResponse(
        id=integration.id,
        user_id=integration.user_id,
        vendor=integration.vendor,
        name=integration.name,
        url=integration.url,
        status=integration.status,
        last_sync=integration.last_sync,
        description=integration.description,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
        credentials_summary=credentials_summary
    )


@router.get("/user/{user_id}", response_model=List[IntegrationResponse])
def get_user_integrations(
    user_id: str,
    vendor: Optional[str] = None,
    db: Session = Depends(get_db_session)
):
    """Get all integrations for a user, optionally filtered by vendor"""
    integrations = IntegrationService.get_user_integrations(db, user_id, vendor)
    
    return [
        IntegrationResponse(
            id=integration.id,
            user_id=integration.user_id,
            vendor=integration.vendor,
            name=integration.name,
            url=integration.url,
            status=integration.status,
            last_sync=integration.last_sync,
            description=integration.description,
            created_at=integration.created_at,
            updated_at=integration.updated_at,
            credentials_summary=IntegrationService.mask_credentials(integration.credentials)
        )
        for integration in integrations
    ]


@router.put("/{integration_id}", response_model=IntegrationResponse)
def update_integration(
    integration_id: int,
    payload: IntegrationUpdate,
    db: Session = Depends(get_db_session)
):
    """Update an existing integration"""
    integration = IntegrationService.update_integration(
        db=db,
        integration_id=integration_id,
        name=payload.name,
        url=payload.url,
        credentials=payload.credentials,
        description=payload.description,
        status=payload.status
    )
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    credentials_summary = IntegrationService.mask_credentials(integration.credentials)
    
    return IntegrationResponse(
        id=integration.id,
        user_id=integration.user_id,
        vendor=integration.vendor,
        name=integration.name,
        url=integration.url,
        status=integration.status,
        last_sync=integration.last_sync,
        description=integration.description,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
        credentials_summary=credentials_summary
    )


@router.delete("/{integration_id}")
def delete_integration(
    integration_id: int,
    db: Session = Depends(get_db_session)
):
    """Delete an integration"""
    success = IntegrationService.delete_integration(db, integration_id)
    if not success:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    return {"message": "Integration deleted successfully"}


@router.post("/{integration_id}/disconnect")
def disconnect_integration(
    integration_id: int,
    db: Session = Depends(get_db_session)
):
    """Mark an integration as disconnected"""
    integration = IntegrationService.disconnect_integration(db, integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    return {"message": "Integration disconnected successfully", "status": integration.status}


@router.post("/{integration_id}/test", response_model=ConnectionTestResponse)
def test_integration_connection(
    integration_id: int,
    db: Session = Depends(get_db_session)
):
    """Test connection to an integration"""
    integration = IntegrationService.get_integration(db, integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    try:
        connector = BaseConnector.get_connector(
            vendor=integration.vendor,
            credentials=integration.credentials,
            url=integration.url
        )
        
        success, message = connector.test_connection()
        
        # If successful, try to count files
        files_found = None
        if success:
            try:
                files = connector.list_files()
                files_found = len(files)
            except:
                pass
        
        return ConnectionTestResponse(
            success=success,
            message=message,
            vendor=integration.vendor,
            files_found=files_found
        )
    
    except Exception as e:
        return ConnectionTestResponse(
            success=False,
            message=f"Connection test failed: {str(e)}",
            vendor=integration.vendor,
            error=str(e)
        )


# ========== File Browsing Endpoints ==========

@router.post("/browse", response_model=BrowseFilesResponse)
def browse_integration_files(
    payload: BrowseFilesRequest,
    db: Session = Depends(get_db_session)
):
    """
    Browse files from an integration

    This endpoint lists files from the connected data source without downloading them.

    Optional: Pass portfolio_id or user_id to also fetch available tickers.
    - portfolio_id: Returns tickers from a specific portfolio
    - user_id: Returns all unique tickers from all user's portfolios

    This allows the UI to show ticker selection options alongside file browsing.
    """
    integration = IntegrationService.get_integration(db, payload.integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    
    try:
        print(f"\n=== Browse Files Debug ===")
        print(f"Integration ID: {payload.integration_id}")
        print(f"Vendor: {integration.vendor}")
        print(f"URL: {integration.url}")
        print(f"Path: {payload.path}")
        print(f"Credentials keys: {list(integration.credentials.keys())}")
        
        connector = BaseConnector.get_connector(
            vendor=integration.vendor,
            credentials=integration.credentials,
            url=integration.url
        )
        
        print(f"Connector created: {type(connector).__name__}")
        
        files = connector.list_files(
            path=payload.path,
            search_query=payload.search_query
        )
        
        print(f"Files retrieved: {len(files) if files else 0}")
        
        # Ensure files is a list
        if files is None:
            files = []
        
        # Convert RemoteFile objects to schema objects
        file_dicts = []
        for f in files:
            try:
                file_dict = RemoteFile(
                    name=f.name,
                    path=f.path,
                    size=f.size,
                    last_modified=f.last_modified,
                    mime_type=f.mime_type,
                    is_directory=f.is_directory
                )
                file_dicts.append(file_dict)
            except Exception as e:
                print(f"Error converting file {getattr(f, 'name', 'unknown')}: {e}")
                continue
        
        print(f"Files converted: {len(file_dicts)}")
        print("=== End Debug ===\n")

        # Fetch available tickers from portfolio if provided
        available_tickers = None
        portfolio_name = None
        portfolio_id_used = None

        if payload.portfolio_id:
            portfolio = PortfolioService.get_portfolio(db, payload.portfolio_id)
            if portfolio:
                # company_names contains the tickers (normalized to lowercase)
                available_tickers = [ticker.upper() for ticker in portfolio.company_names]
                portfolio_name = portfolio.name
                portfolio_id_used = portfolio.id
                print(f"Loaded {len(available_tickers)} tickers from portfolio '{portfolio_name}'")
        elif payload.user_id:
            # Get all portfolios for the user and collect unique tickers
            portfolios = PortfolioService.get_user_portfolios(db, payload.user_id)
            if portfolios:
                # Collect all unique tickers from all portfolios
                ticker_set = set()
                for portfolio in portfolios:
                    ticker_set.update(portfolio.company_names)
                available_tickers = sorted([ticker.upper() for ticker in ticker_set])
                print(f"Loaded {len(available_tickers)} unique tickers from {len(portfolios)} portfolios")

        response = BrowseFilesResponse(
            integration_id=payload.integration_id,
            vendor=integration.vendor,
            path=payload.path or "/",
            files=file_dicts,
            total_count=len(file_dicts),
            available_tickers=available_tickers,
            portfolio_id=portfolio_id_used,
            portfolio_name=portfolio_name
        )

        return response
    
    except Exception as e:
        import traceback
        error_detail = f"Failed to browse files: {str(e)}\n{traceback.format_exc()}"
        print(f"\n=== Browse Error ===")
        print(error_detail)
        print("=== End Error ===\n")
        raise HTTPException(status_code=500, detail=f"Failed to browse files: {str(e)}")


# ========== File Import Endpoints ==========

@router.post("/import", response_model=FileImportResponse)
def import_files(
    payload: FileImportRequest,
    db: Session = Depends(get_db_session)
):
    """
    Import and ingest files from an integration

    This endpoint:
    1. Downloads files from the connected data source
    2. Processes them (PDF extraction, image analysis, etc.)
    3. Ingests them into the vector database for RAG under the specified ticker collection

    Note: All files in a single import request will be ingested to the same ticker collection.
    """
    try:
        # Validate ticker format (should be uppercase alphanumeric)
        ticker = payload.ticker.upper().strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="Ticker symbol is required")

        results = FileImportService.import_files(
            db=db,
            integration_id=payload.integration_id,
            file_paths=payload.file_paths,
            ticker=ticker
        )

        summary = FileImportService.get_import_summary(results)

        return FileImportResponse(
            integration_id=payload.integration_id,
            total_files=summary["total_files"],
            successful=summary["successful"],
            failed=summary["failed"],
            file_results=summary["file_results"]
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to import files: {str(e)}")
