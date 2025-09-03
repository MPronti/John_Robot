# John_Robot
A Discord bot, using the discord.py and Google Gemini APIs, with Google Gemini 2.5 integration!

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
DISCORD_TOKEN=[your_discord_bot_token]
GOOGLE_API_KEY=[your_google_api_key]
```

### API Usage Tracking
John Robot automatically creates and updates a local `api_usage.json` file to track daily Gemini API usage

## Commands
| Command        | Description                                                                           |
|----------------|---------------------------------------------------------------------------------------|
| `/ask_gemini`  | Ask Gemini a question and receive a reply. Supports an optional 'context' parameter.  |

## Notes
- Gemini 2.5 Pro has become problematic with use in this bot, so for now 2.5 Flash is the best option. Future updates will allow for multiple model support.
- Long answers are split into multiple Discord messages automatically.
- John Robot now supports context for each prompt wherein you can specify contextual text (i.e. from a different message).
- John Robot now supports replies to its original response, allowing for longer conversations.
- John Robot now displays the current daily API usage counter in the footer of its embedded message, and there only. As such, the old '/usage' command has been removed.
- See the current Gemini API usage documentation for current call rates, as they change frequently.
