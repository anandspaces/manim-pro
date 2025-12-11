import sqlite3
import logging
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime
from contextlib import contextmanager

from src.config import BASE_DIR

logger = logging.getLogger("manim_ai_server")

# Database path
DB_PATH = BASE_DIR / "animations.db"

class AnimationDatabase:
    """SQLite database handler for animation caching"""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize database and create tables if they don't exist"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Create animations table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS animations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        level INTEGER NOT NULL,
                        subject_id INTEGER NOT NULL,
                        subject_name TEXT,
                        chapter_id INTEGER NOT NULL,
                        chapter_name TEXT,
                        topic_id INTEGER NOT NULL,
                        topic_name TEXT NOT NULL,
                        job_id TEXT NOT NULL UNIQUE,
                        video_name TEXT,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(level, subject_id, chapter_id, topic_id)
                    )
                """)
                
                # Create index for faster lookups
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_animation_lookup 
                    ON animations(level, subject_id, chapter_id, topic_id)
                """)
                
                # Create index for job_id lookups
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_job_id 
                    ON animations(job_id)
                """)
                
                conn.commit()
                logger.info(f"✓ Database initialized at {self.db_path}")
                
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
    
    @contextmanager
    def _get_connection(self):
        """Get database connection context manager"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Return rows as dictionaries
        try:
            yield conn
        finally:
            conn.close()
    
    def check_existing_animation(
        self, 
        level: int, 
        subject_id: int, 
        chapter_id: int, 
        topic_id: int
    ) -> Optional[Dict]:
        """
        Check if animation already exists for given parameters.
        Returns animation data if found, None otherwise.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM animations 
                    WHERE level = ? 
                    AND subject_id = ? 
                    AND chapter_id = ? 
                    AND topic_id = ?
                    AND status = 'completed'
                """, (level, subject_id, chapter_id, topic_id))
                
                row = cursor.fetchone()
                
                if row:
                    animation_data = dict(row)
                    logger.info(
                        f"Found existing animation: job_id={animation_data['job_id']}, "
                        f"level={level}, subject_id={subject_id}, "
                        f"chapter_id={chapter_id}, topic_id={topic_id}"
                    )
                    return animation_data
                
                return None
                
        except Exception as e:
            logger.error(f"Error checking existing animation: {e}")
            return None
    
    def save_animation(
        self,
        level: int,
        subject_id: int,
        subject_name: str,
        chapter_id: int,
        chapter_name: str,
        topic_id: int,
        topic_name: str,
        job_id: str,
        video_name: Optional[str] = None,
        status: str = "pending"
    ) -> bool:
        """
        Save animation metadata to database.
        Returns True on success, False on failure.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                
                # Try to insert, if exists then update
                cursor.execute("""
                    INSERT INTO animations (
                        level, subject_id, subject_name, chapter_id, chapter_name,
                        topic_id, topic_name, job_id, video_name, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(level, subject_id, chapter_id, topic_id) 
                    DO UPDATE SET
                        job_id = excluded.job_id,
                        video_name = excluded.video_name,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                """, (
                    level, subject_id, subject_name, chapter_id, chapter_name,
                    topic_id, topic_name, job_id, video_name, status,
                    now, now
                ))
                
                conn.commit()
                logger.info(f"Saved animation to database: job_id={job_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to save animation: {e}")
            return False
    
    def update_animation_status(
        self,
        job_id: str,
        status: str,
        video_name: Optional[str] = None
    ) -> bool:
        """
        Update animation status and video_name.
        Returns True on success, False on failure.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                
                if video_name:
                    cursor.execute("""
                        UPDATE animations 
                        SET status = ?, video_name = ?, updated_at = ?
                        WHERE job_id = ?
                    """, (status, video_name, now, job_id))
                else:
                    cursor.execute("""
                        UPDATE animations 
                        SET status = ?, updated_at = ?
                        WHERE job_id = ?
                    """, (status, now, job_id))
                
                conn.commit()
                
                if cursor.rowcount > 0:
                    logger.info(f"Updated animation status: job_id={job_id}, status={status}")
                    return True
                else:
                    logger.warning(f"No animation found to update: job_id={job_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to update animation status: {e}")
            return False
    
    def get_animation_by_job_id(self, job_id: str) -> Optional[Dict]:
        """Get animation data by job_id"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM animations WHERE job_id = ?
                """, (job_id,))
                
                row = cursor.fetchone()
                return dict(row) if row else None
                
        except Exception as e:
            logger.error(f"Error getting animation by job_id: {e}")
            return None
    
    def get_all_animations(self, limit: int = 100) -> List[Dict]:
        """Get all animations (most recent first)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM animations 
                    ORDER BY created_at DESC 
                    LIMIT ?
                """, (limit,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"Error getting all animations: {e}")
            return []
    
    def delete_animation(self, job_id: str) -> bool:
        """Delete animation by job_id"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    DELETE FROM animations WHERE job_id = ?
                """, (job_id,))
                
                conn.commit()
                
                if cursor.rowcount > 0:
                    logger.info(f"Deleted animation: job_id={job_id}")
                    return True
                else:
                    logger.warning(f"No animation found to delete: job_id={job_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to delete animation: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """Get database statistics"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Total animations
                cursor.execute("SELECT COUNT(*) as total FROM animations")
                total = cursor.fetchone()["total"]
                
                # By status
                cursor.execute("""
                    SELECT status, COUNT(*) as count 
                    FROM animations 
                    GROUP BY status
                """)
                by_status = {row["status"]: row["count"] for row in cursor.fetchall()}
                
                # By level
                cursor.execute("""
                    SELECT level, COUNT(*) as count 
                    FROM animations 
                    GROUP BY level
                    ORDER BY level
                """)
                by_level = {row["level"]: row["count"] for row in cursor.fetchall()}
                
                return {
                    "total_animations": total,
                    "by_status": by_status,
                    "by_level": by_level,
                    "database_path": str(self.db_path),
                    "database_size_mb": round(self.db_path.stat().st_size / (1024 * 1024), 2) if self.db_path.exists() else 0
                }
                
        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            return {"error": str(e)}

# Global database instance
animation_db = AnimationDatabase()