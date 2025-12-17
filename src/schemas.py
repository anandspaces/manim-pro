from pydantic import BaseModel
from typing import Optional

class AnimationRequest(BaseModel):
    """Request model for generating animations"""
    topic: str
    topic_id: int
    subject: str
    subject_id: int
    chapter: str
    chapter_id: int
    level: int

class JobStatusResponse(BaseModel):
    """Response model for job status"""
    job_id: str
    status: str  # "pending", "generating_script", "rendering", "completed", "failed"
    message: str
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

class CacheCheckRequest(BaseModel):
    """Request model for checking animation cache"""
    topic_id: int
    chapter_id: int
    subject_id: int
    level: int
    chapter: str
    topic: str
    subject: str

class CacheCheckResponse(BaseModel):
    """Response model for cache check"""
    cached: bool
    job_id: Optional[str] = None
    video_url: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    message: str