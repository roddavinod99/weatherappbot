#!/usr/bin/env python3
# file: weather_tweet_bot.py
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz
import requests
import tweepy
from flask import Flask
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --- Logging / Basic config ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Types & Constants ---
@dataclass(frozen=True)
class Coordinate:
    name: str
    lat: float
    lon: float

TWITTER_MAX_CHARS = 280
CITY_TO_MONITOR = os.environ.get("CITY_TO_MONITOR", "Hyderabad")
GENERATED_IMAGE_PATH = Path("weather_report.png")
GENERATED_CHART_PATH = Path("weather_chart.png")

POST_TO_TWITTER_ENABLED = os.environ.get("POST_TO_TWITTER_ENABLED", "false").lower() == "true"

HYDERABAD_COORDINATES: List[Coordinate] = [
    Coordinate("Center (City)", 17.43, 78.49),
    Coordinate("North (Secunderabad)", 17.44, 78.50),
    Coordinate("West (HITEC City)", 17.45, 78.38),
    Coordinate("South (Charminar)", 17.36, 78.47),
    Coordinate("East (Uppal/L.B. Nagar)", 17.38, 78.55),
]

INDIAN_TZ = pytz.timezone("Asia/Kolkata")

if not POST_TO_TWITTER_ENABLED:
    logging.warning("Twitter interactions are DISABLED (TEST MODE). Generated images will be retained.")
else:
    logging.info("Twitter interactions ARE ENABLED (LIVE MODE).")

# --- Flask app ---
app = Flask(__name__)

# --- Utilities ---
def get_env_variable(name: str, critical: bool = True) -> Optional[str]:
    val = os.environ.get(name)
    if val is None and critical:
        raise EnvironmentError(f"Required environment variable missing: {name}")
    return val

def cleanup_temp_files(paths: List[Path]) -> None:
    for p in paths:
        try:
            if p and p.exists():
                p.unlink()
                logging.debug("Removed temp file: %s", p)
        except Exception:
            logging.warning("Couldn't remove file %s", p, exc_info=True)

def safe_mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None

def degrees_to_cardinal(d: Optional[float]) -> str:
    if d is None:
        return "N/A"
    try:
        d = float(d)
    except (TypeError, ValueError):
        return "N/A"
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    ix = int((d + 11.25) / 22.5)
    return dirs[ix % 16]

def format_temp_str(temp: Optional[float]) -> str:
    return f"{temp:.0f}°C" if temp is not None else "N/A"

def get_time_based_greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    return "Good evening"

def get_weather_mood(temp_c: Optional[float], hour: int) -> str:
    if temp_c is None:
        return "typical weather"
    if hour >= 22 or hour < 5:
        return "calm night"
    if temp_c > 35:
        return "warm afternoon" if hour >= 12 else "hot morning"
    if temp_c < 20:
        return "cool morning" if hour < 12 else "chilly afternoon"
    return "pleasant day"

# --- Twitter clients init (v2 + v1.1 for media) ---
bot_api_client_v2: Optional[tweepy.Client] = None
bot_api_client_v1: Optional[tweepy.API] = None

try:
    # If live mode, require keys. In test mode, skip if missing.
    consumer_key = get_env_variable("TWITTER_API_KEY", critical=POST_TO_TWITTER_ENABLED)
    consumer_secret = get_env_variable("TWITTER_API_SECRET", critical=POST_TO_TWITTER_ENABLED)
    access_token = get_env_variable("TWITTER_ACCESS_TOKEN", critical=POST_TO_TWITTER_ENABLED)
    access_token_secret = get_env_variable("TWITTER_ACCESS_TOKEN_SECRET", critical=POST_TO_TWITTER_ENABLED)

    if all([consumer_key, consumer_secret, access_token, access_token_secret]):
        bot_api_client_v2 = tweepy.Client(
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
            wait_on_rate_limit=True,
        )
        auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, access_token, access_token_secret)
        bot_api_client_v1 = tweepy.API(auth)
        logging.info("Twitter clients initialized.")
    else:
        logging.info("Twitter credentials not present; skipping twitter client initialization.")
except EnvironmentError as e:
    logging.error("Twitter init error: %s", e)
    if POST_TO_TWITTER_ENABLED:
        raise
except Exception:
    logging.critical("Unexpected error while initializing Twitter clients.", exc_info=True)
    if POST_TO_TWITTER_ENABLED:
        raise

