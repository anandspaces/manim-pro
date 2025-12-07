# Multi-stage build for optimized image size
FROM python:3.13-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    pkg-config \
    libcairo2-dev \
    libpango1.0-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --user --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --user .

# Verify manim installation
RUN python3 -c "import manim; print(f'Manim {manim.__version__} installed successfully')"

# ============================================
# Final stage
# ============================================
FROM python:3.13-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/root/.local/bin:$PATH \
    DEBIAN_FRONTEND=noninteractive

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core dependencies
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
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .

# Create necessary directories with proper permissions
RUN mkdir -p /app/media /app/scripts /app/jobs /app/logs && \
    chmod -R 755 /app

# Verify installation
RUN python3 -c "from manim import *; print('✓ Manim imported successfully')" && \
    python3 -c "from manim import smooth, linear, rush_into; print('✓ Rate functions available')" && \
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