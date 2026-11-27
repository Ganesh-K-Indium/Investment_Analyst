"""
Chart image hosting on Cloudinary, shared by the RAG graph and the quant MCP servers.

Optional: when CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET are not set (see app/core/config.py), every
call returns {"success": False, "error": ...} and callers keep their local chart. Sync functions are for LangGraph nodes;
the `*_async` variants run the same work in a thread for async code (the MCP servers).
"""
import asyncio
import os
import tempfile
from typing import Optional

from app.core.config import get_settings

try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
except ImportError:  # optional dependency
    CLOUDINARY_AVAILABLE = False


def configured() -> bool:
    cfg = get_settings()
    return bool(CLOUDINARY_AVAILABLE and cfg.cloudinary_cloud_name and cfg.cloudinary_api_key and cfg.cloudinary_api_secret)


def upload_to_cloudinary(file_path: str, cloud_name: Optional[str] = None, api_key: Optional[str] = None,
                         api_secret: Optional[str] = None) -> dict:
    """Upload an image file. Returns {"success", "url", "public_id"} or {"success": False, "error", ...}."""
    if not CLOUDINARY_AVAILABLE:
        return {"success": False, "error": "Cloudinary SDK not installed. Install with: pip install cloudinary"}
    cfg = get_settings()
    cloud_name = cloud_name or cfg.cloudinary_cloud_name
    api_key = api_key or cfg.cloudinary_api_key
    api_secret = api_secret or cfg.cloudinary_api_secret
    if not (cloud_name and api_key and api_secret):
        return {"success": False, "error": "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET must be set"}
    try:
        result = cloudinary.uploader.upload(file_path, secure=True, overwrite=True, resource_type="image",
                                            cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)
    except FileNotFoundError:
        return {"success": False, "error": f"File not found: {file_path}"}
    except Exception as e:  # noqa: BLE001 - hosting is best-effort; the caller keeps the local chart
        return {"success": False, "error": str(e)}
    if "secure_url" in result:
        return {"success": True, "url": result["secure_url"], "public_id": result.get("public_id")}
    return {"success": False, "error": "Upload succeeded but no URL returned", "details": str(result)}


def upload_chart_to_cloudinary(fig_obj, filename: str, width: int = 1000, height: int = 600) -> dict:
    """Render a Plotly figure to an image and upload it. Returns {"success", "cloud_url", "filename", "public_id"} or an error."""
    temp_file = os.path.join(tempfile.gettempdir(), os.path.basename(filename))
    try:
        fig_obj.write_image(temp_file, width=width, height=height)
        result = upload_to_cloudinary(temp_file)
        if result.get("success"):
            return {"success": True, "cloud_url": result.get("url"), "filename": filename, "public_id": result.get("public_id")}
        return {"success": False, "error": result.get("error"), "details": result.get("details")}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}
    finally:
        try:
            os.remove(temp_file)
        except OSError:
            pass


async def upload_to_cloudinary_async(file_path: str, **kwargs) -> dict:
    return await asyncio.to_thread(upload_to_cloudinary, file_path, **kwargs)


async def upload_chart_to_cloudinary_async(fig_obj, filename: str, width: int = 1000, height: int = 600) -> dict:
    return await asyncio.to_thread(upload_chart_to_cloudinary, fig_obj, filename, width, height)
