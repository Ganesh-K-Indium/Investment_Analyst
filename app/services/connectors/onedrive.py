"""
OneDrive connector implementation using Microsoft Graph API
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os
import logging
from .base import BaseConnector, RemoteFile

logger = logging.getLogger("connectors.onedrive")


class OneDriveConnector(BaseConnector):
    """Connector for Microsoft OneDrive using Graph API"""

    def __init__(self, credentials: Dict[str, str], url: Optional[str] = None):
        super().__init__(credentials, url)
        self.tenant_id = credentials.get("tenant_id")
        self.client_id = credentials.get("client_id")
        self.client_secret = credentials.get("client_secret")
        self.folder_path = credentials.get("folder_path", "")  # Optional starting folder
        self.access_token = None
        self.drive_id = None

    def _get_access_token(self) -> str:
        """Get access token for Microsoft Graph API"""
        if self.access_token:
            return self.access_token

        try:
            import requests

            token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': 'https://graph.microsoft.com/.default',
                'grant_type': 'client_credentials'
            }

            response = requests.post(token_url, data=data)
            response.raise_for_status()

            self.access_token = response.json()['access_token']
            return self.access_token

        except ImportError:
            raise ImportError("Please install requests: pip install requests")
        except Exception as e:
            raise Exception(f"Failed to get access token: {str(e)}")

    def _get_drive_id(self) -> str:
        """Get the default OneDrive drive ID for the authenticated user"""
        if self.drive_id:
            return self.drive_id

        try:
            import requests

            token = self._get_access_token()
            headers = {'Authorization': f'Bearer {token}'}

            # Get the user's default drive (OneDrive)
            url = "https://graph.microsoft.com/v1.0/me/drive"
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            self.drive_id = response.json()['id']
            return self.drive_id

        except Exception as e:
            raise Exception(f"Failed to get drive ID: {str(e)}")

    def test_connection(self) -> Tuple[bool, str]:
        """Test OneDrive connection"""
        try:
            # Validate credentials
            if not all([self.tenant_id, self.client_id, self.client_secret]):
                return False, "Missing required credentials (tenant_id, client_id, client_secret)"

            # Try to get access token and drive ID
            self._get_access_token()
            drive_id = self._get_drive_id()

            return True, f"OneDrive connection successful. Drive ID: {drive_id[:20]}..."

        except ImportError as e:
            return False, f"Missing dependencies: {str(e)}"
        except Exception as e:
            return False, f"OneDrive connection failed: {str(e)}"

    def list_files(
        self,
        path: Optional[str] = None,
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """List files from OneDrive"""
        try:
            import requests

            logger.info("[OneDrive] Starting file listing...")
            logger.info("[OneDrive] Path: %s, Search: %s", path, search_query)

            # Get authentication token
            try:
                token = self._get_access_token()
                logger.info("[OneDrive] Access token obtained")
            except Exception as e:
                logger.error("[OneDrive] Failed to get access token: %s", e)
                raise Exception(f"Authentication failed: {str(e)}")

            # Get drive ID
            try:
                drive_id = self._get_drive_id()
                logger.info("[OneDrive] Drive ID: %s", drive_id)
            except Exception as e:
                logger.error("[OneDrive] Failed to get drive ID: %s", e)
                raise Exception(f"Failed to access OneDrive: {str(e)}")

            headers = {'Authorization': f'Bearer {token}'}

            # Determine the folder to list
            folder_path = path or self.folder_path
            logger.info("[OneDrive] Folder path: %s", folder_path)

            # Build the API URL
            if not folder_path or folder_path == "/" or not folder_path.strip():
                # List from root
                items_url = f"https://graph.microsoft.com/v1.0/me/drive/root/children"
                logger.info("[OneDrive] Listing root directory")
            else:
                # List from specific folder path
                # Path format: "folder_name" or "folder_name/subfolder_name"
                items_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder_path}:/children"
                logger.info("[OneDrive] Listing folder: %s", folder_path)

            logger.info("[OneDrive] API URL: %s", items_url)

            try:
                response = requests.get(items_url, headers=headers)
                response.raise_for_status()
                items = response.json().get('value', [])
                logger.info("[OneDrive] Found %d item(s)", len(items))
            except requests.exceptions.HTTPError as e:
                error_detail = ""
                try:
                    error_detail = response.json()
                except:
                    error_detail = response.text
                logger.error("[OneDrive] HTTP Error: %s, Response: %s", e, error_detail)
                raise Exception(f"Failed to list files: {str(e)}")

            # Convert to RemoteFile objects
            remote_files = []
            for item in items:
                try:
                    # Check if it's a folder
                    is_folder = 'folder' in item

                    # Parse modified time
                    modified_time = None
                    if 'lastModifiedDateTime' in item:
                        try:
                            modified_time = datetime.fromisoformat(item['lastModifiedDateTime'].replace('Z', '+00:00'))
                        except:
                            pass

                    # Construct path for navigation/download
                    # For folders: use path string for navigation
                    # For files: use ID for download
                    if is_folder:
                        # Construct navigable path string
                        if not folder_path or folder_path == "/":
                            file_path = item['name']
                        else:
                            file_path = f"{folder_path}/{item['name']}"
                    else:
                        file_path = item.get('id')

                    remote_file = RemoteFile(
                        name=item['name'],
                        path=file_path,
                        size=item.get('size'),
                        last_modified=modified_time,
                        mime_type=item.get('file', {}).get('mimeType') if not is_folder else None,
                        is_directory=is_folder
                    )

                    # Apply search filter
                    if search_query:
                        if search_query.lower() in item['name'].lower():
                            remote_files.append(remote_file)
                    else:
                        remote_files.append(remote_file)

                except Exception as e:
                    logger.error("[OneDrive] Error processing item %s: %s", item.get('name', 'unknown'), e)
                    continue

            logger.info("[OneDrive] Returning %d file(s)", len(remote_files))
            return remote_files

        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            import traceback
            logger.error("[OneDrive] Exception: %s", e)
            logger.error(traceback.format_exc())
            raise Exception(f"Failed to list OneDrive files: {str(e)}")

    def download_file(self, file_path: str) -> str:
        """Download a file from OneDrive"""
        try:
            import requests

            token = self._get_access_token()
            drive_id = self._get_drive_id()

            headers = {'Authorization': f'Bearer {token}'}

            # file_path is the item ID from the API
            file_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_path}"

            try:
                file_response = requests.get(file_url, headers=headers)
                file_response.raise_for_status()

                file_data = file_response.json()
                download_url = file_data.get('@microsoft.graph.downloadUrl')
                filename = file_data.get('name', 'downloaded_file')

                if not download_url:
                    raise Exception("Could not get download URL")

                # Download the file
                temp_dir = tempfile.gettempdir()
                local_path = os.path.join(temp_dir, f"onedrive_{filename}")

                download_response = requests.get(download_url)
                download_response.raise_for_status()

                with open(local_path, 'wb') as f:
                    f.write(download_response.content)

                return local_path

            except requests.exceptions.HTTPError as e:
                error_detail = ""
                try:
                    error_detail = file_response.json()
                except:
                    error_detail = file_response.text
                logger.error("[OneDrive] HTTP Error: %s, Response: %s", e, error_detail)
                raise Exception(f"Failed to download file: {str(e)}")

        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to download OneDrive file {file_path}: {str(e)}")
