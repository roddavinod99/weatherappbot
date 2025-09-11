import tweepy
import requests
import os
import pytz
from datetime import datetime
from flask import Flask
import logging
from PIL import Image, ImageDraw, ImageFont

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---
def get_env_variable(var_name, critical=True):
    """Retrieves an environment variable, raising an error if critical and not found."""
    value = os.environ.get(var_name)
    if value is None and critical:
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    return value

# --- Constants ---
TWITTER_MAX_CHARS = 280
# UPDATED: City is now configurable via an environment variable
CITY_TO_MONITOR = get_env_variable("CITY_TO_MONITOR", critical=False) or "Hyderabad"
GENERATED_IMAGE_PATH = "weather_report.png"

POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "false").lower() == "true"

if not POST_TO_TWITTER_ENABLED:
    logging.warning("Twitter interactions are DISABLED (Test Mode).")
    logging.warning("To enable, set the environment variable POST_TO_TWITTER_ENABLED=true")
else:
    logging.info("Twitter interactions ARE ENABLED. Tweets will be posted to Twitter.")

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions (Continued) ---
def degrees_to_cardinal(d):
    """Converts wind direction in degrees to a cardinal direction."""
    if d is None:
        return "N/A"
    try:
        d = float(d)
    except (ValueError, TypeError):
        return "N/A"
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((d + 11.25) / 22.5)
    return dirs[ix % 16]

def get_time_based_greeting(hour):
    """Returns 'Good morning', 'Good afternoon', or 'Good evening' based on the hour."""
    if 5 <= hour < 12:
        return "Good morning"
    elif 12 <= hour < 17:
        return "Good afternoon"
    else:
        return "Good evening"

def get_weather_mood(temp_c, hour):
    """Generates a dynamic mood phrase based on temperature and time of day."""
    if hour >= 22 or hour < 5:
        return "calm night"
    
    if temp_c > 35:
        return "warm afternoon" if hour >= 12 else "hot morning"
    elif temp_c < 20:
        return "cool morning" if hour < 12 else "chilly afternoon"
    else:
        return "pleasant day"

# --- Initialize Twitter API Clients (v1.1 for media, v2 for tweets) ---
bot_api_client_v2 = None
bot_api_client_v1 = None
try:
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")
    
    bot_api_client_v2 = tweepy.Client(
        consumer_key=consumer_key, consumer_secret=consumer_secret,
        access_token=access_token, access_token_secret=access_token_secret
    )
    auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, access_token, access_token_secret)
    bot_api_client_v1 = tweepy.API(auth)
    logging.info("Twitter v1.1 and v2 clients initialized successfully.")
except EnvironmentError as e:
    logging.error(f"Error initializing Twitter clients due to missing environment variable: {e}")
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")

# --- Weather and Data Fetching Functions ---
def get_city_coordinates(city, api_key):
    """Fetches latitude and longitude for a city using OpenWeatherMap Geocoding API."""
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={city},IN&limit=1&appid={api_key}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            return data[0]['lat'], data[0]['lon']
        else:
            logging.error(f"Could not find coordinates for city: {city}")
            return None, None
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching coordinates for {city}: {err}")
        return None, None

def get_one_call_weather_data(lat, lon, api_key):
    """Fetches weather data using OpenWeatherMap One Call API 3.0, including daily forecast."""
    if not lat or not lon:
        return None
    # Changed: Removed 'daily' from the exclude list to get the daily forecast
    url = f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&appid={api_key}&units=metric&exclude=minutely,alerts"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching One Call weather data: {err}")
        return None

# --- Tweet Content Creation and Formatting ---
def generate_dynamic_hashtags(weather_data, current_day):
    """Generates a list of hashtags based on weather conditions."""
    hashtags = {f'#{CITY_TO_MONITOR.replace(" ", "")}', '#weatherupdate'}

    if not weather_data or 'current' not in weather_data:
        logging.warning("No weather data available for hashtag generation.")
        return list(hashtags)

    current_weather = weather_data['current']
    temp_celsius = current_weather.get('temp', 0)
    sky_description = current_weather.get('weather', [{}])[0].get('description', "").lower()
    wind_speed_mps = current_weather.get('wind_speed', 0)
    
    rain_forecasted_in_12_hours = any(
        'rain' in hour.get('weather', [{}])[0].get('main', '').lower()
        for hour in weather_data.get('hourly', [])[:12]
    )

    if rain_forecasted_in_12_hours:
        hashtags.add(f'#{CITY_TO_MONITOR}Rains')
        hashtags.add('#RainAlert')
    if temp_celsius > 35:
        hashtags.add('#Heatwave')
    elif temp_celsius < 15:
        hashtags.add('#ColdWeather')
    if 'clear' in sky_description:
        hashtags.add('#SunnyDay')
    elif 'cloud' in sky_description:
        hashtags.add('#Cloudy')
    if wind_speed_mps * 3.6 > 25: # Convert m/s to km/h for check
        hashtags.add('#Windy')
    if current_day in ['Saturday', 'Sunday']:
        hashtags.add('#WeekendWeather')
    
    return list(hashtags)

