import os
import time
import sqlite3
import discord
import asyncio
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz

# ---------------------------
# CONFIG
# ---------------------------

EST = pytz.timezone("US/Eastern")
DEFAULT_TIMEZONE = "Europe/Berlin"

BOT_OWNER_ID = 218880619659132928
DOA_ROLE_ID = 1199301817738211338
JOKER_ID = 276430971123924994

GUILD_ID = 1159893108528517240 #low
#GUILD_ID = 633817926088130561 #mine

RUN_CHANNEL_ID = 1169288946707087440 #low
#RUN_CHANNEL_ID = 1505001264214315100 #mine

RUN_OPEN_HOUR = 6
RUN_OPEN_MINUTE = 0

RUN_CLOSE_HOUR = 14
RUN_CLOSE_MINUTE = 30

COOLDOWN_SECONDS = 1

last_close_date = None

# Prevents the scheduler from creating the daily run more than once
checkhasposted = 0

# Prevents the 15-minute reminder from being sent more than once
checkhaspinged = 0

user_cooldowns = {}

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

    if now > target:
        target += timedelta(days=1)

    delta = target - now

    total_seconds = int(delta.total_seconds())

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    return hours, minutes

def get_form_capacity(form_type):
    if form_type == "5-man":
        return 5

    if form_type in ("Deep", "Urgoz"):
        return 12

    return 8

def get_run_timestamp():

    now = datetime.now(EST)

    target = now.replace(
        hour=RUN_CLOSE_HOUR,
        minute=RUN_CLOSE_MINUTE,
        second=0,
        microsecond=0
    )

    if now > target:
        target += timedelta(days=1)

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


def is_officer(member):
    return any(role.name == "Officer" for role in member.roles)


# ---------------------------
# INTENTS
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

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
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    channel_id INTEGER,
    is_open INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_timezones (
    user_id INTEGER PRIMARY KEY,
    timezone TEXT NOT NULL
)
""")

conn.commit()

# General !form posts are stored separately from scheduled daily runs.
cursor.execute("""
CREATE TABLE IF NOT EXISTS form_state (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    channel_id INTEGER,
    form_type TEXT,
    close_timestamp INTEGER,
    show_close_time INTEGER,
    formed_pinged INTEGER DEFAULT 0
)
""")

try:
    cursor.execute("ALTER TABLE form_state ADD COLUMN form_type TEXT")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE form_state ADD COLUMN close_timestamp INTEGER")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE form_state ADD COLUMN show_close_time INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute(
        "ALTER TABLE form_state ADD COLUMN formed_pinged INTEGER DEFAULT 0"
    )
except sqlite3.OperationalError:
    pass

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


def set_run_state(message_id, guild_id, channel_id, is_open):

    cursor.execute("""
        INSERT OR REPLACE INTO run_state
        VALUES (?, ?, ?, ?)
    """, (
        message_id,
        guild_id,
        channel_id,
        int(is_open)
    ))

    conn.commit()


def get_run_state(message_id):

    cursor.execute("""
        SELECT guild_id, channel_id, is_open
        FROM run_state
        WHERE message_id=?
    """, (message_id,))

    return cursor.fetchone()


def get_latest_run(guild_id):

    cursor.execute("""
        SELECT message_id, channel_id, is_open
        FROM run_state
        WHERE guild_id=?
        ORDER BY message_id DESC
        LIMIT 1
    """, (guild_id,))

    return cursor.fetchone()


def set_open_state(message_id, is_open):

    cursor.execute("""
        UPDATE run_state
        SET is_open=?
        WHERE message_id=?
    """, (
        int(is_open),
        message_id
    ))

    conn.commit()


def set_form_state(
    message_id,
    guild_id,
    channel_id,
    form_type,
    close_timestamp=None,
    show_close_time=False
):

    cursor.execute("""
        INSERT OR REPLACE INTO form_state
        (message_id, guild_id, channel_id, form_type,
         close_timestamp, show_close_time, formed_pinged)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (
        message_id,
        guild_id,
        channel_id,
        form_type,
        close_timestamp,
        int(show_close_time)
    ))

    conn.commit()


