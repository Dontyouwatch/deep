import os
import asyncio
import logging
import tempfile
from telethon import TelegramClient, events
from telethon.errors import UserNotParticipantError, ChatAdminRequiredError, UserCreatorError, \
                            UsernameNotOccupiedError, UsernameInvalidError, ValueError as TelethonValueError
from dotenv import load_dotenv # For local development

# Import Flask and Uvicorn
from flask import Flask
import uvicorn

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
telethon_logger = logging.getLogger('telethon') # Get Telethon's logger
telethon_logger.setLevel(logging.WARNING) # Optional: reduce Telethon's verbosity

load_dotenv()

API_ID_STR = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Render will set the PORT environment variable for Web Services
PORT = int(os.getenv("PORT", 8080)) # Default to 8080 for local testing

# --- Validate Configuration ---
missing_vars = []
if not API_ID_STR: missing_vars.append("API_ID")
if not API_HASH: missing_vars.append("API_HASH")
if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")

if missing_vars:
    logger.error(f"Missing critical environment variables: {', '.join(missing_vars)}")
    exit(1)

try:
    API_ID = int(API_ID_STR)
except ValueError:
    logger.error("API_ID is not a valid integer.")
    exit(1)

# --- Initialize Telegram Client ---
SESSION_NAME = 'bot_session' # Stored in Render's ephemeral filesystem
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# --- Initialize Flask App ---
# This is the web server component that Render will talk to
web_app = Flask(__name__)

@web_app.route('/')
async def health_check():
    """Basic health check endpoint for Render."""
    # You can add more checks here, e.g., if the bot is connected
    bot_connected = client.is_connected()
    return f"Bot is running. Telethon client connected: {bot_connected}", 200

# --- Telethon Event Handlers ---
@client.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    logger.info(f"Received /start command from user {event.sender_id}")
    await event.respond("Hi, what's up? Send me a Telegram username (with or without @) to fetch their DPs.")

@client.on(events.NewMessage)
async def username_handler(event):
    if event.out or event.text.startswith('/'):
        return

    text = event.raw_text.strip()
    if not text:
        return

    username_to_fetch = text.replace("@", "").strip()
    if not username_to_fetch:
        await event.respond("Please provide a valid username.")
        return

    logger.info(f"Attempting to fetch DPs for username: {username_to_fetch} (requested by {event.sender_id})")
    temp_files_to_clean = []

    try:
        user_entity = await client.get_entity(username_to_fetch)
        photos = await client.get_profile_photos(user_entity, limit=10)

        if not photos:
            await event.respond(f"No public profile photos found for @{username_to_fetch}.")
            return

        sent_count = 0
        for i, photo in enumerate(photos):
            # Using a context manager for NamedTemporaryFile is good practice
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
                file_path = tmp_file.name
            
            temp_files_to_clean.append(file_path)
            logger.debug(f"Downloading photo {i+1} for {username_to_fetch} to {file_path}")
            actual_file_path = await client.download_media(photo, file=file_path)
            
            if actual_file_path:
                logger.debug(f"Sending photo {i+1} for {username_to_fetch} from {actual_file_path}")
                await client.send_file(event.chat_id, actual_file_path, caption=f"DP {i+1} for @{username_to_fetch}")
                sent_count += 1
            else:
                logger.warning(f"Failed to download photo {i+1} for {username_to_fetch}")

        if sent_count > 0:
            await event.respond(f"Sent {sent_count} profile photo(s) of @{username_to_fetch}.")
        else:
            await event.respond(f"Could not download or send any profile photos for @{username_to_fetch}.")

    except (UsernameNotOccupiedError, UsernameInvalidError, TelethonValueError) as e:
        logger.warning(f"Error resolving username {username_to_fetch}: {e}")
        await event.respond(f"Sorry, couldn't find or access user @{username_to_fetch}. Is the username correct?")
    except ConnectionError as e:
        logger.error(f"Network connection error: {e}")
        await event.respond("Sorry, a network error occurred. Please try again later.")
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching DPs for {username_to_fetch}: {e}", exc_info=True)
        await event.respond(f"Sorry, an unexpected error occurred: {type(e).__name__}")
    finally:
        for temp_file_path in temp_files_to_clean:
            try:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                    logger.debug(f"Cleaned up temporary file: {temp_file_path}")
            except OSError as e:
                logger.error(f"Error removing temporary file {temp_file_path}: {e}")

# --- Main Application Logic to run both Telethon and Web Server ---
async def main():
    """Starts the Telethon client and the Uvicorn web server."""
    try:
        logger.info("Starting Telethon client...")
        # Start the Telethon client. It will run in the background using the same asyncio loop.
        # The bot_token argument logs the bot in.
        await client.start(bot_token=BOT_TOKEN)
        logger.info("Telethon client connected successfully!")
        bot_info = await client.get_me()
        logger.info(f"Bot User ID: {bot_info.id}, Username: @{bot_info.username}")

        # Configure Uvicorn to run the Flask app (web_app)
        # Uvicorn will manage the asyncio event loop that Telethon also uses.
        config = uvicorn.Config(
            web_app, # Your Flask app (or any ASGI app)
            host="0.0.0.0", # Listen on all available network interfaces
            port=PORT,      # Port provided by Render (or default for local)
            log_level="info"
        )
        server = uvicorn.Server(config)

        logger.info(f"Starting Uvicorn web server on http://0.0.0.0:{PORT}")
        # Running the Uvicorn server. This will block until the server is stopped.
        # Telethon's event handlers will run in the background on the same event loop.
        await server.serve()
        
        # This part will be reached if server.serve() finishes (e.g., on shutdown)
        logger.info("Web server has stopped.")

    except Exception as e:
        logger.critical(f"Critical error during startup or runtime: {e}", exc_info=True)
    finally:
        logger.info("Application is shutting down...")
        if client.is_connected():
            logger.info("Disconnecting Telethon client...")
            await client.disconnect()
            logger.info("Telethon client disconnected.")
        logger.info("Shutdown complete.")

if __name__ == '__main__':
    # `asyncio.run(main())` is the standard way to run an asyncio program.
    # Uvicorn will manage the event loop once `server.serve()` is called.
    asyncio.run(main())