def create_weather_tweet_content(city, weather_data):
    """Creates the new conversational tweet content, alt text, and image text."""
    if not weather_data or 'current' not in weather_data or 'hourly' not in weather_data:
        logging.error("Missing or invalid weather data for tweet content creation.")
        return {"lines": ["Could not generate weather report: Data missing."], "hashtags": ["#error"], "alt_text": "", "image_content": []}

    indian_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.now(indian_tz)
    current_day = now.strftime('%A')
    current_hour = now.hour

    # --- Extract Current Weather Data ---
    current = weather_data['current']
    temp_c = current.get('temp')
    feels_like_c = current.get('feels_like')
    humidity = current.get('humidity')
    wind_speed_mps = current.get('wind_speed')
    wind_direction_deg = current.get('wind_deg')
    sky_description_now = current.get('weather', [{}])[0].get('main', 'clouds').lower()
    
    # --- Check for future rain to make text dynamic ---
    future_rain_in_12_hours = any(hour.get('pop', 0) > 0.1 for hour in weather_data.get('hourly', [])[:12])
    
    # --- Data Conversion and Formatting ---
    temp_c_str = f"{temp_c:.0f}°C" if temp_c is not None else "N/A"
    feels_like_c_str = f"{feels_like_c:.0f}°C" if feels_like_c is not None else "N/A"
    humidity_str = f"{humidity:.0f}%" if humidity is not None else "N/A"
    wind_speed_mph_str = f"{wind_speed_mps * 2.237:.0f} mph" if wind_speed_mps is not None else "N/A"
    wind_direction_cardinal = degrees_to_cardinal(wind_direction_deg)
    
    pop_now = weather_data['hourly'][0].get('pop', 0)
    pop_str_now = f"{pop_now * 100:.0f}%"

    # --- Main Tweet Content (A shorter summary) ---
    greeting = get_time_based_greeting(current_hour)
    
    time_str = now.strftime('%I:%M %p')
    date_str = f"{now.day} {now.strftime('%B')}"
    
    greeting_line = f"{greeting.title()}, {city}! 👋"
    tweet_lines = [
        f"{greeting_line}",
        f"It's currently {temp_c_str} (feels like {feels_like_c_str}) with {sky_description_now}.",
    ]
    
    # Add a short forecast summary to the tweet
    daily_forecasts = weather_data.get('daily', [])
    if len(daily_forecasts) > 1:
        tomorrow_data = daily_forecasts[1]
        temp_max = tomorrow_data.get('temp', {}).get('max')
        temp_max_str = f"{temp_max:.0f}°C" if temp_max is not None else ""
        tomorrow_desc = tomorrow_data.get('weather', [{}])[0].get('description', 'clear skies').title()
        tweet_lines.append(f"Tomorrow: {tomorrow_desc}, with a high of {temp_max_str}.")
        
    hashtags = generate_dynamic_hashtags(weather_data, current_day)

    # --- ALT TEXT AND IMAGE CONTENT GENERATION ---
    image_text_lines = []
    
    # Header
    image_text_lines.append(f"Weather Update for {city.title()} City!")
    image_text_lines.append(f"As of {time_str}, {date_str}")
    image_text_lines.append("") # Spacer
    
    # Current Conditions Summary
    image_text_lines.append("Current Conditions:")
    image_text_lines.append(f"Temperature: {temp_c_str} (feels like {feels_like_c_str})")
    image_text_lines.append(f"Weather: {sky_description_now.title()}")
    image_text_lines.append(f"Humidity: {humidity_str}")
    image_text_lines.append(f"Wind: {wind_direction_cardinal} at {wind_speed_mph_str}")
    image_text_lines.append("") # Spacer
    
    # Today's Outlook
    weather_mood = get_weather_mood(temp_c, current_hour)
    main_paragraph_intro = f"The city is experiencing a {weather_mood}."
    rain_sentence = ""
    if pop_now > 0.5:
        rain_sentence = f"There's a high chance of rain today ({pop_str_now}), so don't forget your umbrella!"
    elif pop_now > 0.1:
        rain_sentence = f"There's a small chance of rain today ({pop_str_now}), so keeping an umbrella handy might be a good idea."
    else:
        rain_sentence = f"With a {pop_str_now} chance of rain, you can likely leave your umbrella at home."
        
    image_text_lines.append(f"Today's Outlook: {main_paragraph_intro} {rain_sentence}")
    image_text_lines.append("") # Spacer
    
    # 12-Hour Forecast
    image_text_lines.append("Detailed Hourly Forecast (Next 12h):")
    hourly_forecasts = weather_data.get('hourly', [])
    for i in range(3, 13, 3):
        if i < len(hourly_forecasts):
            hour_data = hourly_forecasts[i]
            forecast_time = datetime.fromtimestamp(hour_data['dt'], tz=indian_tz)
            pop_hourly = hour_data.get('pop', 0)
            temp_hourly = hour_data.get('temp')
            description = hour_data.get('weather', [{}])[0].get('description', '').title()
            
            time_str_hourly = forecast_time.strftime('%I %p')
            temp_hourly_str = f"{temp_hourly:.0f}°C" if temp_hourly is not None else ""
            
            # --- START OF CODE ADDITION ---
            # Get precipitation data, default to 0 if not present
            rain_mm = hour_data.get('rain', {}).get('1h', 0)
            snow_mm = hour_data.get('snow', {}).get('1h', 0)
            precipitation_str = ""
            if rain_mm > 0:
                precipitation_str = f"(Rain: {rain_mm:.1f} mm)"
            elif snow_mm > 0:
                precipitation_str = f"(Snow: {snow_mm:.1f} mm)"
            else:
                precipitation_str = "(Precipitation: 0 mm)"
            
            # Update the detail string to include precipitation
            detail_str = f"By {time_str_hourly}: {description} at {temp_hourly_str}. Rain chance: {pop_hourly * 100:.0f}%. {precipitation_str}"
            # --- END OF CODE ADDITION ---
            image_text_lines.append(detail_str)
            
    image_text_lines.append("") # Spacer
    
    # 3-Day Forecast
    image_text_lines.append("Upcoming 3-Day Forecast:")
    daily_forecasts = weather_data.get('daily', [])
    for i in range(1, min(4, len(daily_forecasts))):
        day_data = daily_forecasts[i]
        forecast_date = datetime.fromtimestamp(day_data['dt'], tz=indian_tz)
        day_of_week = forecast_date.strftime('%A')
        temp_min = day_data.get('temp', {}).get('min')
        temp_max = day_data.get('temp', {}).get('max')
        description = day_data.get('weather', [{}])[0].get('description', '').title()
        
        temp_min_str = f"{temp_min:.0f}°C" if temp_min is not None else "N/A"
        temp_max_str = f"{temp_max:.0f}°C" if temp_max is not None else "N/A"
        
        day_summary = f"{day_of_week}: High {temp_max_str}, Low {temp_min_str}. Expect {description}."
        image_text_lines.append(day_summary)
        
    image_text_lines.append("") # Spacer
    
    # Closing Sentence
    closing_sentence = ""
    if future_rain_in_12_hours:
        closing_sentence = "Stay safe, drive carefully on the wet roads, and enjoy the weather!"
    else:
        closing_sentence = "Stay safe and have a pleasant day ahead!"
    
    image_text_lines.append(closing_sentence)
    
    full_alt_text = "\n".join(image_text_lines)

    return {
        "lines": tweet_lines,
        "hashtags": hashtags,
        "alt_text": full_alt_text,
        "image_content": image_text_lines
    }

