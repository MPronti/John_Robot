import discord
from discord import app_commands
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv
import datetime
import json
import asyncio
from typing import Optional, Tuple
import traceback
import aiofiles

# --- Configuration & Environment Variables ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
TESTING_GUILD_ID = int(os.getenv('TESTING_GUILD_ID', 0))

MODELS = { 
    "2.5 Flash": "gemini-2.5-flash", 
    "2.5 Pro":   "gemini-2.5-pro",
    "3.0 Flash": "gemini-3-flash-preview"
    #"3.0 Pro":   "gemini-3-pro-preview"
}
DEFAULT_MODEL = "3.0 Flash"
DATA_FILE = "data.json"
SYSTEM_PROMPTS = {}
DEFAULT_PERSONALITY = "John Robot"

try:
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
        SYSTEM_PROMPTS = data.get("system_prompts", {})
        print(f"Loaded {len(SYSTEM_PROMPTS)} personalities from '{DATA_FILE}'.")
        if DEFAULT_PERSONALITY not in SYSTEM_PROMPTS and SYSTEM_PROMPTS:
            first_personality = list(SYSTEM_PROMPTS.keys())[0]
            print(f"Warning: Default personality '{DEFAULT_PERSONALITY}' not found. Using '{first_personality}' instead.")
            DEFAULT_PERSONALITY = first_personality
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"Warning: Could not load '{DATA_FILE}': {e}. Personalities will be unavailable.")

EMBED_DESC_LIMIT = 4096
# The limit is 256 for the title. 
# Prompt logic was: "Prompt: " (8 chars) + 250 chars = 258 chars (Bug).
# Adjusted to ensure total title length is safe.
MAX_PROMPT_LENGTH_IN_TITLE = 200 

if not DISCORD_TOKEN: exit("Error: DISCORD_TOKEN environment variable not set.")
if not GOOGLE_API_KEY: exit("Error: GOOGLE_API_KEY environment variable not set.")

# --- Modified APITracker to be more robust ---
class APITracker:
    """A class to encapsulate API usage tracking from a consolidated JSON file."""
    def __init__(self, file_path: str, initial_prompts: dict):
        self.file_path = file_path
        self._count = 0
        self._date = datetime.date.today()
        # Store the prompts loaded at startup to avoid re-reading the file during save
        self.system_prompts = initial_prompts
        self.lock = asyncio.Lock()

    async def load(self):
        """Asynchronously loads usage data, resetting if the date has changed."""
        async with self.lock:
            try:
                async with aiofiles.open(self.file_path, 'r') as f:
                    content = await f.read()
                    full_data = json.loads(content)
                
                usage_data = full_data.get("usage", {})
                # Handle potential malformed date string safely
                try:
                    saved_date = datetime.date.fromisoformat(usage_data.get("date", "1970-01-01"))
                except ValueError:
                    saved_date = datetime.date(1970, 1, 1)

                today = datetime.date.today()

                if saved_date == today:
                    self._count = usage_data.get("count", 0)
                    self._date = today
                    print(f"Loaded usage data: {self._count} calls made today ({today.isoformat()}).")
                else:
                    print(f"Detected new day ({today.isoformat()}). API usage counter reset.")
                    self._count = 0
                    self._date = today
                    await self._save_under_lock()
            except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError) as e:
                print(f"Warning/Error with data file '{self.file_path}': {e}. Initializing count to 0.")
                self._count = 0
                self._date = datetime.date.today()
                await self._save_under_lock()

    async def _save_under_lock(self):
            """Reads current file, updates ONLY usage, and saves back."""
            try:
                # 1. Read current state of file (to preserve external edits to prompts)
                current_data = {}
                if os.path.exists(self.file_path):
                    async with aiofiles.open(self.file_path, 'r') as f:
                        try:
                            content = await f.read()
                            if content.strip():
                                current_data = json.loads(content)
                        except json.JSONDecodeError:
                            pass # Start fresh if corrupt

                # 2. Update only the usage section
                current_data["usage"] = {
                    "date": self._date.isoformat(), 
                    "count": self._count
                }
                
                # 3. Ensure system_prompts key exists if file was empty, using memory version
                if "system_prompts" not in current_data:
                    current_data["system_prompts"] = self.system_prompts

                # 4. Write back
                async with aiofiles.open(self.file_path, 'w') as f:
                    await f.write(json.dumps(current_data, indent=2))
            except IOError as e:
                print(f"Error saving data to '{self.file_path}': {e}")

    def get_count(self) -> int:
        if self._date < datetime.date.today():
            return 0
        return self._count

    async def increment(self):
        """Atomically increments the counter and saves the new state."""
        async with self.lock:
            today = datetime.date.today()
            if self._date < today:
                print(f"Detected new day ({today.isoformat()}) during increment. API usage counter reset.")
                self._count = 0
                self._date = today

            self._count += 1
            await self._save_under_lock()
            print(f"Gemini call successful. Count for today: {self._count}")

