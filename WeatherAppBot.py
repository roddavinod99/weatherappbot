import tweepy
import requests
import os
import time
from datetime import datetime, timedelta
import pytz
import logging
from flask import Flask, request
import math # Added for map tile calculations
import io   # Added for handling image data in memory

# --- Constants ---
TWITTER_MAX_CHARS = 280
TWEET_BUFFER = 15 # Buffer for safety, potential URL shortening, or ellipsis
EFFECTIVE_MAX_CHARS = TWITTER_MAX_CHARS - TWEET_BUFFER
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 15 * 60 # 15 minutes
CITY_TO_MONITOR = "Gachibowli" # Define city here
MAP_TILE_ZOOM = 12 # Zoom level for the map tile (e.g., 10-15)
MAP_TILE_LAYER = "clouds_new" # Weather layer for the map (e.g., clouds_new, temp_new, precipitation_new)


# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.StreamHandler()
        # Add FileHandler if you want to log to a file as well:
        # logging.FileHandler('weather_bot.log', encoding='utf-8')
    ]
)

# --- Flask App ---
app = Flask(__name__)

# --- Environment Variable Handling ---
def get_env_variable(var_name, critical=True):
    """Fetches an environment variable."""
    value = os.environ.get(var_name)
    if value is None and critical:
        logging.critical(f"Critical environment variable '{var_name}' not found.")
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    elif value is None and not critical:
        logging.warning(f"Optional environment variable '{var_name}' not found.")
    return value

# --- Initialize Twitter API Client ---
bot_api_client = None
bot_api_v1_for_media = None # Added for media uploads
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

    # Initialize Tweepy API v1.1 for media uploads
    auth_v1 = tweepy.OAuth1UserHandler(
        consumer_key, consumer_secret, access_token, access_token_secret
    )
    bot_api_v1_for_media = tweepy.API(auth_v1)
    logging.info("Twitter v1.1 API for media initialized successfully.")

except EnvironmentError as e:
    logging.error(f"Error initializing Twitter client due to missing environment variable: {e}. The application might not function correctly.")
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")

# --- Weather Functions ---
def get_weather(city):
    """Fetches weather data for a given city from OpenWeatherMap."""
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch weather.")
        return None

    url = f'https://api.openweathermap.org/data/2.5/weather?q={city}&appid={weather_api_key}&units=metric'
    weather_response = None
    try:
        weather_response = requests.get(url, timeout=10) # Added timeout
        weather_response.raise_for_status()
        logging.info(f"Successfully fetched weather data for {city}.")
        return weather_response.json()
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error fetching weather data for {city}: {http_err} - Status Code: {weather_response.status_code if weather_response else 'N/A'}")
        if weather_response is not None:
            logging.error(f"Response text: {weather_response.text}")
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Error fetching weather data for {city}: {req_err}")
    return None

def create_weather_tweet_from_data(city, weather_data):
    """Creates the text for a weather tweet from pre-fetched data."""
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

        rain_forecast = "No rain detected in recent data." # Updated default message
        if rain_info:
            rain_volume_1h = rain_info.get('1h')
            rain_volume_3h = rain_info.get('3h')
            if rain_volume_1h is not None:
                rain_forecast = f"Rain (last 1h): {rain_volume_1h} mm."
            elif rain_volume_3h is not None: # Check 3h only if 1h is not present
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
            f"Credit:#OpenWeatherMap #{city.replace(' ', '')}Weather" # Tweaked hashtag
        )
        logging.debug(f"Generated tweet content ({len(my_tweet)} chars): {my_tweet}")
        return my_tweet
    else:
        error_message = f"Weather data for {city} was not provided or is empty."
        logging.warning(error_message)
        # Return a specific error string that tweet_post can check for, or handle upstream
        return f"Could not create tweet for {city} due to missing data."


