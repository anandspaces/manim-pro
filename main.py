import uvicorn
import logging
import time
from contextlib import asynccontextmanager
from src.config import (
    MEDIA_ROOT, SCRIPTS_DIR, JOBS_DIR, AUDIO_DIR, NARRATIONS_DIR,
    GEMINI_API_KEY, GEMINI_MODEL
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from src.routes import router
from src.helper_service import load_jobs_from_disk
from src.redis_client import redis_client
from src.tts_service import initialize_tts_engine, get_available_voices


# Setup logging
logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s - %(message)s"
)
logger = logging.getLogger("manim_ai_server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    from src.database import animation_db
    
    # Startup
    logger.info("="*70)
    logger.info("MANIM AI ANIMATION SERVER WITH TTS - STARTUP")
    logger.info("="*70)
    
    # Create directories
    MEDIA_ROOT.mkdir(exist_ok=True)
    SCRIPTS_DIR.mkdir(exist_ok=True)
    JOBS_DIR.mkdir(exist_ok=True)
    AUDIO_DIR.mkdir(exist_ok=True)
    NARRATIONS_DIR.mkdir(exist_ok=True)
    logger.info(f"✓ Directories created/verified")
    
    # Initialize database
    try:
        logger.info("Initializing SQLite database...")
        animation_db._init_database()
        logger.info("✓ Database initialized")
        
        stats = animation_db.get_stats()
        logger.info(f"Database: {stats.get('total_animations', 0)} cached animations")
    except Exception as e:
        logger.error(f"✗ Failed to initialize database: {e}")
    
    # Initialize TTS engine
    try:
        logger.info("Initializing TTS engine...")
        initialize_tts_engine()
        voices = get_available_voices()
        logger.info(f"✓ TTS engine initialized with {len(voices)} voices: {voices}")
    except Exception as e:
        logger.error(f"✗ Failed to initialize TTS: {e}")
        logger.warning("Server will continue without TTS support")
    
    # Wait for Redis
    logger.info("Waiting for Redis to be ready...")
    time.sleep(3)
    
    # Connect to Redis
    try:
        redis_client._connect_with_retry(max_retries=15, retry_delay=2)
        logger.info("✓ Redis connection established")
    except Exception as e:
        logger.error(f"✗ Failed to connect to Redis: {e}")
        logger.warning("Server will start anyway, but jobs may not work until Redis is available")
    
    # Load jobs from disk
    try:
        load_jobs_from_disk()
        logger.info("✓ Jobs loaded from disk")
    except Exception as e:
        logger.error(f"✗ Failed to load jobs: {e}")
    
    logger.info("="*70)
    logger.info("✓ Manim AI Server with TTS startup complete")
    logger.info("="*70)
    
    yield  # Server runs here
    
    # Shutdown
    logger.info("Shutting down Manim AI Server...")
    try:
        redis_client.close()
        logger.info("✓ Redis connection closed")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


app = FastAPI(
    title="ManimPro AI Animation Server with TTS",
    version="2.1 - with TTS Narration",
    description="AI-powered Manim animation generation server with TTS narration and database caching",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router)

# Mount media directory as static files
app.mount("/media", StaticFiles(directory=str(MEDIA_ROOT)), name="media")

# Mount audio directory as static files (for audio playback)
app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")


def startup_message():
    """Display startup information"""
    logger.info("="*70)
    logger.info("MANIM AI ANIMATION SERVER WITH TTS")
    logger.info("="*70)
    logger.info(f"MEDIA_ROOT: {MEDIA_ROOT}")
    logger.info(f"SCRIPTS_DIR: {SCRIPTS_DIR}")
    logger.info(f"JOBS_DIR: {JOBS_DIR}")
    logger.info(f"AUDIO_DIR: {AUDIO_DIR}")
    logger.info(f"NARRATIONS_DIR: {NARRATIONS_DIR}")
    logger.info(f"GEMINI_MODEL: {GEMINI_MODEL}")
    logger.info(f"GEMINI_API_KEY: {'✓ Configured' if GEMINI_API_KEY else '✗ Not set'}")
    
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not configured! Set it in .env file")
    
    logger.info("="*70)

if __name__ == "__main__":
    startup_message()
    uvicorn.run(app, host="0.0.0.0", port=8020, log_level="info")