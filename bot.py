import os
import time
import sqlite3
import discord
from discord.ext import commands, tasks
from datetime import datetime, time as dtime
import pytz

# ---------------------------
# CONFIG
# ---------------------------
EST = pytz.timezone("US/Eastern")

RUN_CHANNEL_ID = 1505001264214315100

RUN_OPEN_HOUR = 6
RUN_OPEN_MINUTE = 0

def get_time_until_open():
    now = datetime.now(EST)

    today_open = now.replace(
        hour=RUN_OPEN_HOUR,
        minute=RUN_OPEN_MINUTE,
        second=0,
        microsecond=0
    )

    # if we are past today's open, use next day
    if now > today_open:
        today_open = today_open.replace(day=now.day + 1)

    delta = today_open - now

    total_seconds = int(delta.total_seconds())

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    return hours, minutes

RUN_CLOSE_HOUR = 14
RUN_CLOSE_MINUTE = 30

user_cooldowns = {}
COOLDOWN_SECONDS = 5

# ---------------------------
# INTENTS
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
active_messages = {}  # guild_id -> message object

# ---------------------------
# DB
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS run_state (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    message_id INTEGER,
    is_open INTEGER
)
""")

conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER
)
""")
conn.commit()

# ---------------------------
# HELPERS
# ---------------------------

def check_cooldown(user_id):
    now = time.time()

    last = user_cooldowns.get(user_id, 0)

    if now - last < COOLDOWN_SECONDS:
        return False

    user_cooldowns[user_id] = now
    return True

def is_guild_member(member):
    return any(role.name == "Member" for role in member.roles)


def add_signup(guild_id, user_id, username, guild_member):
    cursor.execute("""
        INSERT INTO signups VALUES (?, ?, ?, ?, ?)
    """, (guild_id, user_id, username, int(guild_member), time.time()))
    conn.commit()


def remove_signup(guild_id, user_id):
    cursor.execute("""
        DELETE FROM signups WHERE guild_id=? AND user_id=?
    """, (guild_id, user_id))
    conn.commit()