# --- Weather / API functions ---
def get_one_call_weather_data(lat: float, lon: float, api_key: str) -> Optional[Dict[str, Any]]:
    url = f"https://api.openweathermap.org/data/3.0/onecall"
    params = {"lat": lat, "lon": lon, "appid": api_key, "units": "metric", "exclude": "minutely,alerts"}
    try:
        resp = requests.get(url, params=params, timeout=12)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException:
        logging.warning("Weather API request failed for (%.3f, %.3f).", lat, lon, exc_info=True)
        return None

def aggregate_weather_data(all_weather_data: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not all_weather_data:
        logging.error("No weather data to aggregate.")
        return None

    current_temps, feels, humidities, wind_speeds = [], [], [], []
    desc_count: Dict[str, int] = {}

    for d in all_weather_data:
        curr = d.get("current")
        if not curr:
            continue
        current_temps.append(curr.get("temp", 0.0))
        feels.append(curr.get("feels_like", 0.0))
        humidities.append(curr.get("humidity", 0.0))
        wind_speeds.append(curr.get("wind_speed", 0.0))
        desc = curr.get("weather", [{}])[0].get("description", "clear sky").lower()
        desc_count[desc] = desc_count.get(desc, 0) + 1

    avg_temp = safe_mean(current_temps)
    avg_feels = safe_mean(feels)
    avg_humidity = safe_mean(humidities)
    avg_wind = safe_mean(wind_speeds)
    most_common_desc = max(desc_count, key=desc_count.get) if desc_count else "partly cloudy"

    # Start from first dataset and update numeric fields
    rep = {**all_weather_data[0]}  # shallow copy
    rep.setdefault("current", {})
    rep["current"]["temp"] = avg_temp
    rep["current"]["feels_like"] = avg_feels
    rep["current"]["humidity"] = avg_humidity
    rep["current"]["wind_speed"] = avg_wind
    rep["current"].setdefault("weather", [{}])
    rep["current"]["weather"][0]["description"] = most_common_desc

    # Hourly: take max POP per hour across samples
    hourly_len = min(24, max((len(d.get("hourly", [])) for d in all_weather_data)))
    max_pops = [0.0] * hourly_len
    for d in all_weather_data:
        hourly = d.get("hourly", [])
        for i in range(min(hourly_len, len(hourly))):
            max_pops[i] = max(max_pops[i], hourly[i].get("pop", 0.0))
    if "hourly" in rep:
        for i in range(min(len(rep["hourly"]), hourly_len)):
            rep["hourly"][i]["pop"] = max_pops[i]

    # Daily: average min/max temps and take max POPs
    daily_len = min(7, max((len(d.get("daily", [])) for d in all_weather_data)))
    daily_min_temps: List[List[float]] = [[] for _ in range(daily_len)]
    daily_max_temps: List[List[float]] = [[] for _ in range(daily_len)]
    daily_max_pops = [0.0] * daily_len

    for d in all_weather_data:
        daily = d.get("daily", [])
        for i in range(min(daily_len, len(daily))):
            t = daily[i].get("temp", {})
            daily_min_temps[i].append(t.get("min", 0))
            daily_max_temps[i].append(t.get("max", 0))
            daily_max_pops[i] = max(daily_max_pops[i], daily[i].get("pop", 0.0))

    if "daily" in rep:
        for i in range(min(len(rep["daily"]), daily_len)):
            rep["daily"][i]["temp"]["min"] = safe_mean(daily_min_temps[i]) or 0
            rep["daily"][i]["temp"]["max"] = safe_mean(daily_max_temps[i]) or 0
            rep["daily"][i]["pop"] = daily_max_pops[i]

    logging.info("Aggregated weather. Avg Temp: %.2f°C, Condition: %s", (avg_temp or 0), most_common_desc.title())
    return rep

# --- Chart & Image creation ---
def get_hourly_chart_data(weather_data: Dict[str, Any]) -> Dict[str, List[Any]]:
    if not weather_data or "hourly" not in weather_data:
        logging.error("Missing hourly data for chart.")
        return {"times": [], "temperatures": [], "precipitation": []}
    hourly = weather_data["hourly"][:25]  # next 25 hours
    times = [datetime.fromtimestamp(h["dt"], tz=pytz.utc).astimezone(INDIAN_TZ) for h in hourly]
    temperatures = [h.get("temp", 0.0) for h in hourly]
    precipitation = [h.get("rain", {}).get("1h", 0.0) + h.get("snow", {}).get("1h", 0.0) for h in hourly]
    return {"times": times, "temperatures": temperatures, "precipitation": precipitation}

def _load_merriweather_fonts(size: int) -> Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """
    Primary attempt: load the two Merriweather fonts from same directory as script:
      - Merriweather_36pt-MediumItalic.ttf  -> regular (36pt filename used but we load at requested size)
      - Merriweather_24pt-BoldItalic.ttf    -> bold
    Fallbacks: system fonts, then Pillow default.
    Returns (regular_font, bold_font).
    """
    script_dir = Path(__file__).parent
    regular_name = script_dir / "Merriweather_36pt-MediumItalic.ttf"
    bold_name = script_dir / "Merriweather_24pt-BoldItalic.ttf"

    # Try to load the provided Merriweather fonts first
    try:
        font_regular = ImageFont.truetype(str(regular_name), size)
        logging.info("Loaded regular font: %s", regular_name)
    except Exception:
        font_regular = None
        logging.debug("Failed to load Merriweather regular from %s", regular_name, exc_info=True)

    try:
        font_bold = ImageFont.truetype(str(bold_name), size)
        logging.info("Loaded bold font: %s", bold_name)
    except Exception:
        font_bold = None
        logging.debug("Failed to load Merriweather bold from %s", bold_name, exc_info=True)

    # If either font not loaded, try common system fonts
    if font_regular is None or font_bold is None:
        system_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/Library/Fonts/Arial.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
        ]
        for cand in system_candidates:
            if (font_regular is None) and Path(cand).exists():
                try:
                    font_regular = ImageFont.truetype(cand, size)
                    logging.info("Loaded fallback regular font: %s", cand)
                except Exception:
                    font_regular = font_regular
            if (font_bold is None) and Path(cand).exists():
                try:
                    font_bold = ImageFont.truetype(cand, size)
                    logging.info("Loaded fallback bold font: %s", cand)
                except Exception:
                    font_bold = font_bold

    # Final fallback to default
    if font_regular is None:
        font_regular = ImageFont.load_default()
        logging.warning("Using Pillow default font for regular font (Merriweather not found).")
    if font_bold is None:
        font_bold = font_regular
        logging.warning("Using regular font as bold fallback (Merriweather bold not found).")

    return font_regular, font_bold

