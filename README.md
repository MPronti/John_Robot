# John_Robot
A Discord bot, using the discord.py and Google Gemini APIs, with Google Gemini 2.5 Pro integration!

Programmed in conjunction with Gemini

## Dependencies
```
discord.py
python-dotenv
google-generativeai
```

## Setup
Create a `.env` file in your directory with:
```
DISCORD_TOKEN=your_discord_bot_token
GOOGLE_API_KEY=your_google_api_key
```

### API Usage Tracking
John Robot automatically creates and updates a local `api_usage.json` file to track daily Gemini API usage

## Commands
| Command        | Description                                |
|----------------|--------------------------------------------|
| `/ask_gemini`  | Ask Gemini a question and receive a reply  |
| `/usage`       | Show today’s Gemini API usage count        |

## Notes
- Gemini 2.5 Pro is limited to **25 requests/day** and **5 requests/minute**, as of today, to free users
- Long answers are split into multiple Discord messages automatically
- John Robot now supports context for each prompt wherein you can specify contextual text (i.e. from a different message)
- John Robot now supports replies to its original response, allowing for longer conversations
