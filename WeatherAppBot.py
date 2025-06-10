import os
import requests
import logging
from datetime import datetime, timezone, timedelta # Import timedelta for timezone calculation
from playwright.sync_api import sync_playwright
import math # For wind direction conversion

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
# OpenWeatherMap API
OPENWEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
OPENWEATHER_CITY_NAME = os.environ.get("OPENWEATHER_CITY_NAME", "Gachibowli")
OPENWEATHER_UNITS = os.environ.get("OPENWEATHER_UNITS", "metric") # 'metric' or 'imperial'

# Twitter API (v2)
TWITTER_CONSUMER_KEY = os.environ.get("TWITTER_CONSUMER_KEY")
TWITTER_CONSUMER_SECRET = os.environ.get("TWITTER_CONSUMER_SECRET")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")

# Bot settings
POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "true").lower() == "true"

# Placeholder for Twitter client (will be initialized if enabled)
twitter_client_v2 = None

if POST_TO_TWITTER_ENABLED:
    try:
        import tweepy
        twitter_client_v2 = tweepy.Client(
            consumer_key=TWITTER_CONSUMER_KEY,
            consumer_secret=TWITTER_CONSUMER_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
        logger.info("Twitter v2 client initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Twitter v2 client: {e}")
        POST_TO_TWITTER_ENABLED = False # Disable posting if client fails
        logger.warning("Twitter interactions are DISABLED due to client initialization failure.")
else:
    logger.warning("Twitter interactions are DISABLED (Test Mode).")
    logger.warning("To enable, set the environment variable POST_TO_TWITTER_ENABLED=true")


def kelvin_to_celsius(kelvin):
    return kelvin - 273.15

def kelvin_to_fahrenheit(kelvin):
    return (kelvin - 273.15) * 9/5 + 32

def get_wind_direction(deg):
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = round(deg / (360. / len(directions)))
    return directions[idx % len(directions)]

def format_timestamp_to_local_time(timestamp, timezone_offset_seconds):
    """
    Converts a Unix timestamp to a local time string (HH:MM AM/PM).
    timezone_offset_seconds is the offset from UTC provided by OpenWeatherMap.
    """
    # Create a datetime object for the timestamp in UTC
    utc_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    # Apply the timezone offset
    local_dt = utc_dt + timedelta(seconds=timezone_offset_seconds)
    return local_dt.strftime('%I:%M %p') # e.g., 03:30 PM


def get_weather_data(city_name, api_key, units="metric"):
    """Fetches weather data from OpenWeatherMap API."""
    base_url = "http://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city_name,
        "appid": api_key,
        "units": units # 'metric' for Celsius, 'imperial' for Fahrenheit (API handles conversion if provided)
                      # However, the provided JSON seems to be in Kelvin by default, so we'll convert explicitly.
    }
    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        data = response.json()
        logger.info(f"Successfully fetched weather data for {city_name}.")

        # Extract data based on the provided JSON structure
        weather_main = data['weather'][0]['main']
        weather_description = data['weather'][0]['description']
        weather_icon = data['weather'][0]['icon']

        # Temperatures are in Kelvin in the provided JSON, convert them
        temp_kelvin = data['main']['temp']
        feels_like_kelvin = data['main']['feels_like']
        temp_min_kelvin = data['main']['temp_min']
        temp_max_kelvin = data['main']['temp_max']

        # Apply conversion based on the 'units' setting
        if units == "metric":
            temp = kelvin_to_celsius(temp_kelvin)
            feels_like = kelvin_to_celsius(feels_like_kelvin)
            temp_min = kelvin_to_celsius(temp_min_kelvin)
            temp_max = kelvin_to_celsius(temp_max_kelvin)
            temp_unit = "°C"
        elif units == "imperial":
            temp = kelvin_to_fahrenheit(temp_kelvin)
            feels_like = kelvin_to_fahrenheit(feels_like_kelvin)
            temp_min = kelvin_to_fahrenheit(temp_min_kelvin)
            temp_max = kelvin_to_fahrenheit(temp_max_kelvin)
            temp_unit = "°F"
        else: # Default to Kelvin if units is not recognized (though OpenWeatherMap usually handles conversion if 'units' is set)
            temp = temp_kelvin
            feels_like = feels_like_kelvin
            temp_min = temp_min_kelvin
            temp_max = temp_max_kelvin
            temp_unit = "K"


        humidity = data['main']['humidity']
        wind_speed = data['wind']['speed'] # Speed is m/s by default from API, convert to Km/h for metric
        if units == "metric":
            wind_speed = wind_speed * 3.6 # Convert m/s to Km/h
        elif units == "imperial":
            wind_speed = wind_speed * 2.23694 # Convert m/s to mph

        wind_deg = data['wind']['deg']
        city_name_response = data['name']
        country_code = data['sys']['country']
        sunrise_timestamp = data['sys']['sunrise']
        sunset_timestamp = data['sys']['sunset']
        timezone_offset = data['timezone'] # This is the offset in seconds from UTC

        # Check for rain data
        # data.get('rain', {}) safely gets the 'rain' dict or an empty dict if not present
        # then .get('1h', 0) safely gets '1h' or defaults to 0 if not present
        rain_1h = data.get('rain', {}).get('1h', 0)

        # Format wind direction
        wind_direction_str = get_wind_direction(wind_deg)

        # Format sunrise/sunset times
        sunrise_time_str = format_timestamp_to_local_time(sunrise_timestamp, timezone_offset)
        sunset_time_str = format_timestamp_to_local_time(sunset_timestamp, timezone_offset)

        # Return a dictionary with extracted and formatted data
        return {
            "city_name": city_name_response,
            "country_code": country_code,
            "weather_main": weather_main,
            "weather_description": weather_description,
            "weather_icon": weather_icon,
            "temp": temp,
            "feels_like": feels_like,
            "temp_min": temp_min,
            "temp_max": temp_max,
            "temp_unit": temp_unit,
            "humidity": humidity,
            "wind_speed": wind_speed,
            "wind_direction": wind_direction_str,
            "rain_1h": rain_1h, # Included for HTML card, not necessarily tweet text
            "sunrise": sunrise_time_str,
            "sunset": sunset_time_str
        }

    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error fetching weather data: {e}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error fetching weather data: {e}")
        return None
    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout fetching weather data: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"An unexpected error occurred while fetching weather data: {e}")
        return None
    except KeyError as e:
        logger.error(f"Missing key in OpenWeatherMap response: {e}. Full response: {data}")
        return None
    except Exception as e:
        logger.error(f"An unknown error occurred in get_weather_data: {e}")
        return None

