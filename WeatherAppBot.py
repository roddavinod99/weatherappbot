import tweepy
import requests
import os
import time
from datetime import datetime
import pytz
from flask import Flask
import logging
import math
from PIL import Image
from io import BytesIO

# --- Configuration ---
# It's good practice to use standard logging, which integrates with Google Cloud's operations suite.
logging.basicConfig(level=logging.INFO)

# --- Constants ---
TWITTER_MAX_CHARS = 280
TWEET_BUFFER = 15  # For links or Twitter's own additions
EFFECTIVE_MAX_CHARS = TWITTER_MAX_CHARS - TWEET_BUFFER
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 15 * 60
CITY_TO_MONITOR = "Gachibowli" # << City is set here

# --- Map Configuration ---
MAP_ZOOM_LEVEL = 10  # Zoom level for the map (e.g., 10-12 is good for a city)
MAP_TILE_LAYER = "TA2" # Temperature at 2m. Other options: PR0 (Precipitation), WND (Wind)
MAP_GRID_SIZE = 2    # Creates a 2x2 grid of tiles (total 4 tiles) for a 512x512px image

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
# Using v1.1 for media uploads, and v2 for tweet creation
try:
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")

    # v2 Client for creating tweets
    bot_v2_client = tweepy.Client(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=access_token,
        access_token_secret=access_token_secret
    )
    
    # v1.1 API for media uploads (necessary for images)
    auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, access_token, access_token_secret)
    bot_v1_api = tweepy.API(auth)

    logging.info("Twitter v1.1 and v2 clients initialized successfully.")
except EnvironmentError as e:
    logging.error(f"Error initializing Twitter client due to missing environment variable: {e}")
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")
    bot_v2_client = None
    bot_v1_api = None

