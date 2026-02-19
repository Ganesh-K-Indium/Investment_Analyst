"""
Base connector class for data source integrations
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os


class RemoteFile:
    """Represents a file from a remote data source"""
    def __init__(
        self,
        name: str,
        path: str,
        size: Optional[int] = None,
        last_modified: Optional[datetime] = None,
        mime_type: Optional[str] = None,
        is_directory: bool = False
    ):
        self.name = name
        self.path = path
        self.size = size
        self.last_modified = last_modified
        self.mime_type = mime_type
        self.is_directory = is_directory
    
    def to_dict(self):
        """Convert to dictionary for API response"""
        return {
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            "mime_type": self.mime_type,
            "is_directory": self.is_directory
        }


class BaseConnector(ABC):
    """Abstract base class for all data source connectors"""
    
    def __init__(self, credentials: Dict[str, str], url: Optional[str] = None):
        """
        Initialize connector with credentials
        
        Args:
            credentials: Dictionary containing authentication credentials
            url: Optional connection URL
        """
        self.credentials = credentials
        self.url = url
    
    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test the connection to the data source
        
        Returns:
            Tuple[bool, str]: (success, message)
        """
        pass
    
    @abstractmethod
    def list_files(
        self, 
        path: Optional[str] = None, 
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """
        List files from the data source
        
        Args:
            path: Path to list files from (default: root)
            search_query: Optional search query to filter files
        
        Returns:
            List[RemoteFile]: List of files
        """
        pass
    
    @abstractmethod
    def download_file(self, file_path: str) -> str:
        """
        Download a file from the data source to a temporary location
        
        Args:
            file_path: Path to the file in the remote source
        
        Returns:
            str: Local path to the downloaded file
        """
        pass
    
    def download_multiple_files(self, file_paths: List[str]) -> List[Tuple[str, str]]:
        """
        Download multiple files from the data source
        
        Args:
            file_paths: List of file paths to download
        
        Returns:
            List[Tuple[str, str]]: List of (remote_path, local_path) tuples
        """
        results = []
        for file_path in file_paths:
            try:
                local_path = self.download_file(file_path)
                results.append((file_path, local_path))
            except Exception as e:
                print(f"Failed to download {file_path}: {e}")
                results.append((file_path, None))
        return results
    
    @staticmethod
    def get_connector(vendor: str, credentials: Dict[str, str], url: Optional[str] = None):
        """
        Factory method to get the appropriate connector based on vendor

        Args:
            vendor: Vendor type (sharepoint, google_drive, onedrive, confluence, azure_blob, aws_s3, sftp)
            credentials: Authentication credentials
            url: Optional connection URL

        Returns:
            BaseConnector: Appropriate connector instance
        """
        from .sharepoint import SharePointConnector
        from .google_drive import GoogleDriveConnector
        from .onedrive import OneDriveConnector
        from .confluence import ConfluenceConnector
        from .azure_blob import AzureBlobConnector
        from .aws_s3 import AWSS3Connector
        from .sftp import SFTPConnector

        connectors = {
            "sharepoint": SharePointConnector,
            "google_drive": GoogleDriveConnector,
            "onedrive": OneDriveConnector,
            "confluence": ConfluenceConnector,
            "azure_blob": AzureBlobConnector,
            "aws_s3": AWSS3Connector,
            "sftp": SFTPConnector
        }

        connector_class = connectors.get(vendor)
        if not connector_class:
            raise ValueError(f"Unknown vendor: {vendor}")

        return connector_class(credentials, url)
