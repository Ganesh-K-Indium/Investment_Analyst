"""
Service layer for integration management
"""
from sqlalchemy.orm import Session
from app.database.models import Integration
from typing import List, Optional, Dict
from datetime import datetime


class IntegrationService:
    """Business logic for integration operations"""
    
    @staticmethod
    def create_integration(
        db: Session,
        user_id: str,
        vendor: str,
        name: str,
        credentials: Dict[str, str],
        url: Optional[str] = None,
        description: Optional[str] = None
    ) -> Integration:
        """Create a new integration"""
        integration = Integration(
            user_id=user_id,
            vendor=vendor,
            name=name,
            url=url,
            credentials=credentials,
            description=description,
            status="active"
        )
        db.add(integration)
        db.commit()
        db.refresh(integration)
        return integration
    
    @staticmethod
    def get_integration(db: Session, integration_id: int) -> Optional[Integration]:
        """Get integration by ID"""
        return db.query(Integration).filter(Integration.id == integration_id).first()
    
    @staticmethod
    def get_user_integrations(
        db: Session, 
        user_id: str,
        vendor: Optional[str] = None
    ) -> List[Integration]:
        """Get all integrations for a user, optionally filtered by vendor"""
        query = db.query(Integration).filter(Integration.user_id == user_id)
        if vendor:
            query = query.filter(Integration.vendor == vendor)
        return query.order_by(Integration.created_at.desc()).all()
    
    @staticmethod
    def update_integration(
        db: Session,
        integration_id: int,
        name: Optional[str] = None,
        url: Optional[str] = None,
        credentials: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
        status: Optional[str] = None
    ) -> Optional[Integration]:
        """Update an existing integration"""
        integration = db.query(Integration).filter(Integration.id == integration_id).first()
        if not integration:
            return None
        
        if name is not None:
            integration.name = name
        if url is not None:
            integration.url = url
        if credentials is not None:
            integration.credentials = credentials
        if description is not None:
            integration.description = description
        if status is not None:
            integration.status = status
        
        integration.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(integration)
        return integration
    
    @staticmethod
    def delete_integration(db: Session, integration_id: int) -> bool:
        """Delete an integration"""
        integration = db.query(Integration).filter(Integration.id == integration_id).first()
        if not integration:
            return False
        
        db.delete(integration)
        db.commit()
        return True
    
    @staticmethod
    def disconnect_integration(db: Session, integration_id: int) -> Optional[Integration]:
        """Mark an integration as disconnected"""
        return IntegrationService.update_integration(
            db, 
            integration_id, 
            status="disconnected"
        )
    
    @staticmethod
    def update_last_sync(db: Session, integration_id: int) -> Optional[Integration]:
        """Update the last sync timestamp"""
        integration = db.query(Integration).filter(Integration.id == integration_id).first()
        if not integration:
            return None
        
        integration.last_sync = datetime.utcnow()
        db.commit()
        db.refresh(integration)
        return integration
    
    @staticmethod
    def mask_credentials(credentials: Dict[str, str]) -> Dict[str, str]:
        """Mask sensitive credential fields for display"""
        masked = {}
        sensitive_fields = {"client_secret", "password", "secret_key", "access_token", "refresh_token"}
        
        for key, value in credentials.items():
            if any(sensitive in key.lower() for sensitive in sensitive_fields):
                masked[key] = "••••••••" if value else ""
            else:
                masked[key] = value
        
        return masked