def create_weather_image(lines: List[str], output_path: Path = GENERATED_IMAGE_PATH) -> Optional[Path]:
    try:
        img_w, img_h = 985, 650
        bg_color = (255, 255, 255)
        text_color = (40, 40, 40)

        img = Image.new("RGB", (img_w, img_h), color=bg_color)
        draw = ImageDraw.Draw(img)

        font_size = 18
        font_regular, font_bold = _load_merriweather_fonts(font_size)

        line_height = font_size + 6
        padding_x, padding_y = 20, 20
        max_width = img_w - 2 * padding_x

        y = padding_y
        heading_prefixes = ("Weather Update", "Current Conditions", "Today's Outlook", "Detailed Hourly Forecast",
                            "Upcoming 3-Day Forecast")

        for original in lines:
            if not original.strip():
                y += line_height
                continue
            is_heading = any(original.strip().startswith(p) for p in heading_prefixes)
            font = font_bold if is_heading else font_regular

            words = original.split()
            cur_words: List[str] = []
            for w in words:
                test = " ".join(cur_words + [w])
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    cur_words.append(w)
                else:
                    draw.text((padding_x, y), " ".join(cur_words), font=font, fill=text_color)
                    y += line_height
                    cur_words = [w]
            if cur_words:
                draw.text((padding_x, y), " ".join(cur_words), font=font, fill=text_color)
                y += line_height

            if y >= img_h - padding_y:
                logging.warning("Image overflow; content truncated.")
                break

        img.save(output_path)
        logging.info("Saved weather image: %s", output_path)
        return output_path
    except Exception:
        logging.error("Error creating weather image.", exc_info=True)
        return None

