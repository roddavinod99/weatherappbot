import tweepy
import requests
import os
import time
from datetime import datetime, timedelta
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
def get_weather_and_forecast(city):
    """Fetches current weather data and 5-day / 3-hour forecast for the specified city from OpenWeatherMap."""
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch weather.")
        return None, None

    # Current weather API
    # Note: Assuming 'IN' for India. If you need to monitor cities in other countries,
    # consider making the country code dynamic.
    current_weather_url = f'https://api.openweathermap.org/data/2.5/weather?q={city},IN&appid={weather_api_key}&units=metric'
    # Forecast API (5 day / 3 hour)
    forecast_url = f'https://api.openweathermap.org/data/2.5/forecast?q={city},IN&appid={weather_api_key}&units=metric'

    current_weather_data = None
    forecast_data = None

    try:
        weather_response = requests.get(current_weather_url, timeout=10)
        weather_response.raise_for_status()
        current_weather_data = weather_response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching current weather data for {city}: {err}")

    try:
        forecast_response = requests.get(forecast_url, timeout=10)
        forecast_response.raise_for_status()
        forecast_data = forecast_response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching forecast data for {city}: {err}")
    
    return current_weather_data, forecast_data

def generate_dynamic_hashtags(weather_data, current_day, rain_expected_6hr):
    """Generates a list of hashtags based on weather conditions."""
    hashtags = {'#Gachibowli', '#Hyderabad', '#weatherupdate', '#bot'}
    
    main_conditions = weather_data.get('main', {})
    weather_main_info = weather_data.get('weather', [{}])[0]
    wind_conditions = weather_data.get('wind', {})
    
    temp_celsius = main_conditions.get('temp', 0)
    sky_description = weather_main_info.get('description', "").lower()
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6

    if rain_expected_6hr:
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

def create_weather_tweet_content(city, current_weather_data, forecast_data):
    """
    Creates the tweet body and a list of dynamic hashtags.
    Returns a tuple: (list_of_tweet_lines, list_of_hashtags)
    """
    if not current_weather_data:
        return (["Could not generate current weather report: Data missing."], ["#error"])

    # --- Extract and Format Data from Current Weather ---
    weather_main_info = current_weather_data.get('weather', [{}])[0]
    main_conditions = current_weather_data.get('main', {})
    wind_conditions = current_weather_data.get('wind', {})
    
    now = datetime.now(pytz.timezone('Asia/Kolkata'))
    current_day = now.strftime('%A')

    sky_description = weather_main_info.get('description', "N/A").title()
    temp_celsius = main_conditions.get('temp', 0)
    feels_like_celsius = main_conditions.get('feels_like', 0) # Corrected variable name
    humidity = main_conditions.get('humidity', 0)
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6
    wind_direction_cardinal = degrees_to_cardinal(wind_conditions.get('deg', 0))

    # --- Check for rain in next 6 hours from forecast data ---
    rain_expected_6hr = False
    rain_chance_message = "☔ No Rain Expected in next 6 hrs"
    if forecast_data and 'list' in forecast_data:
        time_limit = now + timedelta(hours=6)
        for forecast_entry in forecast_data['list']:
            forecast_time_utc = datetime.fromtimestamp(forecast_entry['dt'], tz=pytz.utc)
            forecast_time_local = forecast_time_utc.astimezone(pytz.timezone('Asia/Kolkata'))

            if forecast_time_local <= time_limit:
                # Check for 'rain' object or 'pop' (probability of precipitation)
                if 'rain' in forecast_entry and forecast_entry['rain'].get('3h', 0) > 0: # 3h rain volume
                    rain_expected_6hr = True
                    rain_chance_message = "🌧️ Rain expected within next 6 hours!"
                    break
                elif forecast_entry.get('pop', 0) > 0.3: # Probability of Precipitation > 30%
                    rain_expected_6hr = True
                    rain_chance_message = "💧 High chance of rain within next 6 hours!"
                    break
            else:
                # Forecast entries are usually in chronological order, so we can stop if we pass the time limit
                break
    
    # --- Dynamic Lines ---
    if rain_expected_6hr: closing_message = "Stay dry out there! 🌧️"
    elif temp_celsius > 35: closing_message = "It's a hot one! Stay cool and hydrated. ☀️"
    elif temp_celsius < 18: closing_message = "Brr, it's cool! Consider a light jacket. 🧣"
    else: closing_message = "Enjoy your day! 😊"

    # --- Assemble tweet content ---
    time_str = now.strftime("%I:%M %p") # e.g., 08:00 PM
    date_str = f"{now.day} {now.strftime('%B')}" # e.g., 8 June
    greeting_line = f"Hello, {city}!👋, {current_day} weather as of {date_str}, {time_str}:"

    tweet_lines = [
        greeting_line,
        f"☁️ Sky: {sky_description}",
        f"🌡️ Temp: {temp_celsius:.0f}°C (feels: {feels_like_celsius:.0f}°C)", # Corrected here
        f"💧 Humidity: {humidity:.0f}%",
        f"💨 Wind: {wind_speed_kmh:.0f} km/h from the {wind_direction_cardinal}",
        rain_chance_message,
        "", # For a line break
        closing_message
    ]
    
    hashtags = generate_dynamic_hashtags(current_weather_data, current_day, rain_expected_6hr)
    
    return tweet_lines, hashtags

