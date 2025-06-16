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
else:
    logging.info("Twitter interactions ARE ENABLED. Tweets will be posted to Twitter.")

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Helper Functions ---
def get_env_variable(var_name, critical=True):
    value = os.environ.get(var_name)
    if value is None and critical:
        logging.critical(f"Critical environment variable '{var_name}' not found.")
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    return value

def degrees_to_cardinal(d):
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((d + 11.25) / 22.5)
    return dirs[ix % 16]

# --- Initialize Twitter API Clients ---
api_v1 = None
client_v2 = None
try:
    consumer_key = get_env_variable("TWITTER_API_KEY")
    consumer_secret = get_env_variable("TWITTER_API_SECRET")
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN")
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET")
    bearer_token = get_env_variable("TWITTER_BEARER_TOKEN")
    auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, access_token, access_token_secret)
    api_v1 = tweepy.API(auth)
    client_v2 = tweepy.Client(
        bearer_token=bearer_token, consumer_key=consumer_key, consumer_secret=consumer_secret,
        access_token=access_token, access_token_secret=access_token_secret
    )
    logging.info("Twitter v1.1 and v2 clients initialized successfully.")
except Exception as e:
    logging.critical(f"An unexpected error occurred during Twitter client initialization: {e}")

# --- Weather and Tweet Creation Functions ---
def get_weather(city):
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
        url = f'https://api.openweathermap.org/data/2.5/weather?q={city},IN&appid={weather_api_key}&units=metric'
        weather_response = requests.get(url, timeout=10)
        weather_response.raise_for_status()
        return weather_response.json()
    except Exception as err:
        logging.error(f"Error fetching weather data for {city}: {err}")
        return None

def generate_dynamic_hashtags(weather_data, current_day):
    hashtags = {'#Gachibowli', '#Hyderabad', '#weatherupdate'}
    main_conditions = weather_data.get('main', {})
    weather_main_info = weather_data.get('weather', [{}])[0]
    temp_celsius = main_conditions.get('temp', 0)
    sky_description = weather_main_info.get('description', "").lower()
    if temp_celsius > 35: hashtags.add('#Heatwave')
    if 'rain' in sky_description: hashtags.add('#HyderabadRains')
    if current_day in ['Saturday', 'Sunday']: hashtags.add('#WeekendWeather')
    return list(hashtags)

def create_weather_tweet_content(city, weather_data):
    if not weather_data: return (["Could not generate weather report: Data missing."], ["#error"])
    weather_main_info = weather_data.get('weather', [{}])[0]
    main_conditions = weather_data.get('main', {})
    wind_conditions = weather_data.get('wind', {})
    now = datetime.now(pytz.timezone('Asia/Kolkata'))
    current_day = now.strftime('%A')
    sky_description = weather_main_info.get('description', "N/A").title()
    temp_celsius = main_conditions.get('temp', 0)
    feels_like_celsius = main_conditions.get('feels_like', 0)
    humidity = main_conditions.get('humidity', 0)
    wind_speed_kmh = wind_conditions.get('speed', 0) * 3.6
    wind_direction_cardinal = degrees_to_cardinal(wind_conditions.get('deg', 0))
    
    time_str = now.strftime("%I:%M %p, %d %b")
    greeting_line = f"Weather in #Gachibowli ({time_str}):"
    tweet_lines = [
        greeting_line,
        f"🌡️ Temp: {temp_celsius:.0f}°C (Feels like: {feels_like_celsius:.0f}°C)",
        f"☁️ Sky: {sky_description}",
        f"💧 Humidity: {humidity:.0f}%",
        f"💨 Wind: {wind_speed_kmh:.0f} km/h {wind_direction_cardinal}",
    ]
    hashtags = generate_dynamic_hashtags(weather_data, current_day)
    return tweet_lines, hashtags

