from pydantic import BaseModel
from typing import Optional

class AnimationRequest(BaseModel):
    """Request model for generating animations"""
    topic: str
    description: Optional[str] = None

class JobStatusResponse(BaseModel):
    """Response model for job status"""
    job_id: str
    status: str  # "pending", "generating_script", "rendering", "completed", "failed"
    message: str
    script: Optional[str] = None
    video_name: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str

class VideoListItem(BaseModel):
    """Model for video list item"""
    name: str
    path: str
    size_mb: float

class JobListItem(BaseModel):
    """Model for job list item"""
    job_id: str
    status: str
    topic: Optional[str] = None
    created_at: str
    video_name: Optional[str] = None