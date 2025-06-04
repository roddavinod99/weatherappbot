import tweepy
import requests
import os
import time
from datetime import datetime
import pytz # For timezone handling
from flask import Flask # Removed request as it's not used in the provided routes' logic

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
    print("WARNING: Twitter interactions are DISABLED (Test Mode). No actual tweets will be posted.")
    print("To enable Twitter interactions, set the environment variable POST_TO_TWITTER_ENABLED=true")
else:
    print("INFO: Twitter interactions ARE ENABLED. Tweets will be posted to Twitter.")

# --- Flask App ---
app = Flask(__name__)

# --- Environment Variable Handling ---
def get_env_variable(var_name, critical=True):
    """
    Retrieves an environment variable.
    Raises EnvironmentError if a critical variable is not found.
    Returns None if an optional variable is not found.
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
    # print("INFO: Twitter v2 client initialized successfully.") # Removed logging

except EnvironmentError as e:
    print(f"ERROR: Error initializing Twitter client due to missing environment variable: {e}. The application might not function correctly.")
except Exception as e:
    print(f"CRITICAL: An unexpected error occurred during Twitter client initialization: {e}")

# --- Weather Functions ---
def get_weather(city):
    """
    Fetches weather data for the specified city from OpenWeatherMap.
    Returns weather data as JSON or None if an error occurs.
    """
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        # print("ERROR: WEATHER_API_KEY not found. Cannot fetch weather.") # Removed logging
        return None

    url = f'https://api.openweathermap.org/data/2.5/weather?q={city}&appid={weather_api_key}&units=metric'
    weather_response = None
    try:
        weather_response = requests.get(url, timeout=10)
        weather_response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)
        return weather_response.json()
    except requests.exceptions.HTTPError as http_err:
        # print(f"ERROR: HTTP error fetching weather data for {city}: {http_err} - Status Code: {weather_response.status_code if weather_response else 'N/A'}") # Removed logging
        # if weather_response is not None: print(f"ERROR: Response text: {weather_response.text}") # Removed logging
        pass # Silently fail or handle as per application requirement
    except requests.exceptions.RequestException as req_err:
        # print(f"ERROR: Error fetching weather data for {city}: {req_err}") # Removed logging
        pass # Silently fail
    return None

def create_weather_tweet_from_data(city, weather_data):
    """
    Formats weather data into a tweet string.
    """
    if not weather_data:
        return f"Could not generate weather report for {city}: Data missing."

    # --- Extract data ---
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
    current_temp_val = main_conditions.get('temp')
    temp_str = f"{current_temp_val:.2f}" if current_temp_val is not None else "N/A"
    feels_like_val = main_conditions.get('feels_like')
    feels_like_str = f"{feels_like_val:.2f}" if feels_like_val is not None else "N/A"
    humidity_val = main_conditions.get('humidity')
    humidity_str = f"{humidity_val:.0f}" if humidity_val is not None else "N/A"
    wind_speed_val = wind_conditions.get('speed')
    wind_str = f"{wind_speed_val:.2f}" if wind_speed_val is not None else "N/A"

    precipitation_status_for_first_line = "0"
    detailed_rain_description_for_second_line = "No rain expected."

    if rain_info_api:
        rain_1h = rain_info_api.get('1h')
        rain_3h = rain_info_api.get('3h')
        if rain_1h is not None and rain_1h > 0:
            precipitation_status_for_first_line = "Active"
            detailed_rain_description_for_second_line = f"Rain (last 1h): {rain_1h:.2f} mm"
        elif rain_3h is not None and rain_3h > 0:
            precipitation_status_for_first_line = "Active"
            detailed_rain_description_for_second_line = f"Rain (last 3h): {rain_3h:.2f} mm"
    elif 'rain' in weather_main_info.get('main', '').lower() and not (rain_info_api and (rain_info_api.get('1h') or rain_info_api.get('3h'))):
        precipitation_status_for_first_line = "Likely"
        detailed_rain_description_for_second_line = "Light rain indicated."

    line_precipitation_status = f"prec:{precipitation_status_for_first_line}%" if precipitation_status_for_first_line == "0" else f"prec:{precipitation_status_for_first_line}"
    cloudiness_val = clouds_info.get('all')
    clouds_text_val = f"{cloudiness_val:.0f}" if cloudiness_val is not None else "N/A"
    line_clouds = f"Clouds:{clouds_text_val}%"

    tweet_lines = [
        f"Current weather in {city} ({timestamp_str}):",
        f"Weather Cond: {description_str}",
        f"Temp:{temp_str}°C (Feels:{feels_like_str}°C)",
        f"Hum:{humidity_str}%",
        f"Wind:{wind_str} m/s",
        line_precipitation_status,
        detailed_rain_description_for_second_line,
        line_clouds,
    ]

    if alerts_api and isinstance(alerts_api, list) and len(alerts_api) > 0:
        first_alert = alerts_api[0]
        alert_event = first_alert.get('event', "Weather advisory")
        max_alert_event_len = 46
        truncated_alert_event = (alert_event[:max_alert_event_len] + ("..." if len(alert_event) > max_alert_event_len else ""))
        tweet_lines.append(f"Alert: {truncated_alert_event}")

    tweet_lines.append("#OpenWeatherMap @OpenWeatherMap") # Corrected tag
    my_tweet = "\n".join(tweet_lines)

    if len(my_tweet) > TWITTER_MAX_CHARS:
        # print(f"WARNING: Generated tweet (len: {len(my_tweet)}) exceeds {TWITTER_MAX_CHARS} characters.") # Removed logging
        pass # Or implement truncation here if strictly needed before tweet_post

    return my_tweet

# --- Tweeting Function ---
def tweet_post(tweet_text):
    """
    Posts the given text to Twitter.
    Handles rate limits with a single retry.
    Returns True on success, False on failure.
    """
    if not tweet_text:
        return False
    if "Could not retrieve weather data" in tweet_text or "Could not generate weather report for" in tweet_text:
        return False

    if len(tweet_text) > TWITTER_MAX_CHARS:
        tweet_text = tweet_text[:EFFECTIVE_MAX_CHARS - 3] + "..."
    elif len(tweet_text) > EFFECTIVE_MAX_CHARS:
        pass # Potentially over effective max, but under absolute; let Twitter handle or adjust logic

    if not POST_TO_TWITTER_ENABLED:
        print(f"[TEST MODE] Skipping actual Twitter post. Tweet content:\n{tweet_text}")
        return True

    if not bot_api_client:
        # print("CRITICAL: Twitter client not initialized. Cannot post tweet.") # Removed logging
        return False

    try:
        bot_api_client.create_tweet(text=tweet_text)
        # print("INFO: Tweet posted successfully to Twitter!") # Removed logging
        return True
    except tweepy.TooManyRequests as err:
        # print(f"WARNING: Rate limit exceeded: {err}") # Removed logging
        retry_after_seconds = DEFAULT_RATE_LIMIT_WAIT_SECONDS
        if err.response is not None and err.response.headers:
            x_rate_limit_reset_header = err.response.headers.get('x-rate-limit-reset')
            retry_after_header = err.response.headers.get('Retry-After')
            if x_rate_limit_reset_header:
                try:
                    reset_timestamp = int(x_rate_limit_reset_header)
                    current_timestamp_epoch = int(time.time())
                    wait_seconds = max(0, reset_timestamp - current_timestamp_epoch) + 5
                    retry_after_seconds = wait_seconds
                except ValueError: pass # Silently use default
            elif retry_after_header:
                try: retry_after_seconds = int(retry_after_header) + 5
                except ValueError: pass # Silently use default
        
        # print(f"INFO: Rate limit: Waiting for {retry_after_seconds:.0f} seconds before retrying...") # Removed logging
        time.sleep(retry_after_seconds)
        try:
            bot_api_client.create_tweet(text=tweet_text)
            # print("INFO: Tweet posted successfully after waiting!") # Removed logging
            return True
        except tweepy.TweepyException as retry_err:
            # print(f"ERROR: Error posting tweet after waiting and retry: {retry_err}") # Removed logging
            # if retry_err.response is not None: print(f"ERROR: Retry Response Text: {retry_err.response.text}") # Removed logging
            return False
        except Exception: # Catch any other unexpected error during retry
            return False
    except tweepy.TweepyException as err:
        # print(f"ERROR: Error posting tweet: {err}") # Removed logging
        # if hasattr(err, 'response') and err.response is not None:
        #     print(f"ERROR: Twitter API Response Status: {err.response.status_code}") # Removed logging
        #     print(f"ERROR: Twitter API Response Text: {err.response.text}") # Removed logging
        return False
    except Exception: # Catch any other unexpected error
        return False
    return False


# --- Task to be Performed on HTTP Request ---
def perform_scheduled_tweet_task():
    """
    Main task to fetch weather, create a tweet, and post it.
    Returns True if the task was successful (or simulated successfully), False otherwise.
    """
    if not bot_api_client and POST_TO_TWITTER_ENABLED:
        # print("ERROR: Cannot perform tweet task: Twitter client v2 not properly initialized and in LIVE mode.") # Removed logging
        return False

    # bot_operational_tz = pytz.timezone('Asia/Kolkata') # Already defined in create_weather_tweet_from_data
    # now_for_log = datetime.now(bot_operational_tz) # Logging removed
    # print(f"--- Running weather tweet job for {CITY_TO_MONITOR} at {now_for_log.strftime('%H:%M %Z, %b %d %Y')} ---") # Logging removed

    weather_data = get_weather(CITY_TO_MONITOR)
    if not weather_data:
        # print(f"WARNING: Could not retrieve weather data for {CITY_TO_MONITOR}. Aborting tweet task.") # Removed logging
        return False

    weather_tweet_content = create_weather_tweet_from_data(CITY_TO_MONITOR, weather_data)
    if "Could not generate weather report for" in weather_tweet_content or not weather_tweet_content:
        # print(f"WARNING: Failed to generate tweet content: {weather_tweet_content}") # Removed logging
        return False

    success = tweet_post(weather_tweet_content)

    # if success:
    #     log_message_suffix = "(simulation)." if not POST_TO_TWITTER_ENABLED else "and posted to Twitter."
    #     print(f"INFO: Tweet task for {CITY_TO_MONITOR} completed successfully {log_message_suffix}") # Logging removed
    # else:
    #     log_prefix = "[TEST MODE] " if not POST_TO_TWITTER_ENABLED else ""
    #     print(f"WARNING: {log_prefix}Tweet task for {CITY_TO_MONITOR} did not complete successfully (tweet might have been skipped or failed).") # Logging removed
    return success

# --- Flask Routes ---
@app.route('/')
def home():
    mode = "LIVE MODE - Twitter interactions ENABLED" if POST_TO_TWITTER_ENABLED else "TEST MODE - Twitter interactions DISABLED"
    # print(f"Home endpoint '/' pinged. Current mode: {mode}") # Logging removed
    return f"Weather Tweet Bot is alive! Current mode: {mode}", 200

@app.route('/run-tweet-task', methods=['POST', 'GET'])
def run_tweet_task_endpoint():
    # print("INFO: '/run-tweet-task' endpoint called.") # Logging removed

    try:
        get_env_variable("WEATHER_API_KEY") # Check if critical key exists
    except EnvironmentError:
        # print("ERROR: Tweet task cannot run: WEATHER_API_KEY is missing.") # Logging removed
        return "Tweet task failed due to missing WEATHER_API_KEY.", 500

    success = perform_scheduled_tweet_task()
    mode_info = "(Simulated)" if not POST_TO_TWITTER_ENABLED else "(Live)"

    if success:
        return f"Tweet task executed {mode_info}. Outcome: Posted successfully or simulated successfully.", 200
    else:
        # For 202, it implies accepted but not completed, or an issue occurred.
        # Since we know the outcome from `success`, we can be more specific.
        # If it failed, a 500 or other appropriate error might be better if it's a server-side failure.
        # However, if it's "skipped" (e.g. data error), 202 or 200 with a specific message is okay.
        # Let's stick to 202 if not fully successful to indicate "processed, but check outcome".
        return f"Tweet task attempted {mode_info}. Outcome: Failed, skipped, or an error occurred. Check application output if available.", 202

# --- Main Execution for Cloud Run (or local Flask dev server) ---
if __name__ == "__main__":
    # For local development, you might want to load .env variables if you use python-dotenv
    # from dotenv import load_dotenv
    # load_dotenv()

    app_port = int(os.environ.get("PORT", 8080))
    print(f"--- Starting Weather Tweet Bot Flask Server on port {app_port} ---")
    # For production, use a WSGI server like gunicorn.
    # Example: gunicorn --bind 0.0.0.0:8080 main:app
    app.run(host='0.0.0.0', port=app_port, debug=False)
