import uvicorn
import logging
from src.config import (
    MEDIA_ROOT, SCRIPTS_DIR, JOBS_DIR, 
    GEMINI_API_KEY, GEMINI_MODEL
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.routes import router
from src.service import load_jobs_from_disk

# Setup logging
logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s - %(message)s"
)
logger = logging.getLogger("manim_ai_server")


app = FastAPI(
    title="ManimPro AI Animation Server",
    version="2.0",
    description="AI-powered Manim animation generation server"
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

# Startup event
@app.on_event("startup")
async def startup():
    """Initialize on startup"""
    load_jobs_from_disk()


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