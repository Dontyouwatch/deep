import os
import asyncio
from telethon import TelegramClient, events, errors
from aiohttp import web

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# TelegramClient session string or session name
SESSION_NAME = 'bot_session'

# Create client for bot using Telethon
client = TelegramClient(SESSION_NAME, API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# We will keep track of users waiting to send username
waiting_for_username = set()

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    sender = await event.get_sender()
    user_id = sender.id
    await event.reply("Hi! Send me a Telegram username (with or without @), and I'll fetch all profile photos of that user.")
    waiting_for_username.add(user_id)

@client.on(events.NewMessage)
async def handle_username(event):
    sender = await event.get_sender()
    user_id = sender.id
    if user_id not in waiting_for_username:
        return  # Ignore messages if not expecting username

    username = event.raw_text.strip()
    if username.startswith('@'):
        username = username[1:]

    waiting_for_username.remove(user_id)  # We got username, remove from waiting

    try:
        # Get the entity (user) from username
        entity = await client.get_entity(username)
    except (ValueError, errors.UsernameNotOccupiedError):
        await event.reply("❌ Invalid username or user not found. Please try /start again with a valid username.")
        return
    except Exception as e:
        await event.reply(f"❌ Error: {str(e)}")
        return

    try:
        photos = await client.get_profile_photos(entity)
        if not photos:
            await event.reply("This user has no profile photos.")
            return

        await event.reply(f"Found {len(photos)} profile photos. Sending now...")
        # Send photos one by one
        for photo in photos:
            await client.send_file(event.chat_id, photo)
    except Exception as e:
        await event.reply(f"❌ Failed to fetch/send photos: {str(e)}")

# aiohttp web server for Render
async def handle(request):
    return web.Response(text="Bot is running")

app = web.Application()
app.add_routes([web.get('/', handle)])

async def main():
    # Start the Telegram client
    await client.start()
    print("Telegram Bot started!")

    # Run both Telegram client and web server concurrently
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

    # Run Telegram client until disconnected
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