def load_signups(guild_id):
    cursor.execute("""
        SELECT user_id, username, guild_member, timestamp
        FROM signups WHERE guild_id=?
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


def set_run_state(guild_id, channel_id, message_id, is_open):
    cursor.execute("""
        INSERT OR REPLACE INTO run_state
        VALUES (?, ?, ?, ?)
    """, (guild_id, channel_id, message_id, is_open))
    conn.commit()


def get_run_state(guild_id):
    cursor.execute("""
        SELECT channel_id, message_id, is_open
        FROM run_state WHERE guild_id=?
    """, (guild_id,))
    return cursor.fetchone()


def set_open_state(guild_id, is_open):
    cursor.execute("""
        UPDATE run_state SET is_open=?
        WHERE guild_id=?
    """, (int(is_open), guild_id))
    conn.commit()

def is_officer(member: discord.Member):
    return any(role.name == "Officer" for role in member.roles)


@bot.command()
async def testrun(ctx):

    if not is_officer(ctx.author):
        await ctx.send("❌ Officer role required.")
        return

    guild = ctx.guild

    # manually trigger run creation
    await create_run(guild)

    await ctx.send("🧪 Test run created.")

# ---------------------------
# LOGIC
# ---------------------------
def sort_and_split(signups):
    sorted_list = sorted(
        signups,
        key=lambda x: (
            not x["guild_member"],
            x["time"]
        )
    )
    return sorted_list[:8], sorted_list[8:]


def build_embed(selected, waitlist, is_open):
    embed = discord.Embed(title=":poggers: Guild Runs")

    hours, minutes = get_time_until_open()

    status = "🟢 runs begin at <t:1778956219:t> ({hours} hours {minutes} minutes)" if is_open else "🔴 CLOSED"
    embed.description = f"Status: **{status}**"

    roster = "\n".join(
        f"{i+1}. <@{u['user_id']}>" for i, u in enumerate(selected)
    ) or "None"

    wait = "\n".join(
        f"{i+1}. <@{u['user_id']}>" for i, u in enumerate(waitlist)
    ) or "None"

    embed.add_field(name="✅ Selected (8 max)", value=roster, inline=False)
    embed.add_field(name="⏳ Waitlist", value=wait, inline=False)

    return embed


# ---------------------------
# VIEW
# ---------------------------
class RunView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def is_open(self, guild_id):
        state = get_run_state(guild_id)
        return state and state[2] == 1

    async def refresh(self, interaction):
        signups = load_signups(interaction.guild.id)
        selected, waitlist = sort_and_split(signups)

        state = get_run_state(interaction.guild.id)
        is_open = state and state[2] == 1

        embed = build_embed(selected, waitlist, is_open)
        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Join Run", style=discord.ButtonStyle.green)
    async def join(self, interaction, button):

        if not check_cooldown(interaction.user.id):
            await interaction.response.send_message(
                "⏳ Slow down — you’re clicking too fast.",
                ephemeral=True
            )
            return

        state = get_run_state(interaction.guild.id)
        if not state or state[2] == 0:
            await interaction.response.send_message(
                "Run is closed.", ephemeral=True
            )
            return

        current = load_signups(interaction.guild.id)

        if any(u["user_id"] == interaction.user.id for u in current):
            await interaction.response.send_message(
                "Already signed up.", ephemeral=True
            )
            return

        add_signup(
            interaction.guild.id,
            interaction.user.id,
            interaction.user.name,
            is_guild_member(interaction.user)
        )

        await interaction.response.defer()
        await self.refresh(interaction)

    @discord.ui.button(label="Leave Run", style=discord.ButtonStyle.red)
    async def leave(self, interaction, button):

        if not check_cooldown(interaction.user.id):
            await interaction.response.send_message(
                "⏳ Slow down — you’re clicking too fast.",
                ephemeral=True
            )
            return

        state = get_run_state(interaction.guild.id)
        if not state or state[2] == 0:
            await interaction.response.send_message(
                "Run is closed.", ephemeral=True
            )
            return

        remove_signup(interaction.guild.id, interaction.user.id)

        await interaction.response.defer()
        await self.refresh(interaction)


# ---------------------------
# SCHEDULE LOOP
# ---------------------------

@tasks.loop(minutes=5)
async def refresh_loop():

    for guild in bot.guilds:
        await refresh_run_message(guild)

@tasks.loop(minutes=1)
async def scheduler():

    now = datetime.now(EST)

    guilds = bot.guilds

    for guild in guilds:

        # OPEN RUN (6:00 AM)
        if now.hour == RUN_OPEN_HOUR and now.minute == RUN_OPEN_MINUTE:
            await create_run(guild)

        # CLOSE RUN (2:30 PM)
        if now.hour == RUN_CLOSE_HOUR and now.minute == RUN_CLOSE_MINUTE:
            await close_run(guild)


async def create_run(guild):

    print("CREATE RUN TRIGGERED")

    channel = guild.get_channel(RUN_CHANNEL_ID)

    if channel is None:
        channel = await guild.fetch_channel(RUN_CHANNEL_ID)

    if channel is None:
        print("Run channel not found")
        return

    cursor.execute("DELETE FROM signups WHERE guild_id=?", (guild.id,))
    conn.commit()

    embed = discord.Embed(
        title="🏃 Daily Run Open!",
        description="Signups are now OPEN. Max 8 players."
    )

    msg = await channel.send(embed=embed, view=RunView())

    active_messages[guild.id] = msg

    set_run_state(guild.id, channel.id, msg.id, 1)

async def refresh_run_message(guild):
    if guild.id not in active_messages:
        return

    msg = active_messages[guild.id]

    signups = load_signups(guild.id)
    selected, waitlist = sort_and_split(signups)

    state = get_run_state(guild.id)
    is_open = state and state[2] == 1

    embed = build_embed(selected, waitlist, is_open)

    try:
        await msg.edit(embed=embed)
    except:
        pass

async def close_run(guild):
    state = get_run_state(guild.id)
    if not state:
        return

    channel_id, message_id, _ = state

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
    except:
        return

    signups = load_signups(guild.id)
    selected, waitlist = sort_and_split(signups)

    embed = build_embed(selected, waitlist, False)
    embed.title = "🏃 Daily Run CLOSED"

    await message.edit(embed=embed, view=None)

    set_open_state(guild.id, 0)


# ---------------------------
# READY
# ---------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.add_view(RunView())
    refresh_loop.start()
    scheduler.start()

# ---------------------------
# RUN BOT
# ---------------------------
bot.run(os.getenv("DISCORD_TOKEN"))