def tweet_post(original_tweet_lines, original_hashtags):
    """
    Assembles and posts a tweet. If too long, it first removes hashtags until it fits.
    If still too long, it removes the last line of the main tweet content.
    """
    if not all([original_tweet_lines, original_hashtags, bot_api_client, POST_TO_TWITTER_ENABLED]):
        if not POST_TO_TWITTER_ENABLED:
            logging.info(f"[TEST MODE] Skipping post. Content:\n" + "\n".join(original_tweet_lines) + "\n" + " ".join(original_hashtags))
            return True
        logging.error("Tweet posting prerequisites not met. Aborting.")
        return False
    
    current_tweet_lines = list(original_tweet_lines) # Make a mutable copy of tweet lines
    current_hashtags = list(original_hashtags)       # Make a mutable copy of hashtags

    tweet_text = ""
    # Flag to ensure we only try removing the last line of main content once.
    # This prevents an infinite loop if the tweet body itself is extremely long.
    last_content_line_removed = False

    while True:
        body = "\n".join(current_tweet_lines)
        hashtag_str = " ".join(current_hashtags)
        
        # Construct the full tweet string for length check
        if hashtag_str:
            full_tweet = f"{body}\n{hashtag_str}"
        else:
            full_tweet = body # No hashtags left, just the body

        if len(full_tweet) <= TWITTER_MAX_CHARS:
            tweet_text = full_tweet
            break # Tweet fits, exit the loop

        # If tweet is too long and we still have hashtags, remove one
        if current_hashtags:
            current_hashtags.pop() # Removes the last hashtag
            logging.info(f"Tweet too long, removed a hashtag. Remaining hashtags: {len(current_hashtags)}")
        # If no hashtags left AND we haven't already removed the last content line, and there's a line to remove
        elif not last_content_line_removed and len(current_tweet_lines) > 0:
            # Remove the last line from the tweet content (e.g., "Enjoy your day! 😊")
            current_tweet_lines.pop()
            last_content_line_removed = True
            logging.info("Tweet still too long after all hashtags removed. Sacrificing the last line of content.")
            # Continue the loop to re-check the length with the shorter body
        else:
            # All shortening attempts exhausted (hashtags removed, last content line removed or none existed)
            # and the tweet is still too long. Set tweet_text to the current state and break.
            logging.warning("Tweet still too long after all shortening attempts. Will attempt to post as is, but may fail due to Twitter character limit.")
            tweet_text = full_tweet
            break # Exit loop, let tweepy handle the API error if it occurs due to length

    # --- Post to Twitter ---
    try:
        # The 'tweet_text' variable now holds the final, shortened tweet content
        bot_api_client.create_tweet(text=tweet_text)
        logging.info("Tweet posted successfully to Twitter!")
        logging.info(f"Final Tweet ({len(tweet_text)} chars): \n{tweet_text}")
        return True
    except tweepy.errors.TooManyRequests:
        logging.warning("Rate limit exceeded. Will not retry in this serverless model.")
        return False
    except tweepy.errors.TweepyException as err:
        # This catches errors including "character limit exceeded" if the tweet is still too long
        logging.error(f"Error posting tweet (it may be over the character limit or other API issue): {err}")
        logging.error(f"Failed tweet content that was attempted to be posted:\n{tweet_text}")
        return False

# --- Core Task Logic ---
def perform_scheduled_tweet_task():
    """Main task to fetch weather, create tweet content, and post it."""
    logging.info(f"--- Running weather tweet job for {CITY_TO_MONITOR} ---")
    current_weather_data, forecast_data = get_weather_and_forecast(CITY_TO_MONITOR)
    if not current_weather_data:
        logging.warning(f"Could not retrieve current weather for {CITY_TO_MONITOR}. Aborting.")
        return False

    tweet_lines, hashtags = create_weather_tweet_content(CITY_TO_MONITOR, current_weather_data, forecast_data)
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
