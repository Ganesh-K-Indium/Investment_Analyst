"""
Azure Blob Storage connector implementation
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os
from .base import BaseConnector, RemoteFile


class AzureBlobConnector(BaseConnector):
    """Connector for Azure Blob Storage"""
    
    def __init__(self, credentials: Dict[str, str], url: Optional[str] = None):
        super().__init__(credentials, url)
        self.account_url = url
        self.account_name = credentials.get("account_name")
        self.account_key = credentials.get("account_key")
        self.sas_token = credentials.get("sas_token")
        self.container_name = credentials.get("folder_path", "documents")
    
    def _get_blob_service_client(self):
        """Get Azure Blob Service Client"""
        try:
            from azure.storage.blob import BlobServiceClient
            
            credential = self.account_key or self.sas_token
            return BlobServiceClient(account_url=self.account_url, credential=credential)
        
        except ImportError:
            raise ImportError("Please install azure-storage-blob: pip install azure-storage-blob")
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test Azure Blob Storage connection"""
        try:
            if not self.account_url or not (self.account_key or self.sas_token):
                return False, "Missing required credentials (account_url and account_key/sas_token)"
            
            blob_service_client = self._get_blob_service_client()
            
            # Try to list containers
            containers = list(blob_service_client.list_containers(max_results=1))
            
            return True, f"Azure Blob Storage connection successful. Found {len(containers)} container(s)."
        
        except ImportError as e:
            return False, f"Missing dependencies: {str(e)}"
        except Exception as e:
            return False, f"Azure Blob Storage connection failed: {str(e)}"
    
    def list_files(
        self, 
        path: Optional[str] = None, 
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """List files from Azure Blob Storage"""
        try:
            blob_service_client = self._get_blob_service_client()
            container_client = blob_service_client.get_container_client(self.container_name)
            
            # List blobs with optional prefix
            prefix = path.strip('/') + '/' if path else None
            blobs = container_client.list_blobs(name_starts_with=prefix)
            
            remote_files = []
            seen_folders = set()
            
            for blob in blobs:
                blob_name = blob.name
                
                # Apply search filter
                if search_query and search_query.lower() not in blob_name.lower():
                    continue
                
                # Check if this is a "folder" (blob name contains /)
                if prefix and blob_name.startswith(prefix):
                    relative_name = blob_name[len(prefix):]
                else:
                    relative_name = blob_name
                
                # If there's a slash, it's in a subfolder
                if '/' in relative_name:
                    folder_name = relative_name.split('/')[0]
                    if folder_name not in seen_folders:
                        seen_folders.add(folder_name)
                        remote_files.append(RemoteFile(
                            name=folder_name,
                            path=f"{prefix or ''}{folder_name}/",
                            size=None,
                            last_modified=None,
                            mime_type=None,
                            is_directory=True
                        ))
                else:
                    # Regular file
                    remote_files.append(RemoteFile(
                        name=os.path.basename(blob_name),
                        path=blob_name,
                        size=blob.size,
                        last_modified=blob.last_modified,
                        mime_type=blob.content_settings.content_type if blob.content_settings else None,
                        is_directory=False
                    ))
            
            return remote_files
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to list Azure Blob Storage files: {str(e)}")
    
    def download_file(self, file_path: str) -> str:
        """Download a file from Azure Blob Storage"""
        try:
            blob_service_client = self._get_blob_service_client()
            blob_client = blob_service_client.get_blob_client(
                container=self.container_name,
                blob=file_path
            )
            
            filename = os.path.basename(file_path)
            temp_dir = tempfile.gettempdir()
            local_path = os.path.join(temp_dir, f"azure_{filename}")
            
            # Download blob
            with open(local_path, "wb") as download_file:
                download_stream = blob_client.download_blob()
                download_file.write(download_stream.readall())
            
            return local_path
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to download Azure blob {file_path}: {str(e)}")
