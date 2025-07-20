# WeatherAppBot

A Python bot for generating real-time weather reports for the Gachibowli area using the **OpenWeatherMap API**. Created to deliver concise and reliable weather updates, this repository helps developers, students, and tech enthusiasts get started with weather data automation for a specific location.

## Features

- **Automated Weather Reports:** Fetches current weather details for Gachibowli.
- **OpenWeatherMap Integration:** Utilizes the OpenWeatherMap API for accurate and up-to-date data.
- **Customizable:** Easily adaptable to other locations or custom features.
- **Lightweight & Modular:** Written in Python for quick setup and extendibility.

## Getting Started

### Prerequisites

- Python 3.7 or newer
- An OpenWeatherMap API key (sign up at openweathermap.org)
- Required Python packages (see below)

### Installation

1. **Clone the Repository**
   ```bash
   git clone https://github.com/roddavinod99/weatherappbot.git
   cd weatherappbot
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   *(Alternatively, manually install: `requests`, `python-dotenv`, etc.)*

3. **Set Up API Key**

   Create a `.env` file in the project root with your OpenWeatherMap API key:
   ```
   OPENWEATHER_API_KEY=your_api_key_here
   ```

## Usage

Run the bot script:

```bash
python weatherbot.py
```

The bot fetches and displays the current weather for Gachibowli, including temperature, humidity, wind speed, and a basic summary.

## Project Structure

| File/Folder       | Description                                      |
|-------------------|--------------------------------------------------|
| weatherbot.py     | Main logic for fetching and displaying weather   |
| requirements.txt  | List of required Python dependencies             |
| .env              | Stores environment variables (API key)           |
| README.md         | Project documentation                            |

## Configuration

- **Changing Location:**  
  The location is set to Gachibowli by default. To change it, modify the location parameters (latitude, longitude, or city name) in `weatherbot.py`.

## Example Output

```
Weather update for Gachibowli:
Temperature: 28°C
Humidity: 65%
Condition: Partly cloudy
Wind: 12 km/h
```

## Contributing

Contributions are welcome! Please open issues or submit pull requests for improvements, bug fixes, or new features.

## License

This project is licensed under the MIT License.

## Credits

Developed by [@roddavinod99][1]

API provided by [OpenWeatherMap][1].

[1]: https://github.com/roddavinod99/weatherappbot

[1] https://github.com/roddavinod99/weatherappbot
[2] https://github.com/daytonaio/sample-nextjs-weather-bot
[3] https://github.com/chris-official/chatbot
[4] https://github.com/topics/weather-app
[5] https://github.com/topics/weatherbot
[6] https://github.com/vardhanam/weatherman_chatbot_qdrant
[7] https://www.youtube.com/watch?v=Res5n81m580
[8] https://github.com/mustafababil/Telegram-Weather-Bot
[9] https://dev.to/renancferro/github-dev-2023-hackathon-weather-app-pil
[10] https://dev.to/buildandcodewithraman/meet-your-interactive-weather-companion-35p0
[11] https://github.com/YuanYuYuan/weather-bot