def create_weather_chart(chart_data: Dict[str, List[Any]], output_path: Path = GENERATED_CHART_PATH) -> Optional[Path]:
    try:
        times = chart_data.get("times") or []
        temps = chart_data.get("temperatures") or []
        prec = chart_data.get("precipitation") or []
        if not times or not temps:
            logging.warning("Insufficient chart data.")
            return None

        fig, ax1 = plt.subplots(figsize=(9.85, 6.5))
        ax2 = ax1.twinx()

        # precipitation bars
        if len(times) > 1:
            delta = (times[1] - times[0]).total_seconds()
            width = delta / (24 * 3600) * 0.7
        else:
            width = 0.03
        ax2.bar(times, prec, alpha=0.7, width=width, label="Precipitation (mm)")
        ax2.set_ylabel("Precipitation (mm)")

        ax1.plot(times, temps, linewidth=2.5, marker="o", label="Temperature (°C)")
        ax1.set_ylabel("Temperature (°C)")

        # annotate temps
        for i, t in enumerate(temps):
            offset = -16 if (0 < i < len(temps)-1 and t < temps[i-1] and t < temps[i+1]) else 8
            ax1.annotate(f"{int(round(t))}°", (times[i], temps[i]), textcoords="offset points", xytext=(0, offset),
                         ha="center", fontsize=10)

        ax1.set_title(f"24-Hour Weather Forecast for {CITY_TO_MONITOR.title()}")
        ax1.set_xlabel("Time")
        ax1.xaxis.set_major_locator(mdates.HourLocator(interval=3))
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%I %p\n%b %d", tz=INDIAN_TZ))

        # reasonable y-limits
        min_t, max_t = min(temps), max(temps)
        ax1.set_ylim(min_t - 5, max_t + 5)
        ax2.set_ylim(0, max(max(prec) * 1.2, 5))

        now = datetime.now(INDIAN_TZ)
        ax1.axvline(now, color="green", linestyle="--", linewidth=1.5, label="Now")

        lines, labels = ax1.get_legend_handles_labels()
        bars, bar_labels = ax2.get_legend_handles_labels()
        ax1.legend(lines + bars, labels + bar_labels, loc="upper left", fontsize=9)

        plt.tight_layout()
        fig.savefig(output_path, dpi=100)
        plt.close(fig)
        logging.info("Saved chart image: %s", output_path)
        return output_path
    except Exception:
        logging.error("Error creating chart.", exc_info=True)
        return None

# --- Content creation ---
def generate_dynamic_hashtags(weather_data: Dict[str, Any], current_day: str) -> List[str]:
    tags = {f"#{CITY_TO_MONITOR.replace(' ', '')}", "#weatherupdate"}
    if not weather_data:
        return list(tags)
    cur = weather_data.get("current", {})
    temp = cur.get("temp", 0)
    desc = cur.get("weather", [{}])[0].get("description", "").lower()
    wind = cur.get("wind_speed", 0)
    if any("rain" in (h.get("weather", [{}])[0].get("main", "").lower()) for h in weather_data.get("hourly", [])[:12]):
        tags.update({f"#{CITY_TO_MONITOR}Rains", "#RainAlert"})
    if temp and temp > 35:
        tags.add("#Heatwave")
    elif temp and temp < 15:
        tags.add("#ColdWeather")
    if "clear" in desc:
        tags.add("#SunnyDay")
    elif "cloud" in desc:
        tags.add("#Cloudy")
    if wind * 3.6 > 25:
        tags.add("#Windy")
    if current_day in ("Saturday", "Sunday"):
        tags.add("#WeekendWeather")
    return list(tags)

