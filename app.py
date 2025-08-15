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
CITY_TO_MONITOR = "Hyderabad"
GENERATED_IMAGE_PATH = "weather_report.png"

POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "false").lower() == "true"

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
    if d is None:
        return "N/A"
    try:
        d = float(d)
    except (ValueError, TypeError):
        return "N/A"
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
        if data:
            return data[0]['lat'], data[0]['lon']
        else:
            logging.error(f"Could not find coordinates for city: {city}")
            return None, None
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching coordinates for {city}: {err}")
        return None, None

def get_one_call_weather_data(lat, lon, api_key):
    """Fetches weather data using OpenWeatherMap One Call API 3.0."""
    if not lat or not lon:
        return None
    url = f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&appid={api_key}&units=metric&exclude=minutely,daily,alerts"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching One Call weather data: {err}")
        return None

def get_air_pollution_data(lat, lon, api_key):
    """Fetches air pollution data (AQI)."""
    if not lat or not lon:
        return None
    url = f"http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={api_key}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching air pollution data: {err}")
        return None

# --- Tweet Content Creation and Formatting ---
def generate_dynamic_hashtags(weather_data, current_day):
    """Generates a list of hashtags based on weather conditions."""
    hashtags = {'#Hyderabad', '#weatherupdate'}

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
        hashtags.add('#HyderabadRains')
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
    hashtags.add(f'#{CITY_TO_MONITOR.replace(" ", "")}')
    return list(hashtags)

def create_weather_tweet_content(city, weather_data, air_pollution_data):
    """
    Creates the new conversational tweet content, alt text, and image text.
    """
    if not weather_data or 'current' not in weather_data or 'hourly' not in weather_data:
        logging.error("Missing or invalid weather data for tweet content creation.")
        return {"lines": ["Could not generate weather report: Data missing."], "hashtags": ["#error"], "alt_text": "", "image_content": "No weather data available."}

    indian_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.now(indian_tz)
    current_day = now.strftime('%A')

    # --- Extract Current Weather Data ---
    current = weather_data['current']
    temp_c = current.get('temp')
    feels_like_c = current.get('feels_like')
    humidity = current.get('humidity')
    wind_speed_mps = current.get('wind_speed')
    wind_direction_deg = current.get('wind_deg')
    uvi = current.get('uvi')
    pop = weather_data['hourly'][0].get('pop', 0) # Chance of rain for the current hour
    
    # --- Data Conversion and Formatting ---
    temp_c_str = f"{temp_c:.0f}°C" if temp_c is not None else "N/A"
    feels_like_c_str = f"{feels_like_c:.0f}°C" if feels_like_c is not None else "N/A"
    humidity_str = f"{humidity:.0f}%" if humidity is not None else "N/A"
    wind_speed_mph_str = f"{wind_speed_mps * 2.237:.0f} mph" if wind_speed_mps is not None else "N/A"
    wind_direction_cardinal = degrees_to_cardinal(wind_direction_deg)
    pop_str = f"{pop * 100:.0f}%" if pop is not None else "N/A"

    # --- Air Quality Data ---
    aqi_str = "moderate" # Default
    if air_pollution_data and 'list' in air_pollution_data and air_pollution_data['list']:
        aqi = air_pollution_data['list'][0]['main']['aqi']
        aqi_map = {1: "good", 2: "fair", 3: "moderate", 4: "poor", 5: "very poor"}
        aqi_str = aqi_map.get(aqi, "moderate")
    
    # --- UV Index Data ---
    uvi_str = f"{uvi:.0f} out of 11" if uvi is not None else "N/A"
    if uvi is not None:
        if uvi <= 2: uvi_level = "low"
        elif uvi <= 5: uvi_level = "moderate"
        elif uvi <= 7: uvi_level = "high"
        else: uvi_level = "very high"
        uvi_str = f"{uvi_str} ({uvi_level})"

    # --- ALT TEXT AND IMAGE CONTENT GENERATION ---
    text_lines = []
    text_lines.append(f"{city} Weather Update: Get Ready for a Day of Rain and Drizzle!")
    text_lines.append(f"Good morning, {city}! The city is waking up to a pleasant and cool morning with a current temperature of {temp_c_str}. It feels like {feels_like_c_str} with a refreshing breeze from the {wind_direction_cardinal} at {wind_speed_mph_str}. The air is humid at {humidity_str}, and there's a {pop_str} chance of rain, so keep those umbrellas handy!")
    text_lines.append("\nDetailed Forecast for the Next 12 Hours:")
    text_lines.append("Expect a day dominated by cloudy skies and intermittent rain. Don't be surprised by sudden downpours as the day progresses.")
    
    target_hours = [11, 14, 17, 20]
    forecast_details = {h: None for h in target_hours}

    for hour_forecast in weather_data['hourly']:
        forecast_time = datetime.fromtimestamp(hour_forecast['dt'], tz=indian_tz)
        if forecast_time.hour in target_hours and forecast_details[forecast_time.hour] is None:
            pop_hourly = hour_forecast.get('pop', 0)
            rain_mm = hour_forecast.get('rain', {}).get('1h', 0)
            description = hour_forecast.get('weather', [{}])[0].get('description', 'cloudy skies').title()
            temp_hourly = hour_forecast.get('temp')
            temp_hourly_str = f"{temp_hourly:.0f}°C" if temp_hourly is not None else ""
            time_str = forecast_time.strftime('%I %p').lstrip('0')
            detail_str = f"By {time_str}: Expect {description} around {temp_hourly_str}. "
            detail_str += f"Chance of rain: {pop_hourly * 100:.0f}%."
            if rain_mm > 0:
                detail_str += f" ({rain_mm:.1f}mm expected)."
            forecast_details[forecast_time.hour] = detail_str

    for hour in target_hours:
        if forecast_details[hour]:
            text_lines.append(forecast_details[hour])

    text_lines.append("\nAir Quality & UV Index:")
    text_lines.append(f"The air quality in {city} is currently {aqi_str}, which is good news for those with respiratory sensitivities. The UV Index is {uvi_str}, so you don't have to worry too much about sun exposure today.")
    text_lines.append(f"\nStay safe, drive carefully on the wet roads, and enjoy the cool weather, {city}!")

    full_text_content = "\n".join(text_lines)
    
    # --- Main Tweet Content (A shorter summary) ---
    sky_description_now = weather_data['current'].get('weather', [{}])[0].get('main', 'clouds')
    
    # Dynamically format the date and time
    time_str = now.strftime('%I:%M %p')
    date_str = f"{now.day} {now.strftime('%B')}"
    
    # Build the new greeting line
    greeting_line = f"Good Morning, {city}! 👋, {current_day} weather as of {date_str}, {time_str}:"

    # Assemble the final tweet lines
    tweet_lines = [
        greeting_line,
        f"It's currently {temp_c_str} (feels like {feels_like_c_str}) with {sky_description_now.lower()}.",
        "Expect intermittent rain. Keep your umbrella handy! ☔",
        f"AQI is {aqi_str}. #StaySafe"
    ]
    
    hashtags = generate_dynamic_hashtags(weather_data, current_day)

    return {
        "lines": tweet_lines,
        "hashtags": hashtags,
        "alt_text": full_text_content,
        "image_content": full_text_content
    }

