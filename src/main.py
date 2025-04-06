import discord
from discord.ext import commands
from discord import app_commands
import os
import google.generativeai as genai
from dotenv import load_dotenv
import datetime
import json
import asyncio

# --- Configuration & Environment Variables ---
load_dotenv() # Load environment variables from .env file

# Retrieve Discord Bot Token and Google API Key
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
# Set to your Discord Server ID (integer) for instant slash command updates during testing
# Leave as 0 or None to sync globally (takes up to an hour)
TESTING_GUILD_ID = 0

# Validate essential configuration
if not DISCORD_TOKEN:
    print("Error: DISCORD_TOKEN environment variable not set")
    exit()
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY environment variable not set")
    exit()

# --- API Usage Tracking ---
USAGE_FILE = "api_usage.json" # File to store daily API call count
api_calls_today = 0 # Counter for API calls made today
last_reset_date = datetime.date.today() # Tracks the date the counter was last reset

def load_usage_data():
    """Loads API usage count and last reset date from USAGE_FILE"""
    global api_calls_today, last_reset_date
    try:
        with open(USAGE_FILE, 'r') as f:
            data = json.load(f)
            # Load saved date, defaulting to epoch if missing
            saved_date_str = data.get("date", "1970-01-01")
            saved_date = datetime.date.fromisoformat(saved_date_str)
            count = data.get("count", 0)

            today = datetime.date.today()
            # If saved data is for today, load the count; otherwise, reset for the new day
            if saved_date == today:
                api_calls_today = count
                last_reset_date = today
                print(f"Loaded usage data: {api_calls_today} calls made today ({today.isoformat()})")
            else:
                api_calls_today = 0
                last_reset_date = today
                save_usage_data() # Persist the reset counter
                print(f"Detected new day ({today.isoformat()}). API usage counter reset")

    except FileNotFoundError:
        # Initialize usage file if it doesn't exist
        print(f"Usage file '{USAGE_FILE}' not found. Initializing count to 0 for today")
        api_calls_today = 0
        last_reset_date = datetime.date.today()
        save_usage_data()
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        # Handle corrupted or invalid file data by resetting
        print(f"Error reading usage file '{USAGE_FILE}': {e}. Initializing count to 0")
        api_calls_today = 0
        last_reset_date = datetime.date.today()
        save_usage_data()

def save_usage_data():
    """Saves the current API usage count and date to USAGE_FILE"""
    global api_calls_today, last_reset_date
    try:
        data = {"date": last_reset_date.isoformat(), "count": api_calls_today}
        with open(USAGE_FILE, 'w') as f:
            json.dump(data, f) # Overwrite file with current data
    except IOError as e:
        print(f"Error saving usage data to '{USAGE_FILE}': {e}")

def check_and_reset_counter():
    """Checks if the current date is past the last reset date; resets counter if so"""
    global api_calls_today, last_reset_date
    today = datetime.date.today()
    if last_reset_date < today:
        print(f"Date changed to {today.isoformat()}. Resetting API usage counter")
        api_calls_today = 0
        last_reset_date = today
        save_usage_data() # Persist the reset counter immediately

# --- Google Gemini API Initialization ---
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-pro-exp-03-25")
    print(f"Successfully configured Google Gemini with model: {model.model_name}")
except Exception as e:
    print(f"Error configuring Google Gemini: {e}")
    exit() # Exit if Gemini configuration fails

# --- Discord Bot Initialization ---
intents = discord.Intents.default()
intents.message_content = True # Required to read message content for process_commands

# Initialize bot with '!' prefix (though commands are slash-based) and defined intents
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Slash Command Definitions ---
@bot.tree.command(name='usage', description='Display daily API call count')
async def usage_command(interaction: discord.Interaction):
    """Slash command to display the daily Gemini API call count"""
    check_and_reset_counter() # Ensure counter is up-to-date
    response_text = (
        f"*This bot instance has made {api_calls_today}/50 calls to the Gemini API today ({last_reset_date.isoformat()})*\n"
        f"*Keep in mind, there is a 2 call per minute limit as well*\n"
    )
    # Send the usage info privately to the invoking user
    await interaction.response.send_message(response_text)

