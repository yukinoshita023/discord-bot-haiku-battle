import discord
from config import TOKEN, HAIKU_BATTLE_CHANNEL_ID
from commands import setup_commands
from features import haiku_battle

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self):
        await setup_commands(self)
        await haiku_battle.setup(self)
        print("全コマンドを追加しました")

        await self.tree.sync()
        print("スラッシュコマンドを同期しました")


bot = MyBot()

@bot.event
async def on_ready():
    print(f"ログインしました: {bot.user}")
    channel = bot.get_channel(HAIKU_BATTLE_CHANNEL_ID)
    if channel:
        await haiku_battle.start_battle(channel)
    else:
        print(f"チャンネル {HAIKU_BATTLE_CHANNEL_ID} が見つかりません")

@bot.event
async def on_message(message):
    await haiku_battle.handle_message(bot, message)

@bot.event
async def on_raw_reaction_add(payload):
    await haiku_battle.handle_reaction(bot, payload)

bot.run(TOKEN)