def create_weather_image(image_text_lines, output_path=GENERATED_IMAGE_PATH):
    """Generates an image with the weather report text from a list of lines, with bold headings and text wrapping."""
    try:
        img_width, img_height = 985, 650
        bg_color, text_color = (236, 239, 241), (66, 66, 66)

        img = Image.new('RGB', (img_width, img_height), color=bg_color)
        d = ImageDraw.Draw(img)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        font_regular_path = os.path.join(script_dir, "Merriweather_36pt-MediumItalic.ttf")
        font_bold_path = os.path.join(script_dir, "Merriweather_24pt-BoldItalic.ttf")
        
        try:
            font_size = 18 
            font_regular = ImageFont.truetype(font_regular_path, font_size)
            font_bold = ImageFont.truetype(font_bold_path, font_size)
            logging.info("Successfully loaded Merriweather fonts.")
        except IOError:
            logging.warning("Custom fonts not found. Using default font.")
            font_regular = ImageFont.load_default()
            font_bold = font_regular
            font_size = 10
        
        line_height = font_size + 7
        heading_prefixes = ("Weather Update", "Current Conditions:", "Today's Outlook:", "Detailed Hourly Forecast", "Upcoming 3-Day Forecast")
        padding_x, padding_y = 20, 20
        max_text_width = img_width - (2 * padding_x)
        y_text = padding_y

        for original_line in image_text_lines:
            if not original_line.strip():
                y_text += line_height
                continue

            current_font = font_bold if original_line.strip().startswith(heading_prefixes) else font_regular
            words = original_line.split(' ')
            current_line_words = []
            
            for word in words:
                test_line = ' '.join(current_line_words + [word])
                bbox = d.textbbox((0, 0), test_line, font=current_font)
                text_w = bbox[2] - bbox[0]

                if text_w <= max_text_width:
                    current_line_words.append(word)
                else:
                    if current_line_words:
                        d.text((padding_x, y_text), ' '.join(current_line_words), font=current_font, fill=text_color)
                        y_text += line_height
                    current_line_words = [word]

            if current_line_words:
                d.text((padding_x, y_text), ' '.join(current_line_words), font=current_font, fill=text_color)
                y_text += line_height

            if y_text >= img_height - padding_y:
                logging.warning("Image content exceeded image height. Truncating.")
                break

        img.save(output_path)
        logging.info(f"Weather image created successfully at {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Error creating weather image: {e}")
        return None

