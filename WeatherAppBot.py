import tweepy
import requests
import os
import time
from datetime import datetime, timedelta
import pytz
import logging
from flask import Flask, request
# Removed: import math
# Removed: import io

# --- Constants ---
TWITTER_MAX_CHARS = 280
TWEET_BUFFER = 15
EFFECTIVE_MAX_CHARS = TWITTER_MAX_CHARS - TWEET_BUFFER
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 15 * 60
CITY_TO_MONITOR = "Gachibowli"
# Removed: MAP_TILE_ZOOM = 12
# Removed: MAP_TILE_LAYER = "clouds_new"

# --- Test Mode Configuration ---
# Set this environment variable to "true" (case-insensitive) to enable actual Twitter interactions.
# Defaults to True (live mode) if the environment variable is not set or not "true" as per your last provided snippet.
# To default to False (test mode), change "true" to "False" in os.environ.get("POST_TO_TWITTER_ENABLED", "False")
POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "true").lower() == "true"

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.StreamHandler()
        # logging.FileHandler('weather_bot.log', encoding='utf-8')
    ]
)

if POST_TO_TWITTER_ENABLED:
    logging.info("Twitter interactions ARE ENABLED. Tweets will be posted to Twitter.")
else:
    logging.warning("Twitter interactions are DISABLED (Test Mode). No actual tweets will be posted.")
    logging.warning("To enable Twitter interactions, set the environment variable POST_TO_TWITTER_ENABLED=true")


# --- Flask App ---
app = Flask(__name__)

# --- Environment Variable Handling ---
def get_env_variable(var_name, critical=True):
    value = os.environ.get(var_name)
    if value is None and critical:
        logging.critical(f"Critical environment variable '{var_name}' not found.")
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    elif value is None and not critical:
        logging.warning(f"Optional environment variable '{var_name}' not found.")
    return value

# --- Initialize Twitter API Client ---
bot_api_client = None
# Removed: bot_api_v1_for_media = None
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

    # Removed: Twitter v1.1 API for media initialization
    # auth_v1 = tweepy.OAuth1UserHandler(...)
    # bot_api_v1_for_media = tweepy.API(auth_v1)
    # logging.info("Twitter v1.1 API for media initialized successfully.")

except EnvironmentError as e:
    logging.error(f"Error initializing Twitter client due to missing environment variable: {e}. The application might not function correctly.")
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")

# --- Weather Functions ---
def get_weather(city):
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch weather.")
        return None

    url = f'https://api.openweathermap.org/data/2.5/weather?q={city}&appid={weather_api_key}&units=metric'
    weather_response = None
    try:
        weather_response = requests.get(url, timeout=10)
        weather_response.raise_for_status()
        logging.info(f"Successfully fetched weather data for {city}.")
        return weather_response.json()
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error fetching weather data for {city}: {http_err} - Status Code: {weather_response.status_code if weather_response else 'N/A'}")
        if weather_response is not None: logging.error(f"Response text: {weather_response.text}")
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Error fetching weather data for {city}: {req_err}")
    return None

def create_weather_tweet_from_data(city, weather_data):
    logging.info(f"Attempting to create weather tweet for {city} from provided data...")
    if weather_data:
        weather_main_info = weather_data.get('weather', [{}])[0]
        main_conditions = weather_data.get('main', {})
        wind_conditions = weather_data.get('wind', {})
        rain_info = weather_data.get('rain', {})

        weather_description = weather_main_info.get('description', 'Not available').capitalize()
        current_temp = main_conditions.get('temp', 'N/A')
        feels_like = main_conditions.get('feels_like', 'N/A')
        humidity = main_conditions.get('humidity', 'N/A')
        wind_speed = wind_conditions.get('speed', 'N/A')

        rain_forecast = "No rain detected in recent data."
        if rain_info:
            rain_volume_1h = rain_info.get('1h')
            rain_volume_3h = rain_info.get('3h')
            if rain_volume_1h is not None:
                rain_forecast = f"Rain (last 1h): {rain_volume_1h} mm."
            elif rain_volume_3h is not None:
                rain_forecast = f"Rain (last 3h): {rain_volume_3h} mm."
        elif 'rain' in [item.get('main', '').lower() for item in weather_data.get('weather', [])]:
            rain_forecast = "Rain indicated in general conditions."

        india_tz = pytz.timezone('Asia/Kolkata')
        now_in_india = datetime.now(india_tz)

        my_tweet = (
            f"{city} Weather ({now_in_india.strftime('%I:%M %p %Z, %b %d')}):\n"
            f"Cond: {weather_description}\n"
            f"Temp: {current_temp}°C (Feels: {feels_like}°C)\n"
            f"Humidity: {humidity}%\n"
            f"Wind: {wind_speed} m/s\n"
            f"{rain_forecast}\n"
            f"#OpenWeatherMap #{city.replace(' ', '')}Weather"
        )
        logging.debug(f"Generated tweet content ({len(my_tweet)} chars): {my_tweet}")
        return my_tweet
    else:
        error_message = f"Weather data for {city} was not provided or is empty."
        logging.warning(error_message)
        return f"Could not create tweet for {city} due to missing data."

