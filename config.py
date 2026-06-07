import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
HAIKU_BATTLE_CHANNEL_ID = 1498699414389395506