# --- Tweeting Function ---
def tweet_post(tweet_content):
    """Assembles and posts a tweet with a dynamically generated image."""
    if not all([bot_api_client_v1, bot_api_client_v2]):
        logging.error("Twitter clients not initialized. Aborting tweet post.")
        return False
    
    if not POST_TO_TWITTER_ENABLED:
        logging.info("[TEST MODE] Skipping actual Twitter post.")
        logging.info("Tweet Content:\n" + "\n".join(tweet_content['lines']) + "\n" + " ".join(tweet_content['hashtags']))
        
        generated_image_path = create_weather_image(tweet_content['image_content'])
        if generated_image_path:
            logging.info(f"Generated image for inspection: {generated_image_path}")
        else:
            logging.error("Failed to generate image for inspection in test mode.")
        return True

    body = "\n".join(tweet_content['lines'])
    hashtags = tweet_content['hashtags']
    full_tweet = f"{body}\n{' '.join(hashtags)}"

    if len(full_tweet) > TWITTER_MAX_CHARS:
        logging.warning("Tweet content exceeds character limit. Adjusting.")
        while hashtags and len(f"{body}\n{' '.join(hashtags)}") > TWITTER_MAX_CHARS:
            hashtags.pop()
        tweet_text = f"{body}\n{' '.join(hashtags)}"
        if len(tweet_text) > TWITTER_MAX_CHARS:
            tweet_text = tweet_text[:TWITTER_MAX_CHARS - 3] + "..."
    else:
        tweet_text = full_tweet
    
    media_ids = []
    generated_image_path = create_weather_image(tweet_content['image_content'])
    if generated_image_path and os.path.exists(generated_image_path):
        try:
            logging.info(f"Uploading media: {generated_image_path}")
            media = bot_api_client_v1.media_upload(filename=generated_image_path)
            media_ids.append(media.media_id)
            alt_text = tweet_content['alt_text']
            if len(alt_text) > 1000:
                alt_text = alt_text[:997] + "..."
            bot_api_client_v1.create_media_metadata(media_id=media.media_id_string, alt_text=alt_text)
            logging.info("Media uploaded and alt text added successfully.")
        except Exception as e:
            logging.error(f"Failed to upload media or add alt text: {e}")
        finally:
            try:
                os.remove(generated_image_path)
            except OSError as e:
                logging.warning(f"Error removing temporary image file {generated_image_path}: {e}")
    else:
        logging.error("Failed to generate weather image. Posting tweet without image.")

    try:
        response = bot_api_client_v2.create_tweet(text=tweet_text, media_ids=media_ids if media_ids else None)
        logging.info(f"Tweet posted successfully! Tweet ID: {response.data['id']}")
        return True
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        return False
    except Exception as e:
        logging.critical(f"An unexpected error occurred during tweet posting: {e}")
        return False

# --- Core Task Logic ---
def perform_scheduled_tweet_task():
    """Main task to fetch data, create content, and post the tweet."""
    logging.info(f"--- Running weather tweet job for {CITY_TO_MONITOR} ---")
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Aborting.")
        return False

    lat, lon = get_city_coordinates(CITY_TO_MONITOR, weather_api_key)
    if not lat or not lon:
        return False

    weather_data = get_one_call_weather_data(lat, lon, weather_api_key)

    if not weather_data:
        logging.warning(f"Could not retrieve weather for {CITY_TO_MONITOR}. Aborting.")
        return False

    tweet_content = create_weather_tweet_content(CITY_TO_MONITOR, weather_data)
    
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

# --- Main Execution Block ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    # UPDATED: Debug mode is now controlled by an environment variable
    is_debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logging.info(f"--- Starting Flask Server on port {app_port} ---")
    logging.info(f"Debug mode is {'ON' if is_debug_mode else 'OFF'}")
    app.run(host='0.0.0.0', port=app_port, debug=is_debug_mode)