def get_form_state(message_id):
    cursor.execute("""
        SELECT guild_id, channel_id, form_type,
               close_timestamp, show_close_time, formed_pinged
        FROM form_state
        WHERE message_id=?
    """, (message_id,))
    return cursor.fetchone()

def is_form_expired(message_id):
    state = get_form_state(message_id)

    if not state:
        return False

    _, _, _, close_timestamp, _, _ = state

    if close_timestamp is None:
        return False

    return int(time.time()) >= close_timestamp

def set_user_timezone(user_id, timezone_name):

    cursor.execute("""
        INSERT OR REPLACE INTO user_timezones
        (user_id, timezone)
        VALUES (?, ?)
    """, (
        user_id,
        timezone_name
    ))

    conn.commit()


def get_user_timezone(user_id):

    cursor.execute("""
        SELECT timezone
        FROM user_timezones
        WHERE user_id=?
    """, (user_id,))

    row = cursor.fetchone()

    if row:
        try:
            return pytz.timezone(row[0])
        except pytz.UnknownTimeZoneError:
            pass

    return pytz.timezone(DEFAULT_TIMEZONE)

# ---------------------------
# LOGIC
# ---------------------------
def sort_and_split(signups, capacity=8):

    sorted_list = sorted(
        signups,
        key=lambda x: (
            not x["guild_member"],
            x["time"]
        )
    )

    return sorted_list[:capacity], sorted_list[capacity:]


def build_embed(selected, waitlist, is_open):

    embed = discord.Embed(
        title="<:poggers:1413932730101665842> Guild Runs"
    )

    run_timestamp = get_run_timestamp()

    now = datetime.now(EST)

    current_minutes = now.hour * 60 + now.minute
    close_minutes = RUN_CLOSE_HOUR * 60 + RUN_CLOSE_MINUTE

    is_closed = current_minutes >= close_minutes

    if is_closed:

        timing = "🔴 CLOSED"

    else:

        timing = (
            f"Runs start <t:{run_timestamp}:t>\n"
            f"⏳ <t:{run_timestamp}:R>"
        )

    signup_count = len(selected)

    now = datetime.now(EST)

    current_minutes = now.hour * 60 + now.minute
    close_minutes = RUN_CLOSE_HOUR * 60 + RUN_CLOSE_MINUTE

    is_closed = current_minutes >= close_minutes

    if is_closed:
        status = "<:SearchingPepe:1318542083962830848>"

    elif signup_count == 0:
        status = "🔴 nobody ticked, I feel so lonely"

    elif signup_count >= 6:
        status = f"🟢 {signup_count} ticked"

    else:
        status = f"🟠 {signup_count} ticked"

    embed.description = timing

    roster = "\n".join(
        f"<@{u['user_id']}>"
        for u in selected
    )

    embed.add_field(
        name=status,
        value=roster,
        inline=False
    )

    # Only show the waitlist once someone is actually waiting
    if waitlist:
        wait = "\n".join(
            f"<@{u['user_id']}>"
            for u in waitlist
        )

        embed.add_field(
            name="<:fradge:1238196826784530452> Waitlist",
            value=wait,
            inline=False
        )

    return embed

