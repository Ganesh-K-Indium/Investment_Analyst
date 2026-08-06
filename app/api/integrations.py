"""
Integration management and file import endpoints
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.database.connection import get_db_session
from app.services.integration import IntegrationService
from app.services.connectors.base import BaseConnector
from app.services.file_import import FileImportService
from app.services.portfolio import PortfolioService
from app.database.models import User
from app.auth.deps import get_current_user, verify_user_id_matches, verify_owner
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

logger = logging.getLogger("api.integrations")
router = APIRouter(prefix="/integrations", tags=["Integrations"])


# ========== Integration Management Endpoints ==========

@router.post("", response_model=IntegrationResponse)
async def create_integration(
    payload: IntegrationCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new data source integration

    Supported vendors:
    - sharepoint: SharePoint Online
    - google_drive: Google Drive
    - onedrive: Microsoft OneDrive
    - confluence: Atlassian Confluence
    - azure_blob: Azure Blob Storage
    - aws_s3: AWS S3
    - sftp: SFTP Server
    """
    verify_user_id_matches(payload.user_id, current_user)
    try:
        integration = await IntegrationService.create_integration(
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
async def get_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get integration by ID"""
    integration = await IntegrationService.get_integration(db, integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    verify_owner(integration.user_id, current_user)

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
async def get_user_integrations(
    user_id: str,
    vendor: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get all integrations for a user, optionally filtered by vendor"""
    verify_user_id_matches(user_id, current_user)
    integrations = await IntegrationService.get_user_integrations(db, user_id, vendor)
    
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
async def update_integration(
    integration_id: int,
    payload: IntegrationUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Update an existing integration"""
    existing = await IntegrationService.get_integration(db, integration_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    verify_owner(existing.user_id, current_user)

    integration = await IntegrationService.update_integration(
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
async def delete_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Delete an integration"""
    existing = await IntegrationService.get_integration(db, integration_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    verify_owner(existing.user_id, current_user)

    success = await IntegrationService.delete_integration(db, integration_id)
    if not success:
        raise HTTPException(status_code=404, detail="Integration not found")

    return {"message": "Integration deleted successfully"}


@router.post("/{integration_id}/disconnect")
async def disconnect_integration(
    integration_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Mark an integration as disconnected"""
    existing = await IntegrationService.get_integration(db, integration_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    verify_owner(existing.user_id, current_user)

    integration = await IntegrationService.disconnect_integration(db, integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")

    return {"message": "Integration disconnected successfully", "status": integration.status}


@router.post("/{integration_id}/test", response_model=ConnectionTestResponse)
async def test_integration_connection(
    integration_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Test connection to an integration"""
    integration = await IntegrationService.get_integration(db, integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    verify_owner(integration.user_id, current_user)

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
async def browse_integration_files(
    payload: BrowseFilesRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Browse files from an integration

    This endpoint lists files from the connected data source without downloading them.

    Optional: Pass portfolio_id or user_id to also fetch available tickers.
    - portfolio_id: Returns tickers from a specific portfolio
    - user_id: Returns all unique tickers from all user's portfolios

    This allows the UI to show ticker selection options alongside file browsing.
    """
    integration = await IntegrationService.get_integration(db, payload.integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    verify_owner(integration.user_id, current_user)

    if payload.user_id:
        verify_user_id_matches(payload.user_id, current_user)

    if payload.portfolio_id:
        portfolio_check = await PortfolioService.get_portfolio(db, payload.portfolio_id)
        if portfolio_check:
            verify_owner(portfolio_check.user_id, current_user)

    try:
        logger.info("=== Browse Files Debug ===")
        logger.info("Integration ID: %s", payload.integration_id)
        logger.info("Vendor: %s", integration.vendor)
        logger.info("URL: %s", integration.url)
        logger.info("Path: %s", payload.path)
        logger.info("Credentials keys: %s", list(integration.credentials.keys()))

        connector = BaseConnector.get_connector(
            vendor=integration.vendor,
            credentials=integration.credentials,
            url=integration.url
        )

        logger.info("Connector created: %s", type(connector).__name__)

        files = connector.list_files(
            path=payload.path,
            search_query=payload.search_query
        )

        logger.info("Files retrieved: %d", len(files) if files else 0)

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
                logger.error("Error converting file %s: %s", getattr(f, 'name', 'unknown'), e)
                continue

        logger.info("Files converted: %d", len(file_dicts))
        logger.info("=== End Debug ===")

        # Fetch available tickers from portfolio if provided
        available_tickers = None
        portfolio_name = None
        portfolio_id_used = None

        if payload.portfolio_id:
            portfolio = await PortfolioService.get_portfolio(db, payload.portfolio_id)
            if portfolio:
                # company_names contains the tickers (normalized to lowercase)
                available_tickers = [ticker.upper() for ticker in portfolio.company_names]
                portfolio_name = portfolio.name
                portfolio_id_used = portfolio.id
                logger.info("Loaded %d tickers from portfolio '%s'", len(available_tickers), portfolio_name)
        elif payload.user_id:
            # Get all portfolios for the user and collect unique tickers
            portfolios = await PortfolioService.get_user_portfolios(db, payload.user_id)
            if portfolios:
                # Collect all unique tickers from all portfolios
                ticker_set = set()
                for portfolio in portfolios:
                    ticker_set.update(portfolio.company_names)
                available_tickers = sorted([ticker.upper() for ticker in ticker_set])
                logger.info("Loaded %d unique tickers from %d portfolios", len(available_tickers), len(portfolios))

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
        logger.error("=== Browse Error ===")
        logger.error(error_detail)
        logger.error("=== End Error ===")
        raise HTTPException(status_code=500, detail=f"Failed to browse files: {str(e)}")


# ========== File Import Endpoints ==========

@router.post("/import", response_model=FileImportResponse)
async def import_files(
    payload: FileImportRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Import and ingest files from an integration

    This endpoint:
    1. Downloads files from the connected data source
    2. Processes them (PDF extraction, image analysis, etc.)
    3. Ingests them into the vector database for RAG under the specified ticker collection

    Note: All files in a single import request will be ingested to the same ticker collection.
    """
    integration = await IntegrationService.get_integration(db, payload.integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    verify_owner(integration.user_id, current_user)

    if payload.portfolio_id:
        portfolio_check = await PortfolioService.get_portfolio(db, payload.portfolio_id)
        if portfolio_check:
            verify_owner(portfolio_check.user_id, current_user)

    try:
        # Validate ticker format (should be uppercase alphanumeric)
        ticker = payload.ticker.upper().strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="Ticker symbol is required")

        results = await FileImportService.import_files(
            db=db,
            integration_id=payload.integration_id,
            file_paths=payload.file_paths,
            ticker=ticker,
            filing_type=payload.filing_type,
            period_end_date=payload.period_end_date,
            year=payload.year,
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
