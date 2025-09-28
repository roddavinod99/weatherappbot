import tweepy
import requests
import os
import pytz
import math
from datetime import datetime
from flask import Flask
import logging
from PIL import Image, ImageDraw, ImageFont 
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates 

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---
def get_env_variable(var_name, critical=True):
    """Retrieves an environment variable, raising an error if critical and not found."""
    value = os.environ.get(var_name)
    if value is None and critical:
        raise EnvironmentError(f"Critical environment variable '{var_name}' not found.")
    return value

def cleanup_temp_files(paths):
    """Removes temporary image files."""
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logging.info(f"Removed temporary file: {path}")
            except OSError as e:
                logging.warning(f"Error removing temporary image file {path}: {e}")

# --- Constants (MODIFIED) ---
TWITTER_MAX_CHARS = 280
CITY_TO_MONITOR = os.environ.get("CITY_TO_MONITOR") or "Hyderabad"
GENERATED_IMAGE_PATH = "weather_report.png"
GENERATED_CHART_PATH = "weather_chart.png"

POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "false").lower() == "true"

# NEW: Coordinates for multi-point sampling across Hyderabad District
HYDERABAD_COORDINATES = [
    {"name": "Center (City)", "lat": 17.43, "lon": 78.49},
    {"name": "North (Secunderabad)", "lat": 17.44, "lon": 78.50},
    {"name": "West (HITEC City)", "lat": 17.45, "lon": 78.38},
    {"name": "South (Charminar)", "lat": 17.36, "lon": 78.47},
    {"name": "East (Uppal/L.B. Nagar)", "lat": 17.38, "lon": 78.55},
]

if not POST_TO_TWITTER_ENABLED:
    logging.warning("Twitter interactions are DISABLED (Test Mode). Generated images will be retained.")
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
    # FIX FOR TEST MODE: If the error is due to missing environment variables AND
    # we are in Test Mode, we log a warning but don't crash.
    if not POST_TO_TWITTER_ENABLED:
        logging.warning(f"Twitter clients skipped due to missing API keys (Test Mode): {e}")
    else:
        logging.error(f"Error initializing Twitter clients due to missing environment variable: {e}")
        # Re-raise the error if in live mode and keys are missing
        raise
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
    url = f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&appid={api_key}&units=metric&exclude=minutely,alerts"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as err:
        logging.error(f"Error fetching One Call weather data for ({lat}, {lon}): {err}")
        return None

