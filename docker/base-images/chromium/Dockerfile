ARG BASE_TAG=latest
FROM browseruse/base-system:${BASE_TAG}

WORKDIR /tmp
COPY pyproject.toml ./

# Install chromium browser using uvx playwright (no permanent playwright dependency)
RUN --mount=type=cache,target=/root/.cache,sharing=locked \
    echo "Installing chromium browser via uvx playwright..." && \
    pip install --no-cache-dir uv && \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright uvx playwright install chromium --with-deps --no-shell && \
    ln -s /opt/playwright/chromium-*/chrome-linux/chrome /usr/bin/chromium-browser && \
    chmod -R 755 /opt/playwright && \
    rm -f pyproject.toml
