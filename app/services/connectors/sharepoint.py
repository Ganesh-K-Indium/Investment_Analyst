"""
SharePoint connector implementation
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os
from .base import BaseConnector, RemoteFile


class SharePointConnector(BaseConnector):
    """Connector for SharePoint Online using Microsoft Graph API"""
    
    def __init__(self, credentials: Dict[str, str], url: Optional[str] = None):
        super().__init__(credentials, url)
        self.site_url = url
        self.tenant_id = credentials.get("tenant_id")
        self.client_id = credentials.get("client_id")
        self.client_secret = credentials.get("client_secret")
        self.site_name = credentials.get("site_name")  # SharePoint site name
        self.folder_path = credentials.get("folder_path", "Documents")
        self.access_token = None
    
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
    
    def _get_site_id(self) -> str:
        """Get SharePoint site ID"""
        try:
            import requests
            
            token = self._get_access_token()
            headers = {'Authorization': f'Bearer {token}'}
            
            # Extract hostname and site path from URL
            # Example: https://company.sharepoint.com/sites/mysite
            url_parts = self.site_url.replace('https://', '').split('/')
            hostname = url_parts[0]
            site_path = '/'.join(url_parts[1:]) if len(url_parts) > 1 else ''
            
            # Get site ID
            url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:/{site_path}"
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            return response.json()['id']
        
        except Exception as e:
            raise Exception(f"Failed to get site ID: {str(e)}")
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test SharePoint connection"""
        try:
            # Validate credentials
            if not all([self.site_url, self.tenant_id, self.client_id, self.client_secret]):
                return False, "Missing required credentials (site_url, tenant_id, client_id, client_secret)"
            
            # Try to get access token and site ID
            self._get_access_token()
            site_id = self._get_site_id()
            
            return True, f"SharePoint connection successful. Site ID: {site_id[:20]}..."
        
        except ImportError as e:
            return False, f"Missing dependencies: {str(e)}"
        except Exception as e:
            return False, f"SharePoint connection failed: {str(e)}"
    
    def list_files(
        self, 
        path: Optional[str] = None, 
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """List files from SharePoint"""
        try:
            import requests
            
            print(f"\n[SharePoint] Starting file listing...")
            print(f"[SharePoint] Path: {path}, Search: {search_query}")
            
            # Get authentication token
            try:
                token = self._get_access_token()
                print(f"[SharePoint] Access token obtained")
            except Exception as e:
                print(f"[SharePoint] Failed to get access token: {e}")
                raise Exception(f"Authentication failed: {str(e)}")
            
            # Get site ID
            try:
                site_id = self._get_site_id()
                print(f"[SharePoint] Site ID: {site_id}")
            except Exception as e:
                print(f"[SharePoint] Failed to get site ID: {e}")
                raise Exception(f"Failed to access SharePoint site: {str(e)}")
            
            headers = {'Authorization': f'Bearer {token}'}
            
            # Get drive (document library)
            drive_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
            print(f"[SharePoint] Getting drives from: {drive_url}")
            
            try:
                drives_response = requests.get(drive_url, headers=headers)
                drives_response.raise_for_status()
                drives = drives_response.json().get('value', [])
                print(f"[SharePoint] Found {len(drives)} drive(s)")
            except Exception as e:
                print(f"[SharePoint] Failed to get drives: {e}")
                raise Exception(f"Failed to access document libraries: {str(e)}")
            
            if not drives:
                print(f"[SharePoint] No drives found, returning empty list")
                return []
            
            # Use first drive or find by name
            drive_id = drives[0]['id']
            drive_name = drives[0].get('name', 'Unknown')
            print(f"[SharePoint] Using drive: {drive_name} (ID: {drive_id})")
            
            # List files in the specified folder
            folder_path = path or self.folder_path
            print(f"[SharePoint] Folder path: {folder_path}")
            
            # If folder_path is empty, '/', or matches the drive name, use root
            # This is because the drive itself might be named "Documents"
            should_use_root = (
                not folder_path or 
                folder_path == '/' or 
                not folder_path.strip() or
                folder_path.lower() == drive_name.lower()
            )
            
            if should_use_root:
                # Get root items
                items_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/root/children"
                print(f"[SharePoint] Using root of drive (folder_path was '{folder_path}')")
            else:
                # Get specific folder
                items_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/root:/{folder_path}:/children"
            
            print(f"[SharePoint] Listing items from: {items_url}")
            
            try:
                response = requests.get(items_url, headers=headers)
                response.raise_for_status()
                items = response.json().get('value', [])
                print(f"[SharePoint] Found {len(items)} item(s)")
            except requests.exceptions.HTTPError as e:
                error_detail = ""
                try:
                    error_detail = response.json()
                except:
                    error_detail = response.text
                print(f"[SharePoint] HTTP Error: {e}, Response: {error_detail}")
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

                    # Folders use path strings for navigation (so list_files can navigate into them).
                    # Files use item IDs for download via the Graph API items endpoint.
                    if is_folder:
                        # Construct navigable path string: base/subfolder_name
                        if should_use_root:
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
                    print(f"[SharePoint] Error processing item {item.get('name', 'unknown')}: {e}")
                    continue
            
            print(f"[SharePoint] Returning {len(remote_files)} file(s)")
            return remote_files
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            import traceback
            print(f"[SharePoint] Exception: {e}")
            print(traceback.format_exc())
            raise Exception(f"Failed to list SharePoint files: {str(e)}")
    
    def download_file(self, file_path: str) -> str:
        """Download a file from SharePoint"""
        try:
            import requests
            
            token = self._get_access_token()
            site_id = self._get_site_id()
            
            headers = {'Authorization': f'Bearer {token}'}
            
            # If file_path is a webUrl, extract the download URL
            # Otherwise, assume it's a file ID
            if file_path.startswith('http'):
                # Need to convert webUrl to download URL
                # This is complex - for now use a simpler approach with file ID
                filename = file_path.split('/')[-1]
                
                # Search for the file by name
                search_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root/search(q='{filename}')"
                search_response = requests.get(search_url, headers=headers)
                search_response.raise_for_status()
                
                items = search_response.json().get('value', [])
                if not items:
                    raise Exception(f"File not found: {filename}")
                
                download_url = items[0].get('@microsoft.graph.downloadUrl')
            else:
                # Assume file_path is a file ID
                file_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{file_path}"
                file_response = requests.get(file_url, headers=headers)
                file_response.raise_for_status()
                
                file_data = file_response.json()
                download_url = file_data.get('@microsoft.graph.downloadUrl')
                filename = file_data.get('name', 'downloaded_file')
            
            if not download_url:
                raise Exception("Could not get download URL")
            
            # Download the file
            temp_dir = tempfile.gettempdir()
            local_path = os.path.join(temp_dir, f"sharepoint_{filename}")
            
            download_response = requests.get(download_url)
            download_response.raise_for_status()
            
            with open(local_path, 'wb') as f:
                f.write(download_response.content)
            
            return local_path
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to download SharePoint file {file_path}: {str(e)}")
