import os
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

SPAM_LIMIT = 20
SPAM_WINDOW = 15
CHANNEL_CREATE_LIMIT = 5
CHANNEL_DELETE_LIMIT = 5
MESSAGE_DELETE_LIMIT = 25
ACTION_WINDOW = 10

enabled_guilds = set()
spam_history = defaultdict(deque)
channel_creates = defaultdict(deque)
channel_deletes = defaultdict(deque)
message_deletes = defaultdict(deque)
emergency_running = set()


def now_ts():
    return discord.utils.utcnow().timestamp()


def prune(history, window):
    cutoff = now_ts() - window
    while history and history[0][0] < cutoff:
        history.popleft()


def can_manage(guild, member):
    me = guild.me
    return me is not None and member.top_role < me.top_role


async def find_audit_actor(guild, action, target_id=None):
    try:
        async for entry in guild.audit_logs(limit=15, action=action):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() > 10:
                continue
            if target_id is None or getattr(entry.target, "id", None) == target_id:
                return entry.user
    except (discord.Forbidden, discord.HTTPException):
        pass
    return None


async def send_log(guild, title, description, color=discord.Color.red()):
    channel = discord.utils.get(guild.text_channels, name="anti-raid-logs")
    if channel is None:
        return
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


async def emergency_response(guild, actor, reason):
    if guild.id in emergency_running:
        return

    emergency_running.add(guild.id)
    try:
        actor_text = (
            f"{actor} (`{actor.id}`)" if actor else "No identificado"
        )

        # Elimina canales creados recientemente por el responsable.
        if actor:
            try:
                async for entry in guild.audit_logs(
                    limit=100,
                    action=discord.AuditLogAction.channel_create,
                ):
                    age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                    if entry.user.id != actor.id or age > ACTION_WINDOW:
                        continue

                    channel = entry.target
                    if isinstance(channel, discord.abc.GuildChannel):
                        try:
                            await channel.delete(
                                reason="Anti-rad.Bot: canal creado durante ataque"
                            )
                        except (discord.Forbidden, discord.HTTPException):
                            pass
            except (discord.Forbidden, discord.HTTPException):
                pass

        # Expulsa bots recientes que el bot pueda moderar.
        cutoff = discord.utils.utcnow().timestamp() - 60
        for member in list(guild.members):
            if not member.bot or member.joined_at is None:
                continue
            if member.joined_at.timestamp() < cutoff:
                continue
            if not can_manage(guild, member):
                continue
            try:
                await member.kick(
                    reason=f"Anti-rad.Bot: bot durante ataque - {reason}"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        await send_log(
            guild,
            "🚨 Anti-Raid: emergencia",
            f"**Responsable:** {actor_text}\n"
            f"**Motivo:** {reason}\n\n"
            "Se aplicó la respuesta disponible según los permisos y "
            "la jerarquía de Discord.",
        )
    finally:
        emergency_running.discard(guild.id)


intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Anti-rad.Bot conectado como {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Comandos sincronizados: {len(synced)}")
    except Exception as error:
        print(f"Error sincronizando comandos: {error}")


class AntiRaidConfirm(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=60)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Solo quien ejecutó el comando puede usar estos botones.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Activar", emoji="🛡️", style=discord.ButtonStyle.success)
    async def activate(self, interaction, button):
        enabled_guilds.add(interaction.guild.id)
        embed = discord.Embed(
            title="🟢 Anti-Raid activado",
            description="Anti-rad.Bot está protegiendo este servidor.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await send_log(
            interaction.guild,
            "🟢 Anti-Raid activado",
            f"Activado por {interaction.user.mention}.",
            discord.Color.green(),
        )

    @discord.ui.button(label="Cancelar", emoji="❌", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        embed = discord.Embed(
            title="❌ Cancelado",
            description="Anti-Raid no fue activado.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


@bot.tree.command(
    name="anti-raid",
    description="Activa la protección Anti-Raid de Discord.",
)
@app_commands.guild_only()
async def anti_raid(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "❌ Necesitas el permiso **Gestionar servidor**.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🛡️ Anti-Raid",
        description=(
            "⚠️ **¿Estás seguro de activar el Anti-Raid?**\n\n"
            "Anti-rad.Bot vigilará el servidor contra raids, "
            "acciones masivas y spam."
        ),
        color=discord.Color.green(),
    )
    await interaction.response.send_message(
        embed=embed,
        view=AntiRaidConfirm(interaction.user.id),
        ephemeral=True,
    )


class DisableAntiRaidConfirm(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=60)
        self.owner_id = owner_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Solo quien ejecutó el comando puede usar estos botones.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Desactivar", emoji="🔴", style=discord.ButtonStyle.danger)
    async def disable(self, interaction, button):
        enabled_guilds.discard(interaction.guild.id)
        embed = discord.Embed(
            title="🔴 Anti-Raid desactivado",
            description="La protección Anti-Raid fue desactivada.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await send_log(
            interaction.guild,
            "🔴 Anti-Raid desactivado",
            f"Desactivado por {interaction.user.mention}.",
            discord.Color.red(),
        )

    @discord.ui.button(label="Cancelar", emoji="❌", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        embed = discord.Embed(
            title="❌ Cancelado",
            description="Anti-Raid continúa como estaba.",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


@bot.tree.command(
    name="no-anti-raid",
    description="Desactiva la protección Anti-Raid de Discord.",
)
@app_commands.guild_only()
async def no_anti_raid(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "❌ Necesitas el permiso **Gestionar servidor**.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="🛡️ Anti-Raid",
        description="⚠️ **¿Estás seguro de desactivar el Anti-Raid?**",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(
        embed=embed,
        view=DisableAntiRaidConfirm(interaction.user.id),
        ephemeral=True,
    )


@bot.event
async def on_message(message):
    if message.guild is None:
        return

    await bot.process_commands(message)

    if message.guild.id not in enabled_guilds:
        return
    if not message.content.strip():
        return

    key = (message.guild.id, message.author.id)
    history = spam_history[key]
    history.append((now_ts(), message.content, message.channel.id, message.id))
    prune(history, SPAM_WINDOW)

    repeated = [item for item in history if item[1] == message.content]
    if len(repeated) <= SPAM_LIMIT:
        return

    for _, _, channel_id, message_id in repeated:
        channel = message.guild.get_channel(channel_id)
        if channel is None:
            continue
        try:
            spam_message = await channel.fetch_message(message_id)
            await spam_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    try:
        await message.author.send(
            f"🛡️ Anti-rad.Bot detectó más de {SPAM_LIMIT} mensajes "
            f"repetidos en **{message.guild.name}** y eliminó el spam."
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

    await send_log(
        message.guild,
        "🚨 Spam detectado",
        f"{message.author.mention} superó los **{SPAM_LIMIT} mensajes repetidos**.",
    )

    if message.author.bot:
        if can_manage(message.guild, message.author):
            try:
                await message.author.kick(reason="Anti-Raid: spam repetido")
            except (discord.Forbidden, discord.HTTPException):
                pass

    history.clear()


@bot.event
async def on_guild_channel_create(channel):
    guild = channel.guild
    if guild.id not in enabled_guilds:
        return

    history = channel_creates[guild.id]
    history.append((now_ts(), channel.id))
    prune(history, ACTION_WINDOW)

    if len(history) >= CHANNEL_CREATE_LIMIT:
        actor = await find_audit_actor(
            guild, discord.AuditLogAction.channel_create, channel.id
        )
        await emergency_response(
            guild,
            actor,
            f"creación excesiva de canales ({len(history)} en {ACTION_WINDOW}s)",
        )


@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    if guild.id not in enabled_guilds:
        return

    history = channel_deletes[guild.id]
    history.append((now_ts(), channel.id))
    prune(history, ACTION_WINDOW)

    if len(history) >= CHANNEL_DELETE_LIMIT:
        actor = await find_audit_actor(
            guild, discord.AuditLogAction.channel_delete, channel.id
        )
        await emergency_response(
            guild,
            actor,
            f"borrado excesivo de canales ({len(history)} en {ACTION_WINDOW}s)",
        )


@bot.event
async def on_raw_bulk_message_delete(payload):
    guild = bot.get_guild(payload.guild_id)
    if guild is None or guild.id not in enabled_guilds:
        return

    history = message_deletes[guild.id]
    history.append((now_ts(), len(payload.message_ids)))
    prune(history, ACTION_WINDOW)

    total = sum(item[1] for item in history)
    if total >= MESSAGE_DELETE_LIMIT:
        actor = await find_audit_actor(
            guild, discord.AuditLogAction.message_bulk_delete
        )
        await emergency_response(
            guild,
            actor,
            f"borrado masivo de {total} mensajes",
        )


if not TOKEN:
    raise RuntimeError("Falta DISCORD_TOKEN en las variables de Railway.")

bot.run(TOKEN)
