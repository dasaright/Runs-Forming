import os
import time
import sqlite3
import discord
from discord.ext import commands, tasks
from datetime import datetime, time as dtime, timedelta
import pytz

# ---------------------------
# CONFIG
# ---------------------------
EST = pytz.timezone("US/Eastern")

#RUN_CHANNEL_ID = 1169288946707087440 #low
RUN_CHANNEL_ID = 1505001264214315100 #mine

RUN_OPEN_HOUR = 8
RUN_OPEN_MINUTE = 15

RUN_CLOSE_HOUR = 14
RUN_CLOSE_MINUTE = 30

COOLDOWN_SECONDS = 1

last_run_date = None
last_close_date = None

# ---------------------------
# HELPERS
# ---------------------------
def get_time_until_open():

    now = datetime.now(EST)

    target = now.replace(
        hour=RUN_CLOSE_HOUR,
        minute=RUN_CLOSE_MINUTE,
        second=0,
        microsecond=0
    )

    # if already past today's close, use tomorrow
    if now > target:
        target = target + timedelta(days=1)

    delta = target - now

    total_seconds = int(delta.total_seconds())

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    return hours, minutes


user_cooldowns = {}

def get_run_timestamp():

    now = datetime.now(EST)

    target = now.replace(
        hour=RUN_CLOSE_HOUR,
        minute=RUN_CLOSE_MINUTE,
        second=0,
        microsecond=0
    )

    # if today's run already passed, use tomorrow
    if now > target:
        target = target + timedelta(days=1)

    return int(target.timestamp())


def check_cooldown(user_id):
    now = time.time()

    last = user_cooldowns.get(user_id, 0)

    if now - last < COOLDOWN_SECONDS:
        return False

    user_cooldowns[user_id] = now
    return True


def is_guild_member(member):
    return any(role.name == "Member" for role in member.roles)


def is_officer(member: discord.Member):
    return any(role.name == "Officer" for role in member.roles)


# ---------------------------
# INTENTS
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

active_messages = {}

