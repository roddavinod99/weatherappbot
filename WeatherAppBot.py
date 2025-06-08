import tweepy
import requests
import os
import time
from datetime import datetime
import pytz
from flask import Flask
import logging

# --- Configuration ---
logging.basicConfig(level=logging.INFO)

# --- Constants ---
TWITTER_MAX_CHARS = 280
CITY_TO_MONITOR = "Gachibowli"
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
    """Retrieves an environment variable, raising an error if critical and not found."""
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
    bearer_token = get_env_variable("TWITTER_BEARER_TOKEN")
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")

    bot_api_client = tweepy.Client(
        bearer_token=bearer_token, consumer_key=consumer_key, consumer_secret=consumer_secret,
        access_token=access_token, access_token_secret=access_token_secret
    )
    logging.info("Twitter v2 client initialized successfully.")
except EnvironmentError as e:
    logging.error(f"Error initializing Twitter client due to missing environment variable: {e}")
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")

# --- Weather and Tweet Creation Functions ---
def get_weather(city):
    """Fetches current weather data for the specified city from OpenWeatherMap."""
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch weather.")
        return None

    url = f'https://api.openweathermap.org/data/2.5/weather?q={city},IN&appid={weather_api_key}&units=metric'
    try:
        weather_response = requests.get(url, timeout=10)
        weather_response.raise_for_status()
        return weather_response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching weather data for {city}: {err}")
        return None

def generate_dynamic_hashtags(weather_data, current_day):
    """Generates a list of hashtags based on weather conditions."""
    hashtags = {'#Gachibowli', '#Hyderabad', '#weatherupdate'}
    
    main_conditions = weather_data.get('main', {})
    weather_main_info = weather_data.get('weather', [{}])[0]
    wind_conditions = weather_data.get('wind', {})
    rain_info_api = weather_data.get('rain', {})
    
    temp_celsius = main_conditions.get('temp', 0)
    sky_description = weather_main_info.get('description', "").lower()
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6
    rain_1h = rain_info_api.get('1h', 0)

    if rain_1h > 0:
        hashtags.add('#HyderabadRains')
        hashtags.add('#rain')
    if temp_celsius > 35:
        hashtags.add('#Heatwave')
    if 'clear' in sky_description:
        hashtags.add('#SunnyDay')
    if wind_speed_kmh > 25: # Add hashtag for strong winds
        hashtags.add('#windy')
    if current_day in ['Saturday', 'Sunday']:
        hashtags.add('#WeekendWeather')

    return list(hashtags)

def create_weather_tweet_content(city, weather_data):
    """
    Creates the tweet body and a list of dynamic hashtags.
    Returns a tuple: (list_of_tweet_lines, list_of_hashtags)
    """
    if not weather_data:
        return (["Could not generate weather report: Data missing."], ["#error"])

    # --- Extract and Format Data ---
    weather_main_info = weather_data.get('weather', [{}])[0]
    main_conditions = weather_data.get('main', {})
    wind_conditions = weather_data.get('wind', {})
    rain_info_api = weather_data.get('rain', {})
    
    now = datetime.now(pytz.timezone('Asia/Kolkata'))
    current_day = now.strftime('%A')

    sky_description = weather_main_info.get('description', "N/A").title()
    temp_celsius = main_conditions.get('temp', 0)
    feels_like_celsius = main_conditions.get('feels_like', 0)
    humidity = main_conditions.get('humidity', 0)
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6
    wind_direction_cardinal = degrees_to_cardinal(wind_conditions.get('deg', 0))
    rain_1h = rain_info_api.get('1h', 0)
    
    # --- Dynamic Lines ---
    rain_forecast = f"☔ Rain Alert: {rain_1h:.2f} mm/hr" if rain_1h > 0 else "☔ No Rain Expected"
    
    if rain_1h > 0.5: closing_message = "Stay dry out there! 🌧️"
    elif temp_celsius > 35: closing_message = "It's a hot one! Stay cool and hydrated. ☀️"
    elif temp_celsius < 18: closing_message = "Brr, it's cool! Consider a light jacket. 🧣"
    else: closing_message = "Enjoy your day! 😊"

    # --- Assemble tweet content ---
    # MODIFIED GREETING LINE
    time_str = now.strftime("%I:%M %p") # e.g., 08:00 PM
    date_str = f"{now.day} {now.strftime('%B')}" # e.g., 8 June
    greeting_line = f"Hello, {city}!👋, {current_day} weather as of {date_str}, {time_str}:"

    tweet_lines = [
        greeting_line,
        f"☁️ Sky: {sky_description}",
        f"🌡️ Temp: {temp_celsius:.0f}°C(feels: {feels_like_celsius:.0f}°C)",
        f"💧 Humidity: {humidity:.0f}%",
        f"💨 Wind: {wind_speed_kmh:.0f} km/h from the {wind_direction_cardinal}",
        rain_forecast,
        "", # For a line break
        closing_message
    ]
    
    hashtags = generate_dynamic_hashtags(weather_data, current_day)
    
    return tweet_lines, hashtags

