# TSXBot Dockerfile for Render
FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Install system dependencies for pyarrow
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . .

# Install the package and all dependencies from pyproject.toml
RUN pip install --no-cache-dir -e .

# Run the daily runner
CMD ["python", "-m", "tsxbot.scheduler.daily_runner"]

