import cloudinary
import cloudinary.uploader
from pathlib import Path

from app.config import settings


def upload_video(local_path: Path) -> str:
    """Upload an mp4 to Cloudinary and return the secure URL."""
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
    )
    result = cloudinary.uploader.upload(
        str(local_path),
        resource_type="video",
        folder="algo-visuals",
    )
    return result["secure_url"]
