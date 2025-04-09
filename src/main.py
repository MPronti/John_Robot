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

# --- System Prompt Definition ---
SYSTEM_PROMPT = """You are a creative, knowledgeable AI agent. Your job is to provide pertinent and informative 
                information in a concise manner. Try to limit your responses to <4000 characters and to avoid 
                using superfluous formatting (i.e. with objective answers to questions). Don't be afraid to be 
                a bit more boisterous and experimental when the situation calls for it (i.e. when writing creative pieces)."""

# Validate essential configuration
if not DISCORD_TOKEN:
    print("Error: DISCORD_TOKEN environment variable not set.")
    exit()
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY environment variable not set.")
    exit()

# --- API Usage Tracking ---
USAGE_FILE = "api_usage.json" # File to store daily API call count
api_calls_today = 0 # Counter for API calls made today
last_reset_date = datetime.date.today() # Tracks the date the counter was last reset

# load_usage_data, save_usage_data, check_and_reset_counter functions remain the same...
def load_usage_data():
    """Loads API usage count and last reset date from USAGE_FILE."""
    global api_calls_today, last_reset_date
    try:
        with open(USAGE_FILE, 'r') as f:
            data = json.load(f)
            saved_date_str = data.get("date", "1970-01-01")
            saved_date = datetime.date.fromisoformat(saved_date_str)
            count = data.get("count", 0)
            today = datetime.date.today()
            if saved_date == today:
                api_calls_today = count
                last_reset_date = today
                print(f"Loaded usage data: {api_calls_today} calls made today ({today.isoformat()}).")
            else:
                api_calls_today = 0
                last_reset_date = today
                save_usage_data()
                print(f"Detected new day ({today.isoformat()}). API usage counter reset.")
    except FileNotFoundError:
        print(f"Usage file '{USAGE_FILE}' not found. Initializing count to 0 for today.")
        api_calls_today = 0
        last_reset_date = datetime.date.today()
        save_usage_data()
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        print(f"Error reading usage file '{USAGE_FILE}': {e}. Initializing count to 0.")
        api_calls_today = 0
        last_reset_date = datetime.date.today()
        save_usage_data()

def save_usage_data():
    """Saves the current API usage count and date to USAGE_FILE."""
    global api_calls_today, last_reset_date
    try:
        data = {"date": last_reset_date.isoformat(), "count": api_calls_today}
        with open(USAGE_FILE, 'w') as f:
            json.dump(data, f)
    except IOError as e:
        print(f"Error saving usage data to '{USAGE_FILE}': {e}")

def check_and_reset_counter():
    """Checks if the current date is past the last reset date; resets counter if so."""
    global api_calls_today, last_reset_date
    today = datetime.date.today()
    if last_reset_date < today:
        print(f"Date changed to {today.isoformat()}. Resetting API usage counter.")
        api_calls_today = 0
        last_reset_date = today
        save_usage_data()

# --- Google Gemini API Initialization ---
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-pro-latest')
    print(f"Successfully configured Google Gemini with model: {model.model_name}")
except Exception as e:
    print(f"Error configuring Google Gemini: {e}")
    exit()

# --- Discord Bot Initialization ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# --- Utility Function for Creating Error Embeds ---
def create_error_embed(message: str) -> discord.Embed:
    """Creates a standardized red embed for error messages."""
    embed = discord.Embed(description=message, color=discord.Color.red())
    return embed

# --- Slash Command Definitions ---
@bot.tree.command(name='usage', description='Display daily API call count')
async def usage_command(interaction: discord.Interaction):
    """Slash command to display the daily Gemini API call count"""
    check_and_reset_counter() # Ensure counter is up-to-date
    response_text = (
        f"*This bot has made {api_calls_today}/25 calls to the Gemini API today ({last_reset_date.isoformat()})*\n"
        f"*Keep in mind, there is a 5 call per minute limit as well*\n"
    )
    # Send the usage info privately to the invoking user
    await interaction.response.send_message(response_text)