# --- Reusable Tweeting Function ---
def assemble_and_post_tweet(tweet_lines, hashtags, media_id=None):
    if not all([client_v2, POST_TO_TWITTER_ENABLED]):
        log_message = "\n".join(tweet_lines) + "\n" + " ".join(hashtags)
        if not POST_TO_TWITTER_ENABLED:
            logging.info(f"[TEST MODE] Skipping post. Content:\n{log_message}")
            return True
        logging.error("Tweet posting prerequisites not met. Aborting.")
        return False
    
    try:
        body = "\n".join(tweet_lines)
        while True:
            hashtag_str = " ".join(hashtags)
            full_tweet = f"{body}\n\n{hashtag_str}"
            if len(full_tweet) <= TWITTER_MAX_CHARS: break
            if not hashtags:
                full_tweet = body
                break
            hashtags.pop()

        logging.info(f"Posting tweet ({len(full_tweet)} chars): \n{full_tweet}")
        client_v2.create_tweet(text=full_tweet, media_ids=[media_id] if media_id else None)
        return True
    except tweepy.errors.TweepyException as err:
        logging.error(f"Error posting tweet: {err}")
        return False

# --- Flask Routes ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/weather-data')
def get_all_weather_data():
    city = request.args.get('city', 'Gachibowli'); country = request.args.get('country', 'IN')
    try:
        api_key = get_env_variable("WEATHER_API_KEY")
        geo_url = f"https://api.openweathermap.org/geo/1.0/direct?q={city},{country}&limit=1&appid={api_key}"
        geo_response = requests.get(geo_url); geo_response.raise_for_status(); geo_data = geo_response.json()
        if not geo_data: return jsonify({"message": f"Location '{city}' not found."}), 404
        location_info = {"name": geo_data[0].get("name"), "state": geo_data[0].get("state"), "country": geo_data[0].get("country")}
        lat, lon = geo_data[0]['lat'], geo_data[0]['lon']
        urls = [f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={api_key}", f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&units=metric&appid={api_key}", f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={api_key}"]
        responses = [requests.get(url) for url in urls]
        for r in responses: r.raise_for_status()
        current_weather, forecast_data, air_pollution_data = [r.json() for r in responses]
        return jsonify({"location": location_info, "current": current_weather, "forecast": forecast_data, "air_quality": air_pollution_data["list"][0]})
    except Exception as e:
        logging.error(f"Error in get_all_weather_data: {e}")
        return jsonify({"message": "An internal server error occurred."}), 500

@app.route('/post-weather-tweet-with-image', methods=['POST'])
def run_tweet_task_endpoint():
    try:
        data = request.get_json(); image_data = base64.b64decode(data['image'])
        with open(TEMP_IMAGE_FILENAME, "wb") as f: f.write(image_data)
        media = api_v1.media_upload(filename=TEMP_IMAGE_FILENAME)
        os.remove(TEMP_IMAGE_FILENAME)
        weather_data = get_weather(CITY_TO_MONITOR)
        if not weather_data: return jsonify({"status": "error", "message": "Could not get weather for tweet text."}), 500
        tweet_lines, hashtags = create_weather_tweet_content(CITY_TO_MONITOR, weather_data)
        success = assemble_and_post_tweet(tweet_lines, hashtags, media_id=media.media_id_string)
        if success: return jsonify({"status": "success", "message": "Tweet posted successfully!"}), 200
        else: return jsonify({"status": "error", "message": "Failed to post tweet."}), 500
    except Exception as e:
        logging.error(f"Error in run_tweet_task_endpoint: {e}")
        return jsonify({"status": "error", "message": "Server error processing image tweet."}), 500

# --- ENDPOINT FOR CLOUD SCHEDULER (SIMPLIFIED & SECURE) ---
@app.route('/execute-tweet-job', methods=['POST'])
def execute_tweet_job():
    # Security is handled by Google Cloud IAM, ensuring only the scheduler's service account can call this.
    logging.info("Cloud Scheduler job started.")
    
    weather_data = get_weather(CITY_TO_MONITOR)
    if not weather_data:
        logging.error("Scheduler job failed: Could not retrieve weather.")
        return "Failed to get weather data", 500

    tweet_lines, hashtags = create_weather_tweet_content(CITY_TO_MONITOR, weather_data)
    success = assemble_and_post_tweet(tweet_lines, hashtags)

    if success:
        logging.info("Scheduler job completed successfully.")
        return "OK", 200
    else:
        logging.error("Scheduler job failed: Could not post tweet.")
        return "Failed to post tweet", 500

if __name__ == "__main__":
    app_port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=app_port, debug=True)