# --- Tweeting Function ---
def tweet_post(tweet_lines, hashtags):
    """
    Assembles and posts a tweet. If too long, it removes hashtags until it fits.
    The tweet body will NOT be modified.
    """
    if not all([tweet_lines, hashtags, bot_api_client, POST_TO_TWITTER_ENABLED]):
        if not POST_TO_TWITTER_ENABLED:
            logging.info(f"[TEST MODE] Skipping post. Content:\n" + "\n".join(tweet_lines) + "\n" + " ".join(hashtags))
            return True
        logging.error("Tweet posting prerequisites not met. Aborting.")
        return False
    
    # --- Assemble tweet and adjust for character limit by removing hashtags ---
    body = "\n".join(tweet_lines)
    
    # Start with all hashtags and remove them until the tweet fits.
    while hashtags:
        hashtag_str = " ".join(hashtags)
        full_tweet = f"{body}\n{hashtag_str}"
        if len(full_tweet) <= TWITTER_MAX_CHARS:
            break # Tweet is good to go
        
        # If too long, remove the last hashtag and try again
        hashtags.pop()
    else:
        # This block executes if the while loop finishes (i.e., all hashtags are removed)
        # The tweet is now just the body.
        full_tweet = body

    # The tweet body is now preserved at all costs. 
    # If the body alone is over the limit, the API will reject it, preventing a broken tweet.
    tweet_text = full_tweet
    
    # --- Post to Twitter ---
    try:
        bot_api_client.create_tweet(text=tweet_text)
        logging.info("Tweet posted successfully to Twitter!")
        logging.info(f"Final Tweet ({len(tweet_text)} chars): \n{tweet_text}")
        return True
    except tweepy.errors.TooManyRequests:
        logging.warning("Rate limit exceeded. Will not retry in this serverless model.")
        return False
    except tweepy.errors.TweepyException as err:
        # This will catch errors if the tweet is still too long after removing all hashtags.
        logging.error(f"Error posting tweet (it may be over the character limit): {err}")
        return False

# --- Core Task Logic ---
def perform_scheduled_tweet_task():
    """Main task to fetch weather, create tweet content, and post it."""
    logging.info(f"--- Running weather tweet job for {CITY_TO_MONITOR} ---")
    weather_data = get_weather(CITY_TO_MONITOR)
    if not weather_data:
        logging.warning(f"Could not retrieve weather for {CITY_TO_MONITOR}. Aborting.")
        return False

    tweet_lines, hashtags = create_weather_tweet_content(CITY_TO_MONITOR, weather_data)
    success = tweet_post(tweet_lines, hashtags)

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
    """Main endpoint for a scheduler to call, triggering the tweet task."""
    logging.info("'/run-tweet-task' endpoint triggered by a request.")
    success = perform_scheduled_tweet_task()
    if success:
        return "Tweet task executed successfully.", 200
    else:
        return "Tweet task execution failed or was skipped.", 500

# --- Main Execution Block for Local Development ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting Flask Server for local development on port {app_port} ---")
    app.run(host='0.0.0.0', port=app_port, debug=True)