# --- Data Aggregation ---
def aggregate_weather_data(all_weather_data):
    """
    Aggregates weather data from multiple coordinates into a single, representative object.
    """
    if not all_weather_data:
        logging.error("No valid weather data received for aggregation.")
        return None

    # --- Aggregation for CURRENT conditions ---
    current_temps = []
    current_feels_like = []
    current_humidity = []
    current_wind_speed = []
    current_descriptions_count = {}
    
    for data in all_weather_data:
        current = data.get('current')
        if current:
            current_temps.append(current.get('temp', 0))
            current_feels_like.append(current.get('feels_like', 0))
            current_humidity.append(current.get('humidity', 0))
            current_wind_speed.append(current.get('wind_speed', 0))
            
            desc = current.get('weather', [{}])[0].get('description', 'clear sky').lower()
            current_descriptions_count[desc] = current_descriptions_count.get(desc, 0) + 1

    # Calculate Averages for numerical data
    avg_temp = sum(current_temps) / len(current_temps) if current_temps else None
    avg_feels_like = sum(current_feels_like) / len(current_feels_like) if current_feels_like else None
    avg_humidity = sum(current_humidity) / len(current_humidity) if current_humidity else None
    avg_wind_speed = sum(current_wind_speed) / len(current_wind_speed) if current_wind_speed else None
    
    # Determine the most common description
    most_common_desc = max(current_descriptions_count, key=current_descriptions_count.get, default='partly cloudy')
    
    # Construct the representative 'current' object (using the first dataset for fixed info like 'dt')
    representative_data = all_weather_data[0].copy() 
    
    # Update current data
    representative_data['current']['temp'] = avg_temp
    representative_data['current']['feels_like'] = avg_feels_like
    representative_data['current']['humidity'] = avg_humidity
    representative_data['current']['wind_speed'] = avg_wind_speed
    
    # Find the 'main' type corresponding to the most common description
    main_type = next((d['current']['weather'][0]['main'] for d in all_weather_data if d.get('current', {}).get('weather', [{}])[0].get('description', '').lower() == most_common_desc and d.get('current', {}).get('weather')), 'Clouds')
    
    representative_data['current']['weather'][0]['description'] = most_common_desc
    representative_data['current']['weather'][0]['main'] = main_type
    
    # --- Aggregation for HOURLY data (Max POP) ---
    max_pops = [0.0] * 24
    for data in all_weather_data:
        hourly = data.get('hourly', [])
        for i in range(min(24, len(hourly))):
            max_pops[i] = max(max_pops[i], hourly[i].get('pop', 0))

    for i in range(min(24, len(representative_data.get('hourly', [])))):
        representative_data['hourly'][i]['pop'] = max_pops[i]

    # --- Aggregation for DAILY data (Avg Temp, Max POP) ---
    daily_min_temps = [[] for _ in range(7)]
    daily_max_temps = [[] for _ in range(7)]
    daily_max_pops = [0.0] * 7
    
    for data in all_weather_data:
        daily = data.get('daily', [])
        for i in range(min(7, len(daily))):
            temp = daily[i].get('temp', {})
            daily_min_temps[i].append(temp.get('min', 0))
            daily_max_temps[i].append(temp.get('max', 0))
            daily_max_pops[i] = max(daily_max_pops[i], daily[i].get('pop', 0))
            
    for i in range(min(7, len(representative_data.get('daily', [])))):
        avg_min = sum(daily_min_temps[i]) / len(daily_min_temps[i]) if daily_min_temps[i] else 0
        avg_max = sum(daily_max_temps[i]) / len(daily_max_temps[i]) if daily_max_temps[i] else 0
        
        representative_data['daily'][i]['temp']['min'] = avg_min
        representative_data['daily'][i]['temp']['max'] = avg_max
        representative_data['daily'][i]['pop'] = daily_max_pops[i]
            
    logging.info(f"Aggregated weather data. Avg Temp: {avg_temp:.2f}°C, Common Condition: {most_common_desc.title()}")
    return representative_data

