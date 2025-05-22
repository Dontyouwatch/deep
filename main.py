import os
import asyncio
import logging
import tempfile
from telethon import TelegramClient, events
# Corrected import: ValueError is a built-in Python exception, not from telethon.errors
from telethon.errors import (
    UserNotParticipantError, ChatAdminRequiredError, UserCreatorError,
    UsernameNotOccupiedError, UsernameInvalidError,
    # Common communication errors it's good to be aware of, though not explicitly handled below:
    # FloodWaitError, RPCError, TimeoutError as TelethonTimeoutError
)
from dotenv import load_dotenv # For local development

# Import Flask and Uvicorn
from flask import Flask, jsonify # Added jsonify for potentially more structured health check
import uvicorn

# --- Configuration ---
# Configure logging
# Using %(name)s can be helpful to see which logger (e.g., 'main_app', 'telethon', 'uvicorn') is emitting the log.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__) # Logger for our application's messages

# Optional: Adjust Telethon's own logger verbosity if needed
telethon_logger = logging.getLogger('telethon')
telethon_logger.setLevel(logging.WARNING) # Example: reduce to WARNING, INFO or DEBUG for more details

# Load environment variables from .env file for local development
# On Render, you'll set these in the Render dashboard
load_dotenv()

API_ID_STR = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Render will set the PORT environment variable for Web Services
# Default to 8080 for local testing if PORT is not set
PORT = int(os.getenv("PORT", "8080"))


# --- Validate Configuration ---
missing_vars = []
if not API_ID_STR: missing_vars.append("API_ID")
if not API_HASH: missing_vars.append("API_HASH")
if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")

if missing_vars:
    logger.critical(f"FATAL: Missing critical environment variables: {', '.join(missing_vars)}. Exiting.")
    exit(1) # Exit if critical variables are missing

try:
    API_ID = int(API_ID_STR)
except ValueError:
    logger.critical("FATAL: API_ID is not a valid integer. Exiting.")
    exit(1)

# --- Initialize Telegram Client ---
# Using a unique session name. For bot tokens, the session file is less critical
# as auth is done via token on each start. Render's ephemeral filesystem means this
# file might be lost on restarts/redeploys, but it should be fine for bot token auth.
SESSION_NAME = 'bot_session_render' # Slightly more descriptive session name
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# --- Initialize Flask App ---
# This is the web server component that Render will talk to
web_app = Flask(__name__)

@web_app.route('/')
async def health_check():
    """Basic health check endpoint for Render."""
    bot_is_connected = client.is_connected()
    status_message = "Bot is running."
    if bot_is_connected:
        status_message += " Telethon client is connected to Telegram."
    else:
        status_message += " Telethon client is NOT connected to Telegram."
    
    # Returning JSON is often a good practice for health checks
    return jsonify(status=status_message, telethon_connected=bot_is_connected), 200

# --- Telethon Event Handlers ---
@client.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    """Handles the /start command."""
    try:
        logger.info(f"Received /start command from user {event.sender_id}")
        await event.respond("Hi, what's up? Send me a Telegram username (with or without @) to fetch their DPs.")
    except Exception as e:
        logger.error(f"Error in start_handler for user {event.sender_id}: {e}", exc_info=True)
        # Optionally, notify the user something went wrong if event.respond fails
        # await event.respond("Sorry, something went wrong with the start command.")


