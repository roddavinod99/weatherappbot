import tweepy
import requests
import os
import pytz
from datetime import datetime, timedelta
from flask import Flask
import logging
from PIL import Image, ImageDraw, ImageFont

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Constants ---
TWITTER_MAX_CHARS = 280
CITY_TO_MONITOR = "Gachibowli"
GENERATED_IMAGE_PATH = "weather_report.png"

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

# --- Initialize Twitter API Clients (v1.1 for media, v2 for tweets) ---
bot_api_client_v2 = None
bot_api_client_v1 = None
try:
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")

    # v2 client for creating tweets
    bot_api_client_v2 = tweepy.Client(
        consumer_key=consumer_key, consumer_secret=consumer_secret,
        access_token=access_token, access_token_secret=access_token_secret
    )

    # v1.1 client is needed for media uploads and metadata
    auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, access_token, access_token_secret)
    bot_api_client_v1 = tweepy.API(auth)

    logging.info("Twitter v1.1 and v2 clients initialized successfully.")
except EnvironmentError as e:
    logging.error(f"Error initializing Twitter clients due to missing environment variable: {e}")
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")

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
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching weather forecast data for {city}: {err}")
        return None

def generate_dynamic_hashtags(weather_data, current_day):
    """Generates a list of hashtags based on weather conditions."""
    hashtags = {'#Gachiboli', '#Hyderabad', '#weatherupdate'} # Using a set to avoid duplicates

    if not weather_data or 'list' not in weather_data or not weather_data['list']:
        return list(hashtags)

    current_weather = weather_data['list'][0]
    main_conditions = current_weather.get('main', {})
    weather_main_info = current_weather.get('weather', [{}])[0]
    wind_conditions = current_weather.get('wind', {})

    temp_celsius = main_conditions.get('temp', 0)
    sky_description = weather_main_info.get('description', "").lower()
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6

    # Check for rain in the upcoming forecast (within the next 12 hours - 4 intervals)
    # Adjusted to check actual forecast items, not just the first 4.
    indian_tz = pytz.timezone('Asia/Kolkata')
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    for item in weather_data.get('list', []):
        forecast_time_utc = datetime.fromtimestamp(item['dt'], tzinfo=pytz.utc)
        if now_utc <= forecast_time_utc <= now_utc + timedelta(hours=12):
            weather_item_info = item.get('weather', [{}])[0]
            if 'rain' in weather_item_info.get('main', '').lower() or (200 <= weather_item_info.get('id', 800) < 600):
                hashtags.add('#HyderabadRains')
                hashtags.add('#rain')
                break

    if temp_celsius > 35:
        hashtags.add('#Heatwave')
    if 'clear' in sky_description:
        hashtags.add('#SunnyDay')
    if wind_speed_kmh > 25:
        hashtags.add('#windy')
    if current_day in ['Saturday', 'Sunday']:
        hashtags.add('#WeekendWeather')

    return list(hashtags)

