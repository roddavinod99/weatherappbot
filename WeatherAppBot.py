import tweepy
import requests
import os
import time
from datetime import datetime
import pytz
from flask import Flask
import logging

# --- Configuration ---
# Standard logging, which integrates with Google Cloud's operations suite.
logging.basicConfig(level=logging.INFO)

# --- Constants ---
TWITTER_MAX_CHARS = 280
CITY_TO_MONITOR = "Gachibowli"  # << City is set here
POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "true").lower() == "true"

if not POST_TO_TWITTER_ENABLED:
    logging.warning("Twitter interactions are DISABLED (Test Mode).")
    logging.warning("To enable, set the environment variable POST_TO_TWITTER_ENABLED=true")
else:
    logging.info("Twitter interactions ARE ENABLED. Tweets will be posted to Twitter.")

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions ---
def get_env_variable(var_name, critical=True):
    """
    Retrieves an environment variable.
    Raises EnvironmentError if a critical variable is not found.
    """
    value = os.environ.get(var_name)
    if value is None and critical:
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    return value

def degrees_to_cardinal(d):
    """Converts wind direction in degrees to a cardinal direction."""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((d + 11.25) / 22.5)
    return dirs[ix % 16]

# --- Initialize Twitter API Client ---
bot_api_client = None
try:
    # It's recommended to keep these checks to ensure the service can start up correctly.
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

    url = f'https://api.openweathermap.org/data/2.5/weather?q={city},IN&appid={weather_api_key}&units=metric'
    try:
        weather_response = requests.get(url, timeout=10)
        weather_response.raise_for_status()  # Raises HTTPError for bad responses
        return weather_response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching weather data for {city}: {err}")
        return None

def create_weather_tweet_from_data(city, weather_data):
    """
    Formats weather data into a tweet string based on the new template.
    """
    if not weather_data:
        return f"Could not generate weather report for {city}: Data missing."

    # --- Extract data from API response ---
    weather_main_info = weather_data.get('weather', [{}])[0]
    main_conditions = weather_data.get('main', {})
    wind_conditions = weather_data.get('wind', {})
    rain_info_api = weather_data.get('rain', {})

    # --- Get current day for the greeting ---
    current_day = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%A')

    # --- Format individual weather components ---
    sky_description = weather_main_info.get('description', "N/A").title()
    temp_celsius = main_conditions.get('temp', 0)
    feels_like_celsius = main_conditions.get('feels_like', 0)
    temp_str = f"{temp_celsius:.0f}°C(feels: {feels_like_celsius:.0f}°C)"

    humidity = main_conditions.get('humidity', 0)
    humidity_str = f"{humidity:.0f}%"

    wind_speed_ms = wind_conditions.get('speed', 0)
    wind_speed_kmh = wind_speed_ms * 3.6
    wind_direction_deg = wind_conditions.get('deg', 0)
    wind_direction_cardinal = degrees_to_cardinal(wind_direction_deg)
    wind_str = f"{wind_speed_kmh:.0f} km/h from the {wind_direction_cardinal}"

    # --- Dynamic Rain Forecast ---
    rain_1h = rain_info_api.get('1h', 0)
    if rain_1h > 0:
        rain_forecast = f"☔ Rain Alert: {rain_1h:.2f} mm of rain in the last hour."
    else:
        rain_forecast = "☔ No Rain Expected"

    # --- Dynamic Closing Message ---
    if rain_1h > 0.5: # Threshold for a more assertive rain message
        closing_message = "Stay dry out there! 🌧️"
    elif temp_celsius > 35:
        closing_message = "It's a hot one! Stay cool and hydrated. ☀️"
    elif temp_celsius < 18:
        closing_message = "Brr, it's cool! Consider a light jacket. 🧣"
    else:
        closing_message = "Enjoy your day! 😊"

    # --- Assemble the tweet ---
    tweet_lines = [
        f"Hello, {city}! 👋 Your {current_day} weather check-in:",
        f"☁️ Sky: {sky_description}",
        f"🌡️ Temp: {temp_str}",
        f"💧 Humidity: {humidity_str}",
        f"💨 Wind: {wind_str}",
        rain_forecast,
        "",  # For a line break
        closing_message,
        f"#{city} #Hyderabad #WeatherUpdate"
    ]

    return "\n".join(tweet_lines)


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
        logging.warning(f"Tweet is too long ({len(tweet_text)} chars). It may be truncated by Twitter.")
        # We let the API handle potential truncation instead of cutting it off ourselves
        # to preserve the closing message and hashtags.

    try:
        bot_api_client.create_tweet(text=tweet_text)
        logging.info("Tweet posted successfully to Twitter!")
        return True
    except tweepy.errors.TooManyRequests:
        logging.warning("Rate limit exceeded. Will not retry in this serverless model.")
        return False
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        return False


# --- Core Task Logic ---
def perform_scheduled_tweet_task():
    """
    Main task to fetch weather, create a tweet, and post it.
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
@app.route('/')
def home():
    """A simple endpoint to check if the service is alive."""
    mode = "LIVE MODE" if POST_TO_TWITTER_ENABLED else "TEST MODE"
    return f"Weather Tweet Bot is alive! Current mode: {mode}", 200

@app.route('/run-tweet-task', methods=['POST', 'GET'])
def run_tweet_task_endpoint():
    """
    This is the main endpoint that a scheduler will call to trigger the tweet-posting task.
    """
    logging.info("'/run-tweet-task' endpoint triggered by a request.")
    success = perform_scheduled_tweet_task()
    if success:
        return "Tweet task executed successfully.", 200
    else:
        # Return a non-200 status to indicate the task may not have completed.
        return "Tweet task execution failed or was skipped.", 500

# --- Main Execution Block for Local Development ---
if __name__ == "__main__":
    # This block allows you to run the app locally for testing
    # e.g., using 'python your_file_name.py'
    # You would then trigger the task by navigating to http://localhost:8080/run-tweet-task
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting Flask Server for local development on port {app_port} ---")
    app.run(host='0.0.0.0', port=app_port, debug=True)