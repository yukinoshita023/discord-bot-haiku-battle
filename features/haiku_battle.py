import asyncio
import random
import re

import discord
import firebase_admin
from firebase_admin import credentials, firestore

from config import HAIKU_BATTLE_CHANNEL_ID

FLOWER_NAMES = [
    "品菊", "牡丹", "梅", "桜", "藤", "菊", "蘭", "椿",
    "桔梗", "朝顔", "蓮", "芍薬", "水仙", "撫子", "紫陽花", "木蓮",
]

VOTE_A = "🌸"
VOTE_B = "🌺"
VOTE_THRESHOLD = 4
VOTE_TIMEOUT_INITIAL = 6 * 3600   # 無投票時: 6時間
VOTE_TIMEOUT_AFTER_VOTE = 1800    # 初票後: 30分
MAX_HAIKU_LENGTH = 10

_URL_RE = re.compile(r'https?://', re.IGNORECASE)
_MENTION_RE = re.compile(r'<[@#][!&]?\d+>')        # @user @role #channel
_CUSTOM_EMOJI_RE = re.compile(r'<a?:\w+:\d+>')     # カスタム絵文字 <:name:id>
_TEXT_EMOJI_RE = re.compile(r':[a-zA-Z0-9_]{2,}:') # :heart_hands: など
_UNICODE_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "☀-➿"
    "]"
)


def _validate_haiku(message: discord.Message) -> str | None:
    if message.attachments or message.stickers:
        return "画像・スタンプは使えません 🙅"
    if _URL_RE.search(message.content):
        return "URLは使えません 🙅"
    if _MENTION_RE.search(message.content):
        return "メンションは使えません 🙅"
    if (
        _CUSTOM_EMOJI_RE.search(message.content)
        or _TEXT_EMOJI_RE.search(message.content)
        or _UNICODE_EMOJI_RE.search(message.content)
    ):
        return "絵文字は使えません 🙅"
    if len(message.content) > MAX_HAIKU_LENGTH:
        return f"10文字以内で書いてください（{len(message.content)}文字） 🙅"
    return None

# state → (team, 句番号, 音数, 次のstate)
_FLOW = {
    "A1": ("A", 1, 5, "A2"),
    "A2": ("A", 2, 7, "A3"),
    "A3": ("A", 3, 5, "B1"),
    "B1": ("B", 1, 5, "B2"),
    "B2": ("B", 2, 7, "B3"),
    "B3": ("B", 3, 5, "VOTING"),
}

_db = None


class _Battle:
    def __init__(self):
        self.state = "IDLE"
        self.names = {"A": "", "B": ""}
        self.parts = {"A": [], "B": []}  # [(user_id, text), ...]
        self.vote_msg_id = None
        self.prompt_msg_id = 0  # この案内より後のメッセージだけ受け付ける
        self._timeout_task = None
        self.first_vote_received = False

    def reset(self):
        if self._timeout_task:
            self._timeout_task.cancel()
            self._timeout_task = None
        flowers = random.sample(FLOWER_NAMES, 2)
        self.names = {"A": flowers[0], "B": flowers[1]}
        self.parts = {"A": [], "B": []}
        self.vote_msg_id = None
        self.prompt_msg_id = 0
        self.first_vote_received = False
        self.state = "A1"

    def members(self, team: str) -> set:
        return {uid for uid, _ in self.parts[team]}

    def haiku(self, team: str) -> str:
        return "　".join(t for _, t in self.parts[team])


_b = _Battle()
_lock = asyncio.Lock()


async def start_battle(channel: discord.TextChannel):
    _b.reset()
    embed = discord.Embed(
        title="⚔️ 俳句バトル開始！",
        description=(
            f"🌸 チーム **{_b.names['A']}** vs 🌺 チーム **{_b.names['B']}**\n\n"
            f"チーム **{_b.names['A']}** の番です！\n"
            "**第1句（5音）** を書いてください"
        ),
        color=discord.Color.from_rgb(255, 182, 193),
    )
    prompt = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    _b.prompt_msg_id = prompt.id


async def handle_message(bot: discord.Client, message: discord.Message):
    if message.channel.id != HAIKU_BATTLE_CHANNEL_ID:
        return
    if message.author.bot:
        return
    if _b.state not in _FLOW:
        return

    error = _validate_haiku(message)
    if error:
        await message.reply(error, mention_author=False)
        return

    user_id = message.author.id
    team = next_state = None

    async with _lock:
        if _b.state not in _FLOW:
            return
        if message.id <= _b.prompt_msg_id:
            return  # 案内より前に送られたメッセージは無視

        team, _, _, next_state = _FLOW[_b.state]
        _b.parts[team].append((user_id, message.content))
        _b.state = next_state

    await message.add_reaction("✅")

    if next_state == "VOTING":
        await _start_voting(bot, message.channel)
    elif next_state == "B1":
        embed = discord.Embed(color=discord.Color.from_rgb(255, 182, 193))
        embed.add_field(
            name=f"🌸 チーム {_b.names['A']} の俳句完成！",
            value=f"```{_b.haiku('A')}```",
            inline=False,
        )
        embed.add_field(
            name=f"次はチーム {_b.names['B']} の番！",
            value="**第1句（5音）** を書いてください",
            inline=False,
        )
        prompt = await message.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        _b.prompt_msg_id = prompt.id
    else:
        next_team, next_part, next_mora, _ = _FLOW[next_state]
        embed = discord.Embed(
            description=(
                f"チーム **{_b.names[next_team]}** — **第{next_part}句（{next_mora}音）**"
            ),
            color=discord.Color.from_rgb(255, 182, 193),
        )
        prompt = await message.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        _b.prompt_msg_id = prompt.id