@client.on(events.NewMessage)
async def username_handler(event):
    """Handles messages containing usernames to fetch profile pictures."""
    # Ignore our own messages and commands other than /start (which has its own handler)
    if event.out or event.text.startswith('/'):
        return

    text = event.raw_text.strip()
    if not text: # Ignore empty messages
        return

    username_to_fetch = text.replace("@", "").strip()
    if not username_to_fetch: # Ignore messages that become empty after stripping @
        await event.respond("Please provide a valid username.")
        return

    logger.info(f"Attempting to fetch DPs for username: '{username_to_fetch}' (requested by {event.sender_id})")

    temp_files_to_clean = [] # Keep track of files to clean up

    try:
        user_entity = await client.get_entity(username_to_fetch)
        # Limit number of photos to prevent abuse and excessive resource usage
        photos = await client.get_profile_photos(user_entity, limit=10)

        if not photos:
            await event.respond(f"No public profile photos found for @{username_to_fetch}.")
            return

        sent_count = 0
        for i, photo in enumerate(photos):
            # Use tempfile for safer temporary file handling
            # Using a context manager ensures the file descriptor is closed,
            # even if 'delete=False' is used.
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
                file_path = tmp_file.name
            
            temp_files_to_clean.append(file_path) # Add to cleanup list *before* download attempt

            logger.debug(f"Downloading photo {i+1} for @{username_to_fetch} to {file_path}")
            actual_file_path = await client.download_media(photo, file=file_path)
            
            if actual_file_path: # Check if download was successful and returned a path
                logger.debug(f"Sending photo {i+1} for @{username_to_fetch} from {actual_file_path}")
                await client.send_file(
                    event.chat_id,
                    actual_file_path,
                    caption=f"DP {i+1} for @{username_to_fetch}"
                )
                sent_count += 1
            else:
                logger.warning(f"Failed to download photo {i+1} for @{username_to_fetch}. `download_media` returned None.")

        if sent_count > 0:
            await event.respond(f"Sent {sent_count} profile photo(s) of @{username_to_fetch}.")
        elif photos: # Photos list was not empty, but we couldn't send any
             await event.respond(f"Found photos for @{username_to_fetch}, but couldn't download or send them.")
        # If photos list was empty, the earlier check handles it.

    # Correctly catch built-in ValueError for issues like invalid entity strings
    except (UsernameNotOccupiedError, UsernameInvalidError, ValueError) as e:
        logger.warning(f"Error resolving username '{username_to_fetch}': {type(e).__name__} - {e}")
        await event.respond(f"Sorry, couldn't find or access user @{username_to_fetch}. The username might be incorrect or the entity type is not supported.")
    except UserNotParticipantError: # More specific error example
        logger.warning(f"User {event.sender_id} not participant in chat, or bot lacks permissions for @{username_to_fetch}.")
        await event.respond(f"I might not have the necessary permissions or the user @{username_to_fetch} is not accessible.")
    except ConnectionError as e: # Catch network-related issues
        logger.error(f"Network connection error while processing for @{username_to_fetch}: {e}", exc_info=True)
        await event.respond("Sorry, a network error occurred. Please try again later.")
    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(f"An unexpected error occurred while fetching DPs for @{username_to_fetch}: {e}", exc_info=True)
        await event.respond(f"Sorry, an unexpected error occurred. Type: {type(e).__name__}")
    finally:
        # Clean up temporary files
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
    # This outer try-except is for fatal errors during startup.
    try:
        logger.info("Application starting...")
        logger.info(f"Attempting to start Telethon client with API_ID: {API_ID}")
        
        # Start the Telethon client. It will run in the background using the same asyncio loop.
        # The bot_token argument logs the bot in.
        await client.start(bot_token=BOT_TOKEN)
        logger.info("Telethon client connected successfully!")
        
        bot_info = await client.get_me()
        logger.info(f"Bot User ID: {bot_info.id}, Username: @{bot_info.username}")

        # Configure Uvicorn to run the Flask app (web_app)
        # Uvicorn will manage the asyncio event loop that Telethon also uses.
        config = uvicorn.Config(
            app=web_app,        # Your Flask app (or any ASGI app)
            host="0.0.0.0",     # Listen on all available network interfaces
            port=PORT,          # Port provided by Render (or default for local)
            log_level="info",   # Uvicorn's own log level
            # loop="asyncio"    # Usually auto-detected, but can be explicit
        )
        server = uvicorn.Server(config)

        logger.info(f"Starting Uvicorn web server on http://0.0.0.0:{PORT}")
        # Running the Uvicorn server. This will block until the server is stopped.
        # Telethon's event handlers will run in the background on the same event loop.
        await server.serve()
        
        # This part will be reached if server.serve() finishes (e.g., on graceful shutdown)
        logger.info("Uvicorn web server has stopped.")

    except ConnectionRefusedError:
        logger.critical("FATAL: Connection refused during Telethon startup. Check network or Telegram's status. Exiting.")
        # No exit(1) here as finally block will run. If you want to force exit, add it.
    except Exception as e:
        logger.critical(f"FATAL: Critical error during application startup or main runtime: {e}", exc_info=True)
        # No exit(1) here as finally block will run.
    finally:
        logger.info("Application is shutting down...")
        if client.is_connected():
            logger.info("Disconnecting Telethon client...")
            try:
                await client.disconnect()
                logger.info("Telethon client disconnected successfully.")
            except Exception as e:
                logger.error(f"Error during Telethon client disconnection: {e}", exc_info=True)
        else:
            logger.info("Telethon client was not connected or already disconnected.")
        logger.info("Shutdown complete.")

if __name__ == '__main__':
    # `asyncio.run(main())` is the standard way to run an asyncio program.
    # Uvicorn will take over managing the event loop once `server.serve()` is called within main().
    asyncio.run(main())