def format_weather_tweet(weather_data):
    """Formats the weather data into a tweet string using the original template."""
    if not weather_data:
        return "Could not retrieve weather information."

    city = weather_data['city_name']
    country = weather_data['country_code']
    temp = weather_data['temp']
    feels_like = weather_data['feels_like']
    description = weather_data['weather_description']
    humidity = weather_data['humidity']
    wind_speed = weather_data['wind_speed']
    wind_direction = weather_data['wind_direction']
    temp_unit = weather_data['temp_unit']

    # Capitalize the first letter of description for better readability
    formatted_description = description.capitalize()

    tweet_text = (
        f"📍 Weather Update for {city}, {country}:\n"
        f"🌡️ Temp: {temp:.1f}{temp_unit} (Feels like: {feels_like:.1f}{temp_unit})\n"
        f"☁️ Sky: {formatted_description}\n"
        f"💧 Humidity: {humidity}%\n"
        f"💨 Wind: {wind_speed:.1f} Km/h from the {wind_direction}\n"
        f"#WeatherUpdate #{city.replace(' ', '')} #{country}"
    )
    return tweet_text

def generate_weather_card_html(weather_data):
    """Generates the HTML content for the weather card using all available data."""
    if not weather_data:
        return "<p>Weather data not available.</p>"

    city = weather_data['city_name']
    country = weather_data['country_code']
    temp = weather_data['temp']
    feels_like = weather_data['feels_like']
    temp_min = weather_data['temp_min']
    temp_max = weather_data['temp_max']
    description = weather_data['weather_description'].capitalize()
    humidity = weather_data['humidity']
    wind_speed = weather_data['wind_speed']
    wind_direction = weather_data['wind_direction']
    temp_unit = weather_data['temp_unit']
    weather_icon_code = weather_data['weather_icon']
    sunrise_time = weather_data['sunrise']
    sunset_time = weather_data['sunset']
    rain_1h = weather_data['rain_1h'] # This is used here for the HTML card!

    # OpenWeatherMap icon URL
    icon_url = f"http://openweathermap.org/img/wn/{weather_icon_code}@2x.png"

    rain_section = ""
    if rain_1h > 0:
        rain_section = f"""
        <div class="detail-item">
            <span class="icon">☔</span>
            <span class="label">Rain (1h):</span>
            <span class="value">{rain_1h:.2f} mm</span>
        </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Weather Card</title>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 0;
                background-color: #f0f2f5;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
            }}
            .weather-card {{
                background: linear-gradient(135deg, #6dd5ed, #2193b0);
                color: #fff;
                border-radius: 15px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
                padding: 30px;
                width: 380px;
                text-align: center;
                position: relative;
                overflow: hidden;
            }}
            .weather-card::before {{
                content: '';
                position: absolute;
                top: -50px;
                left: -50px;
                width: 200px;
                height: 200px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 50%;
                filter: blur(30px);
                transform: rotate(-30deg);
            }}
            .location {{
                font-size: 1.8em;
                font-weight: bold;
                margin-bottom: 10px;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .location .country {{
                font-size: 0.6em;
                opacity: 0.8;
                margin-left: 5px;
            }}
            .main-temp {{
                font-size: 4em;
                font-weight: bold;
                margin: 20px 0 10px;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .main-temp img {{
                width: 80px;
                height: 80px;
                margin-right: 10px;
            }}
            .description {{
                font-size: 1.5em;
                margin-bottom: 20px;
                opacity: 0.9;
            }}
            .temp-range {{
                font-size: 1.1em;
                margin-bottom: 25px;
                opacity: 0.8;
            }}
            .details-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 15px;
                margin-top: 20px;
            }}
            .detail-item {{
                background: rgba(255, 255, 255, 0.2);
                padding: 12px;
                border-radius: 10px;
                display: flex;
                align-items: center;
                justify-content: flex-start;
                font-size: 1em;
            }}
            .detail-item .icon {{
                margin-right: 10px;
                font-size: 1.3em;
            }}
            .detail-item .label {{
                font-weight: normal;
                margin-right: 5px;
                opacity: 0.8;
            }}
            .detail-item .value {{
                font-weight: bold;
            }}
            .footer-info {{
                margin-top: 30px;
                font-size: 0.9em;
                opacity: 0.7;
            }}
        </style>
    </head>
    <body>
        <div class="weather-card">
            <div class="location">
                {city} <span class="country">({country})</span>
            </div>
            <div class="main-temp">
                <img src="{icon_url}" alt="{description}">
                {temp:.1f}{temp_unit}
            </div>
            <div class="description">
                {description}
            </div>
            <div class="temp-range">
                Feels like: {feels_like:.1f}{temp_unit} | Min: {temp_min:.1f}{temp_unit} | Max: {temp_max:.1f}{temp_unit}
            </div>
            <div class="details-grid">
                <div class="detail-item">
                    <span class="icon">💧</span>
                    <span class="label">Humidity:</span>
                    <span class="value">{humidity}%</span>
                </div>
                <div class="detail-item">
                    <span class="icon">💨</span>
                    <span class="label">Wind:</span>
                    <span class="value">{wind_speed:.1f} Km/h {wind_direction}</span>
                </div>
                <div class="detail-item">
                    <span class="icon">☀️</span>
                    <span class="label">Sunrise:</span>
                    <span class="value">{sunrise_time}</span>
                </div>
                <div class="detail-item">
                    <span class="icon">🌙</span>
                    <span class="label">Sunset:</span>
                    <span class="value">{sunset_time}</span>
                </div>
                {rain_section}
            </div>
            <div class="footer-info">
                Data from OpenWeatherMap
            </div>
        </div>
    </body>
    </html>
    """
    return html_content