# --- Removed Map Tile Functions ---
# def deg2num(lat_deg, lon_deg, zoom): ...
# def get_map_tile_image(lat, lon, zoom=MAP_TILE_ZOOM, layer=MAP_TILE_LAYER): ...

def tweet_post(tweet_text): # Removed media_id parameter
    """Posts the tweet to Twitter, or simulates if POST_TO_TWITTER_ENABLED is False."""
    if not tweet_text:
        logging.warning("Tweet text is empty, cannot post.")
        return False
    if "Could not retrieve weather data" in tweet_text or "Could not create tweet for" in tweet_text:
        logging.warning(f"Skipping tweet post due to data error: {tweet_text}")
        return False
    if len(tweet_text) > EFFECTIVE_MAX_CHARS:
        logging.warning(f"Tweet is too long ({len(tweet_text)} chars). Truncating to {EFFECTIVE_MAX_CHARS - 3} chars + '...'.")
        tweet_text = tweet_text[:EFFECTIVE_MAX_CHARS - 3] + "..."
        logging.info(f"Truncated tweet: {tweet_text}")

    log_prefix = "[TEST MODE] " if not POST_TO_TWITTER_ENABLED else ""
    logging.info(f"{log_prefix}Preparing to post tweet: '{tweet_text}'") # Removed media_id from log

    if not POST_TO_TWITTER_ENABLED:
        logging.info(f"{log_prefix}Skipping actual Twitter post as interactions are disabled.")
        return True

    if not bot_api_client:
        logging.critical("Twitter client not initialized. Cannot post tweet.")
        return False
        
    try:
        bot_api_client.create_tweet(text=tweet_text) # Simplified create_tweet call
        logging.info("Tweet posted successfully to Twitter!")
        return True
    except tweepy.TooManyRequests as err:
        logging.warning(f"Rate limit exceeded: {err}")
        retry_after_seconds = DEFAULT_RATE_LIMIT_WAIT_SECONDS
        if err.response is not None and err.response.headers:
            x_rate_limit_reset_header = err.response.headers.get('x-rate-limit-reset')
            retry_after_header = err.response.headers.get('Retry-After')
            if x_rate_limit_reset_header:
                try:
                    reset_timestamp = int(x_rate_limit_reset_header)
                    current_timestamp = int(time.time())
                    wait_seconds = max(0, reset_timestamp - current_timestamp) + 5
                    retry_after_seconds = wait_seconds
                except ValueError: logging.warning(f"Could not parse x-rate-limit-reset header: {x_rate_limit_reset_header}")
            elif retry_after_header:
                try: retry_after_seconds = int(retry_after_header) + 5
                except ValueError: logging.warning(f"Could not parse Retry-After header: {retry_after_header}")
        
        logging.info(f"Rate limit: Waiting for {retry_after_seconds:.0f} seconds before retrying...")
        time.sleep(retry_after_seconds)
        try:
            logging.info(f"Retrying to post tweet: {tweet_text}") # Removed media_id from log
            bot_api_client.create_tweet(text=tweet_text) # Simplified create_tweet call
            logging.info("Tweet posted successfully after waiting!")
            return True
        except tweepy.TweepyException as retry_err:
            logging.error(f"Error posting tweet after waiting and retry: {retry_err}")
            if retry_err.response is not None: logging.error(f"Retry Response Text: {retry_err.response.text}")
            return False
        except Exception as e_retry:
            logging.error(f"An unexpected error occurred during retry tweeting: {e_retry}")
            return False
    except tweepy.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        if err.response is not None: logging.error(f"Twitter API Response Text: {err.response.text}")
        if hasattr(err, 'api_errors') and err.api_errors: logging.error(f"Twitter API Errors Detail: {err.api_errors}")
        if hasattr(err, 'api_codes') and err.api_codes: logging.error(f"Twitter API Codes: {err.api_codes}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during tweeting: {e}")
        return False
    return False

