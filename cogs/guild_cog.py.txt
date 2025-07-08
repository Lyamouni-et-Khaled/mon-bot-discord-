
import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime, timezone
import random
import uuid
from typing import List, Dict, Any, Optional
import aiofiles
import re

from .manager_cog import ManagerCog

# --- Modals & Views ---

class GuildCreationModal(discord.ui.Modal, title="Fonder votre Guilde"):
    guild_name = discord.ui.TextInput(
        label="Nom de la guilde (unique)",
        placeholder="Ex: Les Dragons Ascendants",
        required=True,
        min_length=3,
        max_length=50
    )
    guild_color = discord.ui.TextInput(
        label="Couleur du rôle (code Hex)",
        placeholder="Ex: #e91e63 (doit commencer par #)",
        required=True,
        min_length=7,
        max_length=7,
        default="#99aab5"
    )

    def __init__(self, cog: 'GuildCog'):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.process_guild_creation(interaction, self.guild_name.value, self.guild_color.value)

class ForceOfficialView(discord.ui.View):
    def __init__(self, manager: ManagerCog, guild_id: str):
        super().__init__(timeout=300)
        self.manager = manager
        self.guild_id = guild_id
        self.message: Optional[discord.Message] = None

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(content="Le délai pour cette action a expiré.", view=self)
            except discord.NotFound: pass

    @discord.ui.button(label="Rendre officielle maintenant", style=discord.ButtonStyle.success)
    async def force_official(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild_id = self.guild_id
        user_id_str = str(interaction.user.id)
        
        guild_data = self.manager.guild_data.get(guild_id)
        if not guild_data or guild_data["owner_id"] != user_id_str:
            return await interaction.followup.send("Vous n'êtes pas autorisé à faire cela.", ephemeral=True)
        
        if guild_data.get("status") == "official":
            return await interaction.followup.send("Votre guilde est déjà officielle !", ephemeral=True)

        guild_config = self.manager.config["GAMIFICATION_CONFIG"]["GUILD_SYSTEM"]
        cost = guild_config.get("FORCE_OFFICIAL_COST", 3)
        user_credit = self.manager.user_data[user_id_str].get("store_credit", 0)

        if user_credit < cost:
            return await interaction.followup.send(f"Vous n'avez pas assez de crédits. Il vous faut {cost} crédits.", ephemeral=True)

        await self.manager.add_transaction(user_id_str, "store_credit", -cost, f"Officialisation de la guilde {guild_data['name']}")
        
        guild_data["status"] = "official"
        await self.manager.announce_guild_official(interaction.guild, guild_data)
        
        await self.manager._save_json_data_async(self.manager.GUILD_DATA_FILE, self.manager.guild_data)
        await self.manager._save_json_data_async(self.manager.USER_DATA_FILE, self.manager.user_data)
        
        await interaction.followup.send(f"Votre guilde est maintenant officielle ! {cost} crédits ont été déduits.", ephemeral=True)
        button.disabled = True
        self.children[1].disabled = True
        await interaction.edit_original_response(view=self)


    @discord.ui.button(label="Non merci", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        min_members = self.manager.config["GAMIFICATION_CONFIG"]["GUILD_SYSTEM"]["MIN_MEMBERS_FOR_OFFICIAL_STATUS"]
        await interaction.response.edit_message(content=f"D'accord. Votre guilde deviendra officielle gratuitement lorsque vous aurez {min_members} membres.", view=None)
        self.stop()

# --- Cog ---

class GuildCog(commands.Cog):
    """Cog dédié à la gestion des guildes de joueurs."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager: Optional[ManagerCog] = None

    async def cog_load(self):
        self.manager = self.bot.get_cog('ManagerCog')
        if not self.manager:
            return print("ERREUR CRITIQUE: GuildCog n'a pas pu trouver le ManagerCog.")
        print("✅ GuildCog chargé.")
    
    guild_group = app_commands.Group(name="guilde", description="Commandes relatives aux guildes de joueurs.")

    @guild_group.command(name="fonder", description="Fonde une nouvelle guilde pour rassembler des membres.")
    async def fonder(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        self.manager.initialize_user_data(user_id_str)
        user_data = self.manager.user_data[user_id_str]
        guild_config = self.manager.config["GAMIFICATION_CONFIG"]["GUILD_SYSTEM"]

        if not guild_config.get("ENABLED", False):
            return await interaction.response.send_message("Le système de guildes est désactivé.", ephemeral=True)
        
        if user_data.get("guild_id"):
            return await interaction.response.send_message("Vous faites déjà partie d'une guilde.", ephemeral=True)
            
        if user_data.get("level", 1) < guild_config.get("MIN_LEVEL_TO_CREATE", 10):
            return await interaction.response.send_message(f"Vous devez être au moins niveau {guild_config.get('MIN_LEVEL_TO_CREATE', 10)} pour créer une guilde.", ephemeral=True)
        
        cost = guild_config.get("CREATION_COST", 5)
        if user_data.get("store_credit", 0) < cost:
            return await interaction.response.send_message(f"Il vous faut {cost} crédits pour fonder une guilde.", ephemeral=True)

        modal = GuildCreationModal(self)
        await interaction.response.send_modal(modal)

    async def process_guild_creation(self, interaction: discord.Interaction, nom: str, couleur_hex: str):
        await interaction.response.defer(ephemeral=True)
        guild_config = self.manager.config["GAMIFICATION_CONFIG"]["GUILD_SYSTEM"]
        user_id_str = str(interaction.user.id)
        
        # Validation du nom et de la couleur
        if any(g['name'].lower() == nom.lower() for g in self.manager.guild_data.values()):
            return await interaction.followup.send("Une guilde avec ce nom existe déjà.", ephemeral=True)
        
        if not re.match(r'^#(?:[0-9a-fA-F]{3}){1,2}$', couleur_hex):
            return await interaction.followup.send("Le format de la couleur est invalide. Utilisez un code hexadécimal (ex: #FF5733).", ephemeral=True)
        
        cost = guild_config.get("CREATION_COST", 5)
        
        # Déduction des crédits
        await self.manager.add_transaction(user_id_str, "store_credit", -cost, f"Création de la guilde {nom}")
        
        # Création du rôle
        try:
            guild_role = await interaction.guild.create_role(
                name=nom,
                color=discord.Color(int(couleur_hex.lstrip('#'), 16)),
                hoist=True,
                mentionable=True,
                reason=f"Création de la guilde par {interaction.user.display_name}"
            )
            await interaction.user.add_roles(guild_role)
        except Exception as e:
            await self.manager.add_transaction(user_id_str, "store_credit", cost, f"Remboursement création guilde {nom} échouée")
            return await interaction.followup.send(f"Erreur lors de la création du rôle de guilde : {e}", ephemeral=True)
            
        # Création du canal
        category_name = self.manager.config["CHANNELS"].get("GUILD_PRIVATE_CATEGORY")
        category = discord.utils.get(interaction.guild.categories, name=category_name)
        if not category:
            return await interaction.followup.send(f"La catégorie '{category_name}' pour les guildes est introuvable.", ephemeral=True)
        
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild_role: discord.PermissionOverwrite(view_channel=True)
        }
        
        channel_name = f"🛡️-{nom.lower().replace(' ', '-')}"
        try:
            guild_channel = await category.create_text_channel(name=channel_name, overwrites=overwrites, reason=f"Création de la guilde {nom}")
        except Exception as e:
            await guild_role.delete()
            await self.manager.add_transaction(user_id_str, "store_credit", cost, f"Remboursement création guilde {nom} échouée")
            return await interaction.followup.send(f"Erreur lors de la création du canal de guilde : {e}", ephemeral=True)

        # Enregistrement des données
        guild_id = str(uuid.uuid4())
        async with self.manager.data_lock:
            self.manager.guild_data[guild_id] = {
                "id": guild_id, "name": nom, "owner_id": user_id_str,
                "members": [user_id_str], "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "total_xp": self.manager.user_data[user_id_str].get("xp", 0),
                "weekly_xp": self.manager.user_data[user_id_str].get("weekly_xp", 0),
                "role_id": guild_role.id, "channel_id": guild_channel.id
            }
            self.manager.user_data[user_id_str]["guild_id"] = guild_id

            await self.manager._save_json_data_async(self.manager.GUILD_DATA_FILE, self.manager.guild_data)
            await self.manager._save_json_data_async(self.manager.USER_DATA_FILE, self.manager.user_data)
        
        force_official_cost = guild_config.get('FORCE_OFFICIAL_COST', 3)
        view = ForceOfficialView(self.manager, guild_id)
        msg = await interaction.followup.send(
            f"Félicitations ! Vous avez fondé la guilde **{nom}** pour {cost} crédits. Un rôle et un canal privé ont été créés.\n\n"
            f"Voulez-vous payer **{force_official_cost} crédits supplémentaires** pour la rendre officielle immédiatement ?",
            ephemeral=True, view=view
        )
        view.message = msg

    @guild_group.command(name="inviter", description="Invite un membre à rejoindre votre guilde.")
    @app_commands.describe(membre="Le membre à inviter.")
    async def inviter(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer(ephemeral=True)

        user_id_str = str(interaction.user.id)
        user_data = self.manager.user_data.get(user_id_str)
        guild_id = user_data.get("guild_id") if user_data else None

        if not guild_id or guild_id not in self.manager.guild_data:
            return await interaction.followup.send("Vous n'êtes pas dans une guilde.", ephemeral=True)
            
        guild_data = self.manager.guild_data[guild_id]
        if guild_data["owner_id"] != user_id_str:
            return await interaction.followup.send("Seul le chef de guilde peut inviter des membres.", ephemeral=True)
        
        target_id_str = str(membre.id)
        self.manager.initialize_user_data(target_id_str)
        if self.manager.user_data[target_id_str].get("guild_id"):
             return await interaction.followup.send(f"{membre.display_name} est déjà dans une guilde.", ephemeral=True)

        max_members = self.manager.config["GAMIFICATION_CONFIG"]["GUILD_SYSTEM"].get("MAX_MEMBERS", 10)
        if len(guild_data["members"]) >= max_members:
            return await interaction.followup.send("Votre guilde a atteint le nombre maximum de membres.", ephemeral=True)

        view = GuildInviteView(self.manager, guild_id, interaction.user, membre)
        try:
            await membre.send(f"Vous avez été invité par **{interaction.user.display_name}** à rejoindre la guilde **{guild_data['name']}**.", view=view)
            await interaction.followup.send(f"Invitation envoyée à {membre.display_name}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(f"Impossible d'envoyer une invitation à {membre.display_name}. Ses messages privés sont fermés.", ephemeral=True)

    @guild_group.command(name="quitter", description="Quitte votre guilde actuelle.")
    async def quitter(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id_str = str(interaction.user.id)
        user_data = self.manager.user_data.get(user_id_str)
        guild_id = user_data.get("guild_id") if user_data else None

        if not guild_id or guild_id not in self.manager.guild_data:
            return await interaction.followup.send("Vous n'êtes pas dans une guilde.", ephemeral=True)
        
        guild_data = self.manager.guild_data[guild_id]
        guild_name = guild_data['name']
        
        guild_role = interaction.guild.get_role(guild_data['role_id'])
        if guild_role and guild_role in interaction.user.roles:
            await interaction.user.remove_roles(guild_role)

        async with self.manager.data_lock:
            if guild_data["owner_id"] == user_id_str:
                announcement_channel_name = self.manager.config["CHANNELS"].get("GUILD_ANNOUNCEMENTS")
                announcement_channel = discord.utils.get(interaction.guild.text_channels, name=announcement_channel_name)
                if announcement_channel:
                    await announcement_channel.send(f"⚔️ La guilde **{guild_name}** a été dissoute car son chef, {interaction.user.mention}, est parti.")

                # Supprimer rôle et canal
                if guild_role: await guild_role.delete(reason=f"Guilde {guild_name} dissoute")
                guild_channel = interaction.guild.get_channel(guild_data['channel_id'])
                if guild_channel: await guild_channel.delete(reason=f"Guilde {guild_name} dissoute")
                
                # Retirer la guilde pour tous les membres
                for member_id in guild_data["members"]:
                    if member_id in self.manager.user_data:
                        self.manager.user_data[member_id]["guild_id"] = None
                del self.manager.guild_data[guild_id]
                message = f"Vous avez dissous la guilde **{guild_name}**."
            else:
                guild_data["members"].remove(user_id_str)
                user_data["guild_id"] = None
                message = f"Vous avez quitté la guilde **{guild_name}**."
            
            await self.manager._save_json_data_async(self.manager.GUILD_DATA_FILE, self.manager.guild_data)
            await self.manager._save_json_data_async(self.manager.USER_DATA_FILE, self.manager.user_data)
            
        guild_master_role = discord.utils.get(interaction.guild.roles, name=self.manager.config["ROLES"].get("GUILD_MASTER"))
        if guild_master_role and guild_master_role in interaction.user.roles:
            await interaction.user.remove_roles(guild_master_role, reason="A quitté/dissous sa guilde.")
            
        await interaction.followup.send(message, ephemeral=True)
        
    @guild_group.command(name="renommer", description="Change le nom de votre guilde.")
    @app_commands.describe(nouveau_nom="Le nouveau nom unique pour votre guilde.")
    async def renommer(self, interaction: discord.Interaction, nouveau_nom: str):
        await interaction.response.defer(ephemeral=True)
        user_id_str = str(interaction.user.id)
        user_data = self.manager.user_data.get(user_id_str)
        guild_id = user_data.get("guild_id") if user_data else None

        if not guild_id or guild_id not in self.manager.guild_data:
            return await interaction.followup.send("Vous n'êtes dans aucune guilde.", ephemeral=True)
        
        guild_data = self.manager.guild_data[guild_id]
        if guild_data["owner_id"] != user_id_str:
            return await interaction.followup.send("Seul le chef de guilde peut la renommer.", ephemeral=True)
        
        cost = self.manager.config["GAMIFICATION_CONFIG"]["GUILD_SYSTEM"].get("NAME_CHANGE_COST", 4)
        if user_data.get("store_credit", 0) < cost:
            return await interaction.followup.send(f"Il vous faut {cost} crédits pour renommer votre guilde.", ephemeral=True)
        
        if any(g['name'].lower() == nouveau_nom.lower() for g in self.manager.guild_data.values()):
            return await interaction.followup.send("Une guilde avec ce nom existe déjà.", ephemeral=True)

        await self.manager.add_transaction(user_id_str, "store_credit", -cost, f"Renommage de la guilde {guild_data['name']}")
        
        old_name = guild_data['name']
        guild_data['name'] = nouveau_nom
        
        role = interaction.guild.get_role(guild_data['role_id'])
        if role: await role.edit(name=nouveau_nom)
        
        channel = interaction.guild.get_channel(guild_data['channel_id'])
        if channel: await channel.edit(name=f"🛡️-{nouveau_nom.lower().replace(' ', '-')}")
        
        await self.manager._save_json_data_async(self.manager.GUILD_DATA_FILE, self.manager.guild_data)
        await self.manager._save_json_data_async(self.manager.USER_DATA_FILE, self.manager.user_data)
        
        await interaction.followup.send(f"Votre guilde a été renommée de '{old_name}' à '{nouveau_nom}' pour {cost} crédits.", ephemeral=True)


    @app_commands.command(name="classement_guildes", description="Affiche le classement des guildes officielles.")
    async def classement_guildes(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        official_guilds = [g for g in self.manager.guild_data.values() if g.get('status') == 'official']
        sorted_guilds = sorted(official_guilds, key=lambda g: g.get('total_xp', 0), reverse=True)

        embed = discord.Embed(title="🏆 Classement des Guildes Officielles 🏆", color=discord.Color.from_rgb(153, 45, 34))
        
        leaderboard_text = ""
        for i, guild_data in enumerate(sorted_guilds[:15]):
            rank = i + 1
            leaderboard_text += f"`#{rank: <3}` **{guild_data['name']}** - {int(guild_data.get('total_xp', 0))} XP ({len(guild_data['members'])} membres)\n"
        
        if not leaderboard_text:
            leaderboard_text = "Aucune guilde officielle n'est encore classée. Fondez la vôtre avec `/guilde fonder` et recrutez des membres !"
        
        embed.description = leaderboard_text
        await interaction.followup.send(embed=embed)


class GuildInviteView(discord.ui.View):
    def __init__(self, manager: ManagerCog, guild_id: str, inviter: discord.User, target: discord.User):
        super().__init__(timeout=3600) # 1h timeout
        self.manager = manager
        self.guild_id = guild_id
        self.inviter = inviter
        self.target = target

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.target.id

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(content="Cette invitation a expiré.", view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        user_id_str = str(interaction.user.id)
        self.manager.initialize_user_data(user_id_str)
        user_data = self.manager.user_data[user_id_str]

        if user_data.get("guild_id"):
            await interaction.edit_original_response(content="Vous avez déjà rejoint une guilde.", view=None)
            return
            
        async with self.manager.data_lock:
            guild_data = self.manager.guild_data.get(self.guild_id)
            if not guild_data:
                await interaction.edit_original_response(content="Cette guilde n'existe plus.", view=None)
                return

            guild_config = self.manager.config.get("GAMIFICATION_CONFIG", {}).get("GUILD_SYSTEM", {})
            max_members = guild_config.get("MAX_MEMBERS", 10)
            if len(guild_data["members"]) >= max_members:
                await interaction.edit_original_response(content="Cette guilde est pleine.", view=None)
                return

            guild_data["members"].append(user_id_str)
            user_data["guild_id"] = self.guild_id
            
            # Donner le rôle de la guilde
            guild_role = interaction.guild.get_role(guild_data['role_id'])
            if guild_role:
                await interaction.user.add_roles(guild_role)
            
            # Vérifier si la guilde devient officielle
            official_threshold = guild_config.get("MIN_MEMBERS_FOR_OFFICIAL_STATUS", 7)
            if guild_data.get("status") == "pending" and len(guild_data["members"]) >= official_threshold:
                guild_data["status"] = "official"
                await self.manager.announce_guild_official(interaction.guild, guild_data)
                
            await self.manager._save_json_data_async(self.manager.GUILD_DATA_FILE, self.manager.guild_data)
            await self.manager._save_json_data_async(self.manager.USER_DATA_FILE, self.manager.user_data)
        
        for item in self.children: item.disabled = True
        await interaction.edit_original_response(content=f"Vous avez rejoint la guilde **{guild_data['name']}** !", view=self)

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        for item in self.children: item.disabled = True
        await interaction.edit_original_response(content="Vous avez refusé l'invitation.", view=self)


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildCog(bot))
    
async def announce_guild_official(self, guild: discord.Guild, guild_data: dict):
    """Helper method in ManagerCog to announce a guild becoming official."""
    owner = guild.get_member(int(guild_data["owner_id"]))
    guild_master_role = discord.utils.get(guild.roles, name=self.config["ROLES"].get("GUILD_MASTER"))
    if owner and guild_master_role and guild_master_role not in owner.roles:
        await owner.add_roles(guild_master_role, reason=f"Chef de la nouvelle guilde officielle {guild_data['name']}")

    announcement_channel_name = self.config["CHANNELS"].get("GUILD_ANNOUNCEMENTS")
    announcement_channel = discord.utils.get(guild.text_channels, name=announcement_channel_name)
    if announcement_channel:
        embed = discord.Embed(
            title="🛡️ Nouvelle Guilde Officielle ! 🛡️",
            description=f"La guilde **{guild_data['name']}**, fondée par {owner.mention if owner else 'un chef'}, a atteint le statut officiel ! Souhaitez-leur la bienvenue !",
            color=discord.Color.green()
        )
        await announcement_channel.send(embed=embed)

# This function needs to be added to the ManagerCog class
ManagerCog.announce_guild_official = announce_guild_official
