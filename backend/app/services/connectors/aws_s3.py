"""
AWS S3 connector implementation
"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import tempfile
import os
from .base import BaseConnector, RemoteFile


class AWSS3Connector(BaseConnector):
    """Connector for AWS S3"""
    
    def __init__(self, credentials: Dict[str, str], url: Optional[str] = None):
        super().__init__(credentials, url)
        self.bucket_name = credentials.get("bucket_name")
        self.access_key_id = credentials.get("access_key_id")
        self.secret_access_key = credentials.get("secret_access_key")
        self.region = credentials.get("region", "us-east-1")
        self.prefix = credentials.get("folder_path", "")
    
    def _get_s3_client(self):
        """Get AWS S3 client"""
        try:
            import boto3
            
            return boto3.client(
                's3',
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region
            )
        
        except ImportError:
            raise ImportError("Please install boto3: pip install boto3")
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test AWS S3 connection"""
        try:
            if not all([self.bucket_name, self.access_key_id, self.secret_access_key]):
                return False, "Missing required credentials (bucket_name, access_key_id, secret_access_key)"
            
            s3_client = self._get_s3_client()
            
            # Test bucket access
            s3_client.head_bucket(Bucket=self.bucket_name)
            
            # Try to list a few objects
            response = s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                MaxKeys=1
            )
            
            object_count = response.get('KeyCount', 0)
            
            return True, f"AWS S3 connection successful. Bucket accessible with {object_count} object(s) found."
        
        except ImportError as e:
            return False, f"Missing dependencies: {str(e)}"
        except Exception as e:
            return False, f"AWS S3 connection failed: {str(e)}"
    
    def list_files(
        self, 
        path: Optional[str] = None, 
        search_query: Optional[str] = None
    ) -> List[RemoteFile]:
        """List files from AWS S3"""
        try:
            s3_client = self._get_s3_client()
            
            prefix = path or self.prefix
            if prefix and not prefix.endswith('/'):
                prefix += '/'
            
            # List objects
            response = s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                Delimiter='/'
            )
            
            remote_files = []
            
            # Add folders (CommonPrefixes)
            for folder in response.get('CommonPrefixes', []):
                folder_name = folder['Prefix'].rstrip('/').split('/')[-1]
                
                if search_query and search_query.lower() not in folder_name.lower():
                    continue
                
                remote_files.append(RemoteFile(
                    name=folder_name,
                    path=folder['Prefix'],
                    size=None,
                    last_modified=None,
                    mime_type=None,
                    is_directory=True
                ))
            
            # Add files (Contents)
            for obj in response.get('Contents', []):
                # Skip the folder itself (ends with /)
                if obj['Key'].endswith('/'):
                    continue
                
                file_name = obj['Key'].split('/')[-1]
                
                if search_query and search_query.lower() not in file_name.lower():
                    continue
                
                # Try to determine mime type from extension
                mime_type = None
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
                    path=obj['Key'],
                    size=obj['Size'],
                    last_modified=obj['LastModified'],
                    mime_type=mime_type,
                    is_directory=False
                ))
            
            return remote_files
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to list S3 files: {str(e)}")
    
    def download_file(self, file_path: str) -> str:
        """Download a file from AWS S3"""
        try:
            s3_client = self._get_s3_client()
            
            filename = os.path.basename(file_path)
            temp_dir = tempfile.gettempdir()
            local_path = os.path.join(temp_dir, f"s3_{filename}")
            
            # Download file from S3
            s3_client.download_file(self.bucket_name, file_path, local_path)
            
            return local_path
        
        except ImportError as e:
            raise Exception(f"Missing dependencies: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to download S3 file {file_path}: {str(e)}")
