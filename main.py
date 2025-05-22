import os
from telethon import TelegramClient, events
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

@client.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    await event.respond("Hi, what's up? Send me a Telegram username (with or without @) to fetch their DPs.")

@client.on(events.NewMessage)
async def username_handler(event):
    text = event.raw_text.strip()
    if text.startswith("/"):
        return

    username = text.replace("@", "")

    try:
        user = await client.get_entity(username)
        photos = await client.get_profile_photos(user)

        if not photos:
            await event.respond(f"No public profile photos found for @{username}.")
            return

        count = 0
        for photo in photos:
            file_path = await client.download_media(photo, file=f"temp_{count}.jpg")
            await client.send_file(event.chat_id, file_path)
            os.remove(file_path)
            count += 1

        await event.respond(f"Sent {count} profile photos of @{username}.")

    except Exception as e:
        await event.respond(f"Sorry, couldn't fetch photos. Error: {str(e)}")

print("Bot is running...")
client.run_until_disconnected()
