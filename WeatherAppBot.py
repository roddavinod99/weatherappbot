import tweepy
import requests
from config import * # Ensure this file exists and has your Twitter API credentials
import schedule
import time # Added for potential scheduling loop
from datetime import datetime

# Initialize Twitter API client
try:
    bot_api_client = tweepy.Client(
        bearer_token=TWITTER_BEARER_TOKEN,
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
    )
    print("Twitter client initialized successfully.")
except Exception as e:
    print(f"Error initializing Twitter client: {e}")
    exit() # Exit if client initialization fails


def get_weather(city):
    """Fetches weather data for a given city from OpenWeatherMap."""
    # Assuming WEATHER_API_KEY is defined in config.py or globally
    url = f'https://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric'
    try:
        response = requests.get(url)
        response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)
        if response.status_code == 200:
            print(f"Successfully fetched weather data for {city}.")
            return response.json()
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error fetching weather data for {city}: {http_err} - Status Code: {response.status_code}, Response: {response.text}")
    except requests.exceptions.RequestException as req_err:
        print(f"Error fetching weather data for {city}: {req_err}")
    return None

def create_weather_tweet(city):
    """Creates the text for a weather tweet."""
    print(f"Attempting to create weather tweet for {city}...")
    weather_data = get_weather(city)
    if weather_data:
        weather = weather_data.get('weather', [{}])[0] # Safely get the first weather item
        main = weather_data.get('main', {})
        wind = weather_data.get('wind', {})
        rain_info = weather_data.get('rain', {}) # Get rain information (e.g., {'1h': 0.5})

        weather_description = weather.get('description', 'Not available').capitalize()
        current_temp = main.get('temp', 'N/A')
        feels_like = main.get('feels_like', 'N/A')
        humidity = main.get('humidity', 'N/A')
        wind_speed = wind.get('speed', 'N/A')

        rain_forecast = "No rain expected."
        if rain_info:
            rain_volume_1h = rain_info.get('1h')
            rain_volume_3h = rain_info.get('3h')
            if rain_volume_1h is not None:
                rain_forecast = f"Rain expected in the next hour: {rain_volume_1h} mm."
            elif rain_volume_3h is not None:
                rain_forecast = f"Rain expected in the next 3 hours: {rain_volume_3h} mm."
            else:
                rain_forecast = "Rain information available but no volume specified."
        elif 'rain' in [item.get('main', '').lower() for item in weather_data.get('weather', [])]:
            rain_forecast = "Light rain may be expected."


        my_tweet = f"Weather update for {city}:\n" \
                   f"Weather Description: {weather_description}\n" \
                   f"Temperature: {current_temp}°C\n" \
                   f"Feels like: {feels_like}°C\n" \
                   f"Humidity: {humidity}%\n" \
                   f"Wind Speed: {wind_speed} m/s\n" \
                   f"Rain Forecast: {rain_forecast}\n\n" \
                   f"Weather data provided by #OpenWeatherMap."
        print(f"Tweet content created: {my_tweet}")
        return my_tweet
    else:
        error_message = f"Could not retrieve weather data for {city} to create a tweet."
        print(error_message)
        return error_message # Return the error message to be potentially tweeted

def tweet_post(tweet_text):
    """Posts the given text as a tweet."""
    if not tweet_text:
        print("Tweet text is empty, cannot post.")
        return

    # Check if the tweet content indicates a data retrieval failure
    if "Could not retrieve weather data" in tweet_text:
        print(f"Skipping tweet post because: {tweet_text}")
        # Optionally, you could decide to tweet this error message or handle it differently
        # For now, we'll just print and not tweet if weather data failed.
        return

    try:
        bot_api_client.create_tweet(text=tweet_text)
        print("Tweet posted successfully!")
    except tweepy.TweepyException as err:
        print(f"Error posting tweet: {err}")
    except Exception as e:
        print(f"An unexpected error occurred during tweeting: {e}")

# --- Main execution part ---
def main_job():
    """Defines the main task to be performed."""
    city_to_check = "Gachibowli"
    print(f"\n--- Running weather tweet job for {city_to_check} ---")
    weather_tweet_content = create_weather_tweet(city_to_check)
    tweet_post(weather_tweet_content)
    print("--- Job finished ---")

if __name__ == "__main__":
    # This will run the job once when you execute the script.
    main_job()
    print("\n--- Starting scheduler ---")
    # Schedule the main_job to run every 90 minutes
    schedule.every(90).minutes.do(main_job)

    # Keep the script running to allow the scheduler to work
    try:
        while True:
            schedule.run_pending() # Run all jobs that are scheduled to run
            time.sleep(1) # Wait 1 second before checking again
    except KeyboardInterrupt:
        print("Scheduler stopped by user.")