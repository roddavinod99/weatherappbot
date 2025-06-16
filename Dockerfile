# Use an official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED True
ENV APP_HOME /app
WORKDIR $APP_HOME

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code to the container
COPY . .

# Run the web server with Gunicorn
# It will listen on the port provided by Cloud Run ($PORT)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 300 app:app