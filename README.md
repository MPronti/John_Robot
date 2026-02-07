# John_Robot
A Discord bot, using the discord.py and Google Gemini APIs, with Google Gemini 3.0 integration!

Programmed in conjunction with Gemini

## Dependencies
```
discord.py
python-dotenv
google-generativeai (soon to be defunct - use google.genai instead)
aiofiles
```

## Setup
Create a `.env` file in your directory with:
```
DISCORD_TOKEN=[your_discord_bot_token]
GOOGLE_API_KEY=[your_google_api_key]
TESTING_GUILD_ID=[your_guild_id]
```

### Commands
| Command | Description |
| :--- | :--- |
| `/ask_gemini` | Ask Gemini a question and receive a reply. Supports optional 'personality,' 'model,' and 'context,' parameters. |

### API Usage Tracking
John Robot automatically tracks daily Gemini API usage with a data.json file

### Notes
- Long answers are split into multiple Discord messages automatically.
- John Robot now supports context for each prompt wherein you can specify contextual text (i.e. from a different message).
- John Robot now supports replies to its original response, allowing for longer conversations.
- John Robot now displays the current daily API usage counter in the footer of its embedded message, and there only. As such, the old '/usage' command has been removed.
- See the current Gemini API usage documentation for current call rates, as they change frequently.
- John Robot now supports multiple models of Gemini (Flash 3.0 Preview being the default).
- John Robot now supports multiple "personalities" (system prompts) - add these in data.json.