# --- Tweet Content Creation and Formatting ---
def get_hourly_chart_data(weather_data):
    """
    Extracts the next 25 hours of weather data for the chart.
    CRITICAL FIX APPLIED: OWM hourly data starts at the current or next hour, 
    so we simply take the first 25 hours.
    """
    if not weather_data or 'hourly' not in weather_data:
        logging.error("Missing hourly data for chart generation.")
        return {"times": [], "temperatures": [], "precipitation": []}
    
    indian_tz = pytz.timezone('Asia/Kolkata')
    
    hourly_forecasts = weather_data.get('hourly', [])
            
    # Take the next 25 hours for the chart
    chart_hours = hourly_forecasts[:25] 
    
    times = [datetime.fromtimestamp(h['dt'], tz=pytz.utc).astimezone(indian_tz) for h in chart_hours]
    temperatures = [h['temp'] for h in chart_hours]
    # Precipitation: sum of rain and snow volume in the last hour
    precipitation = [h.get('rain', {}).get('1h', 0) + h.get('snow', {}).get('1h', 0) for h in chart_hours]
    
    return {
        "times": times,
        "temperatures": temperatures,
        "precipitation": precipitation
    }

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
        return {"lines": ["Could not generate weather report: Data missing."], "hashtags": ["#error"], "alt_text": "", "image_content": [], "chart_data": {}}

    indian_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.now(indian_tz)
    current_day = now.strftime('%A')
    current_hour = now.hour

    current = weather_data['current']
    temp_c = current.get('temp')
    feels_like_c = current.get('feels_like')
    humidity = current.get('humidity')
    wind_speed_mps = current.get('wind_speed')
    wind_direction_deg = current.get('wind_deg')
    sky_description_now = current.get('weather', [{}])[0].get('description', 'clouds').lower()
    
    future_rain_in_12_hours = any(hour.get('pop', 0) > 0.1 for hour in weather_data.get('hourly', [])[:12])
    
    temp_c_str = f"{temp_c:.0f}°C" if temp_c is not None else "N/A"
    feels_like_c_str = f"{feels_like_c:.0f}°C" if feels_like_c is not None else "N/A"
    humidity_str = f"{humidity:.0f}%" if humidity is not None else "N/A"
    wind_speed_mph_str = f"{wind_speed_mps * 2.237:.0f} mph" if wind_speed_mps is not None else "N/A"
    wind_direction_cardinal = degrees_to_cardinal(wind_direction_deg)
    
    # Use the max POP across all sampled points for the current hour
    pop_now = weather_data['hourly'][0].get('pop', 0)
    pop_str_now = f"{pop_now * 100:.0f}%"

    greeting = get_time_based_greeting(current_hour)
    
    time_str = now.strftime('%I:%M %p')
    date_str = f"{now.day} {now.strftime('%B')}"
    
    greeting_line = f"{greeting.title()}, {city}! 👋"
    tweet_lines = [
        f"{greeting_line}",
        f"It's currently {temp_c_str} (feels like {feels_like_c_str}) with {sky_description_now}.",
    ]
    
    daily_forecasts = weather_data.get('daily', [])
    if len(daily_forecasts) > 1:
        tomorrow_data = daily_forecasts[1]
        temp_max = tomorrow_data.get('temp', {}).get('max')
        temp_max_str = f"{temp_max:.0f}°C" if temp_max is not None else ""
        tomorrow_desc = tomorrow_data.get('weather', [{}])[0].get('description', 'clear skies').title()
        tweet_lines.append(f"Tomorrow: {tomorrow_desc}, with a high of {temp_max_str}.")
        
    hashtags = generate_dynamic_hashtags(weather_data, current_day)

    image_text_lines = []
    
    image_text_lines.append(f"Weather Update for {city.title()} District!") 
    image_text_lines.append(f"As of {time_str}, {date_str}")
    image_text_lines.append("")
    
    image_text_lines.append("Current Conditions (District Average):") 
    image_text_lines.append(f"Temperature: {temp_c_str} (feels like {feels_like_c_str})")
    image_text_lines.append(f"Weather: {sky_description_now.title()}")
    image_text_lines.append(f"Humidity: {humidity_str}")
    image_text_lines.append(f"Wind: {wind_direction_cardinal} at {wind_speed_mph_str}")
    image_text_lines.append("")
    
    weather_mood = get_weather_mood(temp_c, current_hour)
    main_paragraph_intro = f"The district is experiencing a {weather_mood}."
    rain_sentence = ""
    # Use the aggregated max POP for the current hour (index 0)
    if pop_now > 0.5:
        rain_sentence = f"There's a high chance of rain today ({pop_str_now} at the highest risk spot), so don't forget your umbrella!"
    elif pop_now > 0.1:
        rain_sentence = f"There's a small chance of rain today ({pop_str_now} at the highest risk spot), so keeping an umbrella handy might be a good idea."
    else:
        rain_sentence = f"With a {pop_str_now} maximum chance of rain, you can likely leave your umbrella at home."
        
    image_text_lines.append(f"Today's Outlook: {main_paragraph_intro} {rain_sentence}")
    image_text_lines.append("")
    
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
            
            # The 'rain' and 'snow' keys are present in hourly, but might not have '1h' key if 0
            rain_mm = hour_data.get('rain', {}).get('1h', 0)
            snow_mm = hour_data.get('snow', {}).get('1h', 0)
            precipitation_str = ""
            if rain_mm > 0 or snow_mm > 0:
                 precipitation_str = f"(Rain/Snow Vol: {rain_mm + snow_mm:.1f} mm)"
            else:
                 precipitation_str = "(Precipitation: 0 mm)"
            
            detail_str = f"By {time_str_hourly}: {description} at {temp_hourly_str},  Max Rain chance: {pop_hourly * 100:.0f}%." 
            image_text_lines.append(detail_str)
            
    image_text_lines.append("")
    
    image_text_lines.append("Upcoming 3-Day Forecast (District Avg/Max POP):") 
    daily_forecasts = weather_data.get('daily', [])
    for i in range(1, min(4, len(daily_forecasts))):
        day_data = daily_forecasts[i]
        forecast_date = datetime.fromtimestamp(day_data['dt'], tz=indian_tz)
        day_of_week = forecast_date.strftime('%A')
        temp_min = day_data.get('temp', {}).get('min')
        temp_max = day_data.get('temp', {}).get('max')
        description = day_data.get('weather', [{}])[0].get('description', '').title()
        pop_daily = day_data.get('pop', 0) 
        
        temp_min_str = f"{temp_min:.0f}°C" if temp_min is not None else "N/A"
        temp_max_str = f"{temp_max:.0f}°C" if temp_max is not None else "N/A"
        
        day_summary = f"{day_of_week}: High {temp_max_str}, Low {temp_min_str},  Expect {description}, Max POP: {pop_daily * 100:.0f}%" 
        image_text_lines.append(day_summary)
        
    image_text_lines.append("")
    
    closing_sentence = ""
    if future_rain_in_12_hours:
        closing_sentence = "Stay safe, drive carefully on the wet roads, and enjoy the weather!"
    else:
        closing_sentence = "Stay safe and have a pleasant day ahead!"
    
    image_text_lines.append(closing_sentence)
    
    full_alt_text = "\n".join(image_text_lines)

    chart_data = get_hourly_chart_data(weather_data)
    
    return {
        "lines": tweet_lines,
        "hashtags": hashtags,
        "alt_text": full_alt_text,
        "image_content": image_text_lines,
        "chart_data": chart_data
    }
    
