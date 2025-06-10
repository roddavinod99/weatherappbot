# Use a Python base image (e.g., 3.9-slim-buster)
FROM python:3.9-slim-buster

# Set working directory in the container
WORKDIR /app

# Install core system dependencies required by Playwright browsers (especially Chromium)
# This list is a comprehensive set for headless Chromium on Debian-based slim images.
# It explicitly includes generic font packages like 'fonts-liberation' and 'fontconfig'
# that are typically available and satisfy general font rendering needs, without relying
# on the specific 'ttf-ubuntu-font-family' which caused the error.
RUN apt-get update && apt-get install -y \
    fonts-liberation \
    fontconfig \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm-dev \
    libgbm-dev \
    libgconf-2-4 \
    libgtk-3-0 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxrender1 \
    libxshmfence1 \
    libxtst6 \
    xdg-utils \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements.txt and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser executables INSIDE the container image
# IMPORTANT CHANGE: Removed --with-deps to avoid the problematic font dependency.
# By explicitly installing essential system dependencies above, we can safely omit --with-deps.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install chromium

# Copy the rest of your application code
COPY . .

# Command to run your Flask application
# Cloud Run will automatically set the PORT environment variable
CMD ["python", "Weatherappbot.py"]