def create_weather_tweet_content(city: str, weather_data: Dict[str, Any]) -> Dict[str, Any]:
    if not weather_data or "current" not in weather_data or "hourly" not in weather_data:
        logging.error("Invalid weather data for tweet content.")
        return {"lines": ["Could not generate weather report: Data missing."], "hashtags": ["#error"], "alt_text": "", "image_content": [], "chart_data": {}}

    now = datetime.now(INDIAN_TZ)
    curr = weather_data["current"]
    temp = curr.get("temp")
    feels = curr.get("feels_like")
    humidity = curr.get("humidity")
    wind_speed = curr.get("wind_speed", 0)
    wind_deg = curr.get("wind_deg")
    sky = curr.get("weather", [{}])[0].get("description", "clouds").title()

    pop_now = weather_data.get("hourly", [{}])[0].get("pop", 0.0)

    greeting = get_time_based_greeting(now.hour)
    tweet_lines = [
        f"{greeting}, {city}! 👋",
        f"It's currently {format_temp_str(temp)} (feels like {format_temp_str(feels)}) with {sky}.",
    ]

    # short tomorrow summary
    daily = weather_data.get("daily", [])
    if len(daily) > 1:
        tomorrow = daily[1]
        tmax = tomorrow.get("temp", {}).get("max")
        desc_t = tomorrow.get("weather", [{}])[0].get("description", "").title()
        tweet_lines.append(f"Tomorrow: {desc_t}, high of {format_temp_str(tmax)}.")

    hashtags = generate_dynamic_hashtags(weather_data, now.strftime("%A"))

    image_text: List[str] = [
        f"Weather Update for {city.title()} District!",
        f"As of {now.strftime('%I:%M %p')}, {now.day} {now.strftime('%B')}",
        "",
        "Current Conditions (District Average):",
        f"Temperature: {format_temp_str(temp)} (feels like {format_temp_str(feels)})",
        f"Weather: {sky}",
        f"Humidity: {humidity or 'N/A'}%",
        f"Wind: {degrees_to_cardinal(wind_deg)} at {wind_speed * 3.6:.0f} km/h",
        "",
    ]

    mood = get_weather_mood(temp, now.hour)
    if pop_now > 0.5:
        rain_sentence = f"High chance of rain today ({pop_now*100:.0f}%)."
    elif pop_now > 0.1:
        rain_sentence = f"Small chance of rain ({pop_now*100:.0f}%)."
    else:
        rain_sentence = f"Low chance of rain ({pop_now*100:.0f}%)."
    image_text.append(f"Today's Outlook: The district is experiencing a {mood}. {rain_sentence}")
    image_text.append("")

    image_text.append("Detailed Hourly Forecast (Next 12h):")
    hourly = weather_data.get("hourly", [])
    for i in range(3, 13, 3):
        if i < len(hourly):
            h = hourly[i]
            t = h.get("temp")
            p = h.get("pop", 0.0)
            desc = h.get("weather", [{}])[0].get("description", "").title()
            forecast_time = datetime.fromtimestamp(h["dt"], tz=pytz.utc).astimezone(INDIAN_TZ)
            image_text.append(f"By {forecast_time.strftime('%I %p')}: {desc} at {format_temp_str(t)}, Max Rain chance: {p*100:.0f}%.")

    image_text.append("")
    image_text.append("Upcoming 3-Day Forecast (District Avg/Max POP):")
    for i in range(1, min(4, len(daily))):
        d = daily[i]
        date = datetime.fromtimestamp(d["dt"], tz=pytz.utc).astimezone(INDIAN_TZ)
        desc = d.get("weather", [{}])[0].get("description", "").title()
        tmin = d.get("temp", {}).get("min")
        tmax = d.get("temp", {}).get("max")
        pop = d.get("pop", 0.0)
        image_text.append(f"{date.strftime('%A')}: High {format_temp_str(tmax)}, Low {format_temp_str(tmin)}. Expect {desc}. Max POP: {pop*100:.0f}%")

    closing = "Stay safe and have a pleasant day!" if all(h.get("pop", 0) <= 0.1 for h in hourly[:12]) else "Stay safe — expect wet roads and reduced visibility."
    image_text.append("")
    image_text.append(closing)

    alt_text = "\n".join(image_text)

    return {
        "lines": tweet_lines,
        "hashtags": hashtags,
        "alt_text": alt_text,
        "image_content": image_text,
        "chart_data": get_hourly_chart_data(weather_data),
    }