def create_weather_image(image_text_lines, output_path="weather_report.png"):
    """Generates an image with the weather report text from a list of lines, with bold headings and text wrapping."""
    try:
        img_width, img_height = 985, 650
        bg_color, text_color = (236, 239, 241), (66, 66, 66)

        img = Image.new('RGB', (img_width, img_height), color=bg_color)
        d = ImageDraw.Draw(img)

        # NOTE: Font loading depends on your local environment. Using default if not found.
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            font_regular_path = os.path.join(script_dir, "Merriweather_36pt-MediumItalic.ttf")
            font_bold_path = os.path.join(script_dir, "Merriweather_24pt-BoldItalic.ttf")
            
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

            is_heading = False
            for prefix in heading_prefixes:
                if original_line.strip().startswith(prefix):
                    is_heading = True
                    break
            
            current_font = font_bold if is_heading else font_regular
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

def create_weather_chart(chart_data, output_path="weather_chart.png"):
    """Generates a weather chart with a temperature line and precipitation bar graph."""
    try:
        times = chart_data["times"]
        temperatures = chart_data["temperatures"]
        precipitation = chart_data["precipitation"]

        if not times or not temperatures or not precipitation:
            logging.warning("Insufficient data to create weather chart.")
            return None
            
        indian_tz = pytz.timezone('Asia/Kolkata')
        
        plt.style.use('seaborn-v0_8-white')
        
        # Explicitly set the figure size using the target dimensions
        fig, ax1 = plt.subplots(figsize=(9.85, 6.5), facecolor='#eceff1')
        fig.set_size_inches(9.85, 6.5)

        ax1.grid(True, which='major', linestyle='--', linewidth='0.5', color='grey', alpha=0.6)
        ax1.set_axisbelow(True)

        ax2 = ax1.twinx()
        if len(times) > 1:
            time_delta = (times[1] - times[0]).total_seconds()
            bar_width_in_days = time_delta / (24 * 3600) * 0.7 
        else:
            bar_width_in_days = 0.03
        ax2.bar(times, precipitation, color='#94d6d6', alpha=0.8, width=bar_width_in_days, label='Precipitation (mm)')
        ax2.set_ylabel('Precipitation (mm)', color='#666666', fontsize=12)
        ax2.tick_params(axis='y', colors='#666666')
        # Set min y-limit to 0 for precipitation
        ax2.set_ylim(0, max(max(precipitation) * 1.2, 5)) 

        ax1.plot(times, temperatures, color='#e57373', linewidth=3, marker='o', markersize=6, label='Temperature (°C)')
        
        for i, temp in enumerate(temperatures):
            is_trough = (i > 0 and temp < temperatures[i-1]) and (i < len(temperatures)-1 and temp < temperatures[i+1])
            vertical_offset = -18 if is_trough else 10 
            ax1.annotate(f"{int(round(temp))}°", (times[i], temperatures[i]),
                            textcoords="offset points", xytext=(0, vertical_offset), ha='center',
                            fontsize=12, color='#e57373', fontweight='bold')
        
        ax1.set_ylabel('Temperature (°C)', color='#666666', fontsize=12)
        ax1.tick_params(axis='y', colors='#666666')

        start_time = times[0]
        end_time = times[-1]
        ax1.set_xlim([start_time, end_time])
        
        now = datetime.now(indian_tz)
        ax1.axvline(now, color='green', linestyle='--', linewidth=2)

        # Ensure temperature limits are reasonable
        temp_min_data = min(temperatures) if temperatures else 0
        temp_max_data = max(temperatures) if temperatures else 0
        ax1.set_ylim([temp_min_data - 5, temp_max_data + 5])

        ax1.set_title(f'24-Hour Weather Forecast for {CITY_TO_MONITOR.title()}', fontsize=16, fontweight='bold', color='#666666')
        ax1.set_xlabel('Time of Day', color='#666666', fontsize=12)

        def date_formatter(x, pos):
            # Using timezone-aware conversion
            dt = mdates.num2date(x, tz=indian_tz)
            
            # Check for day change to add date label
            # This logic assumes ax1.get_xticks() returns sorted numeric values
            day_changed = True 
            if pos > 0:
                prev_dt = mdates.num2date(ax1.get_xticks()[pos-1], tz=indian_tz)
                if dt.day == prev_dt.day:
                    day_changed = False
            
            if day_changed:
                return dt.strftime('%I %p\n%b %d')
            else:
                return dt.strftime('%I %p')

        ax1.xaxis.set_major_formatter(plt.FuncFormatter(date_formatter))
        ax1.xaxis.set_major_locator(mdates.HourLocator(interval=3))
        
        for tick in ax1.get_xticklabels():
            tick.set_fontsize(10)
            tick.set_color('#666666')

        ax1.set_facecolor('#ffffff')

        lines, labels = ax1.get_legend_handles_labels()
        bars, bar_labels = ax2.get_legend_handles_labels()
        ax1.legend(lines + bars, labels + bar_labels, loc='upper left', fontsize=10, frameon=True, fancybox=True, shadow=True)

        plt.tight_layout()
        plt.savefig(output_path, dpi=100)
        plt.close()
        logging.info(f"Weather chart created successfully at {output_path}")
        return output_path
    except Exception as e:
        logging.error(f"Error creating weather chart: {e}")
        return None

