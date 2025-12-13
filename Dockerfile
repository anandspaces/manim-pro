# Multi-stage build for optimized image size
FROM python:3.13-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    python3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set uv environment variables
ENV UV_SYSTEM_PYTHON=1

# Copy project files
COPY pyproject.toml .

# Install dependencies with uv
RUN uv pip install --no-cache .

# Verify manim installation
RUN python3 -c "import manim; print(f'Manim {manim.__version__} installed successfully')"

# ============================================
# Final stage
# ============================================
FROM python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    UV_SYSTEM_PYTHON=1

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core dependencies
    dvisvgm \
    ffmpeg \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    # 3D rendering support
    libglew2.2 \
    libglfw3 \
    libosmesa6 \
    # LaTeX support (minimal)
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-science \
    # Process management
    tini \
    # Utilities
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Create necessary directories with proper permissions
RUN mkdir -p /app/media /app/scripts /app/jobs /app/logs && \
    chmod -R 755 /app

# Verify installation
RUN python3 -c "from manim import *; print('✓ Manim imported successfully')" && \
    python3 -c "from manim import smooth, linear, rush_into; print('✓ Rate functions available')" && \
    python3 -c "import redis; print('✓ Redis client installed')" && \
    ffmpeg -version | head -n 1 && \
    echo "✓ Container build successful"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python3 -c "import requests; requests.get('http://localhost:8020/health')" || exit 1

# Expose port
EXPOSE 8020

# Use tini as entrypoint for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run the application
CMD ["python", "main.py"]