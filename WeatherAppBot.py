import tweepy
import requests
import os
import time
from datetime import datetime
import pytz
from flask import Flask
import logging

# --- Configuration ---
# It's good practice to use standard logging, which integrates with Google Cloud's operations suite.
logging.basicConfig(level=logging.INFO)

# --- Constants ---
TWITTER_MAX_CHARS = 280
TWEET_BUFFER = 15  # For links or Twitter's own additions
EFFECTIVE_MAX_CHARS = TWITTER_MAX_CHARS - TWEET_BUFFER
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 15 * 60
CITY_TO_MONITOR = "Gachibowli" # << City is set here

# --- Test Mode Configuration ---
# Defaults to "true" if the environment variable is not set or is not "false"
POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "true").lower() == "true"

if not POST_TO_TWITTER_ENABLED:
    logging.warning("Twitter interactions are DISABLED (Test Mode).")
    logging.warning("To enable, set the environment variable POST_TO_TWITTER_ENABLED=true")
else:
    logging.info("Twitter interactions ARE ENABLED. Tweets will be posted to Twitter.")

# --- Flask App Initialization ---
# This creates a Flask web server application.
# On Cloud Run, this app will only be active when handling a request.
app = Flask(__name__)

# --- Helper Function for Environment Variables ---
def get_env_variable(var_name, critical=True):
    """
    Retrieves an environment variable.
    Raises EnvironmentError if a critical variable is not found.
    """
    value = os.environ.get(var_name)
    if value is None and critical:
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    return value

# --- Initialize Twitter API Client ---
bot_api_client = None
try:
    bearer_token = get_env_variable("TWITTER_BEARER_TOKEN")
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")

    bot_api_client = tweepy.Client(
        bearer_token=bearer_token,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=access_token,
        access_token_secret=access_token_secret
    )
    logging.info("Twitter v2 client initialized successfully.")
except EnvironmentError as e:
    logging.error(f"Error initializing Twitter client due to missing environment variable: {e}")
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")

# --- Weather Functions ---
def get_weather(city):
    """
    Fetches weather data for the specified city from OpenWeatherMap.
    Returns weather data as JSON or None if an error occurs.
    """
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch weather.")
        return None

    url = f'https://api.openweathermap.org/data/2.5/weather?q={city}&appid={weather_api_key}&units=metric'
    try:
        weather_response = requests.get(url, timeout=10)
        weather_response.raise_for_status()  # Raises HTTPError for bad responses
        return weather_response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching weather data for {city}: {err}")
        return None

def create_weather_tweet_from_data(city, weather_data):
    """
    Formats weather data into a tweet string.
    """
    if not weather_data:
        return f"Could not generate weather report for {city}: Data missing."

    weather_main_info = weather_data.get('weather', [{}])[0]
    main_conditions = weather_data.get('main', {})
    wind_conditions = weather_data.get('wind', {})
    rain_info_api = weather_data.get('rain', {})
    clouds_info = weather_data.get('clouds', {})
    alerts_api = weather_data.get('alerts')

    bot_operational_tz = pytz.timezone('Asia/Kolkata')
    now_for_tweet_header = datetime.now(bot_operational_tz)
    timestamp_str = now_for_tweet_header.strftime('%H:%M, %b %d, %Y')

    description_str = weather_main_info.get('description', "N/A").capitalize()
    temp_str = f"{main_conditions.get('temp'):.2f}°C" if main_conditions.get('temp') is not None else "N/A"
    feels_like_str = f"{main_conditions.get('feels_like'):.2f}°C" if main_conditions.get('feels_like') is not None else "N/A"
    humidity_str = f"{main_conditions.get('humidity'):.0f}%" if main_conditions.get('humidity') is not None else "N/A"
    wind_str = f"{wind_conditions.get('speed'):.2f} m/s" if wind_conditions.get('speed') is not None else "N/A"
    cloudiness_str = f"{clouds_info.get('all'):.0f}%" if clouds_info.get('all') is not None else "N/A"

    precipitation_status = "No rain expected."
    if rain_info_api:
        rain_1h = rain_info_api.get('1h')
        if rain_1h is not None and rain_1h > 0:
            precipitation_status = f"Rain (last 1h): {rain_1h:.2f} mm"
        else: # If 'rain' object exists but is empty, it may indicate light showers
            precipitation_status = "Light rain possible."

    tweet_lines = [
        f"Weather in {city} ({timestamp_str}):",
        f"Cond: {description_str}",
        f"Temp: {temp_str} (Feels like: {feels_like_str})",
        f"Humidity: {humidity_str}",
        f"Wind: {wind_str}",
        f"Clouds: {cloudiness_str}",
        precipitation_status,
        "#OpenWeatherMap #Gachibowli"
    ]

    my_tweet = "\n".join(tweet_lines)
    return my_tweet