# --- Task to be Performed on HTTP Request ---
def perform_scheduled_tweet_task():
    # Modified client check
    if not bot_api_client:
        if not POST_TO_TWITTER_ENABLED:
            logging.warning("[TEST MODE] Twitter client might not be fully available, but continuing in test mode.")
        else:
            logging.error("Cannot perform tweet task: Twitter client (v2) not properly initialized.")
            return False

    now_in_india = datetime.now(pytz.timezone('Asia/Kolkata'))
    logging.info(f"--- Running weather tweet job for {CITY_TO_MONITOR} at {now_in_india.strftime('%I:%M %p %Z%z')} ---")

    weather_data = get_weather(CITY_TO_MONITOR)
    if not weather_data:
        logging.warning(f"Could not retrieve weather data for {CITY_TO_MONITOR}. Aborting tweet task.")
        return False

    weather_tweet_content = create_weather_tweet_from_data(CITY_TO_MONITOR, weather_data)
    if "Could not create tweet for" in weather_tweet_content or not weather_tweet_content:
        logging.warning(f"Failed to generate tweet content: {weather_tweet_content}")
        return False

    # --- Removed map tile fetching and media upload logic ---
    # media_id_str = None
    # log_prefix = "[TEST MODE] " if not POST_TO_TWITTER_ENABLED else ""
    # if 'coord' in weather_data:
    # ... entire block removed ...
    # else:
    # logging.warning("Coordinates not available in weather data, cannot fetch map tile.")
    # --- End of removed block ---

    success = tweet_post(weather_tweet_content) # Call without media_id_str

    log_prefix = "[TEST MODE] " if not POST_TO_TWITTER_ENABLED else "" # Keep for consistent logging
    if success:
        if not POST_TO_TWITTER_ENABLED:
            logging.info(f"{log_prefix}Tweet task for {CITY_TO_MONITOR} completed successfully (simulation).")
        else:
            logging.info(f"Tweet task for {CITY_TO_MONITOR} completed successfully and posted to Twitter.")
    else:
        logging.warning(f"{log_prefix}Tweet task for {CITY_TO_MONITOR} did not complete successfully (tweet might have been skipped or failed).")
    return success

# --- Flask Routes ---
@app.route('/')
def home():
    mode = "LIVE MODE - Twitter interactions ENABLED" if POST_TO_TWITTER_ENABLED else "TEST MODE - Twitter interactions DISABLED"
    logging.info(f"Home endpoint '/' pinged. Current mode: {mode}")
    # Updated message to remove "with Map Tile"
    return f"Weather Tweet Bot is alive! Current mode: {mode}", 200

@app.route('/run-tweet-task', methods=['POST', 'GET'])
def run_tweet_task_endpoint():
    logging.info("'/run-tweet-task' endpoint called.")
    
    try:
        get_env_variable("WEATHER_API_KEY") 
    except EnvironmentError:
        logging.error("Tweet task cannot run: WEATHER_API_KEY is missing.")
        return "Tweet task failed due to missing WEATHER_API_KEY.", 500
    
    success = perform_scheduled_tweet_task()
    mode_info = "(Simulated)" if not POST_TO_TWITTER_ENABLED else "(Live)"

    if success:
        return f"Tweet task executed {mode_info}. Outcome: Posted successfully or simulated successfully.", 200
    else:
        return f"Tweet task attempted {mode_info}. Outcome: Check logs (may have failed or been skipped).", 202

# --- Main Execution for Cloud Run ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting WeatherAppBot Flask Server on port {app_port} ---")
    app.run(host='0.0.0.0', port=app_port)