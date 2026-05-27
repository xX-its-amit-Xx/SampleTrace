FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install build deps for any wheels that need compilation, then strip them.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY LICENSE ./

RUN pip install --no-cache-dir ".[benchling]"

# Drop build tools to keep the image lean.
RUN apt-get purge -y --auto-remove build-essential

# Default to mock-mode reconcile so `docker run sampletrace` does something
# meaningful out of the box. Overridable.
ENTRYPOINT ["sampletrace"]
CMD ["--help"]