def build_form_embed(
    selected,
    waitlist,
    form_type="Guild",
    close_timestamp=None,
    show_close_time=False
):

    embed = discord.Embed(
        title=f"<:poggers:1413932730101665842> Guild Form — {form_type}"
    )

    # Only display the close time if the creator
    # explicitly entered one.
    if show_close_time and close_timestamp is not None:

        now_timestamp = int(datetime.now(EST).timestamp())

        if now_timestamp >= close_timestamp:
            embed.description = "🔴 CLOSED"

        else:
            embed.description = (
                f"Closes <t:{close_timestamp}:t>\n"
                f"⏳ <t:{close_timestamp}:R>"
            )

    signup_count = len(selected)
    capacity = get_form_capacity(form_type)

    if signup_count >= capacity:
        status = "🟢 Formed"

    elif signup_count == 0:
        status = "🔴 nobody ticked"

    elif signup_count >= 6:
        status = f"🟢 {signup_count} ticked"

    else:
        status = f"🟠 {signup_count} ticked"

    roster = "\n".join(
        f"<@{u['user_id']}>"
        for u in selected
    )

    if not roster:
        roster = "\u200b"

    embed.add_field(
        name=status,
        value=roster,
        inline=False
    )

    if waitlist:

        wait = "\n".join(
            f"<@{u['user_id']}>"
            for u in waitlist
        )

        embed.add_field(
            name="<:fradge:1238196826784530452> Waitlist",
            value=wait,
            inline=False
        )

    return embed