# ---------------------------
# DATABASE
# ---------------------------
conn = sqlite3.connect("runs.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS signups (
    message_id INTEGER,
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

# ---------------------------
# DATABASE HELPERS
# ---------------------------
def add_signup(message_id, user_id, username, guild_member):

    cursor.execute("""
        INSERT INTO signups
        VALUES (?, ?, ?, ?, ?)
    """, (
        message_id,
        user_id,
        username,
        int(guild_member),
        time.time()
    ))

    conn.commit()


def remove_signup(message_id, user_id):

    cursor.execute("""
        DELETE FROM signups
        WHERE message_id=? AND user_id=?
    """, (
        message_id,
        user_id
    ))

    conn.commit()


def load_signups(message_id):

    cursor.execute("""
        SELECT user_id, username, guild_member, timestamp
        FROM signups
        WHERE message_id=?
    """, (message_id,))

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
    """, (
        guild_id,
        channel_id,
        message_id,
        int(is_open)
    ))
    conn.commit()


def get_run_state(guild_id):
    cursor.execute("""
        SELECT channel_id, message_id, is_open
        FROM run_state
        WHERE guild_id=?
    """, (guild_id,))

    return cursor.fetchone()


def set_open_state(guild_id, is_open):
    cursor.execute("""
        UPDATE run_state
        SET is_open=?
        WHERE guild_id=?
    """, (
        int(is_open),
        guild_id
    ))
    conn.commit()


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

    hours, minutes = get_time_until_open()

    embed = discord.Embed(
        title="<:poggers:1413932730101665842> Guild Runs"
    )

    run_timestamp = get_run_timestamp()
    timing = (
        f"Runs start <t:{run_timestamp}:t>\n"
        f"⏳ <t:{run_timestamp}:R>"
        if is_open else
        "🔴 CLOSED"
    )

    signup_count = len(selected)

    now_ts = int(datetime.now(EST).timestamp())

    if now_ts >= run_timestamp:
        status = "🔴 CLOSED"

    elif signup_count == 0:
        status = f"🔴 no tickers spotted"


    elif signup_count >= 6:
        status = f"🟢 {signup_count} ticked"

    else:
        status = f"🟠 {signup_count} ticked"

    embed.description = f"{timing}\n"

    roster = "\n".join(
        f"<@{u['user_id']}>"
        for u in selected
    ) or "None"

    wait = "\n".join(
        f"<@{u['user_id']}>"
        for u in waitlist
    ) or "None"

    embed.add_field(
        name=status,
        value=roster,
        inline=False
    )

    embed.add_field(
        name="⏳ Waitlist",
        value=wait,
        inline=False
    )

    return embed


# ---------------------------
# VIEW
# ---------------------------
class RunView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    async def refresh(self, interaction):

        signups = load_signups(interaction.message.id)

        selected, waitlist = sort_and_split(signups)

        state = get_run_state(interaction.guild.id)

        is_open = state and state[2] == 1

        embed = build_embed(selected, waitlist, is_open)

        await interaction.message.edit(embed=embed)

    @discord.ui.button(
        label="Join Run",
        style=discord.ButtonStyle.green,
        custom_id="join_run_button"
    )
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
                "Run is closed.",
                ephemeral=True
            )
            return

        current = load_signups(interaction.message.id)

        if any(u["user_id"] == interaction.user.id for u in current):
            await interaction.response.send_message(
                "Already signed up.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        add_signup(
            interaction.message.id,
            interaction.user.id,
            interaction.user.name,
            is_guild_member(interaction.user)
        )

        await self.refresh(interaction)

    @discord.ui.button(
        label="Leave Run",
        style=discord.ButtonStyle.red,
        custom_id="leave_run_button"
    )
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
                "Run is closed.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        remove_signup(
            interaction.message.id,
            interaction.user.id
        )



        await self.refresh(interaction)


# ---------------------------
# RUN MANAGEMENT
# ---------------------------
async def create_run(guild):

    channel = guild.get_channel(RUN_CHANNEL_ID)

    if channel is None:
        channel = await guild.fetch_channel(RUN_CHANNEL_ID)

    if channel is None:
        print("Run channel not found.")
        return

    embed = build_embed([], [], True)

    msg = await channel.send(
        embed=embed,
        view=RunView()
    )

    set_run_state(
        guild.id,
        channel.id,
        msg.id,
        1
    )

    active_messages[guild.id] = msg

    print(f"Run created in {guild.name}")


async def refresh_run_message(guild):

    state = get_run_state(guild.id)

    if not state:
        return

    channel_id, message_id, is_open = state

    try:
        channel = guild.get_channel(channel_id)

        if channel is None:
            channel = await guild.fetch_channel(channel_id)

        if channel is None:
            return

        msg = await channel.fetch_message(message_id)

    except:
        return

    signups = load_signups(message_id)

    selected, waitlist = sort_and_split(signups)

    embed = build_embed(
        selected,
        waitlist,
        is_open
    )

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

    if channel is None:
        channel = await guild.fetch_channel(channel_id)

    if channel is None:
        return

    try:
        message = await channel.fetch_message(message_id)
    except:
        return

    signups = load_signups(message_id)

    selected, waitlist = sort_and_split(signups)

    embed = build_embed(
        selected,
        waitlist,
        False
    )

    embed.title = "🏃 Daily Run CLOSED"

    await message.edit(
        embed=embed,
        view=None
    )

    set_open_state(guild.id, 0)

    print(f"Run closed in {guild.name}")


# ---------------------------
# TASK LOOPS
# ---------------------------
@tasks.loop(minutes=5)
async def refresh_loop():

    for guild in bot.guilds:
        await refresh_run_message(guild)

@tasks.loop(minutes=1)
async def scheduler():

    global last_run_date
    global last_close_date

    now = datetime.now(EST)

    today = now.date()

    for guild in bot.guilds:

        if (
                now.hour == RUN_OPEN_HOUR
                and now.minute >= RUN_OPEN_MINUTE
                and now.minute < RUN_OPEN_MINUTE + 2
                and last_run_date != today
        ):
            last_run_date = today

            await create_run(guild)

        # CLOSE RUN
        if (
                now.hour == RUN_CLOSE_HOUR
                and now.minute >= RUN_CLOSE_MINUTE
                and now.minute < RUN_CLOSE_MINUTE + 2
                and last_close_date != today
        ):
            last_close_date = today

            await close_run(guild)

@scheduler.before_loop
async def before_scheduler():
    await bot.wait_until_ready()

@refresh_loop.before_loop
async def before_refresh():
    await bot.wait_until_ready()

# ---------------------------
# COMMANDS
# ---------------------------
@bot.command()
async def testrun(ctx):

    if not is_officer(ctx.author):
        await ctx.send(
            "❌ Officer role required."
        )
        return

    await create_run(ctx.guild)

# ---------------------------
# READY
# ---------------------------
@bot.event
async def on_ready():

    print(f"Logged in as {bot.user}")

    bot.add_view(RunView())

    if not refresh_loop.is_running():
        refresh_loop.start()

    if not scheduler.is_running():
        scheduler.start()


# ---------------------------
# START BOT
# ---------------------------
bot.run(os.getenv("DISCORD_TOKEN"))