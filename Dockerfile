# Use a Python base image (e.g., 3.9-slim-buster or 3.10-slim-buster)
# -slim-buster is good for smaller images but requires explicit dependency installation.
FROM python:3.9-slim-buster

# Set working directory in the container
WORKDIR /app

# Install system dependencies required by Playwright browsers (especially Chromium)
# This list covers common dependencies needed for headless browser operation.
# --no-install-recommends makes the image smaller.
RUN apt-get update && apt-get install -y \
    libnss3 \
    libasound2 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm-dev \
    libgbm-dev \
    libgconf-2-4 \
    libgtk-3-0 \
    libxkbcommon-x11-0 \
    libxtst6 \
    xdg-utils \
    fonts-liberation \
    libappindicator3-1 \
    libu2f-udev \
    libvulkan1 \
    --no-install-recommends && rm -rf /var/lib/apt/lists/*

# Copy requirements.txt and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser executables INSIDE the container image
# This is THE step that downloads Chromium itself.
# Set PLAYWRIGHT_BROWSERS_PATH to a known, writable location.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install chromium --with-deps

# Copy the rest of your application code
COPY . .

# Command to run your Flask application
# Cloud Run will automatically set the PORT environment variable
CMD ["python", "WeatherAppBot.py"]