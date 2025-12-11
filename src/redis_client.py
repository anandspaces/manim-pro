import json
import logging
import time
from typing import Optional, Dict, List
import redis
from redis.exceptions import RedisError, ConnectionError
from datetime import datetime

from src.config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB, 
    REDIS_PASSWORD, JOB_EXPIRATION_SECONDS
)

logger = logging.getLogger("manim_ai_server")

class RedisClient:
    """Redis client for job storage with automatic reconnection"""
    
    def __init__(self):
        self.client = None
        self._connected = False
        self.connection_params = {
            "host": REDIS_HOST,
            "port": REDIS_PORT,
            "db": REDIS_DB,
            "decode_responses": True,
            "socket_connect_timeout": 5,
            "socket_timeout": 5,
            "socket_keepalive": True,
            "retry_on_timeout": True,
            "health_check_interval": 30
        }
        
        if REDIS_PASSWORD:
            self.connection_params["password"] = REDIS_PASSWORD
        
        logger.info("Redis client initialized (will connect on first use)")
    
    def _connect_with_retry(self, max_retries=10, retry_delay=2):
        """Connect to Redis with retry logic"""
        if self._connected and self.client:
            try:
                self.client.ping()
                return
            except:
                self._connected = False
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Attempting to connect to Redis at {REDIS_HOST}:{REDIS_PORT} (attempt {attempt}/{max_retries})")
                
                self.client = redis.Redis(**self.connection_params)
                self.client.ping()
                
                self._connected = True
                logger.info(f"✓ Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
                return
                
            except Exception as e:
                logger.warning(f"Redis connection attempt {attempt}/{max_retries} failed: {e}")
                self.client = None
                self._connected = False
                
                if attempt < max_retries:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"Failed to connect to Redis after {max_retries} attempts")
                    raise ConnectionError(
                        f"Could not connect to Redis at {REDIS_HOST}:{REDIS_PORT} after {max_retries} attempts. "
                        "Please ensure Redis is running and accessible."
                    )
    
    def _ensure_connection(self):
        """Ensure Redis connection is alive, reconnect if needed"""
        if not self._connected or self.client is None:
            self._connect_with_retry(max_retries=5, retry_delay=1)
        else:
            try:
                self.client.ping()
            except Exception:
                logger.warning("Redis connection lost, attempting to reconnect...")
                self._connected = False
                self.client = None
                self._connect_with_retry(max_retries=5, retry_delay=1)
    
    def _job_key(self, job_id: str) -> str:
        """Generate Redis key for job"""
        return f"job:{job_id}"
    
    def _job_list_key(self) -> str:
        """Generate Redis key for job list"""
        return "jobs:list"
    
    def _get_timestamp(self, job_data: dict) -> float:
        """
        Extract or generate a numeric timestamp from job data.
        Returns a float timestamp suitable for Redis sorted set.
        """
        # Priority 1: Use timestamp_numeric if available
        if "timestamp_numeric" in job_data:
            try:
                ts = job_data["timestamp_numeric"]
                logger.debug(f"Found timestamp_numeric: {ts} (type: {type(ts)})")
                
                # Handle various types
                if isinstance(ts, (int, float)):
                    return float(ts)
                elif isinstance(ts, str):
                    # Try to convert string to float
                    return float(ts)
                else:
                    logger.warning(f"timestamp_numeric is unexpected type: {type(ts)}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not convert timestamp_numeric to float: {e}")
        
        # Priority 2: Parse created_at ISO string
        if "created_at" in job_data:
            try:
                created_at = job_data["created_at"]
                logger.debug(f"Parsing created_at: {created_at}")
                
                # Handle both with and without timezone
                if isinstance(created_at, str):
                    if created_at.endswith('Z'):
                        created_at = created_at[:-1] + '+00:00'
                    
                    dt = datetime.fromisoformat(created_at)
                    ts = dt.timestamp()
                    logger.debug(f"Parsed timestamp from created_at: {ts}")
                    return ts
            except (ValueError, AttributeError) as e:
                logger.warning(f"Could not parse created_at: {e}")
        
        # Priority 3: Use current time as fallback
        timestamp = time.time()
        logger.warning(f"Could not extract timestamp from job data, using current time: {timestamp}")
        return timestamp
    
    def save_job(self, job_id: str, job_data: dict) -> bool:
        """
        Save job to Redis.
        Ensures timestamp_numeric is always present and valid.
        Returns True on success, False on failure
        """
        try:
            self._ensure_connection()
            key = self._job_key(job_id)
            
            # CRITICAL: Ensure timestamp_numeric exists and is valid BEFORE any operations
            if "timestamp_numeric" not in job_data or job_data["timestamp_numeric"] is None:
                job_data["timestamp_numeric"] = time.time()
                logger.debug(f"Added missing timestamp_numeric for job {job_id}: {job_data['timestamp_numeric']}")
            
            # Validate and convert timestamp_numeric to float
            try:
                job_data["timestamp_numeric"] = float(job_data["timestamp_numeric"])
            except (ValueError, TypeError) as e:
                logger.error(f"Invalid timestamp_numeric value: {job_data.get('timestamp_numeric')} - using current time")
                job_data["timestamp_numeric"] = time.time()
            
            # Get timestamp for sorted set (guaranteed to be float now)
            timestamp = float(job_data["timestamp_numeric"])
            
            # Debug logging
            logger.debug(f"Saving job {job_id} with timestamp {timestamp} (type: {type(timestamp)})")
            
            # Convert job data to JSON
            job_json = json.dumps(job_data)
            
            # Save job data with expiration
            self.client.setex(key, JOB_EXPIRATION_SECONDS, job_json)
            
            # Add to job list (sorted set by creation time)
            self.client.zadd(self._job_list_key(), {job_id: timestamp})
            
            logger.debug(f"✓ Successfully saved job {job_id} to Redis")
            return True
            
        except (RedisError, ConnectionError) as e:
            logger.error(f"Failed to save job {job_id} (Redis error): {e}")
            logger.exception("Full Redis error:")
            return False
        except ValueError as e:
            logger.error(f"Failed to save job {job_id} (value conversion error): {e}")
            logger.error(f"Problematic job_data: {job_data}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error saving job {job_id}: {e}")
            logger.exception("Full traceback:")
            return False
    
    def get_job(self, job_id: str) -> Optional[dict]:
        """
        Get job from Redis
        Returns job dict or None if not found
        """
        try:
            self._ensure_connection()
            key = self._job_key(job_id)
            job_json = self.client.get(key)
            
            if not job_json:
                return None
            
            return json.loads(job_json)
            
        except (RedisError, ConnectionError, json.JSONDecodeError) as e:
            logger.error(f"Failed to get job {job_id}: {e}")
            return None
    
    def delete_job(self, job_id: str) -> bool:
        """
        Delete job from Redis
        Returns True on success, False on failure
        """
        try:
            self._ensure_connection()
            key = self._job_key(job_id)
            
            self.client.delete(key)
            self.client.zrem(self._job_list_key(), job_id)
            
            logger.info(f"Deleted job {job_id} from Redis")
            return True
            
        except (RedisError, ConnectionError) as e:
            logger.error(f"Failed to delete job {job_id}: {e}")
            return False
    
    def get_all_jobs(self) -> List[dict]:
        """
        Get all jobs from Redis (most recent first)
        Returns list of job dicts
        """
        try:
            self._ensure_connection()
            job_ids = self.client.zrevrange(self._job_list_key(), 0, -1)
            
            jobs = []
            for job_id in job_ids:
                job = self.get_job(job_id)
                if job:
                    jobs.append(job)
            
            return jobs
            
        except (RedisError, ConnectionError) as e:
            logger.error(f"Failed to get all jobs: {e}")
            return []
    
    def get_jobs_by_status(self, status: str) -> List[dict]:
        """Get all jobs with specific status"""
        all_jobs = self.get_all_jobs()
        return [job for job in all_jobs if job.get("status") == status]
    
    def cleanup_expired_jobs(self) -> int:
        """
        Clean up expired jobs from the job list
        Returns number of cleaned jobs
        """
        try:
            self._ensure_connection()
            job_ids = self.client.zrange(self._job_list_key(), 0, -1)
            
            cleaned = 0
            for job_id in job_ids:
                if not self.client.exists(self._job_key(job_id)):
                    self.client.zrem(self._job_list_key(), job_id)
                    cleaned += 1
            
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} expired jobs")
            
            return cleaned
            
        except (RedisError, ConnectionError) as e:
            logger.error(f"Failed to cleanup expired jobs: {e}")
            return 0
    
    def get_stats(self) -> Dict:
        """Get Redis statistics"""
        try:
            self._ensure_connection()
            info = self.client.info()
            total_jobs = self.client.zcard(self._job_list_key())
            
            return {
                "connected": True,
                "total_jobs": total_jobs,
                "redis_version": info.get("redis_version"),
                "used_memory_human": info.get("used_memory_human"),
                "connected_clients": info.get("connected_clients"),
                "uptime_in_days": info.get("uptime_in_days")
            }
        except (RedisError, ConnectionError) as e:
            logger.error(f"Failed to get Redis stats: {e}")
            return {"connected": False, "error": str(e)}
    
    def ping(self) -> bool:
        """Check if Redis is connected"""
        try:
            self._ensure_connection()
            result = self.client.ping()
            return result is True
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False
    
    def close(self):
        """Close Redis connection"""
        if self.client:
            try:
                self.client.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis connection: {e}")

# Global Redis client instance
redis_client = RedisClient()