def capture_screenshot(html_content, output_path):
    """Captures a screenshot of the generated HTML."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            # Set content and wait for it to load
            page.set_content(html_content)
            page.wait_for_selector('.weather-card') # Wait for the card to be rendered

            # Get the bounding box of the weather-card element
            element_handle = page.query_selector('.weather-card')
            if element_handle:
                bounding_box = element_handle.bounding_box()
                if bounding_box:
                    page.screenshot(path=output_path, clip=bounding_box)
                    logger.info(f"Screenshot captured successfully to {output_path}.")
                else:
                    logger.error("Could not get bounding box for .weather-card element.")
                    return False
            else:
                logger.error(".weather-card element not found on page.")
                return False

            browser.close()
        return True
    except Exception as e:
        logger.error(f"Failed to capture screenshot: {e}")
        return False


def post_tweet(tweet_text, media_path=None):
    """Posts a tweet to Twitter (X) with optional media."""
    if not POST_TO_TWITTER_ENABLED or not twitter_client_v2:
        logger.warning("Twitter posting is disabled or client not initialized. Skipping tweet.")
        return False

    try:
        media_id = None
        if media_path and os.path.exists(media_path):
            # Tweepy v2 Client doesn't directly support media upload via Client.create_tweet
            # You typically need to use the old API's media upload or a separate client for media
            # For simplicity, this example assumes a media_upload function or uses the old client.
            # This part is a common point of confusion for tweepy v1 vs v2.
            # For a full solution, one often needs tweepy.API (v1.1) for media upload.

            # Placeholder: In a real scenario, you'd use tweepy.API for media upload
            # Example (requires initializing tweepy.API too):
            # auth = tweepy.OAuthHandler(TWITTER_CONSUMER_KEY, TWITTER_CONSUMER_SECRET)
            # auth.set_access_token(TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET)
            # api_v1 = tweepy.API(auth)
            # media = api_v1.media_upload(media_path)
            # media_id = media.media_id

            # For now, let's just log a warning if media_path is provided without actual v1 upload logic.
            logger.warning("Media upload for Twitter v2 Client not fully implemented in this example. Only text will be posted.")


        response = twitter_client_v2.create_tweet(text=tweet_text, media_ids=[media_id] if media_id else None)
        logger.info(f"Tweet posted successfully to Twitter!")
        logger.info(f"Final Tweet ({len(tweet_text)} chars):\n{tweet_text}")
        logger.info(f"Tweet ID: {response.data['id']}")
        return True
    except tweepy.TweepyException as e:
        logger.error(f"Error posting tweet: {e}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred while posting tweet: {e}")
        return False

# --- Flask Application ---
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/")
def hello_world():
    mode = "LIVE MODE" if POST_TO_TWITTER_ENABLED else "TEST MODE"
    return f"Weather Tweet Bot is alive! Current mode: {mode}"

@app.route("/run-tweet-task", methods=["GET", "POST"])
def run_tweet_task_endpoint():
    logger.info(f"'/run-tweet-task' endpoint triggered by a {request.method} request.")

    # Get city name from environment or use default
    city_to_fetch = OPENWEATHER_CITY_NAME
    logger.info(f"--- Running weather tweet job for {city_to_fetch} ---")

    weather_data = get_weather_data(city_to_fetch, OPENWEATHER_API_KEY, OPENWEATHER_UNITS)

    if weather_data:
        tweet_text = format_weather_tweet(weather_data)
        html_card = generate_weather_card_html(weather_data) # This uses all the detailed data including rain
        temp_image_path = "/tmp/weather_card.png" # Using /tmp for temporary files

        logger.info(f"Attempting to capture screenshot to {temp_image_path}")
        screenshot_success = capture_screenshot(html_card, temp_image_path)

        tweet_success = False
        if screenshot_success:
            tweet_success = post_tweet(tweet_text, media_path=temp_image_path)
            # Clean up the temporary image file
            if os.path.exists(temp_image_path):
                os.remove(temp_image_path)
                logger.info(f"Removed temporary image file: {temp_image_path}")
        else:
            logger.error("Screenshot capture failed, skipping tweet with image.")
            tweet_success = post_tweet(tweet_text) # Still try to post text tweet if image fails

        if tweet_success:
            logger.info(f"Tweet task for {city_to_fetch} completed successfully with image.")
            return "Tweet task executed successfully with image.", 200
        else:
            logger.error(f"Tweet task for {city_to_fetch} failed.")
            return "Tweet task execution failed or was skipped.", 500
    else:
        logger.error(f"Failed to fetch weather data for {city_to_fetch}.")
        return "Tweet task execution failed or was skipped.", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)