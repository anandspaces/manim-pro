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
AUDIO_DIR = BASE_DIR / "audio"
NARRATIONS_DIR = AUDIO_DIR / "narrations"
PUBLIC_DIR = BASE_DIR / "public"

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

# Redis Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Job expiration (7 days)
JOB_EXPIRATION_SECONDS = 7 * 24 * 60 * 60

# Rendering settings
RENDER_TIMEOUT = 600  # 10 minutes
RENDER_QUALITY = "-ql"  # preview quality low

# TTS Configuration
ONNX_DIR = BASE_DIR / "src" / "assets" / "onnx"
VOICE_STYLES_DIR = BASE_DIR / "src" / "assets" / "voice_styles"
DEFAULT_VOICE_STYLE = "M1"
TTS_SPEED = 1.05
TTS_TOTAL_STEPS = 5
TTS_SILENCE_DURATION = 0.3
USE_GPU_TTS = False

# Audio settings
MAX_NARRATION_LENGTH = 500  # characters
AUDIO_FORMAT = "wav"

# Logo Configuration
LOGO_PATH = PUBLIC_DIR / "dextora-logo.webp"
LOGO_ENABLED = LOGO_PATH.exists()  # Auto-detect if logo exists

# Logo appearance settings
LOGO_POSITION = os.getenv("LOGO_POSITION", "BOTTOM_RIGHT")  # TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT
LOGO_SCALE = float(os.getenv("LOGO_SCALE", "0.3"))  # Scale factor (0.1 to 0.3 recommended)
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "0.7"))  # Opacity (0.0 to 1.0)

# Logging
LOG_LEVEL = "INFO"