async def _start_voting(bot: discord.Client, channel: discord.TextChannel):
    embed = discord.Embed(
        title="🗳️ どちらの俳句が好きですか？",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name=f"🌸 チーム {_b.names['A']}",
        value=f"```{_b.haiku('A')}```",
        inline=False,
    )
    embed.add_field(
        name=f"🌺 チーム {_b.names['B']}",
        value=f"```{_b.haiku('B')}```",
        inline=False,
    )
    embed.set_footer(text=f"🌸か🌺でリアクション！先に{VOTE_THRESHOLD}票で即勝利 / 初票から30分後 or 無投票なら6時間後に多い方の勝ち！")

    vote_msg = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    await vote_msg.add_reaction(VOTE_A)
    await vote_msg.add_reaction(VOTE_B)

    _b.vote_msg_id = vote_msg.id
    _b.state = "VOTING"
    _b._timeout_task = asyncio.create_task(
        _timeout_handler(bot, channel, vote_msg.id, VOTE_TIMEOUT_INITIAL)
    )


async def _timeout_handler(bot: discord.Client, channel: discord.TextChannel, msg_id: int, timeout: int = VOTE_TIMEOUT_AFTER_VOTE):
    try:
        await asyncio.sleep(timeout)
    except asyncio.CancelledError:
        return

    if _b.state != "VOTING":
        return

    try:
        msg = await channel.fetch_message(msg_id)
    except discord.NotFound:
        return

    votes_a, votes_b = _tally(msg)
    if votes_a == votes_b:
        await channel.send("同票のため抽選で勝者を決定します...🎲")
        winner = random.choice(["A", "B"])
    else:
        winner = "A" if votes_a > votes_b else "B"

    await _finish(bot, channel, winner, votes_a, votes_b)


async def handle_reaction(bot: discord.Client, payload: discord.RawReactionActionEvent):
    if _b.state != "VOTING":
        return
    if payload.message_id != _b.vote_msg_id:
        return
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) not in (VOTE_A, VOTE_B):
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return

    # 初リアクション: 6時間タイマーを10分タイマーに切り替え
    async with _lock:
        if _b.state == "VOTING" and not _b.first_vote_received:
            _b.first_vote_received = True
            if _b._timeout_task:
                _b._timeout_task.cancel()
            _b._timeout_task = asyncio.create_task(
                _timeout_handler(bot, channel, payload.message_id, VOTE_TIMEOUT_AFTER_VOTE)
            )

    try:
        msg = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        return

    votes_a, votes_b = _tally(msg)
    if votes_a >= VOTE_THRESHOLD:
        await _finish(bot, channel, "A", votes_a, votes_b)
    elif votes_b >= VOTE_THRESHOLD:
        await _finish(bot, channel, "B", votes_a, votes_b)


def _tally(msg: discord.Message) -> tuple:
    a = b = 0
    for r in msg.reactions:
        if str(r.emoji) == VOTE_A:
            a = max(0, r.count - 1)  # ボット自身のリアクションを除く
        elif str(r.emoji) == VOTE_B:
            b = max(0, r.count - 1)
    return a, b


async def _finish(
    bot: discord.Client,
    channel: discord.TextChannel,
    winner: str,
    votes_a: int,
    votes_b: int,
):
    if _b.state != "VOTING":
        return
    _b.state = "IDLE"

    current_task = asyncio.current_task()
    if _b._timeout_task and _b._timeout_task is not current_task:
        _b._timeout_task.cancel()
    _b._timeout_task = None

    winner_name = _b.names[winner]
    winners = _b.members(winner)

    for user_id in winners:
        doc_ref = _db.collection("users").document(str(user_id))
        doc = doc_ref.get()
        data = doc.to_dict() if doc.exists else {}
        current = data.get("haiku_points", 0) or 0
        doc_ref.set({"haiku_points": current + 1}, merge=True)

    mentions = "　".join(f"<@{uid}>" for uid in winners) or "（なし）"
    embed = discord.Embed(
        title=f"🏆 チーム {winner_name} の勝利！",
        description=(
            f"{VOTE_A} {_b.names['A']}: **{votes_a}票**　vs　"
            f"{VOTE_B} {_b.names['B']}: **{votes_b}票**"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(name="🎉 勝者", value=mentions, inline=False)
    embed.add_field(name="ポイント", value="各自 **+1ポイント** 獲得！", inline=False)
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    await asyncio.sleep(5)
    await start_battle(channel)


async def setup(bot: discord.Client):
    global _db
    if not firebase_admin._apps:
        cred = credentials.Certificate("firebase_key.json")
        firebase_admin.initialize_app(cred)
    _db = firestore.client()

    @bot.tree.command(name="haiku_ranking", description="俳句バトルのランキングを表示します")
    async def haiku_ranking(interaction: discord.Interaction):
        await interaction.response.defer()

        docs = list(_db.collection("users").stream())
        ranking = []
        for doc in docs:
            data = doc.to_dict() or {}
            pts = data.get("haiku_points", 0) or 0
            if pts > 0:
                ranking.append((doc.id, pts))

        ranking.sort(key=lambda x: x[1], reverse=True)
        top10 = ranking[:10]

        if not top10:
            await interaction.followup.send("まだランキングデータがありません。")
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, pts) in enumerate(top10):
            prefix = medals[i] if i < 3 else f"**{i + 1}.**"
            lines.append(f"{prefix} <@{uid}>: {pts}ポイント")

        embed = discord.Embed(
            title="🏆 俳句バトル ランキング TOP10",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed)
