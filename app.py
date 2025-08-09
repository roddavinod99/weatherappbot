import tweepy
import requests
import os
import pytz
from datetime import datetime, timedelta
from flask import Flask
import logging
from PIL import Image, ImageDraw, ImageFont
import time
import json

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Constants ---
TWITTER_MAX_CHARS = 280
CITY_TO_MONITOR = "Hyderabad"
GENERATED_IMAGE_PATH = "weather_report.png"

# Rate limiting for Free plan (17 posts per 24 hours)
MAX_POSTS_PER_24H = 15  # Set slightly below limit for safety
RATE_LIMIT_FILE = "/tmp/tweet_rate_limit.json"

POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "false").lower() == "true"

if not POST_TO_TWITTER_ENABLED:
    logging.warning("Twitter interactions are DISABLED (Test Mode).")
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

def check_rate_limit():
    """Check if we're within the Free plan rate limit (15 posts per 24h)."""
    try:
        if os.path.exists(RATE_LIMIT_FILE):
            with open(RATE_LIMIT_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {"posts": [], "last_reset": datetime.now().isoformat()}
        
        now = datetime.now()
        twenty_four_hours_ago = now - timedelta(hours=24)
        
        # Remove posts older than 24 hours
        recent_posts = [
            post_time for post_time in data["posts"] 
            if datetime.fromisoformat(post_time) > twenty_four_hours_ago
        ]
        
        data["posts"] = recent_posts
        
        # Save updated data
        with open(RATE_LIMIT_FILE, 'w') as f:
            json.dump(data, f)
        
        posts_in_24h = len(recent_posts)
        logging.info(f"Posts in last 24 hours: {posts_in_24h}/{MAX_POSTS_PER_24H}")
        
        if posts_in_24h >= MAX_POSTS_PER_24H:
            logging.warning(f"Rate limit reached: {posts_in_24h}/{MAX_POSTS_PER_24H} posts in 24h")
            return False
        
        return True
        
    except Exception as e:
        logging.error(f"Error checking rate limit: {e}")
        return True  # Allow posting if rate limit check fails

def record_successful_post():
    """Record a successful post for rate limiting."""
    try:
        if os.path.exists(RATE_LIMIT_FILE):
            with open(RATE_LIMIT_FILE, 'r') as f:
                data = json.load(f)
        else:
            data = {"posts": [], "last_reset": datetime.now().isoformat()}
        
        data["posts"].append(datetime.now().isoformat())
        
        with open(RATE_LIMIT_FILE, 'w') as f:
            json.dump(data, f)
            
        logging.info("Successful post recorded for rate limiting")
    except Exception as e:
        logging.error(f"Error recording successful post: {e}")

def degrees_to_cardinal(d):
    """Converts wind direction in degrees to a cardinal direction."""
    if d is None:
        return "N/A"
    try:
        d = float(d)
    except ValueError:
        return "N/A"
    
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((d + 11.25) / 22.5)
    return dirs[ix % 16]

# --- Enhanced Twitter API Initialization ---
bot_api_client_v2 = None
bot_api_client_v1 = None

def initialize_twitter_clients():
    """Initialize Twitter clients with enhanced error handling."""
    global bot_api_client_v2, bot_api_client_v1
    
    try:
        consumer_key = get_env_variable("TWITTER_API_KEY")
        consumer_secret = get_env_variable("TWITTER_API_SECRET")
        access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
        access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")

        # Validate that all credentials are present and not empty
        credentials = [consumer_key, consumer_secret, access_token, access_token_secret]
        if not all(cred and cred.strip() for cred in credentials):
            raise ValueError("One or more Twitter credentials are empty or contain only whitespace")

        # v2 client for creating tweets with user context
        bot_api_client_v2 = tweepy.Client(
            consumer_key=consumer_key, 
            consumer_secret=consumer_secret,
            access_token=access_token, 
            access_token_secret=access_token_secret,
            wait_on_rate_limit=True  # Handle rate limits automatically
        )

        # v1.1 client for media uploads with user context
        auth = tweepy.OAuth1UserHandler(
            consumer_key, 
            consumer_secret, 
            access_token, 
            access_token_secret
        )
        bot_api_client_v1 = tweepy.API(auth, wait_on_rate_limit=True)

        # Test the connection and verify write permissions
        try:
            # Test v2 client - get user info to verify connection
            user_info = bot_api_client_v2.get_me()
            logging.info(f"Twitter v2 client initialized successfully for user: @{user_info.data.username}")
            
            # Test v1.1 client - verify connection (removed the problematic get_rate_limit_status call)
            logging.info("Twitter v1.1 client initialized successfully")
            
        except tweepy.TooManyRequests:
            logging.warning("Rate limit hit during client verification - clients still initialized")
        except tweepy.Forbidden as e:
            logging.error(f"403 Forbidden during client verification: {e}")
            logging.error("This might indicate missing Write permissions or invalid tokens")
            logging.error("Please ensure your X app has Read+Write permissions and regenerate tokens")
        except Exception as e:
            logging.warning(f"Client verification failed but proceeding: {e}")

        logging.info("Twitter clients initialized with enhanced error handling")
        return True
        
    except EnvironmentError as e:
        logging.error(f"Missing environment variable: {e}")
        logging.error("Required: TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET")
        return False
    except ValueError as e:
        logging.error(f"Invalid credentials: {e}")
        return False
    except Exception as e:
        logging.critical(f"Unexpected error during Twitter client initialization: {e}")
        return False

def handle_twitter_error(error, operation):
    """Enhanced error handling for Twitter API errors."""
    if isinstance(error, tweepy.TooManyRequests):
        logging.warning(f"Rate limit exceeded for {operation}")
        return False
    elif isinstance(error, tweepy.Forbidden):
        error_msg = str(error)
        if "not permitted to perform this action" in error_msg.lower():
            logging.error(f"403 Forbidden: Missing permissions for {operation}")
            logging.error("Possible causes:")
            logging.error("1. App permissions don't include Write access")
            logging.error("2. Tokens were generated before setting Write permissions")
            logging.error("3. Free plan rate limit exceeded (17 posts/24h)")
            logging.error("4. Account suspended or restricted")
            logging.error("Solution: Check app permissions and regenerate tokens if needed")
        else:
            logging.error(f"403 Forbidden for {operation}: {error_msg}")
        return False
    elif isinstance(error, tweepy.Unauthorized):
        logging.error(f"401 Unauthorized for {operation}: Invalid credentials")
        return False
    elif isinstance(error, tweepy.NotFound):
        logging.error(f"404 Not Found for {operation}: Resource not found")
        return False
    elif isinstance(error, tweepy.TweepyException):
        logging.error(f"Twitter API error for {operation}: {error}")
        if hasattr(error, 'response') and error.response:
            try:
                error_data = error.response.json()
                logging.error(f"Error details: {error_data}")
            except:
                logging.error(f"Error response: {error.response.text}")
        return False
    else:
        logging.error(f"Unexpected error for {operation}: {error}")
        return False

# Initialize clients on startup
initialize_twitter_clients()

# --- Weather and Tweet Creation Functions ---
def get_weather_forecast(city):
    """Fetches 5-day/3-hour weather forecast data for the specified city."""
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Cannot fetch weather.")
        return None

    # Using the 'forecast' endpoint to get future data
    url = f'https://api.openweathermap.org/data/2.5/forecast?q={city},IN&appid={weather_api_key}&units=metric'
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching weather forecast data for {city}: {err}")
        return None

def generate_dynamic_hashtags(weather_data, current_day):
    """Generates a list of hashtags based on weather conditions."""
    hashtags = {'#Hyderabad', '#weatherupdate'} # Updated initial hashtags

    if not weather_data or 'list' not in weather_data or not weather_data['list']:
        logging.warning("No weather data available for hashtag generation.")
        return list(hashtags)

    # Use the first entry for current conditions for general hashtags
    current_weather = weather_data['list'][0] 
    main_conditions = current_weather.get('main', {})
    weather_main_info = current_weather.get('weather', [{}])[0]
    wind_conditions = current_weather.get('wind', {})

    temp_celsius = main_conditions.get('temp', 0)
    sky_description = weather_main_info.get('description', "").lower()
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6

    # Check for rain in the upcoming forecast (within the next 12 hours)
    indian_tz = pytz.timezone('Asia/Kolkata')
    now_utc = datetime.now(pytz.utc) 
    
    rain_forecasted_in_12_hours = False # Flag to ensure hashtag is added only once
    for item in weather_data.get('list', []):
        forecast_time_utc_aware = datetime.fromtimestamp(item['dt'], tz=pytz.utc)
        
        if now_utc <= forecast_time_utc_aware <= now_utc + timedelta(hours=12):
            weather_item_info = item.get('weather', [{}])[0]
            if 'rain' in weather_item_info.get('main', '').lower() or \
               (200 <= weather_item_info.get('id', 800) < 600) or \
               'rain' in item.get('dt_txt', '').lower():
                hashtags.add('#HyderabadRains')
                hashtags.add('#RainAlert')
                rain_forecasted_in_12_hours = True
                break

    if temp_celsius > 35:
        hashtags.add('#Heatwave')
    elif temp_celsius < 10:
        hashtags.add('#ColdWeather')

    if 'clear' in sky_description or 'sunny' in sky_description:
        hashtags.add('#SunnyDay')
    elif 'cloud' in sky_description:
        hashtags.add('#Cloudy')
    
    if wind_speed_kmh > 25:
        hashtags.add('#WindyWeather')
    
    if current_day in ['Saturday', 'Sunday']:
        hashtags.add('#WeekendWeather')
    
    hashtags.add(f'#{CITY_TO_MONITOR.replace(" ", "")}')

    return list(hashtags)

def create_weather_tweet_content(city, forecast_data):
    """
    Creates tweet body, hashtags, and determines if an image should be posted.
    Returns a dictionary with all necessary components for the tweet.
    """
    if not forecast_data or 'list' not in forecast_data or not forecast_data['list']:
        logging.error("Missing or invalid forecast data for tweet content creation.")
        return {"lines": ["Could not generate weather report: Data missing."], "hashtags": ["#error"], "alt_text": "", "image_content": "No weather data available."}

    indian_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.now(indian_tz) # Current time in Indian timezone
    current_day = now.strftime('%A')
    is_rain_forecasted_for_tweet = False # Separate flag for tweet message vs hashtag

    # --- Current Weather Details for Alt Text / Image ---
    current_weather_entry = forecast_data['list'][0] 
    
    main_conditions = current_weather_entry.get('main', {})
    wind_conditions = current_weather_entry.get('wind', {})
    weather_info = current_weather_entry.get('weather', [{}])[0]

    sky_description = weather_info.get('description', "N/A").title()
    temp_celsius = main_conditions.get('temp', None)
    feels_like_celsius = main_conditions.get('feels_like', None)
    humidity = main_conditions.get('humidity', None)
    pressure_hpa = main_conditions.get('pressure', None)
    visibility_meters = current_weather_entry.get('visibility', None) 
    wind_speed_mps = wind_conditions.get('speed', None)
    wind_direction_deg = wind_conditions.get('deg', None)
    cloudiness = current_weather_entry.get('clouds', {}).get('all', None)

    # Convert units safely
    temp_celsius_str = f"{temp_celsius:.0f}" if temp_celsius is not None else "N/A"
    feels_like_celsius_str = f"{feels_like_celsius:.0f}" if feels_like_celsius is not None else "N/A"
    humidity_str = f"{humidity:.0f}" if humidity is not None else "N/A"
    pressure_hpa_str = f"{pressure_hpa:.0f}" if pressure_hpa is not None else "N/A"
    visibility_km_str = f"{visibility_meters / 1000:.0f}" if visibility_meters is not None else "N/A"
    wind_speed_kmh_str = f"{wind_speed_mps * 3.6:.0f}" if wind_speed_mps is not None else "N/A"
    wind_direction_cardinal = degrees_to_cardinal(wind_direction_deg)
    cloudiness_str = f"{cloudiness:.0f}" if cloudiness is not None else "N/A"

    # --- ALT TEXT AND IMAGE CONTENT GENERATION ---
    alt_text_lines = []
    current_time_str = now.strftime('%I:%M %p')
    alt_text_lines.append(f"Current weather in {city} at {current_time_str}:")
    alt_text_lines.append(f"It's about {temp_celsius_str}°C, but feels like {feels_like_celsius_str}°C with {sky_description.lower()} skies.")
    alt_text_lines.append(f"Humidity is {humidity_str}%, pressure {pressure_hpa_str} hPa. Wind is {wind_speed_kmh_str} km/h from the {wind_direction_cardinal}.")
    alt_text_lines.append(f"Visibility around {visibility_km_str} km, and cloudiness is {cloudiness_str}%.")
    alt_text_lines.append("\n-------------------><-----------------------\n")
    alt_text_lines.append("Here's what to expect for the next 12 hours:")

    # Filter forecast data for the next 12 hours (4 intervals of 3 hours)
    forecast_intervals_to_display = []
    twelve_hours_from_now = now + timedelta(hours=12)

    for forecast in forecast_data['list']:
        forecast_time_utc_aware = datetime.fromtimestamp(forecast['dt'], tz=pytz.utc)
        forecast_time_local = forecast_time_utc_aware.astimezone(indian_tz)
        
        if forecast_time_local > now: # Only consider future forecasts
            forecast_intervals_to_display.append(forecast)
            if len(forecast_intervals_to_display) >= 4: # Take only the next 4
                break 

    if not forecast_intervals_to_display:
        alt_text_lines.append("No future forecast intervals available to display.")

    for forecast in forecast_intervals_to_display:
        forecast_time_utc_aware = datetime.fromtimestamp(forecast['dt'], tz=pytz.utc)
        forecast_time_local = forecast_time_utc_aware.astimezone(indian_tz)

        temp = forecast.get('main', {}).get('temp', None)
        forecast_weather_info = forecast.get('weather', [{}])[0]
        description = forecast_weather_info.get('description', 'N/A').title()
        pop = forecast.get('pop', None)
        rain_volume = forecast.get('rain', {}).get('3h', 0)

        weather_id = forecast_weather_info.get('id', 800)
        if 'rain' in forecast_weather_info.get('main', '').lower() or (200 <= weather_id < 600):
            is_rain_forecasted_for_tweet = True

        temp_str = f"{temp:.0f}°C" if temp is not None else "N/A"
        pop_str = f"{pop * 100:.0f}%" if pop is not None else "N/A"

        forecast_detail = f"By {forecast_time_local.strftime('%I %p')}: Expect {description} around {temp_str}. Chance of rain: {pop_str}."
        if rain_volume > 0:
            forecast_detail += f" ({rain_volume:.1f}mm expected)."
        alt_text_lines.append(forecast_detail)

    alt_text_summary = "\n".join(alt_text_lines)
    if len(alt_text_summary) > 1000:
        logging.warning(f"Alt text exceeded 1000 characters ({len(alt_text_summary)}). Truncating.")
        alt_text_summary = alt_text_summary[:997] + "..."

    # --- Main Tweet Content ---
    time_str = now.strftime("%I:%M %p")
    date_str = f"{now.day} {now.strftime('%B')}"
    greeting_line = f"Hello, {city}! 👋, {current_day} weather as of {date_str}, {time_str}:"

    tweet_lines = [
        greeting_line,
        f"☁️ Sky: {sky_description}",
        f"🌡️ Temp: {temp_celsius_str}°C (feels: {feels_like_celsius_str}°C)",
        f"💧 Humidity: {humidity_str}%",
        f"💨 Wind: {wind_speed_kmh_str} km/h from the {wind_direction_cardinal}",
    ]

    if is_rain_forecasted_for_tweet:
        tweet_lines.append("Heads up! Looks like rain is on the way. Stay dry! 🌧️")
        closing_message = ""
    else:
        tweet_lines.append("☔ No significant rain expected soon.")
        closing_message = "Have a great day! 😊"

    tweet_lines.extend(["", closing_message])
    
    hashtags = generate_dynamic_hashtags(forecast_data, current_day)

    return {
        "lines": tweet_lines,
        "hashtags": hashtags,
        "alt_text": alt_text_summary,
        "image_content": alt_text_summary # Pass the text for the image
    }

def create_weather_image(image_text, output_path=GENERATED_IMAGE_PATH):
    """
    Generates an image with the weather report text, preserving newlines and wrapping long lines,
    aligned to the left.
    """
    try:
        img_width = 800
        img_height = 350 # Increased height again to ensure ample space
        bg_color = (34,71,102)  # Light gray background for better readability
        text_color = (255,255,255)   # Dark gray text

        img = Image.new('RGB', (img_width, img_height), color=bg_color)
        d = ImageDraw.Draw(img)

        try:
            # Prefer common system fonts for better compatibility.
            # You might need to adjust these paths or names based on your OS.
            font_paths = ["consolas.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Regular.ttf"]
            selected_font_path = None
            for fp in font_paths:
                if os.path.exists(fp):
                    selected_font_path = fp
                    break
            
            if selected_font_path:
                font = ImageFont.truetype(selected_font_path, 17)
                font_size = 17
            else:
                logging.warning("No suitable custom font found, using default PIL font.")
                font = ImageFont.load_default()
                font_size = 10 # Default font is smaller
            line_height = font_size + 7 

        except IOError:
            logging.warning("Failed to load specified fonts, using default PIL font.")
            font = ImageFont.load_default()
            font_size = 10
            line_height = font_size + 3

        padding_x = 20
        padding_y = 20
        max_text_width = img_width - (2 * padding_x)
        
        y_text = padding_y

        # Process the input text line by line (respecting existing newlines)
        for original_line in image_text.split('\n'):
            # Special handling for the separator line to ensure it's not wrapped
            if original_line.strip() == "-------------------><-----------------------":
                d.text((padding_x, y_text), original_line.strip(), font=font, fill=text_color)
                y_text += line_height
                continue # Move to the next original line

            words = original_line.split(' ')
            current_line_words = []
            
            if not original_line.strip():
                y_text += line_height 
                continue

            for word in words:
                test_line = ' '.join(current_line_words + [word])
                
                try:
                    text_w = d.textlength(test_line, font=font)
                except AttributeError:
                    bbox = d.textbbox((0, 0), test_line, font=font)
                    text_w = bbox[2] - bbox[0]

                if text_w <= max_text_width:
                    current_line_words.append(word)
                else:
                    if current_line_words:
                        line_to_draw = ' '.join(current_line_words)
                        d.text((padding_x, y_text), line_to_draw, font=font, fill=text_color)
                        y_text += line_height
                    current_line_words = [word]

            if current_line_words:
                line_to_draw = ' '.join(current_line_words)
                d.text((padding_x, y_text), line_to_draw, font=font, fill=text_color)
                y_text += line_height

            if y_text >= img_height - padding_y:
                logging.warning(f"Image content exceeded image height. Truncating text in image.")
                break 

        img.save(output_path)
        logging.info(f"Weather image created successfully at {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Error creating weather image: {e}")
        return None

# --- Enhanced Tweeting Function ---
def tweet_post(tweet_content):
    """Enhanced tweet posting with comprehensive error handling and rate limiting."""
    if not all([bot_api_client_v1, bot_api_client_v2]):
        logging.error("Twitter clients not initialized. Aborting tweet post.")
        return False
        
    if not POST_TO_TWITTER_ENABLED:
        logging.info("[TEST MODE] Skipping actual Twitter post.")
        logging.info("Tweet Content:\n" + "\n".join(tweet_content['lines']) + "\n" + " ".join(tweet_content['hashtags']))
        
        generated_image_path = create_weather_image(tweet_content['image_content'])
        if generated_image_path:
            logging.info(f"Generated image for inspection: {generated_image_path}")
        return True

    # Check rate limit before posting
    if not check_rate_limit():
        logging.warning("Skipping post due to rate limit (Free plan: 15 posts/24h)")
        return False

    body = "\n".join(tweet_content['lines'])
    hashtags = tweet_content['hashtags']
    full_tweet = f"{body}\n{' '.join(hashtags)}"
    tweet_text = full_tweet
    
    if len(full_tweet) > TWITTER_MAX_CHARS:
        logging.warning("Tweet content + hashtags exceed character limit. Adjusting.")
        while hashtags and len(f"{body}\n{' '.join(hashtags)}") > TWITTER_MAX_CHARS:
            hashtags.pop()
        tweet_text = f"{body}\n{' '.join(hashtags)}" if hashtags else body
        
        if len(tweet_text) > TWITTER_MAX_CHARS:
            logging.warning("Tweet body too long. Truncating.")
            tweet_text = tweet_text[:TWITTER_MAX_CHARS - 3] + "..."

    # Handle media upload
    media_ids = []
    if tweet_content['image_content']:
        generated_image_path = create_weather_image(tweet_content['image_content'])
        
        if generated_image_path and os.path.exists(generated_image_path):
            try:
                logging.info(f"Uploading media: {generated_image_path}")
                media = bot_api_client_v1.media_upload(filename=generated_image_path)
                media_ids.append(media.media_id)
                
                # Add alt text
                bot_api_client_v1.create_media_metadata(
                    media_id=media.media_id_string, 
                    alt_text=tweet_content['alt_text']
                )
                logging.info("Media uploaded successfully with alt text")
                
            except Exception as e:
                if not handle_twitter_error(e, "media upload"):
                    logging.error("Media upload failed, posting without image")
            finally:
                try:
                    os.remove(generated_image_path)
                    logging.info(f"Removed temporary image: {generated_image_path}")
                except OSError as e:
                    logging.warning(f"Could not remove temp file: {e}")

    # Post the tweet with retries
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            if media_ids:
                response = bot_api_client_v2.create_tweet(text=tweet_text, media_ids=media_ids)
            else:
                response = bot_api_client_v2.create_tweet(text=tweet_text)
            
            # Success!
            tweet_id = response.data['id']
            logging.info(f"Tweet posted successfully! ID: {tweet_id}")
            logging.info(f"Tweet ({len(tweet_text)} chars): {tweet_text}")
            
            # Record successful post for rate limiting
            record_successful_post()
            return True
            
        except Exception as e:
            is_handled = handle_twitter_error(e, "create tweet")
            
            # Don't retry for permanent errors
            if isinstance(e, (tweepy.Forbidden, tweepy.Unauthorized, tweepy.NotFound)):
                logging.error("Permanent error - not retrying")
                break
                
            # Retry for temporary errors
            if attempt < max_retries - 1:
                logging.warning(f"Attempt {attempt + 1} failed, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logging.error(f"All {max_retries} attempts failed")
    
    return False

# --- Core Task Logic ---
def perform_scheduled_tweet_task():
    """Main task to fetch weather, create tweet content, and post it."""
    logging.info(f"--- Running weather tweet job for {CITY_TO_MONITOR} ---")
    forecast_data = get_weather_forecast(CITY_TO_MONITOR)
    if not forecast_data:
        logging.warning(f"Could not retrieve weather for {CITY_TO_MONITOR}. Aborting.")
        return False

    tweet_content = create_weather_tweet_content(CITY_TO_MONITOR, forecast_data)
    
    if "Could not generate weather report" in tweet_content['lines'][0]:
        logging.error("Tweet content generation failed. Aborting tweet post.")
        return False

    success = tweet_post(tweet_content)

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
