import os
import json
import logging
from cryptography.fernet import Fernet, InvalidToken
from typing import Dict, Any, Optional

logger = logging.getLogger("core.security")

_fernet: Optional[Fernet] = None

def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    
    secret_key = os.getenv("INTEGRATION_SECRET_KEY")
    
    if not secret_key:
        logger.error("INTEGRATION_SECRET_KEY is not set in the environment. Credentials cannot be encrypted securely.")
        raise ValueError("Missing INTEGRATION_SECRET_KEY in environment configuration.")
    
    try:
        _fernet = Fernet(secret_key.encode('utf-8'))
    except Exception as e:
        logger.error(f"Failed to initialize Fernet with INTEGRATION_SECRET_KEY: {e}")
        raise ValueError("Invalid INTEGRATION_SECRET_KEY configuration")
    
    return _fernet

def encrypt_dict(data: Dict[str, Any]) -> str:
    """Serialize a dictionary to JSON and encrypt it using Fernet."""
    if not data:
        return ""
    try:
        json_str = json.dumps(data)
        encrypted_bytes = _get_fernet().encrypt(json_str.encode('utf-8'))
        return encrypted_bytes.decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to encrypt data: {e}")
        raise ValueError("Encryption failed")

def decrypt_dict(token: str) -> Dict[str, Any]:
    """Decrypt a Fernet token and deserialize it to a dictionary."""
    if not token:
        return {}
    try:
        decrypted_bytes = _get_fernet().decrypt(token.encode('utf-8'))
        return json.loads(decrypted_bytes.decode('utf-8'))
    except InvalidToken:
        logger.error("Failed to decrypt data: Invalid Fernet token. (Was the secret key changed?)")
        raise ValueError("Decryption failed: invalid token")
    except Exception as e:
        logger.error(f"Failed to decrypt data: {e}")
        raise ValueError("Decryption failed")
