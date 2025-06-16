import tweepy
import requests
import os
from datetime import datetime
import pytz
from flask import Flask, request, jsonify, render_template
import logging
import base64

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Constants ---
TWITTER_MAX_CHARS = 280
CITY_TO_MONITOR = "Gachibowli"
TEMP_IMAGE_FILENAME = "temp_weather_card.jpg"
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
        logging.critical(f"Critical environment variable '{var_name}' not found.")
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    return value

def degrees_to_cardinal(d):
    """Converts wind direction in degrees to a cardinal direction."""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((d + 11.25) / 22.5)
    return dirs[ix % 16]

# --- Initialize Twitter API Clients (v1.1 for Media, v2 for Tweeting) ---
api_v1 = None
client_v2 = None
try:
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")
    bearer_token = get_env_variable("TWITTER_BEARER_TOKEN")

    # v1.1 API for media uploads
    auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, access_token, access_token_secret)
    api_v1 = tweepy.API(auth)
    
    # v2 Client for creating tweets
    client_v2 = tweepy.Client(
        bearer_token=bearer_token, consumer_key=consumer_key, consumer_secret=consumer_secret,
        access_token=access_token, access_token_secret=access_token_secret
    )
    logging.info("Twitter v1.1 and v2 clients initialized successfully.")
except EnvironmentError as e:
    logging.error(f"Error initializing Twitter clients due to missing environment variable: {e}")
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
        hashtags.add('#HyderabadRains'); hashtags.add('#rain')
    if temp_celsius > 35: hashtags.add('#Heatwave')
    if 'clear' in sky_description: hashtags.add('#SunnyDay')
    if wind_speed_kmh > 25: hashtags.add('#windy')
    if current_day in ['Saturday', 'Sunday']: hashtags.add('#WeekendWeather')
    return list(hashtags)

def create_weather_tweet_content(city, weather_data):
    if not weather_data: return (["Could not generate weather report: Data missing."], ["#error"])
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
    rain_forecast = f"☔ Rain Alert: {rain_1h:.2f} mm/hr" if rain_1h > 0 else "☔ No Rain Expected"
    
    if rain_1h > 0.5: closing_message = "Stay dry out there! 🌧️"
    elif temp_celsius > 35: closing_message = "It's a hot one! Stay cool and hydrated. ☀️"
    elif temp_celsius < 18: closing_message = "Brr, it's cool! Consider a light jacket. 🧣"
    else: closing_message = "Enjoy your day! 😊"

    time_str = now.strftime("%I:%M %p"); date_str = f"{now.day} {now.strftime('%B')}"
    greeting_line = f"Hello, {city}!👋, {current_day} weather as of {date_str}, {time_str}:"
    tweet_lines = [
        greeting_line, f"☁️ Sky: {sky_description}",
        f"🌡️ Temp: {temp_celsius:.0f}°C(feels: {feels_like_celsius:.0f}°C)",
        f"💧 Humidity: {humidity:.0f}%", f"💨 Wind: {wind_speed_kmh:.0f} km/h from the {wind_direction_cardinal}",
        rain_forecast, "", closing_message
    ]
    hashtags = generate_dynamic_hashtags(weather_data, current_day)
    return tweet_lines, hashtags

def tweet_post_with_image(tweet_lines, hashtags, image_path):
    if not all([api_v1, client_v2, POST_TO_TWITTER_ENABLED]):
        if not POST_TO_TWITTER_ENABLED:
            logging.info(f"[TEST MODE] Skipping post. Content:\n" + "\n".join(tweet_lines) + "\n" + " ".join(hashtags))
            return True
        logging.error("Tweet posting prerequisites not met. Aborting.")
        return False
        
    try:
        media = api_v1.media_upload(filename=image_path)
        media_id = media.media_id_string
        logging.info(f"Image '{image_path}' uploaded to Twitter. Media ID: {media_id}")
        body = "\n".join(tweet_lines)
        while hashtags:
            hashtag_str = " ".join(hashtags)
            full_tweet = f"{body}\n{hashtag_str}"
            if len(full_tweet) <= TWITTER_MAX_CHARS: break
            hashtags.pop()
        else: full_tweet = body
        client_v2.create_tweet(text=full_tweet, media_ids=[media_id])
        logging.info("Tweet with image posted successfully to Twitter!")
        logging.info(f"Final Tweet ({len(full_tweet)} chars): \n{full_tweet}")
        return True
    except tweepy.errors.TooManyRequests:
        logging.warning("Rate limit exceeded. Will not retry.")
        return False
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet with image: {err}")
        return False
    finally:
        if os.path.exists(image_path):
            os.remove(image_path)
            logging.info(f"Temporary image file '{image_path}' deleted.")


