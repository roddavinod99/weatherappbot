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
            
            # Test v1.1 client - verify rate limit status
            rate_limit_status = bot_api_client_v1.get_rate_limit_status()
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

# [Keep all your existing weather functions unchanged]
# ... (get_weather_forecast, generate_dynamic_hashtags, create_weather_tweet_content, create_weather_image functions remain the same)

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

# [Keep all your existing functions unchanged - perform_scheduled_tweet_task, Flask routes, etc.]