# --- Pass the loaded prompts to the tracker during initialization ---
api_tracker = APITracker(DATA_FILE, SYSTEM_PROMPTS)

# --- Google Gemini API Initialization ---
try:
    # Initialize the new Google GenAI Client
    client_genai = genai.Client(api_key=GOOGLE_API_KEY)
    print("Successfully initialized Google GenAI Client.")
except Exception as e:
    print(f"Error configuring Google Gemini: {e}")
    exit()

# --- Discord Bot Initialization ---
class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if TESTING_GUILD_ID:
            guild = discord.Object(id=TESTING_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Copied and synced commands to Guild ID: {TESTING_GUILD_ID}")

intents = discord.Intents.default()
intents.message_content = True
client = MyClient(intents=intents)

# --- Utility Function for Creating Embeds ---
def create_error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=message, color=discord.Color.red())

# --- Process Gemini Request Function ---
async def process_gemini_request(interaction: discord.Interaction, prompt: str, model_name: str, system_prompt: Optional[str], personality_name: str, context: Optional[str] = None) -> Optional[Tuple[str, discord.Message]]:
    if context: final_prompt = f"Previous Context Provided by User:\n{context}\n\nUser Question: {prompt}"
    else: final_prompt = prompt
    print(f"\nProcessing prompt for {interaction.user.name} using model '{model_name}': '{final_prompt[:100]}...'")
    
    config = types.GenerateContentConfig(
        system_instruction=system_prompt
    )

    try:
        # Use the asynchronous method from the new client
        response = await client_genai.aio.models.generate_content(
            model=model_name,
            contents=final_prompt,
            config=config
        )
        
        # Check for empty candidates or block reasons
        if not response.candidates:
            await interaction.followup.send(embed=create_error_embed("The model returned no response."), ephemeral=True)
            return None
        
        # New SDK finish reason check (basic implementation)
        # If the model is blocked, text access might fail or finish_reason will be SAFETY
        try:
            answer = response.text
        except (ValueError, AttributeError):
            # This catches cases where safety filters block text access
            print(f"Response blocked. Finish Reason: {response.candidates[0].finish_reason if response.candidates else 'Unknown'}")
            await interaction.followup.send(embed=create_error_embed("The model refused to answer (Safety/Invalid response)."), ephemeral=True)
            return None
            
        print(f"Gemini Response (length {len(answer)}): '{answer[:100]}...'")
        
        # Safe title truncation ensuring < 256 chars
        title_text = f"Prompt: {prompt}"
        if len(title_text) > 256:
            truncated_prompt = title_text[:253] + "..."
        else:
            truncated_prompt = title_text

        await api_tracker.increment()
        current_api_count = api_tracker.get_count()
        embed_color = discord.Color.blue()
        sent_messages = []
        model_display_name = next((name for name, value in MODELS.items() if value == model_name), model_name)
        
        if len(answer) <= EMBED_DESC_LIMIT:
            embed = discord.Embed(title=truncated_prompt, description=answer, color=embed_color)
            embed.set_author(name=f"Responding as: {personality_name}")
            embed.set_footer(text=f"Model: {model_display_name} | API Call #{current_api_count}")
            msg = await interaction.followup.send(embed=embed, wait=True)
            sent_messages.append(msg)
        else:
            chunks = [answer[i:i + EMBED_DESC_LIMIT] for i in range(0, len(answer), EMBED_DESC_LIMIT)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    embed = discord.Embed(title=truncated_prompt, description=chunk, color=embed_color)
                    embed.set_author(name=f"Responding as: {personality_name}")
                    embed.set_footer(text=f"Model: {model_display_name} | API Call #{current_api_count} | Part {i+1}/{len(chunks)}")
                else:
                    embed = discord.Embed(title=f"Part {i+1}/{len(chunks)}", description=chunk, color=embed_color)
                    embed.set_author(name=f"Responding as: {personality_name}")
                msg = await interaction.followup.send(embed=embed, wait=True)
                sent_messages.append(msg)
                await asyncio.sleep(0.2)
        return answer, sent_messages[-1] if sent_messages else None

    except Exception as e:
        print(f"An unexpected error occurred: {type(e).__name__} - {e}")
        traceback.print_exc()
        try: 
            await interaction.followup.send(embed=create_error_embed("Sorry, I encountered a critical unexpected error."), ephemeral=True)
        except discord.errors.NotFound: 
            # If interaction is invalid (e.g. timed out), try sending to channel
            if interaction.channel:
                await interaction.channel.send(f"{interaction.user.mention}", embed=create_error_embed("Sorry, I encountered a critical unexpected error."))
        return None

# --- UI Components for Follow-up ---
class FollowupModal(discord.ui.Modal, title='Ask a Follow-up Question'):
    def __init__(self, original_answer: str, model_name: str, system_prompt: Optional[str], personality_name: str):
        super().__init__(timeout=300)
        self.original_answer, self.model_name, self.system_prompt, self.personality_name = original_answer, model_name, system_prompt, personality_name
        self.followup_prompt = discord.ui.TextInput(label='Your Follow-up Question', placeholder='Enter your next question here...', style=discord.TextStyle.paragraph, required=True, max_length=1500)
        self.add_item(self.followup_prompt)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        await process_gemini_request(interaction, self.followup_prompt.value, model_name=self.model_name, system_prompt=self.system_prompt, personality_name=self.personality_name, context=self.original_answer)
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"Error in FollowupModal: {error}")
        await interaction.followup.send(embed=create_error_embed("Oops! Something went wrong processing your follow-up."), ephemeral=True)

