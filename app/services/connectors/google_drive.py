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
            results = service.files().list(
                pageSize=1,
                fields="files(id, name)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True
            ).execute()
            
            return True, f"Google Drive connection successful. Service initialized."
        
        except ImportError as e:
            return False, f"Missing dependencies: {str(e)}"
        except Exception as e:
            return False, f"Google Drive connection failed: {str(e)}"
    
    def _is_shared_drive(self, service, drive_id: str) -> bool:
        """Return True if drive_id is a Shared Drive root (Team Drive)."""
        try:
            service.drives().get(driveId=drive_id, fields='id').execute()
            return True
        except Exception:
            return False

    def _build_files_list(
        self,
        service,
        q: str,
        corpora: Optional[str] = None,
        drive_id: Optional[str] = None,
        extra_fields: str = "",
    ) -> List[dict]:
        """Execute a files.list call and return the raw file dicts."""
        base_fields = "files(id, name, size, modifiedTime, mimeType"
        if extra_fields:
            base_fields += f", {extra_fields}"
        base_fields += ")"

        kwargs = dict(
            q=q,
            pageSize=200,
            fields=base_fields,
            orderBy="name",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        )
        if corpora:
            kwargs["corpora"] = corpora
        if drive_id:
            kwargs["driveId"] = drive_id
        return service.files().list(**kwargs).execute().get("files", [])

    def list_files(
        self,
        path: Optional[str] = None,
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """List files from Google Drive, handling My Drive, Shared Drives, and shortcuts."""
        try:
            service = self._get_service()

            folder_id = path or self.folder_id or 'root'
            name_filter = f" and name contains '{search_query}'" if search_query else ""

            # ------------------------------------------------------------------
            # Step 1: For non-root paths, fetch the item's own metadata so we
            # know (a) whether it is a shortcut and (b) which Shared Drive it
            # lives in (the `driveId` field).  This is the only reliable way to
            # choose the correct corpora/driveId combination for the children
            # listing below.
            # ------------------------------------------------------------------
            target_drive_id = None
            if folder_id != 'root':
                try:
                    meta = service.files().get(
                        fileId=folder_id,
                        fields='id,name,mimeType,shortcutDetails,driveId',
                        supportsAllDrives=True,
                    ).execute()
                    print(f"[GoogleDrive] Target metadata: name='{meta.get('name')}' "
                          f"mimeType={meta.get('mimeType')} driveId={meta.get('driveId')}")

                    # Follow shortcuts to their actual target folder
                    if meta.get('mimeType') == 'application/vnd.google-apps.shortcut':
                        target_id = (meta.get('shortcutDetails') or {}).get('targetId')
                        if target_id:
                            print(f"[GoogleDrive] Shortcut detected — following to target {target_id}")
                            folder_id = target_id
                            meta = service.files().get(
                                fileId=folder_id,
                                fields='id,name,mimeType,driveId',
                                supportsAllDrives=True,
                            ).execute()
                            print(f"[GoogleDrive] Target after shortcut: name='{meta.get('name')}' "
                                  f"mimeType={meta.get('mimeType')} driveId={meta.get('driveId')}")

                    # driveId is populated only when the item lives in a Shared Drive
                    target_drive_id = meta.get('driveId') or None

                except Exception as e:
                    print(f"[GoogleDrive] Could not fetch folder metadata: {e}")

            # ------------------------------------------------------------------
            # Step 2: List children using the right corpora/driveId.
            # ------------------------------------------------------------------
            if folder_id == 'root':
                # At root we only show FOLDERS that have been shared with the
                # service account.  Showing individual files here is misleading
                # because they appear at "root" only because they were once shared
                # directly with the service account (not through a folder), and
                # they clutter the navigation.  Users navigate into a folder and
                # select files there.
                #
                # If a search_query is present we relax the folder-only filter so
                # users can find individual files too.
                if search_query:
                    mime_filter = ""
                else:
                    mime_filter = " and mimeType='application/vnd.google-apps.folder'"
                files = self._build_files_list(
                    service,
                    q=f"sharedWithMe=true and trashed=false{mime_filter}{name_filter}",
                )
                print(f"[GoogleDrive] Root (sharedWithMe folders): {len(files)} items")

            elif target_drive_id:
                # Folder is inside a Shared Drive
                print(f"[GoogleDrive] Listing inside Shared Drive {target_drive_id}")
                files = self._build_files_list(
                    service,
                    q=f"'{folder_id}' in parents and trashed=false{name_filter}",
                    corpora="drive",
                    drive_id=target_drive_id,
                )

            else:
                # Regular My Drive folder (or folder shared with service account)
                files = self._build_files_list(
                    service,
                    q=f"'{folder_id}' in parents and trashed=false{name_filter}",
                )

            # ------------------------------------------------------------------
            # Step 3: Broad-search fallback.
            # If in-parents returned nothing, fetch ALL files the service account
            # can access (own + sharedWithMe) and filter by parent client-side.
            # This catches files that are inside a shared folder but weren't
            # explicitly shared with the service account individually.
            # ------------------------------------------------------------------
            if not files and folder_id != 'root':
                print(f"[GoogleDrive] in-parents returned 0 — trying broad search with client-side parent filter")
                broad = self._build_files_list(
                    service,
                    q=f"trashed=false{name_filter}",
                    extra_fields="parents",
                )
                files = [f for f in broad if folder_id in (f.get("parents") or [])]
                print(f"[GoogleDrive] Broad search: {len(broad)} total accessible, "
                      f"{len(files)} with parent={folder_id}")

            # ------------------------------------------------------------------
            # Step 4: Shared Drive root fallback (drives().get() probe).
            # ------------------------------------------------------------------
            if not files and folder_id != 'root' and not target_drive_id:
                print(f"[GoogleDrive] Probing {folder_id} as Shared Drive root")
                if self._is_shared_drive(service, folder_id):
                    print(f"[GoogleDrive] Confirmed Shared Drive root")
                    files = self._build_files_list(
                        service,
                        q=f"trashed=false{name_filter}",
                        corpora="drive",
                        drive_id=folder_id,
                    )

            print(f"[GoogleDrive] Total files found: {len(files)}")

            # Convert to RemoteFile objects
            remote_files = []
            for file in files:
                is_folder = file['mimeType'] == 'application/vnd.google-apps.folder'

                modified_time = None
                if 'modifiedTime' in file:
                    try:
                        modified_time = datetime.fromisoformat(file['modifiedTime'].replace('Z', '+00:00'))
                    except Exception:
                        pass

                remote_files.append(RemoteFile(
                    name=file['name'],
                    path=file['id'],
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
            
            # Get file metadata — supportsAllDrives needed for Shared Drive files
            file_metadata = service.files().get(
                fileId=file_id,
                fields='name,mimeType',
                supportsAllDrives=True
            ).execute()
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
                # Regular file download — supportsAllDrives for Shared Drive files
                request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
            
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
