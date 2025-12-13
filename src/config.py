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
GEMINI_MODEL = "gemini-2.0-flash-exp"

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

# Logging
LOG_LEVEL = "INFO"