def create_weather_tweet_content(city, forecast_data):
    """
    Creates tweet body, hashtags, and determines if an image should be posted.
    Returns a dictionary with all necessary components for the tweet.
    """
    if not forecast_data or 'list' not in forecast_data or not forecast_data['list']:
        return {"lines": ["Could not generate weather report: Data missing."], "hashtags": ["#error"], "alt_text": ""}

    indian_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.now(indian_tz)
    current_day = now.strftime('%A')
    is_rain_forecasted = False

    # --- Current Weather Details for Alt Text ---
    # Find the current weather entry. The first entry in 'list' is usually the closest.
    current_weather_entry = None
    for entry in forecast_data['list']:
        entry_time = datetime.fromtimestamp(entry['dt'], tz=pytz.utc).astimezone(indian_tz)
        # Assuming the first entry is "current" or very close to it
        # Or, find the entry whose time is closest to 'now'
        if entry_time.hour == now.hour and entry_time.day == now.day: # Simplistic check, can be improved
            current_weather_entry = entry
            break
    if not current_weather_entry:
        current_weather_entry = forecast_data['list'][0] # Fallback to first if exact match not found
        
    main_conditions = current_weather_entry.get('main', {})
    wind_conditions = current_weather_entry.get('wind', {})
    weather_info = current_weather_entry.get('weather', [{}])[0]

    sky_description = weather_info.get('description', "N/A").title()
    temp_celsius = main_conditions.get('temp', 0)
    feels_like_celsius = main_conditions.get('feels_like', 0)
    humidity = main_conditions.get('humidity', 0)
    pressure_hpa = main_conditions.get('pressure', 0)
    visibility_km = current_weather_entry.get('visibility', 0) / 1000
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6
    wind_direction_cardinal = degrees_to_cardinal(wind_conditions.get('deg', 0))
    cloudiness = current_weather_entry.get('clouds', {}).get('all', 0)

    # --- ALT TEXT AND IMAGE CONTENT GENERATION ---
    alt_text_lines = []
    current_time_str = now.strftime('%I:%M %p')
    alt_text_lines.append(f"Current weather in {city} at {current_time_str}:")
    alt_text_lines.append(f"It's about {temp_celsius:.0f}°C, but feels like {feels_like_celsius:.0f}C with {sky_description.lower()} skies. Humidity is {humidity:.0f}%, pressure {pressure_hpa:.0f} hPa. Wind is {wind_speed_kmh:.0f} km/h from the {wind_direction_cardinal}. Visibility around {visibility_km:.0f} km, and cloudiness is {cloudiness:.0f}%.")
    alt_text_lines.append("\n-------------------><-----------------------\n")
    alt_text_lines.append("Here's what to expect for the next 12 hours:")

    # Filter forecast data for the next 12 hours
    forecast_intervals_to_display = []
    twelve_hours_from_now = now + timedelta(hours=12)

    for forecast in forecast_data['list']:
        forecast_time_utc = datetime.fromtimestamp(forecast['dt'], tz=pytz.utc)
        forecast_time_local = forecast_time_utc.astimezone(indian_tz)
        
        # We want forecasts starting from *after* the current time, up to 12 hours from now.
        # OpenWeatherMap's 'forecast' endpoint provides data at 3-hour steps.
        # We need 4 intervals to cover roughly 12 hours (e.g., 3, 6, 9, 12 hours from now).
        if forecast_time_local > now and len(forecast_intervals_to_display) < 4:
            forecast_intervals_to_display.append(forecast)

    for forecast in forecast_intervals_to_display:
        forecast_time_utc = datetime.fromtimestamp(forecast['dt'], tz=pytz.utc)
        forecast_time_local = forecast_time_utc.astimezone(indian_tz)

        temp = forecast.get('main', {}).get('temp', 0)
        forecast_weather_info = forecast.get('weather', [{}])[0]
        description = forecast_weather_info.get('description', 'N/A').title()
        pop = forecast.get('pop', 0) * 100
        rain_volume = forecast.get('rain', {}).get('3h', 0)

        weather_id = forecast_weather_info.get('id', 800)
        if 'rain' in forecast_weather_info.get('main', '').lower() or (200 <= weather_id < 600):
            is_rain_forecasted = True

        forecast_detail = f"By {forecast_time_local.strftime('%I %p')}: Expect {description} around {temp:.0f}°C. Chance of rain: {pop:.0f}%."
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
    greeting_line = f"Hello, {city}!👋, {current_day} weather as of {date_str}, {time_str}:"

    tweet_lines = [
        greeting_line,
        f"☁️ Sky: {sky_description}",
        f"🌡️ Temp: {temp_celsius:.0f}°C (feels: {feels_like_celsius:.0f}°C)",
        f"💧 Humidity: {humidity:.0f}%",
        f"💨 Wind: {wind_speed_kmh:.0f} km/h from the {wind_direction_cardinal}",
    ]

    if is_rain_forecasted:
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
        img_height = 350
        bg_color = (255, 255, 255)  # White background
        text_color = (0, 0, 0)      # Black text

        img = Image.new('RGB', (img_width, img_height), color=bg_color)
        d = ImageDraw.Draw(img)

        try:
            # Use Consolas font
            font_size = 17
            font = ImageFont.truetype("consolas.ttf", font_size)
        except IOError:
            logging.warning("consolas.ttf not found, using default font.")
            font = ImageFont.load_default()

        # Define padding for the text
        padding_x = 20
        padding_y = 20
        max_text_width = img_width - (2 * padding_x)
        
        y_text = padding_y

        # Process the input text line by line (respecting existing newlines)
        for original_line in image_text.split('\n'):
            words = original_line.split(' ')
            current_line_words = []
            
            # Wrap words for the current original line
            for word in words:
                # Test line with the next word to see if it fits
                test_line = ' '.join(current_line_words + [word])
                bbox = d.textbbox((0, 0), test_line, font=font)
                text_w = bbox[2] - bbox[0]

                if text_w <= max_text_width:
                    current_line_words.append(word)
                else:
                    # If it exceeds, draw the accumulated current_line and start a new one
                    if current_line_words: # Only draw if there are words to draw
                        line_to_draw = ' '.join(current_line_words)
                        # ALIGNMENT CHANGE: Set x_text to padding_x for left alignment
                        x_text = padding_x
                        d.text((x_text, y_text), line_to_draw, font=font, fill=text_color)
                        y_text += font_size + 5 # Move to next line (font_size + small line spacing)
                    current_line_words = [word] # Start new line with the current word

            # Draw any remaining words from the current original line or handle empty lines
            if current_line_words:
                line_to_draw = ' '.join(current_line_words)
                # ALIGNMENT CHANGE: Set x_text to padding_x for left alignment
                x_text = padding_x
                d.text((x_text, y_text), line_to_draw, font=font, fill=text_color)
                y_text += font_size + 5 # Move to next line (font_size + small line spacing)
            elif not original_line.strip(): # If it was an intentional blank line (e.g., "----><----")
                 y_text += font_size + 5 # Just add spacing for a blank line


        img.save(output_path)
        logging.info(f"Weather image created successfully at {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Error creating weather image: {e}")
        return None


