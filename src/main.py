import discord
from discord.ext import commands
from discord import app_commands
import os
import google.generativeai as genai
from dotenv import load_dotenv
import datetime
import json
import asyncio
from typing import Optional, Tuple
import traceback
from google.api_core import exceptions as google_exceptions

# --- Configuration & Environment Variables ---
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
TESTING_GUILD_ID = int(os.getenv('TESTING_GUILD_ID', 0))

# --- Constants ---
SYSTEM_PROMPT = """You are a stoic, knowledgeable AI agent. Your job is to provide pertinent and informative
                 information in a concise manner. Try to limit your responses to under 4096 characters and to avoid
                 using superfluous formatting (i.e. with objective answers to questions). Also, use unicode in place of latex formatting."""

GEMINI_MODEL_NAME = "gemini-2.5-flash" # As of now, 2.5 Pro is not working properly for me, so flash is being substituted

# Get the absolute path of the directory where the script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Join that directory path with the desired filename
USAGE_FILE = os.path.join(SCRIPT_DIR, "api_usage.json")

EMBED_DESC_LIMIT = 4096           # Discord's actual limit for embed descriptions
MAX_PROMPT_LENGTH_IN_TITLE = 250  # Discord's limit for embed titles is 256

# --- Environment Variable Checks ---
if not DISCORD_TOKEN:
    print("Error: DISCORD_TOKEN environment variable not set.")
    exit()
if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY environment variable not set.")
    exit()

# --- API Usage Tracking (Refactored into a Class) ---
class APITracker:
    """A class to encapsulate API usage tracking logic, now with async safety."""
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._count = 0
        self._date = datetime.date.today()
        self.lock = asyncio.Lock()
        self.load()

    def load(self):
        """Loads usage data, resetting if the date has changed. This is synchronous and fine at startup."""
        try:
            with open(self.file_path, 'r') as f:
                data = json.load(f)
            saved_date = datetime.date.fromisoformat(data.get("date", "1970-01-01"))
            today = datetime.date.today()

            if saved_date == today:
                self._count = data.get("count", 0)
                self._date = today
                print(f"Loaded usage data: {self._count} calls made today ({today.isoformat()}).")
            else:
                print(f"Detected new day ({today.isoformat()}). API usage counter reset.")
                self._count = 0
                self._date = today
                self.save() # Save the reset state
        except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError) as e:
            print(f"Warning/Error with usage file '{self.file_path}': {e}. Initializing count to 0.")
            self._count = 0
            self._date = datetime.date.today()
            self.save()

    def save(self):
        """Saves the current usage count and date to the file. This is synchronous."""
        try:
            data = {"date": self._date.isoformat(), "count": self._count}
            with open(self.file_path, 'w') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"Error saving usage data to '{self.file_path}': {e}")

    def get_count(self) -> int:
        """Returns the current count. Note: For display only, not for increment logic."""
        # This simple check is fine for display purposes.
        if self._date < datetime.date.today():
            return 0
        return self._count

    async def increment(self):
        """Atomically increments the counter and saves the new state."""
        async with self.lock:
            # Now, the code inside this block is protected from race conditions.
            # Check if the date has changed since the last operation.
            today = datetime.date.today()
            if self._date < today:
                print(f"Detected new day ({today.isoformat()}) during increment. API usage counter reset.")
                self._count = 0
                self._date = today

            self._count += 1
            self.save() # save() is synchronous, which is fine inside the locked block.
            print(f"Gemini call successful. Count for today: {self._count}")

api_tracker = APITracker(USAGE_FILE)

# --- Google Gemini API Initialization ---
try:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel(
        GEMINI_MODEL_NAME,
        system_instruction=SYSTEM_PROMPT
    )
    print(f"Successfully configured Google Gemini with model: {model.model_name}")
except Exception as e:
    print(f"Error configuring Google Gemini: {e}")
    exit()

# --- Discord Bot Initialization ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Utility Function for Creating Embeds ---
def create_error_embed(message: str) -> discord.Embed:
    """Creates a standardized red embed for error messages."""
    return discord.Embed(description=message, color=discord.Color.red())

