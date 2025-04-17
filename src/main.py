import discord
from discord.ext import commands
from discord import app_commands
import os
import google.generativeai as genai
from dotenv import load_dotenv
import datetime
import json
import asyncio
from typing import Optional

# --- Configuration & Environment Variables ---
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
# Keep 0 for global synchronization, or your own server ID
TESTING_GUILD_ID = 0

SYSTEM_PROMPT = """You are a creative, knowledgeable AI agent. Your job is to provide pertinent and informative
                 information in a concise manner. Try to limit your responses to <4000 characters and to avoid
                 using superfluous formatting (i.e. with objective answers to questions). Don't be afraid to be
                 a bit more boisterous and experimental when the situation calls for it (i.e. when writing creative pieces)."""

if not DISCORD_TOKEN:
    print("Error: DISCORD_TOKEN environment variable not set.")
    exit()
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY environment variable not set.")
    exit()

# --- API Usage Tracking ---
USAGE_FILE = "api_usage.json"
api_calls_today = 0
last_reset_date = datetime.date.today()

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
    model = genai.GenerativeModel("gemini-2.5-pro-exp-03-25")
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

# --- Refactored Core Gemini Processing Logic ---
async def process_gemini_request(interaction: discord.Interaction, prompt: str, context: Optional[str] = None, is_followup: bool = False):
    """Handles calling Gemini, processing response, sending embeds, and updating counter."""
    global api_calls_today

    check_and_reset_counter() # Ensure counter is up-to-date before potentially making a call

    # Construct the prompt, including context if provided
    if context:
        full_prompt = f"{SYSTEM_PROMPT}\n\nPrevious Context Provided by User:\n{context}\n\nUser Question: {prompt}"
    else:
        full_prompt = f"{SYSTEM_PROMPT}\n\nUser Question: {prompt}"

    print(f"\nProcessing prompt for {interaction.user.name}: '{prompt[:100]}...' (Context provided: {context is not None})")

    try:
        response = model.generate_content(full_prompt)

        block_reason = None
        if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
            block_reason = f" Reason: {response.prompt_feedback.block_reason.name}"
            print(f"Gemini response blocked. Reason: {block_reason}")
            error_msg = f"Sorry, I couldn't generate a response due to safety settings.{block_reason}"
            await interaction.followup.send(embed=create_error_embed(error_msg), ephemeral=True)
            return None # Indicate failure

        if response.text:
            api_calls_today += 1 # Increment only on successful generation
            save_usage_data()
            print(f"Gemini call successful. Count for today: {api_calls_today}")
            answer = response.text
            print(f"Gemini Response (length {len(answer)}): '{answer[:100]}...'")

            EMBED_DESC_LIMIT = 4000
            embed_color = discord.Color.blue()
            sent_messages = [] # To potentially attach view later

            if len(answer) > EMBED_DESC_LIMIT:
                print(f"Response long ({len(answer)} chars). Splitting embeds.")
                first_chunk = answer[:EMBED_DESC_LIMIT]
                remaining_text = answer[EMBED_DESC_LIMIT:]
                embed = discord.Embed(title="Gemini Response (Part 1)", description=first_chunk, color=embed_color)
                embed.set_footer(text=f"API Call #{api_calls_today}")
                msg = await interaction.followup.send(embed=embed, wait=True) # Use wait=True to get Message object
                sent_messages.append(msg)
                print(f"Sent embed part 1 ({len(first_chunk)} chars)")

                part_num = 2
                while remaining_text:
                    await asyncio.sleep(0.5)
                    chunk = remaining_text[:EMBED_DESC_LIMIT]
                    remaining_text = remaining_text[EMBED_DESC_LIMIT:]
                    followup_embed = discord.Embed(description=chunk, color=embed_color)
                    msg = await interaction.followup.send(embed=followup_embed, wait=True)
                    sent_messages.append(msg)
                    print(f"Sent embed part {part_num} ({len(chunk)} chars)")
                    part_num += 1
                print(f"Completed sending {part_num - 1} embed parts.")
            else:
                embed = discord.Embed(title="Gemini Response", description=answer, color=embed_color)
                embed.set_footer(text=f"API Call #{api_calls_today}")
                msg = await interaction.followup.send(embed=embed, wait=True)
                sent_messages.append(msg)
                print("Sent response in single embed.")

            # Return the full answer text and the last sent message
            return answer, sent_messages[-1] if sent_messages else None

        else:
            await interaction.followup.send(embed=create_error_embed("Sorry, I received an empty response from Gemini."), ephemeral=True)
            print("Gemini returned no text content, but wasn't explicitly blocked.")
            return None # Indicate failure

    except Exception as e:
        # Error handling logic remains similar
        error_message = "Sorry, I encountered an unexpected error trying to get an answer from Gemini."
        if "API key" in str(e) or "permission" in str(e).lower():
             print(f"Error related to Gemini API key/permissions: {e}")
             error_message = "Sorry, there seems to be an issue with the connection to the AI service (API Key or Permissions). Please check the bot console."
        elif "quota" in str(e).lower():
             print(f"Error related to Gemini API quota: {e}")
             error_message = "Sorry, the AI service quota may have been reached for today."
        else:
            print(f"Unexpected error during Gemini processing: {e}")

        try:
            # If it's a followup from a modal/button, respond ephemerally
            # If it's the initial interaction, response might already be deferred non-ephemerally,
            # but sending the error ephemerally is usually better.
            await interaction.followup.send(embed=create_error_embed(error_message), ephemeral=True)
        except discord.errors.NotFound:
             print("Interaction expired before error could be sent.")
             await interaction.channel.send(f"{interaction.user.mention}", embed=create_error_embed(error_message)) # Fallback

        return None # Indicate failure


