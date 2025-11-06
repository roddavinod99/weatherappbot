# 🌦️ Weather Tweet Bot

A Python-based Twitter bot that automatically fetches real-time weather data, generates a visually-rich report, and posts it to Twitter. This bot is designed to be easily configurable for any city and deployable in a containerized environment.

## ✨ Features

-   **Automated Weather Updates**: Fetches weather data from the OpenWeatherMap API and generates a concise weather summary.
-   **Dynamic Image Generation**: Creates a rich, informative image for each tweet, summarizing current and upcoming weather conditions using Pillow.
-   **Weather Charting**: Generates a 24-hour forecast chart using Matplotlib.
-   **Intelligent Hashtagging**: Generates relevant hashtags based on current weather conditions (e.g., `#<City>Rains`, `#Heatwave`, `#Cloudy`).
-   **Tweet Content Optimization**: Automatically adjusts tweet text and hashtags to fit within Twitter's character limit.
-   **Accessible Alt Text**: Generates a detailed alt-text description for the weather image, making tweets accessible.
-   **Container-Ready**: Includes a `Dockerfile` and `Procfile` for easy deployment on platforms like Docker, Heroku, or Google Cloud Run.
-   **Test Mode**: Includes a safe "Test Mode" to run the application without posting live tweets, allowing for easy testing and debugging.

## ⚙️ How It Works

The bot is a lightweight Flask application designed to be triggered by an external scheduler (like a cron job or a cloud scheduler).

1.  **Trigger**: An external scheduler makes a `POST` or `GET` request to the `/run-tweet-task` endpoint.
2.  **Weather Data Fetching**: The application calls the OpenWeatherMap API to get the latest forecast for the configured city.
3.  **Content Generation**: It uses the retrieved weather data to:
    -   Create the text content for the tweet.
    -   Generate a dynamic set of hashtags.
    -   Produce a detailed alt-text for accessibility.
    -   Create an image and a chart summarizing the weather using `Pillow` and `Matplotlib`.
4.  **Twitter API Interaction**: The bot uses `tweepy` to interact with both Twitter API v1.1 (for media uploads) and v2 (for posting tweets).
5.  **Post**: The tweet, complete with the generated images and alt text, is posted to the configured Twitter account.

## 🚀 Getting Started

### Prerequisites

-   A Twitter Developer account with a Project/App that has **Elevated Access** or higher.
-   An OpenWeatherMap API key.
-   Python 3.8+ installed.
-   Git installed.

### Step 1: Clone the Repository

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
```

### Step 2: Install Dependencies

It's recommended to use a virtual environment.

```bash
# Create and activate a virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install the required libraries
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables

The application uses environment variables for configuration. You can set these in your deployment environment or create a `.env` file for local development (note: `.env` files are not committed to git).

-   **Twitter API Keys**:
    -   `TWITTER_API_KEY`
    -   `TWITTER_API_SECRET`
    -   `TWITTER_ACCESS_TOKEN`
    -   `TWITTER_ACCESS_TOKEN_SECRET`
-   **Weather API Key**:
    -   `WEATHER_API_KEY`
-   **Configuration**:
    -   `CITY_TO_MONITOR`: The city for which to fetch weather data (e.g., "London"). Defaults to "Hyderabad".
    -   `POST_TO_TWITTER_ENABLED`: Set to `true` to enable live tweeting. Set to `false` for test mode (default).
    -   `PORT`: The port for the Flask application (e.g., `8080`).

**Example `.env` file for local testing:**

```
TWITTER_API_KEY="your_consumer_key_here"
TWITTER_API_SECRET="your_consumer_secret_here"
TWITTER_ACCESS_TOKEN="your_access_token_here"
TWITTER_ACCESS_TOKEN_SECRET="your_access_token_secret_here"

WEATHER_API_KEY="your_openweathermap_api_key_here"

CITY_TO_MONITOR="Hyderabad"
POST_TO_TWITTER_ENABLED="false"
```

### Step 4: Run the Application Locally

```bash
python app.py
```

The server will start. You can trigger the tweet task by visiting `http://localhost:8080/run-tweet-task` in your browser or sending a request with `curl`.

### Step 5: Deployment and Scheduling

For the bot to run automatically, you need to deploy it and set up a scheduler.

-   **Deployment**:
    -   **Heroku**: The `Procfile` allows for easy deployment to Heroku.
    -   **Docker**: You can build and run a Docker container using the provided `Dockerfile`.
      ```bash
      docker build -t weather-bot .
      docker run -p 8080:8080 --env-file .env weather-bot
      ```
    -   **Google Cloud Run**: The `Dockerfile` can be used to deploy the application as a serverless container on Google Cloud Run.

-   **Scheduling**:
    -   **Cron Job**: Use a cron job to call the endpoint periodically.
      ```crontab
      # Every 3 hours
      0 */3 * * * curl http://your-deployed-url/run-tweet-task
      ```
    -   **Cloud Scheduler**: If deployed on a cloud platform, use their native scheduler (e.g., Google Cloud Scheduler, Heroku Scheduler) to trigger the `/run-tweet-task` endpoint.

## 🤝 Contributing

Contributions are welcome! If you find a bug or have a suggestion, please open an issue or submit a pull request.