# --- Map Tile Functions ---
def deg2num(lat_deg, lon_deg, zoom):
    """Converts latitude, longitude, and zoom level to tile numbers (x, y)."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def get_map_tile_image(lat, lon, zoom=MAP_TILE_ZOOM, layer=MAP_TILE_LAYER):
    """
    Fetches a specific weather map tile image from OpenWeatherMap.
    Returns image content (bytes) or None.
    """
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch map tile.")
        return None

    xtile, ytile = deg2num(lat, lon, zoom)
    tile_url = f"https://tile.openweathermap.org/map/{layer}/{zoom}/{xtile}/{ytile}.png?appid={weather_api_key}"
    logging.info(f"Fetching map tile from: {tile_url}")

    try:
        response = requests.get(tile_url, timeout=15)
        response.raise_for_status()
        if 'image/png' in response.headers.get('Content-Type', '').lower():
            logging.info(f"Successfully fetched map tile for layer {layer} at lat:{lat}, lon:{lon}.")
            return response.content
        else:
            logging.warning(f"Unexpected content type for map tile: {response.headers.get('Content-Type')}. URL: {tile_url} Response: {response.text[:200]}")
            return None
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error fetching map tile: {http_err} - Status: {response.status_code if response else 'N/A'} for URL: {tile_url}")
        if response is not None: logging.error(f"Response text: {response.text}")
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Error fetching map tile: {req_err} for URL: {tile_url}")
    return None

def tweet_post(tweet_text, media_id=None):
    """Posts the tweet to Twitter, handling length and rate limits, optionally with media."""
    if not bot_api_client:
        logging.critical("Twitter client not initialized. Cannot post tweet.")
        return False

    if not tweet_text:
        logging.warning("Tweet text is empty, cannot post.")
        return False

    # Check for specific error messages from tweet creation functions
    if "Could not retrieve weather data" in tweet_text or "Could not create tweet for" in tweet_text:
        logging.warning(f"Skipping tweet post due to data error: {tweet_text}")
        return False

    if len(tweet_text) > EFFECTIVE_MAX_CHARS:
        logging.warning(f"Tweet is too long ({len(tweet_text)} chars). Truncating to {EFFECTIVE_MAX_CHARS - 3} chars + '...'.")
        tweet_text = tweet_text[:EFFECTIVE_MAX_CHARS - 3] + "..."
        logging.info(f"Truncated tweet: {tweet_text}")

    logging.info(f"Attempting to post tweet: {tweet_text}" + (f" with media_id: {media_id}" if media_id else ""))
    try:
        if media_id:
            bot_api_client.create_tweet(text=tweet_text, media_ids=[media_id])
        else:
            bot_api_client.create_tweet(text=tweet_text)
        logging.info("Tweet posted successfully!")
        return True
    except tweepy.TooManyRequests as err:
        logging.warning(f"Rate limit exceeded: {err}")
        retry_after_seconds = DEFAULT_RATE_LIMIT_WAIT_SECONDS
        if err.response is not None and err.response.headers:
            x_rate_limit_reset_header = err.response.headers.get('x-rate-limit-reset')
            retry_after_header = err.response.headers.get('Retry-After') # Standard HTTP header
            if x_rate_limit_reset_header:
                try:
                    reset_timestamp = int(x_rate_limit_reset_header)
                    current_timestamp = int(time.time()) # Ensure int for comparison
                    wait_seconds = max(0, reset_timestamp - current_timestamp) + 5 # Add buffer
                    retry_after_seconds = wait_seconds
                except ValueError:
                    logging.warning(f"Could not parse x-rate-limit-reset header: {x_rate_limit_reset_header}")
            elif retry_after_header:
                try:
                    retry_after_seconds = int(retry_after_header) + 5 # Add buffer
                except ValueError:
                    logging.warning(f"Could not parse Retry-After header: {retry_after_header}")
        logging.info(f"Rate limit: Waiting for {retry_after_seconds:.0f} seconds before retrying...")
        time.sleep(retry_after_seconds)
        try:
            logging.info(f"Retrying to post tweet: {tweet_text}" + (f" with media_id: {media_id}" if media_id else ""))
            if media_id:
                bot_api_client.create_tweet(text=tweet_text, media_ids=[media_id])
            else:
                bot_api_client.create_tweet(text=tweet_text)
            logging.info("Tweet posted successfully after waiting!")
            return True
        except tweepy.TweepyException as retry_err:
            logging.error(f"Error posting tweet after waiting and retry: {retry_err}")
            if retry_err.response is not None: logging.error(f"Retry Response Text: {retry_err.response.text}")
        except Exception as e_retry:
            logging.error(f"An unexpected error occurred during retry tweeting: {e_retry}")
        return False # Explicitly return False if retry fails
    except tweepy.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        if err.response is not None: logging.error(f"Twitter API Response Text: {err.response.text}")
        if hasattr(err, 'api_errors') and err.api_errors: logging.error(f"Twitter API Errors Detail: {err.api_errors}")
        if hasattr(err, 'api_codes') and err.api_codes: logging.error(f"Twitter API Codes: {err.api_codes}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during tweeting: {e}")
    return False

# --- Task to be Performed on HTTP Request ---
def perform_scheduled_tweet_task():
    """
    Fetches weather, creates a tweet, optionally attaches a map image, and posts it.
    Returns True if tweet was posted successfully, False otherwise.
    """
    if not bot_api_client or not bot_api_v1_for_media:
        logging.error("Cannot perform tweet task: Twitter client(s) not available.")
        return False

    now_in_india = datetime.now(pytz.timezone('Asia/Kolkata'))
    logging.info(f"--- Running weather tweet job for {CITY_TO_MONITOR} at {now_in_india.strftime('%I:%M %p %Z%z')} ---")

    # 1. Get weather data (which includes coordinates)
    weather_data = get_weather(CITY_TO_MONITOR)
    if not weather_data:
        logging.warning(f"Could not retrieve weather data for {CITY_TO_MONITOR}. Aborting tweet task.")
        return False

    # 2. Create tweet text using the fetched data
    weather_tweet_content = create_weather_tweet_from_data(CITY_TO_MONITOR, weather_data)
    if "Could not create tweet for" in weather_tweet_content or not weather_tweet_content: # Check for error from creation
        logging.warning(f"Failed to generate tweet content: {weather_tweet_content}")
        return False

    # 3. Get coordinates for the map and attempt to fetch map tile
    media_id_str = None
    if 'coord' in weather_data:
        lat = weather_data['coord']['lat']
        lon = weather_data['coord']['lon']

        logging.info(f"Attempting to fetch map tile for {CITY_TO_MONITOR} (Lat: {lat}, Lon: {lon}, Zoom: {MAP_TILE_ZOOM}, Layer: {MAP_TILE_LAYER})")
        image_bytes = get_map_tile_image(lat, lon, zoom=MAP_TILE_ZOOM, layer=MAP_TILE_LAYER)

        if image_bytes:
            try:
                img_file = io.BytesIO(image_bytes) # Use io.BytesIO to treat bytes as a file
                uploaded_media = bot_api_v1_for_media.media_upload(filename="weather_map.png", file=img_file)
                media_id_str = uploaded_media.media_id_string
                logging.info(f"Map image successfully uploaded to Twitter. Media ID: {media_id_str}")
            except tweepy.TweepyException as e:
                logging.error(f"Twitter media upload failed: {e}")
                if hasattr(e, 'api_errors') and e.api_errors: logging.error(f"Twitter API Errors Detail: {e.api_errors}")
                if hasattr(e, 'api_codes') and e.api_codes: logging.error(f"Twitter API Codes: {e.api_codes}")
            except Exception as e:
                logging.error(f"An unexpected error occurred during media upload: {e}")
        else:
            logging.warning("Could not retrieve map image, will attempt to post tweet without map.")
    else:
        logging.warning("Coordinates not available in weather data, cannot fetch map tile.")

    # 4. Post the tweet (with or without media_id)
    success = tweet_post(weather_tweet_content, media_id=media_id_str)

    if success:
        logging.info(f"Tweet task for {CITY_TO_MONITOR} completed successfully.")
    else:
        logging.warning(f"Tweet task for {CITY_TO_MONITOR} did not complete successfully (tweet might have been skipped or failed).")
    return success

# --- Flask Routes ---
@app.route('/')
def home():
    """A simple health check endpoint."""
    logging.info("Home endpoint '/' pinged.")
    return "Weather Tweet Bot with Map Tile is alive!", 200

@app.route('/run-tweet-task', methods=['POST', 'GET'])
def run_tweet_task_endpoint():
    """ Endpoint to trigger the tweet task. """
    # Security considerations from original script can be added here if needed
    # (e.g., checking a secret header for unauthenticated Cloud Run services)
    logging.info("'/run-tweet-task' endpoint called.")
    
    # Basic check for critical components before attempting the task
    if not bot_api_client or not bot_api_v1_for_media or not get_env_variable("WEATHER_API_KEY", critical=False):
        logging.error("Tweet task cannot run due to missing critical configuration (Twitter clients or Weather API Key).")
        return "Tweet task failed due to missing critical configuration.", 500

    success = perform_scheduled_tweet_task()
    if success:
        return "Tweet task executed and posted successfully.", 200
    else:
        # Distinguish between configuration failure (already checked) and operational failure
        return "Tweet task attempted. Outcome: Check logs (may have failed or been skipped, e.g., no data or media issue).", 202


# --- Main Execution for Cloud Run ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting WeatherAppBot Flask Server on port {app_port} ---")
    # For production, use a WSGI server like Gunicorn.
    # Flask's dev server is fine for local testing or simple Cloud Run source deployments.
    app.run(host='0.0.0.0', port=app_port)