async def process_gemini_request(interaction: discord.Interaction, prompt: str, context: Optional[str] = None) -> Optional[Tuple[str, discord.Message]]:
    """
    Handles calling Gemini, processing the response, sending embeds, and updating the counter.
    Returns the full answer and the last message sent on success, otherwise None.
    """
    if context:
        final_prompt = f"Previous Context Provided by User:\n{context}\n\nUser Question: {prompt}"
    else:
        final_prompt = prompt

    print(f"\nProcessing prompt for {interaction.user.name}: '{final_prompt[:100]}...'")

    safety_settings = {
        'HARM_CATEGORY_HARASSMENT': 'BLOCK_ONLY_HIGH',
        'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_ONLY_HIGH',
        'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_ONLY_HIGH',
        'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_ONLY_HIGH',
    }

    try:
        response = await model.generate_content_async(
            final_prompt,
            safety_settings=safety_settings
        )

        if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
            block_reason = f" Reason: {response.prompt_feedback.block_reason.name}"
            await interaction.followup.send(embed=create_error_embed(f"Your prompt was blocked by safety filters.{block_reason}"), ephemeral=True)
            return None

        if not response.candidates:
            await interaction.followup.send(embed=create_error_embed("The model generated a response, but it was blocked by safety filters. Please try rephrasing."), ephemeral=True)
            return None

        answer = response.text
        print(f"Gemini Response (length {len(answer)}): '{answer[:100]}...'")

        if len(prompt) > MAX_PROMPT_LENGTH_IN_TITLE:
            truncated_prompt = f"Prompt: {prompt[:MAX_PROMPT_LENGTH_IN_TITLE-10]}..."
        else:
            truncated_prompt = f"Prompt: {prompt}"

        await api_tracker.increment()
        current_api_count = api_tracker.get_count()
        embed_color = discord.Color.blue()
        sent_messages = []

        if len(answer) <= EMBED_DESC_LIMIT:
            embed = discord.Embed(title=truncated_prompt, description=answer, color=embed_color)
            embed.set_footer(text=f"API Call #{current_api_count}")
            msg = await interaction.followup.send(embed=embed, wait=True)
            sent_messages.append(msg)
        else:
            chunks = [answer[i:i + EMBED_DESC_LIMIT] for i in range(0, len(answer), EMBED_DESC_LIMIT)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    embed = discord.Embed(title=truncated_prompt, description=chunk, color=embed_color)
                    embed.set_footer(text=f"API Call #{current_api_count} | Part {i+1}/{len(chunks)}")
                else:
                    embed = discord.Embed(title=f"Part {i+1}/{len(chunks)}", description=chunk, color=embed_color)
                msg = await interaction.followup.send(embed=embed, wait=True)
                sent_messages.append(msg)
                await asyncio.sleep(0.2)
        
        return answer, sent_messages[-1] if sent_messages else None

    except google_exceptions.InternalServerError as e:
        print(f"Google API Internal Server Error: {e}")
        traceback.print_exc()
        error_message = "The AI service encountered an internal error. Please try again."
        await interaction.followup.send(embed=create_error_embed(error_message), ephemeral=True)
        return None
        
    except Exception as e:
        print(f"An unexpected error occurred: {type(e).__name__} - {e}")
        traceback.print_exc()
        error_message = "Sorry, I encountered a critical unexpected error. Please check the bot console for details."
        try:
            await interaction.followup.send(embed=create_error_embed(error_message), ephemeral=True)
        except discord.errors.NotFound:
             await interaction.channel.send(f"{interaction.user.mention}", embed=create_error_embed(error_message))
        return None
    
# --- UI Components: Modal and View ---
class FollowupModal(discord.ui.Modal, title='Ask a Follow-up Question'):
    def __init__(self, original_answer: str):
        super().__init__(timeout=300)
        self.original_answer = original_answer
        self.followup_prompt = discord.ui.TextInput(
            label='Your Follow-up Question',
            placeholder='Enter your next question here...',
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1500
        )
        self.add_item(self.followup_prompt)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        new_prompt = self.followup_prompt.value
        await process_gemini_request(interaction, new_prompt, context=self.original_answer)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"Error in FollowupModal: {error}")
        await interaction.followup.send(embed=create_error_embed("Oops! Something went wrong processing your follow-up."), ephemeral=True)

class ReplyView(discord.ui.View):
    def __init__(self, original_answer: str, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.original_answer = original_answer

    @discord.ui.button(label='Reply', style=discord.ButtonStyle.primary, emoji='↪️')
    async def reply_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = FollowupModal(self.original_answer)
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        print("ReplyView timed out, button disabled implicitly on client-side.")


# --- Slash Command Definitions ---
@bot.tree.command(name="ask_gemini", description="Ask Gemini a question!")
@app_commands.describe(
    prompt="The question you want to ask",
    context="Optional: Relevant text/context from a previous message to include"
)
async def ask_gemini(interaction: discord.Interaction, prompt: str, context: Optional[str] = None):
    await interaction.response.defer(thinking=True, ephemeral=False)
    result = await process_gemini_request(interaction, prompt, context=context)
    if result:
        answer_text, last_message = result
        if last_message:
            reply_view = ReplyView(original_answer=answer_text)
            try:
                await last_message.edit(view=reply_view)
                print("Added Reply button to the response.")
            except discord.HTTPException as e:
                print(f"Error editing message to add view: {e}")

# --- Bot Event Handlers ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    try:
        if TESTING_GUILD_ID:
            guild = discord.Object(id=TESTING_GUILD_ID)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} slash command(s) to Guild ID: {TESTING_GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} slash command(s) globally.")
    except Exception as e:
        print(f"Error syncing slash commands: {e}")
    print(f'Bot is ready. API calls today: {api_tracker.get_count()}')
    print('------')

# --- Run the Bot ---
if __name__ == "__main__":
    try:
        print("Starting bot...")
        bot.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        print("Error: Invalid Discord Token. Please check your .env file/environment variables.")
    except Exception as e:
        print(f"An unexpected error occurred while running the bot: {e}")
