# Dockerfile
# Use an official Python runtime as a parent image
# --- CHANGE HERE: from buster to bullseye ---
FROM python:3.9-slim-bullseye

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed dependencies specified in requirements.txt
# --no-cache-dir reduces the image size by not storing pip cache
RUN pip install --no-cache-dir -r requirements.txt

# Install fontconfig for fc-cache to work
RUN apt-get update && apt-get install -y --no-install-recommends \
    fontconfig \
    # Clean up apt caches to keep image size small
    && rm -rf /var/lib/apt/lists/*

# Copy the new Merriweather font files into the container
COPY Merriweather_36pt-MediumItalic.ttf /usr/share/fonts/truetype/merriweather/
COPY Merriweather_24pt-BoldItalic.ttf /usr/share/fonts/truetype/merriweather/

# Rebuild the font cache to make the new fonts available to the system
RUN fc-cache -f -v

# Copy the entire application code into the container
COPY . .

# Expose the port that the Flask app will listen on
# Cloud Run typically uses the PORT environment variable
ENV PORT 8080
EXPOSE $PORT

# Command to run the application using Gunicorn
# Gunicorn will serve your Flask app (app:app refers to 'app' variable in 'app.py')
# The --timeout 0 is often useful for long-running processes or to avoid timeouts with external APIs
# Before (incorrect):
# CMD ["gunicorn", "-b", "0.0.0.0:$PORT", "app:app"]

# After (correct):
CMD gunicorn -b 0.0.0.0:$PORT app:app