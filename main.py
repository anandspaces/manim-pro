import uvicorn
import logging
import time
from contextlib import asynccontextmanager
from src.config import (
    MEDIA_ROOT, SCRIPTS_DIR, JOBS_DIR, 
    GEMINI_API_KEY, GEMINI_MODEL
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.routes import router
from src.service import load_jobs_from_disk
from src.redis_client import redis_client

# Setup logging
logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s - %(message)s"
)
logger = logging.getLogger("manim_ai_server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    # Startup
    logger.info("Starting up Manim AI Server...")
    
    # Wait a bit for Redis to be fully ready
    logger.info("Waiting for Redis to be ready...")
    time.sleep(3)
    
    # Try to connect to Redis
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
    
    logger.info("✓ Manim AI Server startup complete")
    
    yield  # Server runs here
    
    # Shutdown
    logger.info("Shutting down Manim AI Server...")
    try:
        redis_client.close()
        logger.info("✓ Redis connection closed")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


app = FastAPI(
    title="ManimPro AI Animation Server",
    version="2.0",
    description="AI-powered Manim animation generation server",
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


def startup_message():
    """Display startup information"""
    logger.info("="*70)
    logger.info("MANIM AI ANIMATION SERVER STARTUP")
    logger.info("="*70)
    logger.info(f"MEDIA_ROOT: {MEDIA_ROOT}")
    logger.info(f"SCRIPTS_DIR: {SCRIPTS_DIR}")
    logger.info(f"JOBS_DIR: {JOBS_DIR}")
    logger.info(f"GEMINI_MODEL: {GEMINI_MODEL}")
    logger.info(f"GEMINI_API_KEY: {'✓ Configured' if GEMINI_API_KEY else '✗ Not set'}")
    
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not configured! Set it in .env file")
    
    # Create directories
    MEDIA_ROOT.mkdir(exist_ok=True)
    SCRIPTS_DIR.mkdir(exist_ok=True)
    JOBS_DIR.mkdir(exist_ok=True)
    
    logger.info("="*70)

if __name__ == "__main__":
    startup_message()
    uvicorn.run(app, host="0.0.0.0", port=8020, log_level="info")