def create_weather_image(image_text, output_path=GENERATED_IMAGE_PATH):
    """
    Generates an image with the weather report text, with bold headings.
    """
    try:
        img_width = 860
        img_height = 600
        bg_color = (34, 71, 102)
        text_color = (255, 255, 255)

        img = Image.new('RGB', (img_width, img_height), color=bg_color)
        d = ImageDraw.Draw(img)

        font_regular = None
        font_bold = None
        try:
            # Assumes 'consolas.ttf' and 'consolasb.ttf' (bold) are in the directory
            font_regular = ImageFont.truetype("consolas.ttf", 18)
            font_bold = ImageFont.truetype("consolasb.ttf", 18)
            font_size = 18
            logging.info("Successfully loaded Consolas regular and bold fonts.")
        except IOError:
            logging.warning("Could not find 'consolas.ttf' or 'consolasb.ttf'. Using default font.")
            font_regular = ImageFont.load_default()
            font_bold = font_regular # Fallback to regular if bold isn't found
            font_size = 10
        
        line_height = font_size + 7

        headings = [
            "Hyderabad Weather Update: Get Ready for a Day of Rain and Drizzle!",
            "Detailed Forecast for the Next 12 Hours:",
            "Air Quality & UV Index:"
        ]

        padding_x = 20
        padding_y = 20
        max_text_width = img_width - (2 * padding_x)
        y_text = padding_y

        for original_line in image_text.split('\n'):
            current_font = font_regular # Default to regular
            if original_line.strip() in headings:
                current_font = font_bold

            words = original_line.split(' ')
            current_line_words = []
            
            if not original_line.strip():
                y_text += line_height
                continue

            for word in words:
                test_line = ' '.join(current_line_words + [word])
                
                try:
                    text_w = d.textlength(test_line, font=current_font)
                except AttributeError:
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
                logging.warning(f"Image content exceeded image height. Truncating text in image.")
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
        logging.info(f"[TEST MODE] Would generate image with alt text starting with: {tweet_content['alt_text'][:100]}...")
        
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
        logging.warning("Tweet content + hashtags exceed character limit. Adjusting.")
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
    air_pollution_data = get_air_pollution_data(lat, lon, weather_api_key)

    if not weather_data:
        logging.warning(f"Could not retrieve weather for {CITY_TO_MONITOR}. Aborting.")
        return False

    tweet_content = create_weather_tweet_content(CITY_TO_MONITOR, weather_data, air_pollution_data)
    
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
    logging.info(f"--- Starting Flask Server for local development on port {app_port} ---")
    app.run(host='0.0.0.0', port=app_port, debug=True)