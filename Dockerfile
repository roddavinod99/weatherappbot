# Dockerfile

# Use an official lightweight Python image.
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file and install them
# This is done in a separate step for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code
COPY . .

# Set the command to run your app using Gunicorn
# The PORT environment variable is automatically set by Cloud Run.
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "main:app"]