@bot.tree.command(name="ask_gemini", description="Ask Gemini 2.5 Pro a question!")
@app_commands.describe(prompt="The question you want to ask")
async def ask_gemini(interaction: discord.Interaction, prompt: str):
    """Slash command to ask Gemini a question and receive a response"""
    global api_calls_today # Access the global counter

    # Acknowledge the command immediately to prevent timeout; response will follow
    await interaction.response.defer(thinking=True, ephemeral=False)

    check_and_reset_counter() # Ensure counter is up-to-date

    # Redundant check for empty prompt since 'prompt' is a required argument, but harmless
    if not prompt:
        await interaction.followup.send("It seems you didn't provide a question", ephemeral=True)
        return

    print(f"\nReceived slash command prompt from {interaction.user.name}: '{prompt}'")
    try:
        # Call the Gemini API to generate content based on the prompt
        response = model.generate_content(prompt)

        # Check if the response was blocked by safety filters
        block_reason = None # Initialize block_reason
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
            block_reason = f" Reason: {response.prompt_feedback.block_reason.name}"
            print(f"Gemini response blocked. Reason: {block_reason}")
            # Notify user privately if response is blocked
            await interaction.followup.send(f"Sorry, I couldn't generate a response due to safety settings.{block_reason}", ephemeral=True)
            return

        # Process the successful response from Gemini
        if response.text:
            api_calls_today += 1 # Increment counter on successful call
            save_usage_data()    # Save updated count
            print(f"Gemini call successful. Count for today: {api_calls_today}")
            answer = response.text
            print(f"Gemini Response (length {len(answer)}): '{answer[:100]}...'") # Log snippet

            MAX_MSG_LENGTH = 1990 # Discord message character limit safety margin
            # Handle responses potentially exceeding Discord's limit
            if len(answer) > MAX_MSG_LENGTH:
                print(f"Response is long ({len(answer)} chars). Splitting into chunks")
                # Define prefixes for split messages
                reply_intro = f"The answer is long, sending in parts:"
                part_indicator_1 = f"\n**(Part 1/?)**\n" # '?' as total parts unknown initially
                full_prefix_1 = reply_intro + part_indicator_1

                # Calculate max content length for the first chunk considering prefix length
                max_content_len_1 = max(0, MAX_MSG_LENGTH - len(full_prefix_1))
                first_chunk_content = answer[:max_content_len_1]
                remaining_text = answer[max_content_len_1:]

                # Send the first chunk via followup message
                await interaction.followup.send(f"{full_prefix_1}{first_chunk_content}")
                print(f"Sent part 1 (approx {len(first_chunk_content)} chars)")

                # Loop to send remaining chunks
                part_num = 2
                while remaining_text:
                    await asyncio.sleep(0.5) # Short delay to avoid potential rate limits
                    part_indicator_n = f"**(Part {part_num}/?)**\n"
                    # Calculate max content length for this chunk
                    max_content_len_n = max(0, MAX_MSG_LENGTH - len(part_indicator_n))
                    chunk_content = remaining_text[:max_content_len_n]
                    remaining_text = remaining_text[max_content_len_n:]

                    # Send subsequent chunk via followup message
                    await interaction.followup.send(f"{part_indicator_n}{chunk_content}")
                    print(f"Sent part {part_num} (approx {len(chunk_content)} chars)")
                    part_num += 1

                # Part count update logic is omitted for simplicity as editing followups is complex
                final_num_parts = part_num - 1
                if final_num_parts > 1:
                    print(f"Completed sending {final_num_parts} parts. Edit skipped for simplicity")

            else:
                # Send the complete response if it's within the length limit
                await interaction.followup.send(f"{answer}")
                print("Sent short response in one message")
        else:
            # Handle cases where Gemini returns a non-blocked, empty response
            await interaction.followup.send("Sorry, I received an empty response from Gemini", ephemeral=True)
            print("Gemini returned no text content, but wasn't explicitly blocked")

    except Exception as e:
        # General error handling for Gemini API call or processing
        error_message = "Sorry, I encountered an unexpected error trying to get an answer from Gemini"
        # Provide more specific feedback for common API issues if possible
        # Note: Checking exception types would be more robust if available from the library
        if "API key" in str(e) or "permission" in str(e).lower():
             print(f"An error occurred likely related to the Gemini API key or permissions: {e}")
             error_message = "Sorry, there seems to be an issue with the connection to the AI service (API Key or Permissions). Please check the bot console"
        elif "quota" in str(e).lower():
             print(f"An error occurred related to Gemini API quota: {e}")
             error_message = "Sorry, the AI service quota may have been reached for today"
        else:
            # Log the unexpected error
            print(f"An unexpected error occurred during Gemini processing: {e}")

        # Send error message privately; use fallback if interaction expires
        try:
            await interaction.followup.send(error_message, ephemeral=True)
        except discord.errors.NotFound:
             print("Interaction expired before error could be sent")
             # Ping user in channel as a fallback notification
             await interaction.channel.send(f"{interaction.user.mention} {error_message}")

# --- Bot Event Handlers ---
@bot.event
async def on_ready():
    """Event handler triggered when the bot logs in and is ready"""
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    load_usage_data() # Load persisted usage data on startup

    # Sync slash commands with Discord
    try:
        if TESTING_GUILD_ID and TESTING_GUILD_ID != 0:
            # Sync to a specific guild for faster updates
            guild = discord.Object(id=TESTING_GUILD_ID)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash command(s) to Guild ID: {TESTING_GUILD_ID}")
        else:
            # Sync globally (can take up to an hour)
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} slash command(s) globally")
            print("Note: Global sync can take up to an hour to update")
    except Exception as e:
        print(f"Error syncing slash commands: {e}")

    print(f'Bot is ready. Current API calls recorded for today: {api_calls_today}')
    print('------')

@bot.event
async def on_message(message: discord.Message):
    """Event handler triggered for every message the bot can see"""
    # Ignore messages sent by the bot itself or other bots
    if message.author == bot.user or message.author.bot:
        return

    # Allow the commands extension to process any potential prefix commands
    # Necessary if prefix commands (e.g., "!help") are ever added
    await bot.process_commands(message)

# --- Run the Bot ---
try:
    print("Starting bot...")
    bot.run(DISCORD_TOKEN) # Start the bot using the token
except discord.LoginFailure:
    print("Error: Invalid Discord Token. Please check your .env file or environment variables")
except Exception as e:
    # Catch any other unexpected errors during bot startup or runtime
    print(f"An unexpected error occurred while running the bot: {e}")