# --- Tweeting Function (MODIFIED) ---
def tweet_post(tweet_content):
    """Assembles and posts a tweet with dynamically generated images."""
    
    image_paths_to_manage = [GENERATED_IMAGE_PATH, GENERATED_CHART_PATH]
    
    # NEW: Clean up old files before generating new ones (good practice)
    cleanup_temp_files(image_paths_to_manage)
    
    # Use constants for output paths
    generated_image_path = create_weather_image(tweet_content['image_content'], GENERATED_IMAGE_PATH)
    generated_chart_path = create_weather_chart(tweet_content['chart_data'], GENERATED_CHART_PATH)
    
    if not POST_TO_TWITTER_ENABLED:
        logging.info("[TEST MODE] Skipping actual Twitter post.")
        logging.info("Tweet Content:\n" + "\n".join(tweet_content['lines']) + "\n" + " ".join(tweet_content['hashtags']))
        
        if generated_image_path and os.path.exists(generated_image_path):
            logging.info(f"Generated text image retained for inspection: {generated_image_path}")
        if generated_chart_path and os.path.exists(generated_chart_path):
            logging.info(f"Generated chart image retained for inspection: {generated_chart_path}")

        return True

    # --- Live Mode Logic (Only runs if POST_TO_TWITTER_ENABLED is true) ---
    if not all([bot_api_client_v1, bot_api_client_v2]):
        logging.error("Twitter clients not initialized. Aborting tweet post in LIVE mode.")
        # NEW: Clean up generated files if we're in live mode but aborting
        cleanup_temp_files(image_paths_to_manage)
        return False
        
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
    
    
    if generated_image_path and os.path.exists(generated_image_path):
        try:
            logging.info(f"Uploading media: {generated_image_path}")
            media = bot_api_client_v1.media_upload(filename=generated_image_path)
            media_ids.append(media.media_id)
            alt_text = tweet_content['alt_text']
            if len(alt_text) > 1000:
                alt_text = alt_text[:997] + "..."
            bot_api_client_v1.create_media_metadata(media_id=media.media_id_string, alt_text=alt_text)
            logging.info("Text image uploaded and alt text added successfully.")
        except Exception as e:
            logging.error(f"Failed to upload text image or add alt text: {e}")
    else:
        logging.error("Failed to generate weather text image.")

    if generated_chart_path and os.path.exists(generated_chart_path):
        try:
            logging.info(f"Uploading media: {generated_chart_path}")
            media = bot_api_client_v1.media_upload(filename=generated_chart_path)
            media_ids.append(media.media_id)
            chart_alt_text = f"A chart showing the 24-hour temperature and precipitation forecast for {CITY_TO_MONITOR}."
            bot_api_client_v1.create_media_metadata(media_id=media.media_id_string, alt_text=chart_alt_text)
            logging.info("Chart image uploaded and alt text added successfully.")
        except Exception as e:
            logging.error(f"Failed to upload chart image or add alt text: {e}")
    else:
        logging.error("Failed to generate weather chart image.")
        
    # NEW: Cleanup logic centralized in the helper function
    cleanup_temp_files(image_paths_to_manage) 

    if not media_ids:
        logging.warning("No images were successfully uploaded. Posting tweet without media.")
        
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
    """Main task to fetch data from multiple points, aggregate, create content, and post the tweet."""
    logging.info(f"--- Running district-wide weather tweet job for {CITY_TO_MONITOR} ---")
    try:
        # WEATHER_API_KEY is still critical, even in test mode, to fetch weather data.
        weather_api_key = get_env_variable("WEATHER_API_KEY") 
    except EnvironmentError:
        logging.error("WEATHER_API_KEY not found. Aborting.")
        return False

    all_weather_data = []

    # 1. Execute Multiple API Calls for the entire district
    for coord in HYDERABAD_COORDINATES:
        lat = coord['lat']
        lon = coord['lon']
        logging.info(f"Fetching weather for {coord['name']} ({lat}, {lon})...")
        data = get_one_call_weather_data(lat, lon, weather_api_key)
        if data:
            all_weather_data.append(data)
        else:
            logging.warning(f"Skipping point {coord['name']} due to failed API call.")

    if not all_weather_data:
        logging.error(f"Failed to retrieve weather data from any point in {CITY_TO_MONITOR}. Aborting.")
        return False

    # 2. Aggregate the Data
    aggregated_data = aggregate_weather_data(all_weather_data)

    if not aggregated_data:
        logging.warning(f"Could not aggregate weather for {CITY_TO_MONITOR}. Aborting.")
        return False

    # 3. Create Content and Tweet 
    tweet_content = create_weather_tweet_content(CITY_TO_MONITOR, aggregated_data)
    
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
    is_debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logging.info(f"--- Starting Flask Server on port {app_port} ---")
    logging.info(f"Debug mode is {'ON' if is_debug_mode else 'OFF'}")
    app.run(host='0.0.0.0', port=app_port, debug=is_debug_mode)