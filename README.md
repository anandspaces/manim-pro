Manim AI Animation Server
AI-powered animation generation server using Manim and Google Gemini AI. Generate beautiful mathematical animations from simple text descriptions.
Features

🤖 AI-Powered Script Generation: Uses Google Gemini to generate Manim animation scripts
🎬 Automated Rendering: Background processing with job queue system
📦 Redis Storage: Persistent job tracking with automatic expiration
🐳 Docker Support: Easy deployment with Docker Compose
🔄 Job Retry: Automatic retry for failed animations
📊 Health Monitoring: Built-in health checks and statistics

Architecture
manim-ai-server/
├── src/
│   ├── config.py          # Configuration and environment variables
│   ├── redis_client.py    # Redis client and job storage
│   ├── routes.py          # FastAPI endpoints
│   ├── schemas.py         # Pydantic models
│   └── service.py         # Core business logic
├── media/                 # Generated videos
├── scripts/               # Generated Manim scripts
├── jobs/                  # Job metadata backup
├── main.py               # Application entry point
├── Dockerfile            # Multi-stage Docker build
├── docker-compose.yml    # Docker Compose configuration
├── redis.conf            # Redis configuration
├── pyproject.toml        # Python dependencies
└── .env                  # Environment variables
Prerequisites

Python 3.13+
Redis 7.4+
FFmpeg
LaTeX (for text rendering)
Google Gemini API key

Installation
Option 1: Docker (Recommended)

Clone the repository

bashgit clone <repository-url>
cd manim-ai-server

Configure environment variables

bashcp .env.example .env
Edit .env and add your Gemini API key:
envGEMINI_API_KEY=your_gemini_api_key_here
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0
LOG_LEVEL=INFO

Start services

bashdocker-compose up -d

Check status

bashdocker-compose ps
docker-compose logs -f manim-ai-server
The server will be available at http://localhost:8020
Option 2: Local Development

Install system dependencies

Ubuntu/Debian:
bashsudo apt-get update
sudo apt-get install -y \
    python3.13 \
    python3-pip \
    ffmpeg \
    libcairo2-dev \
    libpango1.0-dev \
    texlive-latex-base \
    texlive-latex-extra \
    redis-server
macOS:
bashbrew install python@3.13 ffmpeg cairo pango redis
brew install --cask mactex

Install Python dependencies

bash# Using uv (recommended)
pip install uv
uv pip install -e .

# Or using pip
pip install -e .

Start Redis

bashredis-server redis.conf

Configure environment

bashcp .env.example .env
# Edit .env with your GEMINI_API_KEY

Run the server

bashpython main.py
API Endpoints
Generate Animation
httpPOST /generate_animation
Content-Type: application/json

{
  "topic": "Pythagorean Theorem",
  "description": "Show the relationship between sides of a right triangle"
}
Response:
json{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Animation generation started",
  "script": "from manim import *\n\nclass PythagoreanTheoremScene(Scene):..."
}
Check Job Status
httpGET /job_status/{job_id}
Response:
json{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "message": "Animation completed successfully",
  "script": "...",
  "video_name": "PythagoreanTheoremScene.mp4",
  "created_at": "2024-12-08T10:30:00",
  "updated_at": "2024-12-08T10:32:15"
}
Status values:

generating_script - AI is generating the Manim script
pending - Script generated, waiting to render
rendering - Animation is being rendered
completed - Video is ready
failed - Error occurred

Get Video
httpGET /get_video/{job_id}
Returns the rendered video file (MP4).
Download Video
httpGET /download_video/{job_id}
Downloads the video file with proper Content-Disposition header.
Retry Failed Job
httpPOST /retry_job/{job_id}
Regenerates script and retries rendering for failed jobs.
List All Jobs
httpGET /list_jobs
Response:
json{
  "count": 5,
  "jobs": [
    {
      "job_id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "completed",
      "topic": "Pythagorean Theorem",
      "created_at": "2024-12-08T10:30:00",
      "video_name": "PythagoreanTheoremScene.mp4"
    }
  ]
}
List Videos
httpGET /list_videos
Lists all available video files.
Health Check
httpGET /health
Response:
json{
  "status": "ok",
  "media_root": "/app/media",
  "media_root_exists": true,
  "active_jobs": 3,
  "gemini_api_key_configured": true,
  "gemini_model": "gemini-3-pro-preview",
  "redis": {
    "connected": true,
    "total_jobs": 15,
    "redis_version": "7.4.0",
    "used_memory_human": "2.5M"
  }
}
Configuration
Environment Variables
VariableDefaultDescriptionGEMINI_API_KEY-Required. Google Gemini API keyGEMINI_MODELgemini-3-pro-previewGemini model to useREDIS_HOSTlocalhostRedis server hostnameREDIS_PORT6379Redis server portREDIS_DB0Redis database numberLOG_LEVELINFOLogging level
Redis Configuration
The included redis.conf provides:

Persistence: RDB snapshots + AOF logs
Memory Management: 256MB limit with LRU eviction
Performance: Optimized for job queue operations
Security: Bind to localhost, protected mode enabled

Rendering Settings
Defined in src/config.py:

Quality: -ql (preview quality, low resolution)
Timeout: 600 seconds (10 minutes)
Job Expiration: 7 days
Max Objects: 10 per animation
Max Duration: 10 seconds

Usage Examples
Python Client
pythonimport requests
import time

API_URL = "http://localhost:8020"

# Generate animation
response = requests.post(f"{API_URL}/generate_animation", json={
    "topic": "Circle Area Formula",
    "description": "Show how the area of a circle is πr²"
})
job_id = response.json()["job_id"]
print(f"Job created: {job_id}")

# Poll for completion
while True:
    status = requests.get(f"{API_URL}/job_status/{job_id}").json()
    print(f"Status: {status['status']} - {status['message']}")
    
    if status["status"] == "completed":
        print(f"Video ready: {status['video_name']}")
        break
    elif status["status"] == "failed":
        print(f"Error: {status['error']}")
        break
    
    time.sleep(5)

# Download video
video = requests.get(f"{API_URL}/get_video/{job_id}")
with open("animation.mp4", "wb") as f:
    f.write(video.content)
cURL
bash# Generate animation
curl -X POST http://localhost:8020/generate_animation \
  -H "Content-Type: application/json" \
  -d '{"topic": "Fibonacci Spiral", "description": "Visualize the golden ratio"}'

# Check status
curl http://localhost:8020/job_status/YOUR_JOB_ID

# Download video
curl -O http://localhost:8020/get_video/YOUR_JOB_ID
Docker Commands
bash# Start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down

# Rebuild after code changes
docker-compose up -d --build

# View Redis data
docker-compose exec redis redis-cli

# Shell into container
docker-compose exec manim-ai-server bash

# Check resource usage
docker stats
Monitoring
View Logs
bash# Application logs
docker-compose logs -f manim-ai-server

# Redis logs
docker-compose logs -f redis

# Follow both
docker-compose logs -f
Redis CLI
bashdocker-compose exec redis redis-cli

# Check all jobs
ZRANGE jobs:list 0 -1

# Get job data
GET job:YOUR_JOB_ID

# Check memory usage
INFO memory

# Monitor commands
MONITOR
Health Checks
bash# Application health
curl http://localhost:8020/health

# Redis health
docker-compose exec redis redis-cli ping
Troubleshooting
Common Issues
1. "GEMINI_API_KEY not configured"

Add your API key to .env file
Restart the container: docker-compose restart manim-ai-server

2. "Redis connection unavailable"

Check Redis is running: docker-compose ps redis
Check logs: docker-compose logs redis
Verify connection: docker-compose exec redis redis-cli ping

3. "Rendering timeout"

Animation is too complex
Use retry endpoint to generate simpler script
Increase RENDER_TIMEOUT in src/config.py

4. "Video file not found"

Check media/ directory exists
Verify FFmpeg is installed: ffmpeg -version
Check render logs for errors

5. Out of Memory

Reduce maxmemory in redis.conf
Limit concurrent jobs
Increase Docker memory limit

Debug Mode
Enable detailed logging:
envLOG_LEVEL=DEBUG
View Manim output:
bashdocker-compose logs -f manim-ai-server | grep "Manim"
Performance Optimization
Redis Tuning

Memory: Adjust maxmemory based on job volume
Persistence: Disable AOF for better write performance
Eviction: Use allkeys-lru for automatic cleanup

Animation Limits

Keep animations under 10 seconds
Use maximum 10-15 objects
Avoid 3D scenes (slow to render)
Prefer simple 2D shapes
No physics simulations or updaters

Docker Resources
yaml# docker-compose.yml
deploy:
  resources:
    limits:
      cpus: '4'
      memory: 6G
```

## Security Considerations

1. **API Key Protection**: Never commit `.env` to version control
2. **Redis Password**: Set `requirepass` in production
3. **Network Isolation**: Use Docker networks
4. **File Access**: Path traversal protection enabled
5. **Resource Limits**: Container CPU/memory limits
6. **Input Validation**: Pydantic schemas validate all inputs

## Development

### Project Structure
```
src/
├── config.py           # Configuration management
├── redis_client.py     # Redis operations
├── routes.py          # API endpoints
├── schemas.py         # Request/response models
└── service.py         # Business logic
Adding New Features

New Endpoint: Add to src/routes.py
Business Logic: Implement in src/service.py
Data Models: Define in src/schemas.py
Configuration: Add to src/config.py

Testing
bash# Install dev dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest tests/
License
MIT License - see LICENSE file for details
Support

Documentation: Manim Docs
Issues: Create an issue on GitHub
Discussions: GitHub Discussions

Acknowledgments

Manim Community - Animation engine
Google Gemini - AI script generation
FastAPI - Web framework
Redis - Job storage