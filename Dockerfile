FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install minimal system dependencies required for Chrome
RUN apt-get update -qq && apt-get install -y --no-install-recommends -qq \
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

# Build Python package
RUN python3 setup.py sdist

# Install Python dependencies from built package
RUN python3 -m pip install --upgrade pip --quiet \
    && python3 -m pip install dist/*.tar.gz --quiet

# Default command
CMD ["python3", "-m", "browser_use"]
