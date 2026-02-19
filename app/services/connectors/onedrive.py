"""
OneDrive connector implementation using Microsoft Graph API
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os
from .base import BaseConnector, RemoteFile


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

            print(f"\n[OneDrive] Starting file listing...")
            print(f"[OneDrive] Path: {path}, Search: {search_query}")

            # Get authentication token
            try:
                token = self._get_access_token()
                print(f"[OneDrive] Access token obtained")
            except Exception as e:
                print(f"[OneDrive] Failed to get access token: {e}")
                raise Exception(f"Authentication failed: {str(e)}")

            # Get drive ID
            try:
                drive_id = self._get_drive_id()
                print(f"[OneDrive] Drive ID: {drive_id}")
            except Exception as e:
                print(f"[OneDrive] Failed to get drive ID: {e}")
                raise Exception(f"Failed to access OneDrive: {str(e)}")

            headers = {'Authorization': f'Bearer {token}'}

            # Determine the folder to list
            folder_path = path or self.folder_path
            print(f"[OneDrive] Folder path: {folder_path}")

            # Build the API URL
            if not folder_path or folder_path == "/" or not folder_path.strip():
                # List from root
                items_url = f"https://graph.microsoft.com/v1.0/me/drive/root/children"
                print(f"[OneDrive] Listing root directory")
            else:
                # List from specific folder path
                # Path format: "folder_name" or "folder_name/subfolder_name"
                items_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder_path}:/children"
                print(f"[OneDrive] Listing folder: {folder_path}")

            print(f"[OneDrive] API URL: {items_url}")

            try:
                response = requests.get(items_url, headers=headers)
                response.raise_for_status()
                items = response.json().get('value', [])
                print(f"[OneDrive] Found {len(items)} item(s)")
            except requests.exceptions.HTTPError as e:
                error_detail = ""
                try:
                    error_detail = response.json()
                except:
                    error_detail = response.text
                print(f"[OneDrive] HTTP Error: {e}, Response: {error_detail}")
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
                    print(f"[OneDrive] Error processing item {item.get('name', 'unknown')}: {e}")
                    continue

            print(f"[OneDrive] Returning {len(remote_files)} file(s)")
            return remote_files

        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            import traceback
            print(f"[OneDrive] Exception: {e}")
            print(traceback.format_exc())
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
                print(f"[OneDrive] HTTP Error: {e}, Response: {error_detail}")
                raise Exception(f"Failed to download file: {str(e)}")

        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to download OneDrive file {file_path}: {str(e)}")