# --- Tweeting Function ---
def tweet_post(tweet_content):
    """Assembles and posts a tweet, always with a dynamically generated image."""
    if not all([bot_api_client_v1, bot_api_client_v2]):
        logging.error("Twitter clients not initialized. Aborting tweet post.")
        return False
        
    if not POST_TO_TWITTER_ENABLED:
        logging.info("[TEST MODE] Skipping post.")
        logging.info("Tweet Content:\n" + "\n".join(tweet_content['lines']) + "\n" + " ".join(tweet_content['hashtags']))
        logging.info(f"[TEST MODE] Would generate and post image with alt text: {tweet_content['alt_text']}")
        
        # Generate the image even in test mode so user can see it
        generated_image_path = create_weather_image(tweet_content['image_content'])
        if generated_image_path:
            logging.info(f"Generated image for inspection: {generated_image_path}")
            logging.info("NOTE: This image file is NOT deleted in test mode for your inspection.")
        else:
            logging.error("Failed to generate image for inspection in test mode.")
        
        # IMPORTANT: We are NOT calling os.remove() here in test mode
        # The file will remain for you to inspect.
        
        return True # Indicate success in test mode

    body = "\n".join(tweet_content['lines'])
    hashtags = tweet_content['hashtags']

    full_tweet = f"{body}\n{' '.join(hashtags)}"
    if len(full_tweet) > TWITTER_MAX_CHARS:
        logging.warning("Tweet content + hashtags exceed character limit. Adjusting hashtags.")
        while hashtags and len(f"{body}\n{' '.join(hashtags)}") > TWITTER_MAX_CHARS:
            hashtags.pop()
        tweet_text = f"{body}\n{' '.join(hashtags)}" if hashtags else body
    else:
        tweet_text = full_tweet

    media_ids = []
    generated_image_path = create_weather_image(tweet_content['image_content']) # Use image_content for drawing
    
    if generated_image_path and os.path.exists(generated_image_path):
        try:
            logging.info(f"Uploading dynamically generated media: {generated_image_path}")
            media = bot_api_client_v1.media_upload(filename=generated_image_path)
            media_ids.append(media.media_id)
            bot_api_client_v1.create_media_metadata(media_id=media.media_id, alt_text=tweet_content['alt_text'])
            logging.info("Media uploaded and alt text added successfully.")
        except Exception as e:
            logging.error(f"Failed to upload media or add alt text: {e}")
        finally:
            # Clean up the generated image file after use (ONLY IN LIVE MODE)
            try:
                os.remove(generated_image_path)
                logging.info(f"Removed temporary image file: {generated_image_path}")
            except OSError as e:
                logging.warning(f"Error removing temporary image file {generated_image_path}: {e}")
    else:
        logging.error("Failed to generate weather image. Posting tweet without image.")

    try:
        bot_api_client_v2.create_tweet(text=tweet_text, media_ids=media_ids if media_ids else None)
        logging.info("Tweet posted successfully to Twitter!")
        logging.info(f"Final Tweet ({len(tweet_text)} chars): \n{tweet_text}")
        return True
    except tweepy.errors.TooManyRequests:
        logging.warning("Rate limit exceeded. Will not retry.")
        return False
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
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
    # Note: debug=True is for development only. Set to False in production.
    app.run(host='0.0.0.0', port=app_port, debug=True)