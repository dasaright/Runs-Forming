import os
import time
import sqlite3
import discord
from discord.ext import commands

# ---------------------------
# INTENTS
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------
# DATABASE (PERSISTENT)
# ---------------------------
conn = sqlite3.connect("runs.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS signups (
    guild_id INTEGER,
    user_id INTEGER,
    username TEXT,
    guild_member INTEGER,
    timestamp REAL
)
""")
conn.commit()


# ---------------------------
# HELPERS
# ---------------------------
def is_guild_member(member: discord.Member):
    return any(role.name == "Member" for role in member.roles)


def add_signup(guild_id, user_id, username, guild_member):
    cursor.execute("""
        INSERT INTO signups (guild_id, user_id, username, guild_member, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (guild_id, user_id, username, int(guild_member), time.time()))
    conn.commit()


def remove_signup(guild_id, user_id):
    cursor.execute("""
        DELETE FROM signups
        WHERE guild_id = ? AND user_id = ?
    """, (guild_id, user_id))
    conn.commit()


def load_signups(guild_id):
    cursor.execute("""
        SELECT user_id, username, guild_member, timestamp
        FROM signups
        WHERE guild_id = ?
    """, (guild_id,))

    rows = cursor.fetchall()

    return [
        {
            "user_id": r[0],
            "username": r[1],
            "guild_member": bool(r[2]),
            "time": r[3]
        }
        for r in rows
    ]


def sort_and_split(signups):
    sorted_list = sorted(
        signups,
        key=lambda x: (
            not x["guild_member"],  # guild first
            x["time"]               # FIFO
        )
    )

    return sorted_list[:8], sorted_list[8:]


def build_embed(selected, waitlist):
    embed = discord.Embed(title="🏃 Run Signup System")

    roster = "\n".join(
        f"{i+1}. <@{u['user_id']}>" for i, u in enumerate(selected)
    ) or "None"

    wait = "\n".join(
        f"{i+1}. <@{u['user_id']}>" for i, u in enumerate(waitlist)
    ) or "None"

    embed.add_field(name="✅ Selected (Max 8)", value=roster, inline=False)
    embed.add_field(name="⏳ Waitlist", value=wait, inline=False)

    return embed


# ---------------------------
# UI VIEW (BUTTONS)
# ---------------------------
class RunView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def get_data(self, guild_id):
        return load_signups(guild_id)

    async def refresh(self, interaction: discord.Interaction):
        signups = self.get_data(interaction.guild.id)

        selected, waitlist = sort_and_split(signups)
        embed = build_embed(selected, waitlist)

        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Join Run", style=discord.ButtonStyle.green)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):

        guild_id = interaction.guild.id

        # prevent duplicates
        current = load_signups(guild_id)
        if any(u["user_id"] == interaction.user.id for u in current):
            await interaction.response.send_message(
                "You're already signed up.", ephemeral=True
            )
            return

        add_signup(
            guild_id,
            interaction.user.id,
            interaction.user.name,
            is_guild_member(interaction.user)
        )

        await interaction.response.defer()
        await self.refresh(interaction)

    @discord.ui.button(label="Leave Run", style=discord.ButtonStyle.red)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):

        remove_signup(interaction.guild.id, interaction.user.id)

        await interaction.response.defer()
        await self.refresh(interaction)


# ---------------------------
# COMMANDS
# ---------------------------
@bot.command()
async def run(ctx):
    """Create a run signup panel"""

    embed = discord.Embed(
        title="🏃 Run Signup",
        description="Click below to join or leave. Max 8 selected."
    )

    view = RunView()

    await ctx.send(embed=embed, view=view)


# ---------------------------
# READY EVENT
# ---------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


# ---------------------------
# START BOT (RAILWAY SAFE)
# ---------------------------
bot.run(os.getenv("DISCORD_TOKEN"))