class ReplyView(discord.ui.View):
    def __init__(self, original_prompt: str, original_answer: str, model_name: str, system_prompt: Optional[str], personality_name: str, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.original_prompt = original_prompt # Store original user prompt
        self.original_answer = original_answer
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.personality_name = personality_name
        self.message: Optional[discord.Message] = None # Store reference to message

    @discord.ui.button(label='Reply', style=discord.ButtonStyle.primary, emoji='↪️')
    async def reply_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Pass BOTH prompt and answer for full context
        full_context = f"User asked: {self.original_prompt}\nAI Answered: {self.original_answer}"
        await interaction.response.send_modal(FollowupModal(full_context, self.model_name, self.system_prompt, self.personality_name))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

# --- Slash Command Definitions ---
model_choices = [app_commands.Choice(name=name, value=value) for name, value in MODELS.items()]
personality_choices = [app_commands.Choice(name=name, value=name) for name in SYSTEM_PROMPTS.keys()]

@client.tree.command(name="ask_gemini", description="Ask Gemini a question!")
@app_commands.describe(prompt="The question you want to ask", personality=f"The personality for the AI (default: {DEFAULT_PERSONALITY})", model=f"The AI model to use (default: {DEFAULT_MODEL})", context="Optional: Relevant text/context from a previous message to include")
@app_commands.choices(model=model_choices, personality=personality_choices)
async def ask_gemini(interaction: discord.Interaction, prompt: str, personality: str = None, model: str = None, context: Optional[str] = None):
    await interaction.response.defer(thinking=True, ephemeral=False)
    chosen_model_name = model if model else MODELS.get(DEFAULT_MODEL, "gemini-3-flash-preview") # Fallback safety
    
    if not SYSTEM_PROMPTS:
         await interaction.followup.send(embed=create_error_embed("Error: No personalities configured or loaded. Cannot process request."), ephemeral=True)
         return

    chosen_personality_name = personality if personality else DEFAULT_PERSONALITY
    chosen_system_prompt = SYSTEM_PROMPTS.get(chosen_personality_name)
    result = await process_gemini_request(interaction, prompt, model_name=chosen_model_name, system_prompt=chosen_system_prompt, personality_name=chosen_personality_name, context=context)
    if result:
        answer_text, last_message = result
        if last_message:
            # Pass 'prompt' here so we can preserve context
            reply_view = ReplyView(original_prompt=prompt, original_answer=answer_text, model_name=chosen_model_name, system_prompt=chosen_system_prompt, personality_name=chosen_personality_name)
            reply_view.message = last_message # Assign the message so timeout works
            await last_message.edit(view=reply_view)

# --- Bot Event Handlers ---
@client.event
async def on_ready():
    await api_tracker.load()
    print(f'Logged in as {client.user.name} (ID: {client.user.id})')
    print(f'Bot is ready. API calls today: {api_tracker.get_count()}')
    print('------')

# --- Run the Bot ---
if __name__ == "__main__":
    try:
        print("Starting bot...")
        client.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        print("Error: Invalid Discord Token. Please check your .env file/environment variables")
    except Exception as e:
        print(f"An unexpected error occurred while running the bot: {e}")