# --- Tweet posting ---
def tweet_post(tweet_content: Dict[str, Any]) -> bool:
    tmp_paths = [GENERATED_IMAGE_PATH, GENERATED_CHART_PATH]
    cleanup_temp_files(tmp_paths)  # start clean

    img_path = create_weather_image(tweet_content["image_content"], GENERATED_IMAGE_PATH)
    chart_path = create_weather_chart(tweet_content["chart_data"], GENERATED_CHART_PATH)

    if not POST_TO_TWITTER_ENABLED:
        logging.info("[TEST MODE] Tweet would be:\n%s\n%s", "\n".join(tweet_content["lines"]), " ".join(tweet_content["hashtags"]))
        if img_path:
            logging.info("Retained image: %s", img_path)
        if chart_path:
            logging.info("Retained chart: %s", chart_path)
        return True

    if not all([bot_api_client_v1, bot_api_client_v2]):
        logging.error("Twitter clients unavailable in LIVE mode.")
        cleanup_temp_files(tmp_paths)
        return False

    body = "\n".join(tweet_content["lines"])
    hashtags = tweet_content.get("hashtags", [])
    # trim hashtags until fits
    tweet_text = f"{body}\n{' '.join(hashtags)}"
    while len(tweet_text) > TWITTER_MAX_CHARS and hashtags:
        hashtags.pop()
        tweet_text = f"{body}\n{' '.join(hashtags)}"
    if len(tweet_text) > TWITTER_MAX_CHARS:
        tweet_text = tweet_text[: TWITTER_MAX_CHARS - 3] + "..."

    media_ids: List[int] = []
    # Upload text image
    if img_path and img_path.exists():
        try:
            media = bot_api_client_v1.media_upload(filename=str(img_path))
            media_ids.append(media.media_id)
            alt = tweet_content.get("alt_text", "")
            if len(alt) > 1000:
                alt = alt[:997] + "..."
            bot_api_client_v1.create_media_metadata(media_id=media.media_id_string, alt_text=alt)
            logging.info("Uploaded text image.")
        except Exception:
            logging.error("Failed to upload text image.", exc_info=True)
    else:
        logging.error("Text image missing; skipping media upload.")

    # Upload chart
    if chart_path and chart_path.exists():
        try:
            media = bot_api_client_v1.media_upload(filename=str(chart_path))
            media_ids.append(media.media_id)
            chart_alt = f"A chart showing the 24-hour temperature and precipitation forecast for {CITY_TO_MONITOR}."
            bot_api_client_v1.create_media_metadata(media_id=media.media_id_string, alt_text=chart_alt)
            logging.info("Uploaded chart image.")
        except Exception:
            logging.error("Failed to upload chart image.", exc_info=True)
    else:
        logging.error("Chart image missing; skipping chart upload.")

    # Do not attempt to post if no media and you rely on them — but posting without media is allowed
    try:
        resp = bot_api_client_v2.create_tweet(text=tweet_text, media_ids=media_ids if media_ids else None)
        tid = getattr(resp, "data", {}).get("id")
        logging.info("Tweet posted successfully. Tweet ID: %s", tid)
        cleanup_temp_files(tmp_paths)
        return True
    except Exception:
        logging.error("Failed to post tweet.", exc_info=True)
        cleanup_temp_files(tmp_paths)
        return False

# --- Main scheduled task ---
def perform_scheduled_tweet_task() -> bool:
    logging.info("--- Running district-wide weather tweet job for %s ---", CITY_TO_MONITOR)
    try:
        weather_api_key = get_env_variable("WEATHER_API_KEY")
    except EnvironmentError:
        logging.error("WEATHER_API_KEY missing. Aborting.")
        return False

    all_data: List[Dict[str, Any]] = []
    for coord in HYDERABAD_COORDINATES:
        logging.info("Fetching weather for %s (%s, %s)...", coord.name, coord.lat, coord.lon)
        d = get_one_call_weather_data(coord.lat, coord.lon, weather_api_key)
        if d:
            all_data.append(d)
        else:
            logging.warning("Failed to fetch for %s; skipping.", coord.name)

    if not all_data:
        logging.error("No weather data retrieved; aborting.")
        return False

    agg = aggregate_weather_data(all_data)
    if not agg:
        logging.error("Aggregation failed; aborting.")
        return False

    content = create_weather_tweet_content(CITY_TO_MONITOR, agg)
    if content["lines"] and "Could not generate weather report" in content["lines"][0]:
        logging.error("Tweet content generation failed.")
        return False

    ok = tweet_post(content)
    if ok:
        logging.info("Tweet task completed.")
    else:
        logging.warning("Tweet task failed.")
    return ok

# --- Flask routes ---
@app.route("/")
def home():
    mode = "LIVE MODE" if POST_TO_TWITTER_ENABLED else "TEST MODE"
    return f"Weather Tweet Bot is alive! Current mode: {mode}", 200

@app.route("/run-tweet-task", methods=["POST", "GET"])
def run_tweet_task_endpoint():
    logging.info("Triggered run-tweet-task endpoint.")
    ok = perform_scheduled_tweet_task()
    if ok:
        return "Tweet task executed successfully.", 200
    return "Tweet task execution failed or was skipped.", 500

# --- CLI / Entrypoint ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    logging.info("Starting Flask server on port %d (debug=%s)", port, debug)
    app.run(host="0.0.0.0", port=port, debug=debug)