# --- UI Components: Modal and View ---

# Modal for getting the follow-up prompt
class FollowupModal(discord.ui.Modal, title='Ask a Follow-up Question'):
    def __init__(self, original_answer: str):
        super().__init__(timeout=300) # 5 minute timeout
        self.original_answer = original_answer # Store the context

        # Text input field for the user's follow-up question
        self.followup_prompt = discord.ui.TextInput(
            label='Your Follow-up Question',
            placeholder='Enter your next question here...',
            style=discord.TextStyle.paragraph, # Allow multi-line input
            required=True,
            max_length=1000 # Limit input length reasonably
        )
        self.add_item(self.followup_prompt)

    async def on_submit(self, interaction: discord.Interaction):
        # Acknowledge modal submission
        await interaction.response.defer(thinking=True)

        # Get the new prompt from the modal
        new_prompt = self.followup_prompt.value

        # Call the refactored processing function with the new prompt and original answer as context
        # The interaction object from the modal submission is used for followups
        await process_gemini_request(interaction, new_prompt, context=self.original_answer, is_followup=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        print(f"Error in FollowupModal: {error}")
        await interaction.followup.send(embed=create_error_embed("Oops! Something went wrong processing your follow-up."), ephemeral=True)

    async def on_timeout(self) -> None:
        print("FollowupModal timed out.")


# View containing the "Reply" button
class ReplyView(discord.ui.View):
    def __init__(self, original_answer: str, timeout: float = 180): # Default 3 min timeout
        super().__init__(timeout=timeout)
        self.original_answer = original_answer # Store context for the modal

    # Define the reply button
    @discord.ui.button(label='Reply', style=discord.ButtonStyle.primary, emoji='↪️')
    async def reply_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Create and send the modal when the button is clicked
        modal = FollowupModal(self.original_answer)
        await interaction.response.send_modal(modal)

    # Disable button on timeout
    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        # We need the original message to edit the view, which is tricky here.
        # Best practice is often to just let it timeout visually without editing the message.
        print("ReplyView timed out, button disabled implicitly.")
        # If you really need to edit the message, the original message ID/object needs to be passed to the view.


# --- Slash Command Definitions ---
@bot.tree.command(name='usage', description='Display daily API call count')
async def usage_command(interaction: discord.Interaction):
    """Slash command to display the daily Gemini API call count"""
    check_and_reset_counter()
    response_text = (
        f"*This bot has made {api_calls_today}/25 calls to the Gemini API today ({last_reset_date.isoformat()})*\n"
        f"*Keep in mind, there is a 5 call per minute limit as well*\n"
    )
    await interaction.response.send_message(response_text)

@bot.tree.command(name="ask_gemini", description="Ask Gemini 2.5 Pro a question!")
@app_commands.describe(
    prompt="The question you want to ask",
    context="Optional: Relevant text/context from a previous message to include"
)
async def ask_gemini(interaction: discord.Interaction, prompt: str, context: Optional[str] = None):
    """Initial slash command handler for asking Gemini."""
    # Acknowledge interaction (needed before calling the processing function)
    await interaction.response.defer(thinking=True, ephemeral=False)

    # Call the main processing function
    result = await process_gemini_request(interaction, prompt, context=context)

    # If processing was successful and returned the answer text and last message
    if result:
        answer_text, last_message = result
        if last_message:
            # Create and attach the view with the reply button to the LAST message sent
            reply_view = ReplyView(original_answer=answer_text)
            try:
                # Edit the last message sent by the followup to add the view
                await last_message.edit(view=reply_view)
                print("Added Reply button to the response.")
            except discord.HTTPException as e:
                print(f"Error editing message to add view: {e}")
        else:
            print("No message returned from process_gemini_request to attach view to.")


# --- Bot Event Handlers ---
@bot.event
async def on_ready():
    """Event handler triggered when the bot logs in and is ready."""
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    load_usage_data()
    try:
        guild_id = TESTING_GUILD_ID
        if guild_id and guild_id != 0:
            guild = discord.Object(id=guild_id)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash command(s) to Guild ID: {guild_id}")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} slash command(s) globally.")
            print("Note: Global sync can take up to an hour.")
    except Exception as e:
        print(f"Error syncing slash commands: {e}")
    print(f'Bot is ready. Current API calls recorded for today: {api_calls_today}')
    print('------')

@bot.event
async def on_message(message: discord.Message):
    """Event handler triggered for every message the bot can see."""
    if message.author == bot.user or message.author.bot: return
    await bot.process_commands(message)

# --- Run the Bot ---
try:
    print("Starting bot...")
    bot.run(DISCORD_TOKEN)
except discord.LoginFailure:
    print("Error: Invalid Discord Token. Please check your .env file/env variables.")
except Exception as e:
    print(f"An unexpected error occurred while running the bot: {e}")
