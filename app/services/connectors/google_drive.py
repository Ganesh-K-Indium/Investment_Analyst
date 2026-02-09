"""
Google Drive connector implementation
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os
import json
from .base import BaseConnector, RemoteFile


class GoogleDriveConnector(BaseConnector):
    """Connector for Google Drive"""
    
    def __init__(self, credentials: Dict[str, str], url: Optional[str] = None):
        super().__init__(credentials, url)
        
        # Check if using service account credentials (JSON string or dict)
        self.service_account_info = credentials.get("service_account_json")
        if self.service_account_info and isinstance(self.service_account_info, str):
            try:
                self.service_account_info = json.loads(self.service_account_info)
            except json.JSONDecodeError:
                self.service_account_info = None
        
        # Fallback to OAuth2 credentials
        self.client_id = credentials.get("client_id")
        self.client_secret = credentials.get("client_secret")
        self.refresh_token = credentials.get("refresh_token")
        
        self.folder_id = credentials.get("folder_path") or credentials.get("folder_id")  # Folder ID or 'root'
        self.service = None
    
    def _get_service(self):
        """Initialize and return Google Drive service"""
        if self.service:
            return self.service
        
        try:
            from googleapiclient.discovery import build
            from google.oauth2 import service_account
            from google.oauth2.credentials import Credentials
            
            # Use service account if available
            if self.service_account_info:
                credentials = service_account.Credentials.from_service_account_info(
                    self.service_account_info,
                    scopes=['https://www.googleapis.com/auth/drive.readonly']
                )
                self.service = build('drive', 'v3', credentials=credentials)
            # Otherwise use OAuth2
            elif self.refresh_token:
                credentials = Credentials(
                    token=None,
                    refresh_token=self.refresh_token,
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    token_uri='https://oauth2.googleapis.com/token'
                )
                self.service = build('drive', 'v3', credentials=credentials)
            else:
                raise ValueError("No valid credentials provided")
            
            return self.service
        
        except ImportError:
            raise ImportError("Please install google-api-python-client: pip install google-api-python-client google-auth")
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test Google Drive connection"""
        try:
            # Check credentials
            if not self.service_account_info and not (self.client_id and self.client_secret and self.refresh_token):
                return False, "Missing required credentials (service_account_json OR client_id+client_secret+refresh_token)"
            
            # Try to list files
            service = self._get_service()
            results = service.files().list(pageSize=1, fields="files(id, name)").execute()
            
            return True, f"Google Drive connection successful. Service initialized."
        
        except ImportError as e:
            return False, f"Missing dependencies: {str(e)}"
        except Exception as e:
            return False, f"Google Drive connection failed: {str(e)}"
    
    def list_files(
        self, 
        path: Optional[str] = None, 
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """List files from Google Drive"""
        try:
            service = self._get_service()
            
            # Build query
            folder_id = path or self.folder_id or 'root'
            
            # Query for files in the specified folder
            query_parts = [f"'{folder_id}' in parents", "trashed=false"]
            
            # Add search query if provided
            if search_query:
                query_parts.append(f"name contains '{search_query}'")
            
            query = " and ".join(query_parts)
            
            # Execute query
            results = service.files().list(
                q=query,
                pageSize=100,
                fields="files(id, name, size, modifiedTime, mimeType)",
                orderBy="name"
            ).execute()
            
            files = results.get('files', [])
            
            # Convert to RemoteFile objects
            remote_files = []
            for file in files:
                is_folder = file['mimeType'] == 'application/vnd.google-apps.folder'
                
                # Parse modified time
                modified_time = None
                if 'modifiedTime' in file:
                    try:
                        modified_time = datetime.fromisoformat(file['modifiedTime'].replace('Z', '+00:00'))
                    except:
                        pass
                
                remote_files.append(RemoteFile(
                    name=file['name'],
                    path=file['id'],  # Google Drive uses file IDs
                    size=int(file.get('size', 0)) if 'size' in file else None,
                    last_modified=modified_time,
                    mime_type=file['mimeType'],
                    is_directory=is_folder
                ))
            
            return remote_files
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to list Google Drive files: {str(e)}")
    
    def download_file(self, file_path: str) -> str:
        """Download a file from Google Drive"""
        try:
            from googleapiclient.http import MediaIoBaseDownload
            import io
            
            service = self._get_service()
            file_id = file_path  # In Google Drive, path is the file ID
            
            # Get file metadata to get the filename
            file_metadata = service.files().get(fileId=file_id, fields='name,mimeType').execute()
            filename = file_metadata['name']
            mime_type = file_metadata['mimeType']
            
            # Create temporary file
            temp_dir = tempfile.gettempdir()
            local_path = os.path.join(temp_dir, f"gdrive_{filename}")
            
            # Handle Google Workspace files (Docs, Sheets, etc.) - export them
            export_mime_types = {
                'application/vnd.google-apps.document': ('application/pdf', '.pdf'),
                'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
                'application/vnd.google-apps.presentation': ('application/pdf', '.pdf'),
            }
            
            if mime_type in export_mime_types:
                export_mime, ext = export_mime_types[mime_type]
                if not local_path.endswith(ext):
                    local_path += ext
                
                request = service.files().export_media(fileId=file_id, mimeType=export_mime)
            else:
                # Regular file download
                request = service.files().get_media(fileId=file_id)
            
            # Download file
            with open(local_path, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    if status:
                        print(f"Download progress: {int(status.progress() * 100)}%")
            
            return local_path
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to download Google Drive file {file_path}: {str(e)}")