# --- Map Generation Functions (NEW) ---
def deg_to_tile_coords(lat, lon, zoom):
    """Converts lat/lon to slippy map tile coordinates."""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = int((lon + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)

def generate_weather_map(lat, lon, city):
    """
    Generates a weather map image by fetching and stitching tiles.
    Returns a file-like object (BytesIO) of the final image or None.
    """
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch map.")
        return None

    logging.info(f"Generating a {MAP_GRID_SIZE}x{MAP_GRID_SIZE} weather map for {city}.")
    
    center_x, center_y = deg_to_tile_coords(lat, lon, MAP_ZOOM_LEVEL)
    
    # Create a blank canvas for the final image
    stitched_image = Image.new('RGB', (256 * MAP_GRID_SIZE, 256 * MAP_GRID_SIZE))
    
    start_x = center_x - (MAP_GRID_SIZE // 2)
    start_y = center_y - (MAP_GRID_SIZE // 2)

    for x_offset in range(MAP_GRID_SIZE):
        for y_offset in range(MAP_GRID_SIZE):
            tile_x = start_x + x_offset
            tile_y = start_y + y_offset
            
            tile_url = (f"http://maps.openweathermap.org/maps/2.0/weather/{MAP_TILE_LAYER}/"
                        f"{MAP_ZOOM_LEVEL}/{tile_x}/{tile_y}?appid={weather_api_key}")
            
            try:
                response = requests.get(tile_url, timeout=10)
                response.raise_for_status()
                tile_image = Image.open(BytesIO(response.content)).convert("RGBA")
                
                # Paste the tile onto the canvas
                stitched_image.paste(tile_image, (x_offset * 256, y_offset * 256))

            except requests.exceptions.RequestException as err:
                logging.error(f"Failed to download map tile {tile_x},{tile_y}: {err}")
                # Paste a black square as a fallback for a missing tile
                black_tile = Image.new('RGB', (256, 256), color='black')
                stitched_image.paste(black_tile, (x_offset * 256, y_offset * 256))

    # Save the final image to a bytes buffer
    image_buffer = BytesIO()
    stitched_image.save(image_buffer, format='PNG')
    image_buffer.seek(0)
    
    logging.info("Successfully generated and stitched map image.")
    return image_buffer

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
        else:
            precipitation_status = "Light rain possible."

    tweet_lines = [
        f"Weather in {city} ({timestamp_str}):",
        f"Cond: {description_str}",
        f"Temp: {temp_str} (Feels like: {feels_like_str})",
        f"Humidity: {humidity_str}",
        f"Wind: {wind_str}",
        f"Clouds: {cloudiness_str}",
        precipitation_status,
        f"#OpenWeatherMap #{city.replace(' ', '')} #{MAP_TILE_LAYER}"
    ]

    my_tweet = "\n".join(tweet_lines)
    return my_tweet

# --- Tweeting Function (UPDATED) ---
def tweet_post(tweet_text, image_buffer=None):
    """
    Posts the given text and an optional image to Twitter.
    """
    if not all([tweet_text, bot_v1_api, bot_v2_client, POST_TO_TWITTER_ENABLED]):
        if not POST_TO_TWITTER_ENABLED:
            logging.info(f"[TEST MODE] Skipping actual post. Tweet content:\n{tweet_text}")
            if image_buffer:
                logging.info("[TEST MODE] An image would have been attached.")
            return True
        logging.error("Tweet posting prerequisites not met. Aborting.")
        return False

    if len(tweet_text) > TWITTER_MAX_CHARS:
        logging.warning("Tweet is too long, truncating...")
        tweet_text = tweet_text[:EFFECTIVE_MAX_CHARS] + "..."

    try:
        media_ids = []
        if image_buffer:
            # The v1.1 API is needed for media uploads
            logging.info("Uploading media to Twitter...")
            media = bot_v1_api.media_upload(filename="weather_map.png", file=image_buffer)
            media_ids.append(media.media_id)
            logging.info(f"Media uploaded successfully. Media ID: {media.media_id}")

        # The v2 client is used to create the tweet
        bot_v2_client.create_tweet(text=tweet_text, media_ids=media_ids if media_ids else None)
        logging.info("Tweet posted successfully to Twitter!")
        return True
    except tweepy.errors.TooManyRequests as err:
        logging.warning(f"Rate limit exceeded: {err}. Will not retry in this serverless model.")
        return False
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        return False
    return False

# --- Core Task Logic (UPDATED) ---
def perform_scheduled_tweet_task():
    """
    Main task to fetch weather, create a map and tweet, and post it.
    """
    logging.info(f"--- Running weather tweet job for {CITY_TO_MONITOR} ---")

    weather_data = get_weather(CITY_TO_MONITOR)
    if not weather_data:
        logging.warning(f"Could not retrieve weather data for {CITY_TO_MONITOR}. Aborting tweet task.")
        return False

    # Generate the tweet text
    weather_tweet_content = create_weather_tweet_from_data(CITY_TO_MONITOR, weather_data)
    
    # Generate the weather map
    map_image_buffer = None
    coords = weather_data.get('coord')
    if coords and 'lat' in coords and 'lon' in coords:
        map_image_buffer = generate_weather_map(coords['lat'], coords['lon'], CITY_TO_MONITOR)
    else:
        logging.warning("Latitude/Longitude not found in weather data. Skipping map generation.")

    # Post the tweet with or without the map
    success = tweet_post(weather_tweet_content, image_buffer=map_image_buffer)

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
    This is the main endpoint that Cloud Scheduler will call.
    It triggers the tweet-posting task.
    """
    logging.info("'/run-tweet-task' endpoint triggered by a request.")
    success = perform_scheduled_tweet_task()
    if success:
        return "Tweet task executed successfully.", 200
    else:
        return "Tweet task execution failed or was skipped.", 500

# --- Main Execution Block ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting Flask Server for local development on port {app_port} ---")
    app.run(host='0.0.0.0', port=app_port, debug=True)