"""
Confluence connector implementation using Atlassian Cloud API
Adapted from proven implementation
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os
from urllib.parse import urljoin
import requests
from .base import BaseConnector, RemoteFile


class ConfluenceConnector(BaseConnector):
    """Connector for Atlassian Confluence Cloud"""

    def __init__(self, credentials: Dict[str, str], url: Optional[str] = None):
        super().__init__(credentials, url)
        self.confluence_url = url  # e.g., https://company.atlassian.net/wiki
        self.username = credentials.get("username")  # Email for Cloud
        self.api_token = credentials.get("api_token")  # Personal API token
        self.folder_path = credentials.get("folder_path", "")  # Optional starting space key

        # Normalize URL
        if self.confluence_url:
            if not self.confluence_url.startswith(('http://', 'https://')):
                self.confluence_url = f"https://{self.confluence_url}"
            if not self.confluence_url.endswith('/'):
                self.confluence_url += '/'

        # Setup authentication
        self.session = None
        self._init_session()

    def _init_session(self):
        """Initialize authenticated session"""
        if self.username and self.api_token:
            self.session = requests.Session()
            self.session.auth = (self.username, self.api_token)
            self.session.headers.update({
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            })

    def _make_request(self, method: str, endpoint: str, **kwargs):
        """Make HTTP request to Confluence API"""
        if not self.session:
            raise Exception("Session not initialized")

        url = urljoin(self.confluence_url, endpoint)
        print(f"[Confluence] {method} {url}")

        try:
            response = self.session.request(method, url, **kwargs)

            if not response.ok:
                try:
                    error_body = response.json()
                    error_msg = error_body.get('message', response.text)
                except:
                    error_msg = response.text
                print(f"[Confluence] API Error: {response.status_code} - {error_msg}")

            return response
        except Exception as e:
            print(f"[Confluence] Request failed: {e}")
            raise

    def test_connection(self) -> Tuple[bool, str]:
        """Test Confluence connection"""
        try:
            if not all([self.confluence_url, self.username, self.api_token]):
                return False, "Missing required credentials (url, username, api_token)"

            response = self._make_request('GET', 'rest/api/space', params={'limit': 1})
            response.raise_for_status()

            return True, f"Confluence connection successful. Connected to {self.confluence_url}"

        except Exception as e:
            return False, f"Confluence connection failed: {str(e)}"

    def list_files(
        self,
        path: Optional[str] = None,
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """List spaces and their attachments from Confluence"""
        try:
            print(f"\n[Confluence] Starting file listing...")
            print(f"[Confluence] Path: {path}, Search: {search_query}")

            if not path or path == "/" or not path.strip():
                # List spaces
                return self._list_spaces(search_query)
            else:
                # List attachments in space
                return self._list_space_attachments(path, search_query)

        except Exception as e:
            import traceback
            print(f"[Confluence] Exception: {e}")
            print(traceback.format_exc())
            raise Exception(f"Failed to list Confluence files: {str(e)}")

    def _list_spaces(self, search_query: Optional[str] = None) -> List[RemoteFile]:
        """List all accessible spaces"""
        try:
            params = {'limit': 100}
            if search_query:
                params['spaceKey'] = search_query

            print(f"[Confluence] Listing spaces...")

            response = self._make_request('GET', 'rest/api/space', params=params)
            response.raise_for_status()

            spaces_data = response.json()
            spaces = spaces_data.get('results', [])
            print(f"[Confluence] Found {len(spaces)} space(s)")

            remote_files = []
            for space in spaces:
                space_key = space.get('key')
                space_name = space.get('name', 'Unknown')

                remote_file = RemoteFile(
                    name=space_name,
                    path=space_key,
                    size=None,
                    last_modified=None,
                    mime_type='application/vnd.confluence.space',
                    is_directory=True
                )
                remote_files.append(remote_file)

            return remote_files

        except Exception as e:
            print(f"[Confluence] Error listing spaces: {e}")
            raise

    def _get_page_attachments_recursive(self, page_id: str, page_title: str, space_key: str) -> Tuple[List[RemoteFile], int]:
        """Recursively fetch attachments from a page and its child pages"""
        remote_files = []
        total_attachments = 0

        try:
            # Fetch attachments from current page
            att_response = self._make_request(
                'GET',
                f'rest/api/content/{page_id}/child/attachment',
                params={'limit': 100, 'expand': 'version,extensions'}
            )
            att_response.raise_for_status()
            child_attachments = att_response.json().get('results', [])

            print(f"[Confluence] Page '{page_title}' has {len(child_attachments)} attachment(s)")

            for attachment in child_attachments:
                att_title = attachment.get('title', 'unknown')
                att_id = attachment.get('id')
                att_size = attachment.get('extensions', {}).get('fileSize', 0)
                att_created = attachment.get('version', {}).get('when')
                att_media_type = attachment.get('extensions', {}).get('mediaType', 'application/octet-stream')

                # Create file name with page context
                display_name = f"{page_title} - {att_title}"

                remote_file = RemoteFile(
                    name=display_name,
                    path=f"{space_key}/{page_id}/{att_id}",
                    size=att_size,
                    last_modified=self._parse_datetime(att_created),
                    mime_type=att_media_type,
                    is_directory=False
                )
                remote_files.append(remote_file)
                total_attachments += 1
                print(f"[Confluence] Added attachment: {display_name}")

            # Fetch child pages
            try:
                child_response = self._make_request(
                    'GET',
                    f'rest/api/content/{page_id}/child/page',
                    params={'limit': 100}
                )
                child_response.raise_for_status()
                child_pages = child_response.json().get('results', [])

                print(f"[Confluence] Page '{page_title}' has {len(child_pages)} child page(s)")

                for child_page in child_pages:
                    child_id = child_page.get('id')
                    child_title = child_page.get('title', 'Unknown')
                    print(f"[Confluence] Processing child page: {child_title} (ID: {child_id})")

                    # Recursively process child pages
                    child_files, child_count = self._get_page_attachments_recursive(child_id, child_title, space_key)
                    remote_files.extend(child_files)
                    total_attachments += child_count

            except Exception as e:
                print(f"[Confluence] Error fetching child pages for '{page_title}': {e}")

        except Exception as e:
            print(f"[Confluence] Error getting attachments for page '{page_title}' ({page_id}): {e}")
            import traceback
            print(traceback.format_exc())

        return remote_files, total_attachments

    def _list_space_attachments(self, space_key: str, search_query: Optional[str] = None) -> List[RemoteFile]:
        """List all attachments in a space and its pages (including child pages)"""
        try:
            print(f"[Confluence] Listing attachments in space: {space_key}")

            # List all pages in the space
            cql = f'space = "{space_key}" and type = "page"'
            if search_query:
                cql += f' and (title ~ "{search_query}" or attachment ~ "{search_query}")'
            cql += ' ORDER BY created DESC'

            print(f"[Confluence] Using CQL query: {cql}")

            params = {
                'cql': cql,
                'limit': 100,
                'expand': 'space,version'
            }

            response = self._make_request('GET', 'rest/api/content/search', params=params)
            response.raise_for_status()

            search_data = response.json()
            pages = search_data.get('results', [])
            print(f"[Confluence] Found {len(pages)} total page(s) in space")

            remote_files = []
            total_attachments = 0

            for page in pages:
                page_id = page.get('id')
                page_title = page.get('title', 'Unknown')
                print(f"[Confluence] Processing page: {page_title} (ID: {page_id})")

                # Recursively fetch attachments from this page and its child pages
                page_files, page_count = self._get_page_attachments_recursive(page_id, page_title, space_key)
                remote_files.extend(page_files)
                total_attachments += page_count

            print(f"[Confluence] Total attachments found: {total_attachments}")
            if total_attachments == 0:
                print(f"[Confluence] WARNING: No attachments found in space '{space_key}'")

            return remote_files

        except Exception as e:
            print(f"[Confluence] Error listing space attachments: {e}")
            import traceback
            print(traceback.format_exc())
            raise

    def _parse_datetime(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 datetime string"""
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except Exception:
            return None

    def download_file(self, file_path: str) -> str:
        """
        Download an attachment from Confluence.

        file_path format: "space_key/page_id/attachment_id"
        """
        try:
            print(f"[Confluence] Downloading file: {file_path}")

            parts = file_path.split('/')
            if len(parts) != 3:
                raise Exception("Invalid file path format. Expected: space_key/page_id/attachment_id")

            attachment_id = parts[2]

            # Get attachment details
            att_response = self._make_request('GET', f'rest/api/content/{attachment_id}')
            att_response.raise_for_status()

            attachment_data = att_response.json()
            filename = attachment_data.get('title', 'attachment')
            download_url = attachment_data.get('_links', {}).get('download', '')

            if not download_url:
                raise Exception("Could not get download URL for attachment")

            # Convert relative URL to full URL if needed
            if not download_url.startswith('http'):
                download_url = urljoin(self.confluence_url, download_url.lstrip('/'))

            print(f"[Confluence] Downloading: {download_url}")

            # Download the file
            file_response = requests.get(download_url, auth=(self.username, self.api_token))
            file_response.raise_for_status()

            # Save to temporary location
            temp_dir = tempfile.gettempdir()
            local_path = os.path.join(temp_dir, f"confluence_{filename}")

            with open(local_path, 'wb') as f:
                f.write(file_response.content)

            print(f"[Confluence] Downloaded to: {local_path}")
            return local_path

        except Exception as e:
            raise Exception(f"Failed to download Confluence attachment {file_path}: {str(e)}")