class FormView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    async def refresh(self, interaction):

        signups = load_signups(interaction.message.id)

        state = get_form_state(interaction.message.id)

        if state:
            _, _, form_type, close_timestamp, show_close_time, formed_pinged = state
        else:
            form_type = "Guild"
            close_timestamp = None
            show_close_time = False
            formed_pinged = False

        capacity = get_form_capacity(form_type)

        selected, waitlist = sort_and_split(
            signups,
            capacity
        )

        embed = build_form_embed(
            selected,
            waitlist,
            form_type,
            close_timestamp,
            bool(show_close_time)
        )

        await interaction.message.edit(
            embed=embed
        )

        # Form once the correct capacity has been reached.
        if (
                len(selected) >= capacity
                and not formed_pinged
        ):

            mentions = " ".join(
                f"<@{u['user_id']}>"
                for u in selected
            )

            # Only one interaction is allowed to claim
            # the formation ping.
            cursor.execute("""
                UPDATE form_state
                SET formed_pinged=1
                WHERE message_id=?
                AND formed_pinged=0
            """, (interaction.message.id,))

            conn.commit()

            if cursor.rowcount == 1:
                await interaction.channel.send(
                    f"🟢 **{form_type} Formed!** {mentions}"
                )

                print(
                    f"{form_type} form {interaction.message.id} formed "
                    f"with {len(selected)} members."
                )

    @discord.ui.button(
        label="Join Form",
        style=discord.ButtonStyle.green,
        custom_id="join_form_button"
    )
    async def join(self, interaction, button):

        if is_form_expired(interaction.message.id):
            await interaction.response.send_message(
                "🔴 This form has closed.",
                ephemeral=True
            )

            return

        if not check_cooldown(interaction.user.id):

            await interaction.response.send_message(
                "⏳ Slow down — you’re clicking too fast.",
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
        label="Leave Form",
        style=discord.ButtonStyle.red,
        custom_id="leave_form_button"
    )
    async def leave(self, interaction, button):

        if is_form_expired(interaction.message.id):
            await interaction.response.send_message(
                "🔴 This form has closed.",
                ephemeral=True
            )

            return

        if not check_cooldown(interaction.user.id):

            await interaction.response.send_message(
                "⏳ Slow down — you’re clicking too fast.",
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
# VIEW
# ---------------------------
class RunView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    async def refresh(self, interaction):

        # Scheduled daily runs always use the normal 8-player
        # scheduled-run logic.
        signups = load_signups(interaction.message.id)

        selected, waitlist = sort_and_split(signups)

        state = get_run_state(interaction.message.id)

        if state:
            _, _, is_open = state
        else:
            is_open = False

        embed = build_embed(
            selected,
            waitlist,
            bool(is_open)
        )

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

        current = load_signups(interaction.message.id)

        if any(
            u["user_id"] == interaction.user.id
            for u in current
        ):

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

        await interaction.response.defer()

        remove_signup(
            interaction.message.id,
            interaction.user.id
        )

        await self.refresh(interaction)


# ---------------------------
# RUN MANAGEMENT
# ---------------------------
async def create_run(guild, channel=None):

    if channel is None:
        channel = guild.get_channel(RUN_CHANNEL_ID)

        if channel is None:
            channel = await guild.fetch_channel(RUN_CHANNEL_ID)

    if channel is None:
        print("Run channel not found.")
        return False

    embed = build_embed([], [], True)

    cursor.execute(
        "DELETE FROM run_state WHERE guild_id=?",
        (guild.id,)
    )
    conn.commit()

    msg = await channel.send(
        embed=embed,
        view=RunView()
    )

    set_run_state(
        msg.id,
        guild.id,
        channel.id,
        1
    )

    print(f"Run created in {guild.name}")

    return True


async def create_form(ctx):
    channel = ctx.channel

    # Custom forms expire after 24 hours
    now = datetime.now(EST)
    close_timestamp = int((now + timedelta(hours=24)).timestamp())

    # Do not display the hidden 24-hour expiration
    embed = build_form_embed(
        [],
        [],
        "Guild",
        close_timestamp,
        False
    )

    msg = await channel.send(
        embed=embed,
        view=FormView()
    )

    set_form_state(
        msg.id,
        ctx.guild.id,
        channel.id,
        "Guild",
        close_timestamp,
        False
    )

    print(f"General form created in {ctx.guild.name}")
    return True

async def refresh_run_message(guild):

    state = get_latest_run(guild.id)

    if not state:
        return

    message_id, channel_id, is_open = state
    if not is_open:
        return

    try:

        channel = guild.get_channel(channel_id)

        if channel is None:
            channel = await guild.fetch_channel(channel_id)

        msg = await channel.fetch_message(message_id)

    except:
        return

    signups = load_signups(message_id)

    selected, waitlist = sort_and_split(signups)

    embed = build_embed(
        selected,
        waitlist,
        bool(is_open)
    )

    try:
        await msg.edit(embed=embed)
    except:
        pass


async def close_run(guild):

    state = get_latest_run(guild.id)

    if not state:
        return

    message_id, channel_id, _ = state

    try:

        channel = guild.get_channel(channel_id)

        if channel is None:
            channel = await guild.fetch_channel(channel_id)

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

    embed.title = "<:poggers:1413932730101665842> Guild Runs"

    await message.edit(
        embed=embed,
        view=None
    )

    set_open_state(message_id, 0)

    print(f"Run closed in {guild.name}")


# ---------------------------
# TASK LOOPS
# ---------------------------
@tasks.loop(minutes=5)
async def refresh_loop():

    for guild in bot.guilds:
        await refresh_run_message(guild)

@tasks.loop(minutes=1)
async def form_close_loop():

    now_timestamp = int(time.time())

    cursor.execute("""
        SELECT message_id, guild_id, channel_id, form_type,
               close_timestamp, show_close_time
        FROM form_state
        WHERE close_timestamp IS NOT NULL
        AND close_timestamp <= ?
    """, (now_timestamp,))

    expired_forms = cursor.fetchall()

    for (
        message_id,
        guild_id,
        channel_id,
        form_type,
        close_timestamp,
        show_close_time
    ) in expired_forms:

        guild = bot.get_guild(guild_id)

        if guild is None:
            continue

        try:
            channel = guild.get_channel(channel_id)

            if channel is None:
                channel = await guild.fetch_channel(channel_id)

            message = await channel.fetch_message(message_id)

            signups = load_signups(message_id)

            capacity = get_form_capacity(form_type)

            selected, waitlist = sort_and_split(signups, capacity)

            embed = build_form_embed(
                selected,
                waitlist,
                form_type,
                close_timestamp,
                bool(show_close_time)
            )

            embed.description = "🔴 CLOSED"

            await message.edit(
                embed=embed,
                view=None
            )

            # Give Discord a moment before closing another form
            await asyncio.sleep(1)

            # IMPORTANT:
            # Remove it from the active form list so it isn't
            # processed again on the next loop.
            cursor.execute(
                "DELETE FROM form_state WHERE message_id=?",
                (message_id,)
            )
            conn.commit()

            print(
                f"Custom form {message_id} closed in {guild.name}"
            )

        except discord.NotFound:
            print(
                f"Custom form message {message_id} no longer exists."
            )

            # Also remove nonexistent forms from the database
            cursor.execute(
                "DELETE FROM form_state WHERE message_id=?",
                (message_id,)
            )
            conn.commit()

        except Exception as e:
            print(
                f"Could not close custom form {message_id}: {e}"
            )

@form_close_loop.before_loop
async def before_form_close_loop():

    await bot.wait_until_ready()

@tasks.loop(minutes=1)
async def start_run(guild, force=False):

    global checkhasposted

    # If today's run has already been posted, do nothing
    if checkhasposted == 1 and not force:
        return False

    # Create the run
    await create_run(guild)

    # Mark today's run as posted
    checkhasposted = 1

    return True

@tasks.loop(minutes=1)
async def scheduler():

    global last_close_date
    global checkhasposted
    global checkhaspinged

    now = datetime.now(EST)
    today = now.date()

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print("Configured guild not found.")
        return

    # -----------------------
    # RESET DAILY POST CHECK
    # -----------------------
    # Reset checkhasposted one hour before the run opens
    reset_hour = (RUN_OPEN_HOUR - 1) % 24

    if (
        now.hour == reset_hour
        and now.minute == RUN_OPEN_MINUTE
    ):
        checkhasposted = 0
        checkhaspinged = 0
        print("Daily run post check reset.")


    # -----------------------
    # 15 MINUTE RUN REMINDER
    # -----------------------
    reminder_time = (
        datetime.now(EST).replace(
            hour=RUN_CLOSE_HOUR,
            minute=RUN_CLOSE_MINUTE,
            second=0,
            microsecond=0
        ) - timedelta(minutes=15)
    )

    if (
        now.hour == reminder_time.hour
        and now.minute == reminder_time.minute
        and checkhaspinged == 0
    ):
        latest_run = get_latest_run(guild.id)

        if latest_run:
            message_id, channel_id, is_open = latest_run

            signups = load_signups(message_id)

            selected, waitlist = sort_and_split(signups)

            # Only remind if 6 or more people are in the run
            if len(selected) >= 6:

                mentions = " ".join(
                    f"<@{u['user_id']}>"
                    for u in selected
                )

                channel = guild.get_channel(channel_id)

                if channel is None:
                    channel = await guild.fetch_channel(channel_id)

                # 6 people
                if len(selected) == 6:
                    reminder_message = (
                        "6 people are ticked, maybe ping teach "
                        f"{mentions}"
                    )

                # 7 people
                elif len(selected) == 7:
                    reminder_message = (
                        "Just need 1 more "
                        f"{mentions}"
                    )

                # 8 people
                else:
                    reminder_message = (
                        "Run is full, 15 minute reminder "
                        f"{mentions}"
                    )

                await channel.send(reminder_message)

                checkhaspinged = 1

                print(
                    f"15-minute reminder sent in {guild.name} "
                    f"with {len(selected)} people."
                )

            else:
                print(
                    f"15-minute reminder skipped - "
                    f"only {len(selected)} people signed up."
                )


    # -----------------------
    # OPEN RUN
    # -----------------------
    if (
        now.hour == RUN_OPEN_HOUR
        and now.minute == RUN_OPEN_MINUTE
        and checkhasposted == 0
    ):
        created = await start_run(guild)

        if created:
            print(f"Opened run in {guild.name}")
        else:
            print("Run was not created.")

    # -----------------------
    # CLOSE RUN
    # -----------------------
    latest_run = get_latest_run(guild.id)

    if (
        now.hour == RUN_CLOSE_HOUR
        and RUN_CLOSE_MINUTE <= now.minute < RUN_CLOSE_MINUTE + 2
        and last_close_date != today
        and latest_run
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
        await ctx.send("❌ Officer role required.")
        return

    created = await create_run(ctx.guild, channel=ctx.channel)

    if not created:
        await ctx.send("A run is already open.")

@bot.tree.command(
    name="timezone",
    description="Set your timezone for scheduling forms"
)
@discord.app_commands.describe(
    timezone="Choose your timezone"
)
@discord.app_commands.choices(
    timezone=[
        discord.app_commands.Choice(
            name="CET / Berlin",
            value="Europe/Berlin"
        ),
        discord.app_commands.Choice(
            name="Bulgaria / Sofia",
            value="Europe/Sofia"
        ),
        discord.app_commands.Choice(
            name="UK / London",
            value="Europe/London"
        ),
        discord.app_commands.Choice(
            name="Eastern / New York",
            value="America/New_York"
        ),
        discord.app_commands.Choice(
            name="Central / Chicago",
            value="America/Chicago"
        ),
        discord.app_commands.Choice(
            name="Mountain / Denver",
            value="America/Denver"
        ),
        discord.app_commands.Choice(
            name="Pacific / Los Angeles",
            value="America/Los_Angeles"
        ),
        discord.app_commands.Choice(
            name="UTC",
            value="UTC"
        )
    ]
)
async def timezone(
    interaction: discord.Interaction,
    timezone: discord.app_commands.Choice[str]
):

    timezone_name = timezone.value

    # Verify that the timezone is valid
    try:
        pytz.timezone(timezone_name)
    except pytz.UnknownTimeZoneError:
        await interaction.response.send_message(
            "❌ Invalid timezone.",
            ephemeral=True
        )
        return

    set_user_timezone(
        interaction.user.id,
        timezone_name
    )

    await interaction.response.send_message(
        f"✅ Your timezone has been set to **{timezone.name}**.",
        ephemeral=True
    )

    print(
        f"{interaction.user} set timezone to {timezone_name}"
    )

@bot.tree.command(
    name="form",
    description="Create a custom Guild form"
)
@discord.app_commands.describe(
    form_type="Choose the type of run",
    form_time="Optional close time, e.g. 14:30",
    ping="Ping the DoA role"
)
@discord.app_commands.choices(
    form_type=[
        discord.app_commands.Choice(name="6-0", value="6-0"),
        discord.app_commands.Choice(name="RoJ", value="RoJ"),
        discord.app_commands.Choice(name="Offmeta", value="Offmeta"),
        discord.app_commands.Choice(name="5-man", value="5-man"),
        discord.app_commands.Choice(name="UW", value="UW"),
        discord.app_commands.Choice(name="FoW", value="FoW"),
        discord.app_commands.Choice(name="Deep", value="Deep"),
        discord.app_commands.Choice(name="Urgoz", value="Urgoz")
    ],
    ping=[
        discord.app_commands.Choice(name="No", value="no"),
        discord.app_commands.Choice(name="Yes", value="yes")
    ]
)
async def slash_form(
    interaction: discord.Interaction,
    form_type: discord.app_commands.Choice[str],
    form_time: str = None,
    ping: discord.app_commands.Choice[str] = None
):

    # Members and Officers can create forms
    if not is_officer(interaction.user) and not is_guild_member(interaction.user):
        await interaction.response.send_message(
            "❌ Member role required.",
            ephemeral=True
        )
        return

    user_timezone = get_user_timezone(interaction.user.id)

    now = datetime.now(user_timezone)

    # Every custom form has a hidden maximum lifetime of 24 hours
    max_close_time = now + timedelta(hours=24)

    # Default: no close time is displayed
    show_close_time = False

    # Default hidden expiration is 24 hours
    close_timestamp = int(max_close_time.timestamp())

    # If a time was supplied, use that as the displayed close time
    if form_time:
        try:
            parsed_time = datetime.strptime(form_time, "%H:%M")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid time. Use 24-hour format such as `14:30`.",
                ephemeral=True
            )
            return

        close_time = user_timezone.localize(
            datetime(
                now.year,
                now.month,
                now.day,
                parsed_time.hour,
                parsed_time.minute
            )
        )

        # If the requested time already passed today,
        # interpret it as tomorrow
        if close_time <= now:
            close_time += timedelta(days=1)

        # Never allow a form to live longer than 24 hours
        if close_time > max_close_time:
            close_time = max_close_time

        close_timestamp = int(close_time.timestamp())
        show_close_time = True

    # Build the initial embed
    embed = build_form_embed(
        [],
        [],
        form_type.value,
        close_timestamp,
        show_close_time
    )

    await interaction.response.defer()

    # Send the form itself
    msg = await interaction.channel.send(
        embed=embed,
        view=FormView()
    )

    # Save the custom form
    set_form_state(
        msg.id,
        interaction.guild.id,
        interaction.channel.id,
        form_type.value,
        close_timestamp,
        show_close_time
    )

    # Ping DoA if requested
    if ping and ping.value == "yes":
        await interaction.channel.send(
            f"<@&{DOA_ROLE_ID}>"
        )

    await interaction.followup.send(
        f"Created `{form_type.value}` form.",
        ephemeral=True
    )

    print(
        f"Custom form created in {interaction.guild.name}: "
        f"{form_type.value}, close={close_timestamp}, "
        f"ping={ping.value if ping else 'no'}"
    )

@bot.command()
async def form(ctx):

    if not is_officer(ctx.author) and not is_guild_member(ctx.author):
        await ctx.send("❌ Member role required.")
        return

    created = await create_form(ctx)

    if not created:
        await ctx.send("Could not create the form.")

@bot.command()
async def add(ctx, target: str):

    # Only the bot owner can use this command
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Improper credentials idiot")
        return

    # Try to get the member from a mention
    member = None

    if ctx.message.mentions:
        member = ctx.message.mentions[0]

    # If no mention, try to interpret the target as a Discord user ID
    elif target.isdigit():
        member = ctx.guild.get_member(int(target))

    if member is None:
        await ctx.send(
            "Couldn't find that user. Use @mention or their Discord user ID."
        )
        return

    # Get the latest run
    latest_run = get_latest_run(ctx.guild.id)

    if not latest_run:
        await ctx.send("There is no run form.")
        return

    message_id, channel_id, is_open = latest_run

    # Don't allow adding people to a closed run
    if not is_open:
        await ctx.send("The current run is closed.")
        return

    # Check if the user is already on the form
    current = load_signups(message_id)

    if any(u["user_id"] == member.id for u in current):
        await ctx.send(f"{member.name} is already ticked.")
        return

    # Add them using the same system as the Join Run button
    add_signup(
        message_id,
        member.id,
        member.name,
        is_guild_member(member)
    )

    # Refresh the form
    await refresh_run_message(ctx.guild)

    # Don't ping the user in the private command channel
    await ctx.send(f"Added {member.name} to the run.")

@bot.command()
async def remove(ctx, target: str):

    # Only the bot owner can use this command
    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Improper credentials idiot")
        return

    # Try to get the member from a mention
    member = None

    if ctx.message.mentions:
        member = ctx.message.mentions[0]

    # If no mention, try to interpret the target as a Discord user ID
    elif target.isdigit():
        member = ctx.guild.get_member(int(target))

    if member is None:
        await ctx.send(
            "Couldn't find that user. Use @mention or their Discord user ID."
        )
        return

    # Get the latest run
    latest_run = get_latest_run(ctx.guild.id)

    if not latest_run:
        await ctx.send("There is no run form.")
        return

    message_id, channel_id, is_open = latest_run

    # Don't allow removing people from a closed run
    if not is_open:
        await ctx.send("The current run is closed.")
        return

    # Check if the user is actually on the form
    current = load_signups(message_id)

    if not any(u["user_id"] == member.id for u in current):
        await ctx.send(f"{member.name} isn't currently ticked.")
        return

    # Remove them
    remove_signup(
        message_id,
        member.id
    )

    # Refresh the form
    await refresh_run_message(ctx.guild)

    # Don't ping the user in the private command channel
    await ctx.send(f"Removed {member.name} from the run.")

@bot.command()
async def clearvars(ctx):

    global checkhasposted
    global checkhaspinged

    if ctx.author.id != BOT_OWNER_ID:
        await ctx.send("Improper credentials idiot")
        return

    checkhasposted = 0
    checkhaspinged = 0

    await ctx.send(
        "Check variables cleared.\n"
        "checkhasposted = 0\n"
        "checkhaspinged = 0"
    )

# ---------------------------
# MEME COMMANDS
# ---------------------------
@bot.command()
async def deleteserver(ctx):

    if ctx.author.id != BOT_OWNER_ID:

        await ctx.send("Improper credentials idiot")

        return

    await ctx.send("Initiating server deletion protocol")

    await asyncio.sleep(5)

    await ctx.send("Server will be deleted in 3")

    await asyncio.sleep(1)

    await ctx.send("Server will be deleted in 2")

    await asyncio.sleep(1)

    await ctx.send("Server will be deleted in 1")

    await asyncio.sleep(5)

    await ctx.send("Wait that didn't work?")


@bot.command()
async def rallytroops(ctx):

    if ctx.author.id != BOT_OWNER_ID:

        await ctx.send("Improper credentials idiot")

        return

    await ctx.send(
        f"Hey <@&{DOA_ROLE_ID}> we need a few more people on the form, "
        f"I'd appreciate it if you gave me some yummy tickies"
    )


@bot.command()
async def whereis(ctx, member: discord.Member):

    if ctx.author.id != BOT_OWNER_ID:

        await ctx.send("Ping them yourself idiot")

        return

    await ctx.send(
        f"Hey {member.mention} why are you taking so long"
    )

@bot.command()
async def remindjoker(ctx):

    await ctx.send(
        f"<@{JOKER_ID}> hey you're still ticked and also I think it's about time I get "
        f"a raise for all my hard work and contributions you can help me out by going to "
        f"your nearest grocery store and buying some apple itunes gift cards and messaging "
        f"me the codes do not redeem the cards"
    )


async def restore_current_run():
    global checkhasposted
    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print("Configured guild not found.")
        return

    state = get_latest_run(guild.id)

    if not state:
        print("No current run to restore.")
        return

    message_id, channel_id, is_open = state

    if is_open:
        checkhasposted = 1

    if not is_open:
        print("Current run is closed. Nothing to restore.")
        return

    try:
        channel = guild.get_channel(channel_id)

        if channel is None:
            channel = await guild.fetch_channel(channel_id)

        message = await channel.fetch_message(message_id)

        # Reattach the persistent buttons
        await message.edit(view=RunView())

        # Reload signups from SQLite and refresh the embed
        signups = load_signups(message_id)

        selected, waitlist = sort_and_split(signups)

        embed = build_embed(
            selected,
            waitlist,
            True
        )

        await message.edit(
            embed=embed,
            view=RunView()
        )

        print(
            f"Restored current run in {guild.name} "
            f"with {len(signups)} signups."
        )

    except Exception as e:
        print(f"Could not restore current run: {e}")


# ---------------------------
# READY
# ---------------------------
@bot.event
async def on_ready():

    print(f"Logged in as {bot.user}")

    bot.add_view(RunView())
    bot.add_view(FormView())

    try:
        synced = await bot.tree.sync()

        print(
            f"Synced {len(synced)} global slash commands."
        )

        for command in synced:
            print(f"  /{command.name}")

    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

    await restore_current_run()

    if not refresh_loop.is_running():
        refresh_loop.start()

    if not scheduler.is_running():
        scheduler.start()

    if not form_close_loop.is_running():
        form_close_loop.start()

# ---------------------------
# START BOT
# ---------------------------

#
bot.run(os.getenv("DISCORD_TOKEN"))