@bot.tree.command(name="ask_gemini", description="Ask Gemini 2.5 Pro a question!")
@app_commands.describe(prompt="The question you want to ask")
async def ask_gemini(interaction: discord.Interaction, prompt: str):
    """Slash command to ask Gemini a question and receive a response as an embed."""
    global api_calls_today

    await interaction.response.defer(thinking=True, ephemeral=False)
    check_and_reset_counter()

    if not prompt:
        await interaction.followup.send(embed=create_error_embed("It seems you didn't provide a question."), ephemeral=True)
        return

    print(f"\nReceived slash command prompt from {interaction.user.name}: '{prompt}'")
    full_prompt = f"{SYSTEM_PROMPT}\n\nUser Question: {prompt}"

    try:
        response = model.generate_content(full_prompt)

        block_reason = None
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
            block_reason = f" Reason: {response.prompt_feedback.block_reason.name}"
            print(f"Gemini response blocked. Reason: {block_reason}")
            error_msg = f"Sorry, I couldn't generate a response due to safety settings.{block_reason}"
            await interaction.followup.send(embed=create_error_embed(error_msg), ephemeral=True)
            return

        if response.text:
            api_calls_today += 1
            save_usage_data()
            print(f"Gemini call successful. Count for today: {api_calls_today}")
            answer = response.text
            print(f"Gemini Response (length {len(answer)}): '{answer[:100]}...'")

            # --- Embed Creation and Sending ---
            EMBED_DESC_LIMIT = 4000 # Safer limit for embed description (max is 4096)
            embed_color = discord.Color.blue() # Or discord.Color.random(), etc.

            # Handle responses potentially exceeding the embed description limit
            if len(answer) > EMBED_DESC_LIMIT:
                print(f"Response is long ({len(answer)} chars). Splitting into multiple embeds.")
                # Create the first embed with title and footer
                first_chunk = answer[:EMBED_DESC_LIMIT]
                remaining_text = answer[EMBED_DESC_LIMIT:]

                embed = discord.Embed(
                    title="Gemini Response (Part 1)",
                    description=first_chunk,
                    color=embed_color
                )
                embed.set_footer(text=f"API Call #{api_calls_today}")
                # Send the first embed
                await interaction.followup.send(embed=embed)
                print(f"Sent embed part 1 (approx {len(first_chunk)} chars)")

                # Loop to send remaining chunks in separate embeds
                part_num = 2
                while remaining_text:
                    await asyncio.sleep(0.5) # Short delay
                    chunk = remaining_text[:EMBED_DESC_LIMIT]
                    remaining_text = remaining_text[EMBED_DESC_LIMIT:]

                    # Create subsequent embeds (simpler, only description)
                    followup_embed = discord.Embed(
                        description=chunk,
                        color=embed_color
                    )
                    # Optionally add part number to subsequent embeds if desired
                    # followup_embed.title = f"Gemini Response (Part {part_num})"
                    await interaction.followup.send(embed=followup_embed)
                    print(f"Sent embed part {part_num} (approx {len(chunk)} chars)")
                    part_num += 1

                print(f"Completed sending {part_num - 1} embed parts.")

            else:
                # Send the complete response in a single embed
                embed = discord.Embed(
                    title="Gemini Response",
                    description=answer,
                    color=embed_color
                )
                embed.set_footer(text=f"API Call #{api_calls_today}")
                await interaction.followup.send(embed=embed)
                print("Sent response in single embed.")
        else:
            await interaction.followup.send(embed=create_error_embed("Sorry, I received an empty response from Gemini."), ephemeral=True)
            print("Gemini returned no text content, but wasn't explicitly blocked.")

    except Exception as e:
        error_message = "Sorry, I encountered an unexpected error trying to get an answer from Gemini."
        if "API key" in str(e) or "permission" in str(e).lower():
             print(f"An error occurred likely related to the Gemini API key or permissions: {e}")
             error_message = "Sorry, there seems to be an issue with the connection to the AI service (API Key or Permissions). Please check the bot console."
        elif "quota" in str(e).lower():
             print(f"An error occurred related to Gemini API quota: {e}")
             error_message = "Sorry, the AI service quota may have been reached for today."
        else:
            print(f"An unexpected error occurred during Gemini processing: {e}")

        try:
            await interaction.followup.send(embed=create_error_embed(error_message), ephemeral=True)
        except discord.errors.NotFound:
             print("Interaction expired before error could be sent.")
             # Send error embed to channel as fallback
             await interaction.channel.send(f"{interaction.user.mention}", embed=create_error_embed(error_message))


# --- Bot Event Handlers ---
@bot.event
async def on_ready():
    """Event handler triggered when the bot logs in and is ready."""
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    load_usage_data()

    try:
        if TESTING_GUILD_ID and TESTING_GUILD_ID != 0:
            guild = discord.Object(id=TESTING_GUILD_ID)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash command(s) to Guild ID: {TESTING_GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} slash command(s) globally.")
            print("Note: Global sync can take up to an hour to update.")
    except Exception as e:
        print(f"Error syncing slash commands: {e}")

    print(f'Bot is ready. Current API calls recorded for today: {api_calls_today}')
    print('------')

@bot.event
async def on_message(message: discord.Message):
    """Event handler triggered for every message the bot can see."""
    if message.author == bot.user or message.author.bot:
        return
    await bot.process_commands(message)


# --- Run the Bot ---
try:
    print("Starting bot...")
    bot.run(DISCORD_TOKEN)
except discord.LoginFailure:
    print("Error: Invalid Discord Token. Please check your .env file or environment variables.")
except Exception as e:
    print(f"An unexpected error occurred while running the bot: {e}")
