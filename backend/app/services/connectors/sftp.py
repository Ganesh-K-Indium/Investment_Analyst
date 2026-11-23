"""
SFTP connector implementation
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os
from .base import BaseConnector, RemoteFile


class SFTPConnector(BaseConnector):
    """Connector for SFTP servers"""
    
    def __init__(self, credentials: Dict[str, str], url: Optional[str] = None):
        super().__init__(credentials, url)
        self.host = url or credentials.get("host")
        self.port = int(credentials.get("port", 22))
        self.username = credentials.get("username")
        self.password = credentials.get("password")
        self.private_key = credentials.get("private_key")
        self.remote_path = credentials.get("folder_path", "/")
    
    def _get_sftp_client(self):
        """Get SFTP client"""
        try:
            import paramiko
            
            transport = paramiko.Transport((self.host, self.port))
            
            # Connect using private key or password
            if self.private_key:
                # Try to parse private key from string
                from io import StringIO
                key_file = StringIO(self.private_key)
                try:
                    pkey = paramiko.RSAKey.from_private_key(key_file)
                except:
                    key_file = StringIO(self.private_key)
                    try:
                        pkey = paramiko.Ed25519Key.from_private_key(key_file)
                    except:
                        key_file = StringIO(self.private_key)
                        pkey = paramiko.ECDSAKey.from_private_key(key_file)
                
                transport.connect(username=self.username, pkey=pkey)
            else:
                transport.connect(username=self.username, password=self.password)
            
            sftp = paramiko.SFTPClient.from_transport(transport)
            return sftp, transport
        
        except ImportError:
            raise ImportError("Please install paramiko: pip install paramiko")
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test SFTP connection"""
        try:
            if not all([self.host, self.username]):
                return False, "Missing required credentials (host, username)"
            
            if not self.password and not self.private_key:
                return False, "Either password or private_key must be provided"
            
            sftp, transport = self._get_sftp_client()
            
            # Try to list directory
            sftp.listdir('.')
            
            sftp.close()
            transport.close()
            
            return True, "SFTP connection successful"
        
        except ImportError as e:
            return False, f"Missing dependencies: {str(e)}"
        except Exception as e:
            return False, f"SFTP connection failed: {str(e)}"
    
    def list_files(
        self, 
        path: Optional[str] = None, 
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """List files from SFTP server"""
        try:
            sftp, transport = self._get_sftp_client()
            
            remote_path = path or self.remote_path
            
            # List directory
            items = sftp.listdir_attr(remote_path)
            
            remote_files = []
            for item in items:
                file_name = item.filename
                
                # Apply search filter
                if search_query and search_query.lower() not in file_name.lower():
                    continue
                
                # Check if directory
                import stat
                is_directory = stat.S_ISDIR(item.st_mode)
                
                # Build full path
                if remote_path.endswith('/'):
                    full_path = f"{remote_path}{file_name}"
                else:
                    full_path = f"{remote_path}/{file_name}"
                
                # Convert timestamp to datetime
                modified_time = None
                if item.st_mtime:
                    modified_time = datetime.fromtimestamp(item.st_mtime)
                
                # Determine mime type from extension
                mime_type = None
                if not is_directory:
                    if file_name.endswith('.pdf'):
                        mime_type = 'application/pdf'
                    elif file_name.endswith(('.txt', '.csv')):
                        mime_type = 'text/plain'
                    elif file_name.endswith(('.jpg', '.jpeg')):
                        mime_type = 'image/jpeg'
                    elif file_name.endswith('.png'):
                        mime_type = 'image/png'
                
                remote_files.append(RemoteFile(
                    name=file_name,
                    path=full_path,
                    size=item.st_size if not is_directory else None,
                    last_modified=modified_time,
                    mime_type=mime_type,
                    is_directory=is_directory
                ))
            
            sftp.close()
            transport.close()
            
            return remote_files
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to list SFTP files: {str(e)}")
    
    def download_file(self, file_path: str) -> str:
        """Download a file from SFTP server"""
        try:
            sftp, transport = self._get_sftp_client()
            
            filename = os.path.basename(file_path)
            temp_dir = tempfile.gettempdir()
            local_path = os.path.join(temp_dir, f"sftp_{filename}")
            
            # Download file
            sftp.get(file_path, local_path)
            
            sftp.close()
            transport.close()
            
            return local_path
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to download SFTP file {file_path}: {str(e)}")