# --- Flask Routes ---
@app.route('/')
def home():
    """Serves the main HTML page that generates the weather card."""
    return render_template('index.html')

@app.route('/api/weather-data')
def get_all_weather_data():
    """
    This endpoint securely fetches all necessary weather data from the backend,
    hiding the API key from the client.
    """
    city = request.args.get('city', 'Gachibowli')
    country = request.args.get('country', 'IN')

    try:
        api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError as e:
        return jsonify({"message": str(e)}), 500

    try:
        # 1. Geocode to get lat/lon
        geo_url = f"https://api.openweathermap.org/geo/1.0/direct?q={city},{country}&limit=1&appid={api_key}"
        geo_response = requests.get(geo_url)
        geo_response.raise_for_status()
        geo_data = geo_response.json()

        if not geo_data:
            return jsonify({"message": f"Location '{city}' not found."}), 404
        
        location_info = {
            "name": geo_data[0].get("name"),
            "state": geo_data[0].get("state"),
            "country": geo_data[0].get("country")
        }
        lat, lon = geo_data[0]['lat'], geo_data[0]['lon']

        # 2. Sequentially fetch current weather, forecast, and air pollution
        current_weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={api_key}"
        forecast_url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&units=metric&appid={api_key}"
        air_pollution_url = f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={api_key}"

        current_weather_response = requests.get(current_weather_url)
        current_weather_response.raise_for_status()
        
        forecast_response = requests.get(forecast_url)
        forecast_response.raise_for_status()

        air_pollution_response = requests.get(air_pollution_url)
        air_pollution_response.raise_for_status()

        # 3. Combine into a single response object for the frontend
        combined_data = {
            "location": location_info,
            "current": current_weather_response.json(),
            "forecast": forecast_response.json(),
            "air_quality": air_pollution_response.json()["list"][0]
        }
        return jsonify(combined_data)

    except requests.exceptions.RequestException as e:
        logging.error(f"Network error fetching weather data: {e}")
        return jsonify({"message": "Network error communicating with the weather service."}), 500
    except Exception as e:
        logging.error(f"An unexpected error occurred in get_all_weather_data: {e}")
        return jsonify({"message": "An internal server error occurred."}), 500


@app.route('/post-weather-tweet-with-image', methods=['POST'])
def run_tweet_task_endpoint():
    """Receives the image from the frontend and triggers the tweet task."""
    logging.info("'/post-weather-tweet-with-image' endpoint triggered.")
    try:
        data = request.get_json()
        if 'image' not in data:
            return jsonify({"status": "error", "message": "No image data found in request."}), 400
        
        image_data = base64.b64decode(data['image'])
        with open(TEMP_IMAGE_FILENAME, "wb") as f: f.write(image_data)
        logging.info(f"Image received from frontend and saved as '{TEMP_IMAGE_FILENAME}'.")
    except Exception as e:
        logging.error(f"Error processing received image: {e}")
        return jsonify({"status": "error", "message": "Failed to process image data."}), 500

    weather_data = get_weather(CITY_TO_MONITOR)
    if not weather_data:
        return jsonify({"status": "error", "message": f"Could not retrieve weather for {CITY_TO_MONITOR}."}), 500

    tweet_lines, hashtags = create_weather_tweet_content(CITY_TO_MONITOR, weather_data)
    success = tweet_post_with_image(tweet_lines, hashtags, TEMP_IMAGE_FILENAME)

    if success:
        return jsonify({"status": "success", "message": "Tweet posted successfully!"}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to post tweet."}), 500

# --- Main Execution Block for Local Development ---
if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    logging.info(f"--- Starting Flask Server for local development on port {app_port} ---")
    app.run(host='0.0.0.0', port=app_port, debug=True)