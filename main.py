import os
import asyncio
import logging
import tempfile
from telethon import TelegramClient, events
from telethon.errors import (
    UserNotParticipantError, ChatAdminRequiredError, UserCreatorError,
    UsernameNotOccupiedError, UsernameInvalidError,
)
from dotenv import load_dotenv

from flask import Flask, jsonify
import uvicorn
from asgiref.wsgi import WsgiToAsgi # <--- MAKE SURE THIS IMPORT IS PRESENT

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
telethon_logger = logging.getLogger('telethon')
telethon_logger.setLevel(logging.WARNING)

load_dotenv()

API_ID_STR = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "8080"))

# --- Validate Configuration ---
missing_vars = []
if not API_ID_STR: missing_vars.append("API_ID")
if not API_HASH: missing_vars.append("API_HASH")
if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")

if missing_vars:
    logger.critical(f"FATAL: Missing critical environment variables: {', '.join(missing_vars)}. Exiting.")
    exit(1)

try:
    API_ID = int(API_ID_STR)
except ValueError:
    logger.critical("FATAL: API_ID is not a valid integer. Exiting.")
    exit(1)

# --- Initialize Telegram Client ---
SESSION_NAME = 'bot_session_render'
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# --- Initialize Flask App (this is the WSGI app) ---
flask_wsgi_app = Flask(__name__) # Renamed for clarity

@flask_wsgi_app.route('/') # Decorate the Flask WSGI app instance
async def health_check():
    bot_is_connected = client.is_connected()
    status_message = "Bot is running."
    if bot_is_connected:
        status_message += " Telethon client is connected to Telegram."
    else:
        status_message += " Telethon client is NOT connected to Telegram."
    return jsonify(status=status_message, telethon_connected=bot_is_connected), 200

# --- Telethon Event Handlers ---
@client.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    try:
        logger.info(f"Received /start command from user {event.sender_id}")
        await event.respond("Hi, what's up? Send me a Telegram username (with or without @) to fetch their DPs.")
    except Exception as e:
        logger.error(f"Error in start_handler for user {event.sender_id}: {e}", exc_info=True)

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

    logger.info(f"Attempting to fetch DPs for username: '{username_to_fetch}' (requested by {event.sender_id})")
    temp_files_to_clean = []

    try:
        user_entity = await client.get_entity(username_to_fetch)
        photos = await client.get_profile_photos(user_entity, limit=10)

        if not photos:
            await event.respond(f"No public profile photos found for @{username_to_fetch}.")
            return

        sent_count = 0
        for i, photo in enumerate(photos):
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
                file_path = tmp_file.name
            temp_files_to_clean.append(file_path)

            logger.debug(f"Downloading photo {i+1} for @{username_to_fetch} to {file_path}")
            actual_file_path = await client.download_media(photo, file=file_path)
            
            if actual_file_path:
                logger.debug(f"Sending photo {i+1} for @{username_to_fetch} from {actual_file_path}")
                await client.send_file(
                    event.chat_id,
                    actual_file_path,
                    caption=f"DP {i+1} for @{username_to_fetch}"
                )
                sent_count += 1
            else:
                logger.warning(f"Failed to download photo {i+1} for @{username_to_fetch}.")

        if sent_count > 0:
            await event.respond(f"Sent {sent_count} profile photo(s) of @{username_to_fetch}.")
        elif photos:
             await event.respond(f"Found photos for @{username_to_fetch}, but couldn't download or send them.")

    except (UsernameNotOccupiedError, UsernameInvalidError, ValueError) as e: # Catch built-in ValueError
        logger.warning(f"Error resolving username '{username_to_fetch}': {type(e).__name__} - {e}")
        await event.respond(f"Sorry, couldn't find or access user @{username_to_fetch}. The username might be incorrect or the entity type is not supported.")
    except ConnectionError as e:
        logger.error(f"Network connection error while processing for @{username_to_fetch}: {e}", exc_info=True)
        await event.respond("Sorry, a network error occurred. Please try again later.")
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching DPs for @{username_to_fetch}: {e}", exc_info=True)
        await event.respond(f"Sorry, an unexpected error occurred. Type: {type(e).__name__}")
    finally:
        for temp_file_path in temp_files_to_clean:
            try:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                    logger.debug(f"Cleaned up temporary file: {temp_file_path}")
            except OSError as e:
                logger.error(f"Error removing temporary file {temp_file_path}: {e}")

# --- Main Application Logic ---
async def main():
    try:
        logger.info("Application starting...")
        logger.info(f"Attempting to start Telethon client with API_ID: {API_ID}")
        
        await client.start(bot_token=BOT_TOKEN)
        logger.info("Telethon client connected successfully!")
        
        bot_info = await client.get_me()
        logger.info(f"Bot User ID: {bot_info.id}, Username: @{bot_info.username}")

        # THIS IS THE CRITICAL FIX: Wrap the Flask WSGI app with WsgiToAsgi
        asgi_app = WsgiToAsgi(flask_wsgi_app)

        config = uvicorn.Config(
            app=asgi_app,        # Pass the wrapped ASGI app to Uvicorn
            host="0.0.0.0",
            port=PORT,
            log_level="info",
        )
        server = uvicorn.Server(config)

        logger.info(f"Starting Uvicorn web server on http://0.0.0.0:{PORT}")
        await server.serve()
        
        logger.info("Uvicorn web server has stopped.")

    except ConnectionRefusedError:
        logger.critical("FATAL: Connection refused during Telethon startup. Exiting.")
    except Exception as e:
        logger.critical(f"FATAL: Critical error during application startup or main runtime: {e}", exc_info=True)
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
    asyncio.run(main())
