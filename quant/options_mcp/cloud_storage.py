import asyncio
import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure Cloudinary with official SDK
try:
    import cloudinary
    import cloudinary.uploader
    from cloudinary.utils import cloudinary_url
    
    # Initialize Cloudinary config
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True
    )
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False


async def upload_to_cloudinary(file_path: str, cloud_name: Optional[str] = None, api_key: Optional[str] = None, api_secret: Optional[str] = None) -> dict:
    """
    Upload image to Cloudinary using official SDK.
    
    Args:
        file_path: Path to the image file to upload
        cloud_name: Cloudinary cloud name (from environment or parameter)
        api_key: Cloudinary API key (from environment or parameter)
        api_secret: Cloudinary API secret (from environment or parameter)
    
    Returns:
        Dict with 'success', 'url' (public URL), and optional 'error'
    """
    try:
        if not CLOUDINARY_AVAILABLE:
            return {
                "success": False,
                "error": "Cloudinary SDK not installed. Install with: pip install cloudinary"
            }
        
        cloud_name = cloud_name or os.getenv("CLOUDINARY_CLOUD_NAME")
        api_key = api_key or os.getenv("CLOUDINARY_API_KEY")
        api_secret = api_secret or os.getenv("CLOUDINARY_API_SECRET")
        
        if not cloud_name or not api_key or not api_secret:
            return {
                "success": False,
                "error": "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET must be set in environment"
            }
        
        # Use official Cloudinary SDK for upload
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: cloudinary.uploader.upload(
                file_path,
                secure=True,
                overwrite=True,
                resource_type="image"
            )
        )
        
        if "secure_url" in result:
            return {
                "success": True,
                "url": result["secure_url"],
                "public_id": result.get("public_id")
            }
        else:
            return {
                "success": False,
                "error": "Upload succeeded but no URL returned",
                "details": str(result)
            }
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"File not found: {file_path}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


async def upload_chart_to_cloudinary(fig_obj, filename: str, width: int = 1000, height: int = 600) -> dict:
    """
    Save chart as image and upload to Cloudinary.
    
    Args:
        fig_obj: Plotly figure object
        filename: Desired filename
        width: Chart width
        height: Chart height
    
    Returns:
        Dict with 'success', 'cloud_url' (public URL), and optional error details
    """
    temp_file = None
    try:
        # Save locally first (temporarily)
        temp_file = f"/tmp/{filename}"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: fig_obj.write_image(temp_file, width=width, height=height))
        
        # Upload to Cloudinary
        result = await upload_to_cloudinary(temp_file)
        
        if result.get("success"):
            return {
                "success": True,
                "cloud_url": result.get("url"),
                "filename": filename,
                "public_id": result.get("public_id")
            }
        else:
            return {
                "success": False,
                "error": result.get("error"),
                "details": result.get("details")
            }
    
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
    
    finally:
        # Clean up temporary file
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass
