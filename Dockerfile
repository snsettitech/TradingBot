# TSXBot Dockerfile for Render
FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Install system dependencies for pyarrow
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Install the package
RUN pip install --no-cache-dir -e .

# Run the daily runner
CMD ["python", "-m", "tsxbot.scheduler.daily_runner"]
