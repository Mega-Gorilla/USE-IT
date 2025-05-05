FROM python:3.11.3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install minimal system dependencies required for Chrome
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    unzip \
    libnss3 \
    libxss1 \
    libasound2 \
    libx11-xcb1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd appgroup && useradd -m -g appgroup appuser

WORKDIR /app

# Copy project files
COPY . /app/

# Change ownership
RUN chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Install Python dependencies
RUN pip install --upgrade pip \
    && pip install .

# Healthcheck (optional)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Default command
CMD ["python", "-m", "browser_use"]
