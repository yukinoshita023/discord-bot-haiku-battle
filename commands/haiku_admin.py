import discord
from config import HAIKU_BATTLE_CHANNEL_ID
from features import haiku_battle


async def setup(bot):
    @bot.tree.command(
        name="haiku_reset",
        description="今の俳句バトルを強制終了して新しいゲームを始めます（管理者専用）",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def haiku_reset(interaction: discord.Interaction):
        channel = bot.get_channel(HAIKU_BATTLE_CHANNEL_ID)
        if channel is None:
            await interaction.response.send_message(
                "俳句バトルのチャンネルが見つかりません。", ephemeral=True
            )
            return
        await interaction.response.send_message("ゲームをリセットします...", ephemeral=True)
        await haiku_battle.start_battle(channel)

    @haiku_reset.error
    async def haiku_reset_error(interaction: discord.Interaction, error: Exception):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message(
                "このコマンドは管理者のみ使用できます。", ephemeral=True
            )
