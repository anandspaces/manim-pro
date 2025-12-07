import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Directories
BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_ROOT = BASE_DIR / "media"
SCRIPTS_DIR = BASE_DIR / "scripts"
JOBS_DIR = BASE_DIR / "jobs"

# File settings
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm"
}

# API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3-pro-preview"

# Rendering settings
RENDER_TIMEOUT = 600  # 10 minutes
RENDER_QUALITY = "-pql"  # preview quality low

# Logging
LOG_LEVEL = "INFO"