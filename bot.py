import os
import discord
from discord.ext import commands
import time

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Store signups per server (simple version)
runs = {}  # guild_id -> list of signups


def is_guild_member(member: discord.Member):
    return any(role.name == "Member" for role in member.roles)


def sort_and_split(signups):
    sorted_list = sorted(
        signups,
        key=lambda x: (
            not x["guild_member"],  # guild first
            x["time"]
        )
    )

    return sorted_list[:8], sorted_list[8:]


def build_embed(selected, waitlist):
    embed = discord.Embed(title="🏃 Run Signup")

    roster_text = "\n".join(
        f"{i+1}. <@{u['user_id']}>" for i, u in enumerate(selected)
    ) or "None"

    wait_text = "\n".join(
        f"{i+1}. <@{u['user_id']}>" for i, u in enumerate(waitlist)
    ) or "None"

    embed.add_field(name="✅ Selected (8 max)", value=roster_text, inline=False)
    embed.add_field(name="⏳ Waitlist", value=wait_text, inline=False)

    return embed


class RunView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        if guild_id not in runs:
            runs[guild_id] = []

    @discord.ui.button(label="Join Run", style=discord.ButtonStyle.green)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):

        guild_id = interaction.guild.id
        user = interaction.user

        # initialize
        if guild_id not in runs:
            runs[guild_id] = []

        # prevent duplicates
        for u in runs[guild_id]:
            if u["user_id"] == user.id:
                await interaction.response.send_message(
                    "You're already signed up.", ephemeral=True
                )
                return

        runs[guild_id].append({
            "user_id": user.id,
            "guild_member": is_guild_member(user),
            "time": time.time()
        })

        await self.update_message(interaction)

    @discord.ui.button(label="Leave Run", style=discord.ButtonStyle.red)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):

        guild_id = interaction.guild.id

        if guild_id in runs:
            runs[guild_id] = [
                u for u in runs[guild_id] if u["user_id"] != interaction.user.id
            ]

        await self.update_message(interaction)

    async def update_message(self, interaction):
        guild_id = interaction.guild.id
        signups = runs.get(guild_id, [])

        selected, waitlist = sort_and_split(signups)

        embed = build_embed(selected, waitlist)

        await interaction.response.edit_message(embed=embed, view=self)


@bot.command()
async def run(ctx):
    """Creates a new run signup panel"""

    runs[ctx.guild.id] = []

    embed = discord.Embed(title="🏃 Run Signup")
    embed.description = "Click below to join or leave the run (max 8 players)."

    view = RunView(ctx.guild.id)

    await ctx.send(embed=embed, view=view)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")



bot.run(os.getenv("MTUwNTI1ODE1NjQ5MjU5MTEzOQ.GuBbiY.N8qRZMHdKBAXVx6i1xKa7Ax_9Sw4IPFPbCsKnQ"))