# --- Tweeting Function ---
def tweet_post(tweet_text):
    """
    Posts the given text to Twitter.
    """
    if not all([tweet_text, bot_api_client, POST_TO_TWITTER_ENABLED]):
        if not POST_TO_TWITTER_ENABLED:
            logging.info(f"[TEST MODE] Skipping actual post. Tweet content:\n{tweet_text}")
            return True # In test mode, we consider this a success
        logging.error("Tweet posting prerequisites not met. Aborting.")
        return False

    if len(tweet_text) > TWITTER_MAX_CHARS:
        logging.warning("Tweet is too long, truncating...")
        tweet_text = tweet_text[:EFFECTIVE_MAX_CHARS] + "..."

    try:
        bot_api_client.create_tweet(text=tweet_text)
        logging.info("Tweet posted successfully to Twitter!")
        return True
    except tweepy.TooManyRequests as err:
        logging.warning(f"Rate limit exceeded: {err}. Will not retry in this serverless model.")
        # In a serverless function, it's often better to fail fast and let the next scheduled run handle it,
        # rather than waiting and holding the instance active.
        return False
    except tweepy.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        return False
    return False

# --- Core Task Logic ---
def perform_scheduled_tweet_task():
    """
    Main task to fetch weather, create a tweet, and post it.
    This is the core logic that will be executed on each scheduled run.
    """
    logging.info(f"--- Running weather tweet job for {CITY_TO_MONITOR} ---")

    weather_data = get_weather(CITY_TO_MONITOR)
    if not weather_data:
        logging.warning(f"Could not retrieve weather data for {CITY_TO_MONITOR}. Aborting tweet task.")
        return False

    weather_tweet_content = create_weather_tweet_from_data(CITY_TO_MONITOR, weather_data)
    success = tweet_post(weather_tweet_content)

    if success:
        logging.info(f"Tweet task for {CITY_TO_MONITOR} completed successfully.")
    else:
        logging.warning(f"Tweet task for {CITY_TO_MONITOR} did not complete successfully.")
    return success

# --- Flask Routes ---
# These endpoints allow Cloud Run to receive HTTP requests.

@app.route('/')
def home():
    """A simple endpoint to check if the service is alive."""
    mode = "LIVE MODE" if POST_TO_TWITTER_ENABLED else "TEST MODE"
    return f"Weather Tweet Bot is alive! Current mode: {mode}", 200

@app.route('/run-tweet-task', methods=['POST', 'GET'])
def run_tweet_task_endpoint():
    """
    This is the main endpoint that Cloud Scheduler will call.
    It triggers the tweet-posting task.
    """
    logging.info("'/run-tweet-task' endpoint triggered by a request.")
    success = perform_scheduled_tweet_task()
    if success:
        return "Tweet task executed successfully.", 200
    else:
        # Return a non-200 status to indicate the task may not have completed.
        # This can be useful for monitoring in Cloud Scheduler.
        return "Tweet task execution failed or was skipped.", 500

# --- Main Execution Block ---
# This block is used for local development.
# When deployed to Cloud Run, a Gunicorn server is used to run the 'app' object directly,
# so this part of the code will not be executed.
if __name__ == "__main__":
    # The following line is for running the app locally for testing
    # e.g., python main.py
    # You would then open a browser to http://localhost:8080/run-tweet-task
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting Flask Server for local development on port {app_port} ---")
    app.run(host='0.0.0.0', port=app_port, debug=True)