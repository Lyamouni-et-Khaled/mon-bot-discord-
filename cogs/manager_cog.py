import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime, timedelta, timezone
import random
import math
import uuid
from typing import List, Dict, Any, Optional
import aiofiles
import re
import traceback

# Dépendance pour la génération d'image
try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    import io
    IMAGING_AVAILABLE = True
except ImportError:
    IMAGING_AVAILABLE = False


# --- Configuration de l'IA Gemini ---
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# --- Fonctions d'aide pour la génération d'image ---
def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convertit une couleur hexadécimale en tuple (R, G, B)."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def create_gradient(width, height, color1_rgb, color2_rgb):
    """Crée une image avec un dégradé linéaire horizontal."""
    base = Image.new('RGB', (width, 1))
    draw = ImageDraw.Draw(base)
    for x in range(width):
        r = int(color1_rgb[0] + (color2_rgb[0] - color1_rgb[0]) * (x / width))
        g = int(color1_rgb[1] + (color2_rgb[1] - color1_rgb[1]) * (x / width))
        b = int(color1_rgb[2] + (color2_rgb[2] - color1_rgb[2]) * (x / width))
        draw.point((x, 0), (r, g, b))
    return base.resize((width, height), Image.Resampling.BICUBIC)


# --- Classes pour les Vues d'Interaction ---

class MissionView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager
    
    @discord.ui.button(label="Activer/Désactiver les notifications de mission", style=discord.ButtonStyle.secondary, custom_id="toggle_mission_dms")
    async def toggle_dms(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id_str = str(interaction.user.id)
        self.manager.initialize_user_data(user_id_str)
        
        current_status = self.manager.user_data[user_id_str].get("missions_opt_in", True)
        new_status = not current_status
        self.manager.user_data[user_id_str]["missions_opt_in"] = new_status
        
        status_text = "activées" if new_status else "désactivées"
        await interaction.response.send_message(f"Vos notifications de mission par message privé sont maintenant {status_text}.", ephemeral=True)
        await self.manager._save_json_data_async(self.manager.USER_DATA_FILE, self.manager.user_data)


class ChallengeSubmissionModal(discord.ui.Modal, title="Soumission de Défi"):
    submission_text = discord.ui.TextInput(
        label="Décrivez comment vous avez complété le défi",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: J'ai aidé @utilisateur à configurer son compte en lui expliquant comment faire...",
        required=True
    )

    def __init__(self, manager: 'ManagerCog', challenge_type: str = "community"):
        super().__init__()
        self.manager = manager
        self.challenge_type = challenge_type # "community" or "prestige"

    async def on_submit(self, interaction: discord.Interaction):
        await self.manager.handle_challenge_submission(interaction, self.submission_text.value, self.challenge_type)

class CashoutModal(discord.ui.Modal, title="Demande de Retrait d'Argent"):
    amount = discord.ui.TextInput(label="Montant en crédit à retirer", placeholder="Ex: 10.50", required=True)
    paypal_email = discord.ui.TextInput(label="Votre email PayPal", placeholder="Ex: votre.email@example.com", style=discord.TextStyle.short, required=True)

    def __init__(self, manager: 'ManagerCog'):
        super().__init__()
        self.manager = manager

    async def on_submit(self, interaction: discord.Interaction):
        await self.manager.handle_cashout_submission(interaction, self.amount.value, self.paypal_email.value)

class CashoutRequestView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="✅ Approuver", style=discord.ButtonStyle.success, custom_id="approve_cashout")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        msg_id = str(interaction.message.id)
        
        async with self.manager.data_lock:
            cashout_data = self.manager.pending_actions["cashouts"].get(msg_id)
            if not cashout_data:
                button.disabled = True
                self.children[1].disabled = True
                await interaction.message.edit(view=self)
                return await interaction.followup.send("Cette demande de retrait est introuvable ou a déjà été traitée.", ephemeral=True)

            user_id_str = str(cashout_data['user_id'])
            self.manager.initialize_user_data(user_id_str)
            user_data = self.manager.user_data[user_id_str]
            
            await self.manager.add_transaction(user_id_str, "cashout_count", 1, "Approbation de retrait")

            member = interaction.guild.get_member(cashout_data['user_id'])
            if member:
                await self.manager.check_achievements(member)
                try:
                    await member.send(f"✅ Votre demande de retrait de `{cashout_data['euros_to_send']:.2f}€` a été approuvée ! Le paiement sera effectué sous peu sur l'adresse `{cashout_data['paypal_email']}`.")
                except discord.Forbidden: pass
                
                # --- Logique de Commission de Second Niveau ---
                if user_data.get("referrer"):
                    referrer_id_str = user_data["referrer"]
                    self.manager.initialize_user_data(referrer_id_str)
                    referrer = interaction.guild.get_member(int(referrer_id_str))
                    aff_pro_config = self.manager.config.get("GAMIFICATION_CONFIG", {}).get("AFFILIATE_SYSTEM", {}).get("AFFILIATE_PRO_SYSTEM", {})
                    
                    if referrer and aff_pro_config.get("ENABLED") and self.manager.is_affiliate_pro_active(referrer_id_str):
                        commission_rate = aff_pro_config.get("COMMISSION_RATE", 0.1)
                        commission_earned = cashout_data['euros_to_send'] * commission_rate
                        
                        await self.manager.add_transaction(
                            referrer_id_str, "store_credit", commission_earned,
                            f"Commission 'Parrain Pro' sur le retrait de {member.display_name}"
                        )
                        await self.manager.log_public_transaction(
                            interaction.guild,
                            f"💎 **{referrer.display_name}** a gagné une commission de parrain pro !",
                            f"**Montant :** `{commission_earned:.2f}` crédits\n**Source :** Retrait de `{member.display_name}`",
                            discord.Color.from_rgb(0, 255, 255) # Cyan
                        )
                        try:
                            await referrer.send(f"💎 Votre filleul {member.display_name} a effectué un retrait ! En tant que Parrain Pro, vous gagnez **{commission_earned:.2f} crédits** de commission.")
                        except discord.Forbidden: pass

            await self.manager.log_public_transaction(
                interaction.guild,
                f"✅ Demande de retrait approuvée pour **{member.display_name if member else 'Utilisateur Inconnu'}**.",
                f"**Montant :** `{cashout_data['euros_to_send']:.2f}€`\n**Validé par :** {interaction.user.mention}",
                discord.Color.green()
            )

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.title = "Demande de Retrait APPROUVÉE"
            embed.set_footer(text=f"Approuvé par {interaction.user.display_name}")

            button.disabled = True
            self.children[1].disabled = True
            await interaction.message.edit(embed=embed, view=self)

            del self.manager.pending_actions["cashouts"][msg_id]
            await self.manager._save_json_data_async(self.manager.PENDING_ACTIONS_FILE, self.manager.pending_actions)
        
        await interaction.followup.send("Demande approuvée.", ephemeral=True)


    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger, custom_id="deny_cashout")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        msg_id = str(interaction.message.id)

        async with self.manager.data_lock:
            cashout_data = self.manager.pending_actions["cashouts"].get(msg_id)
            if not cashout_data:
                button.disabled = True
                self.children[0].disabled = True
                await interaction.message.edit(view=self)
                return await interaction.followup.send("Cette demande de retrait est introuvable ou a déjà été traitée.", ephemeral=True)

            user_id_str = str(cashout_data['user_id'])
            self.manager.initialize_user_data(user_id_str)
            
            await self.manager.add_transaction(
                user_id_str,
                "store_credit",
                cashout_data['credit_to_deduct'],
                "Remboursement suite au refus de retrait"
            )
            
            member = interaction.guild.get_member(cashout_data['user_id'])
            if member:
                try:
                    await member.send(f"❌ Votre demande de retrait a été refusée par le staff. Vos `{cashout_data['credit_to_deduct']:.2f}` crédits vous ont été remboursés.")
                except discord.Forbidden: pass
            
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.title = "Demande de Retrait REFUSÉE"
            embed.set_footer(text=f"Refusé par {interaction.user.display_name}")

            button.disabled = True
            self.children[0].disabled = True
            await interaction.message.edit(embed=embed, view=self)

            del self.manager.pending_actions["cashouts"][msg_id]
            await self.manager._save_json_data_async(self.manager.PENDING_ACTIONS_FILE, self.manager.pending_actions)

        await interaction.followup.send("Demande refusée et crédits remboursés.", ephemeral=True)


class VerificationView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager
    
    @discord.ui.button(label="✅ Accepter le règlement", style=discord.ButtonStyle.success, custom_id="verify_member_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        verified_role_name = self.manager.config["ROLES"]["VERIFIED"]
        unverified_role_name = self.manager.config["ROLES"]["UNVERIFIED"]
        
        verified_role = discord.utils.get(interaction.guild.roles, name=verified_role_name)
        unverified_role = discord.utils.get(interaction.guild.roles, name=unverified_role_name)

        if not verified_role:
            return await interaction.response.send_message(f"Erreur : Le rôle `{verified_role_name}` est introuvable.", ephemeral=True)
            
        if verified_role in interaction.user.roles:
            return await interaction.response.send_message("Vous êtes déjà vérifié !", ephemeral=True)

        try:
            await interaction.user.add_roles(verified_role, reason="Vérification via bouton")
            if unverified_role and unverified_role in interaction.user.roles:
                await interaction.user.remove_roles(unverified_role, reason="Vérification via bouton")
            await interaction.response.send_message("Vous avez été vérifié avec succès ! Bienvenue sur le serveur.", ephemeral=True)
            
            # Grant XP to referrer if the new member validates
            user_id_str = str(interaction.user.id)
            self.manager.initialize_user_data(user_id_str)
            user_data = self.manager.user_data[user_id_str]
            if user_data.get("referrer"):
                referrer_id_str = user_data["referrer"]
                self.manager.initialize_user_data(referrer_id_str)
                referrer = interaction.guild.get_member(int(referrer_id_str))
                if referrer:
                    xp_config = self.manager.config["GAMIFICATION_CONFIG"]["XP_SYSTEM"]
                    xp_to_add = xp_config["XP_PER_VERIFIED_INVITE"]
                    await self.manager.grant_xp(referrer, xp_to_add, "Parrainage validé")
                    
        except discord.Forbidden:
            await interaction.response.send_message("Je n'ai pas les permissions pour vous donner le rôle. Veuillez contacter un administrateur.", ephemeral=True)

class TicketCreationView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="🎫 Ouvrir un ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket_button")
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket_types = self.manager.config.get("TICKET_SYSTEM", {}).get("TICKET_TYPES", [])
        if not ticket_types:
            return await interaction.response.send_message("Le système de tickets n'est pas correctement configuré.", ephemeral=True)
        
        await interaction.response.send_message(view=TicketTypeSelect(self.manager, ticket_types), ephemeral=True)

class TicketTypeSelect(discord.ui.View):
    def __init__(self, manager: 'ManagerCog', ticket_types: List[Dict]):
        super().__init__(timeout=180)
        self.manager = manager
        
        options = [
            discord.SelectOption(label=tt['label'], description=tt.get('description'), value=tt['label'])
            for tt in ticket_types
        ]
        self.select_menu = discord.ui.Select(placeholder="Choisissez le type de ticket...", options=options)
        self.select_menu.callback = self.on_select
        self.add_item(self.select_menu)

    async def on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_label = self.select_menu.values[0]
        ticket_type = next(tt for tt in self.manager.config["TICKET_SYSTEM"]["TICKET_TYPES"] if tt['label'] == selected_label)

        initial_message = "Veuillez décrire votre problème en détail. Un membre du staff sera bientôt avec vous."
        ticket_channel = await self.manager.create_ticket(interaction.user, interaction.guild, ticket_type, initial_message)

        if ticket_channel:
            await interaction.followup.send(f"Votre ticket a été créé : {ticket_channel.mention}", ephemeral=True)
        else:
            await interaction.followup.send("Impossible de créer le ticket. Veuillez contacter un administrateur.", ephemeral=True)
        
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

class TicketCloseView(discord.ui.View):
    def __init__(self, manager: 'ManagerCog'):
        super().__init__(timeout=None)
        self.manager = manager
    
    @discord.ui.button(label="🔒 Fermer le Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        channel = interaction.channel
        button.disabled = True
        await interaction.message.edit(view=self)

        await self.manager.log_ticket_closure(interaction, channel)
        
        await channel.delete(reason=f"Ticket fermé par {interaction.user}")

# --- Le Cog Principal ---

class ManagerCog(commands.Cog):
    """Le cerveau du bot, gère la gamification, l'économie et les données utilisateurs."""
    USER_DATA_FILE = 'data/user_data.json'
    CONFIG_FILE = 'config.json'
    PRODUCTS_FILE = 'products.json'
    ACHIEVEMENTS_FILE = 'achievements_config.json'
    KNOWLEDGE_BASE_FILE = 'knowledge_base.json'
    CURRENT_CHALLENGE_FILE = 'data/current_challenge.json'
    PENDING_ACTIONS_FILE = 'data/pending_actions.json'
    GUILD_DATA_FILE = 'data/guild_data.json'


    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_lock = asyncio.Lock()
        
        self.config = {}
        self.products = []
        self.achievements = []
        self.knowledge_base = {}
        self.user_data = {}
        self.guild_data = {}
        self.invites_cache = {}
        self.current_challenge: Optional[Dict[str, Any]] = None
        self.pending_actions = {}
        
        if not IMAGING_AVAILABLE:
            print("⚠️ ATTENTION: La librairie 'Pillow' est manquante. La commande /profil utilisera un embed standard.")

        self.model = None
        if not AI_AVAILABLE:
            print("ATTENTION: Le package google-generativeai n'est pas installé. Les fonctionnalités d'IA seront désactivées.")
        else:
            gemini_key = os.environ.get("GEMINI_API_KEY")
            if gemini_key:
                genai.configure(api_key=gemini_key)
                self.model = genai.GenerativeModel('gemini-2.5-flash-preview-04-17')
                print("✅ Modèle Gemini initialisé avec succès.")
            else:
                print("⚠️ ATTENTION: La clé API Gemini (GEMINI_API_KEY) est manquante dans l'environnement. L'IA est désactivée.")

    async def cog_load(self):
        print("Chargement des données du ManagerCog...")
        await self._load_all_data()
        self.bot.add_view(VerificationView(self))
        self.bot.add_view(TicketCreationView(self))
        self.bot.add_view(TicketCloseView(self))
        self.bot.add_view(CashoutRequestView(self))
        self.bot.add_view(MissionView(self))

    def cog_unload(self):
        self.weekly_leaderboard_task.cancel()
        self.mission_assignment_task.cancel()
        self.check_expired_subscriptions_task.cancel()
        self.check_expired_boosts_task.cancel()
        print("ManagerCog déchargé.")

    @commands.Cog.listener()
    async def on_ready(self):
        print("ManagerCog: Le bot est prêt. Finalisation de la configuration...")
        guild_id_str = self.config.get("GUILD_ID")
        if guild_id_str == "VOTRE_VRAI_ID_DE_SERVEUR_ICI" or not guild_id_str:
            print("ATTENTION: GUILD_ID non configuré. De nombreuses fonctionnalités seront désactivées.")
            return

        guild = self.bot.get_guild(int(guild_id_str))
        if guild:
            await self._update_invite_cache(guild)
            print(f"Cache des invitations mis à jour pour la guilde : {guild.name}")
        else:
            print(f"ATTENTION: Guilde avec l'ID {guild_id_str} non trouvée.")

        try:
            if not self.weekly_leaderboard_task.is_running():
                self.weekly_leaderboard_task.start()
                print("Tâche de fond 'weekly_leaderboard_task' démarrée.")
            if not self.mission_assignment_task.is_running():
                self.mission_assignment_task.start()
                print("Tâche de fond 'mission_assignment_task' démarrée.")
            if not self.check_expired_subscriptions_task.is_running():
                self.check_expired_subscriptions_task.start()
                print("Tâche de fond 'check_expired_subscriptions_task' démarrée.")
            if not self.check_expired_boosts_task.is_running():
                self.check_expired_boosts_task.start()
                print("Tâche de fond 'check_expired_boosts_task' démarrée.")
        except Exception as e:
            print(f"Erreur au démarrage des tâches de fond: {e}")

    async def _load_json_data_async(self, file_path: str) -> any:
        if not os.path.exists(file_path):
            print(f"Fichier {file_path} non trouvé, création d'un fichier vide.")
            dir_name = os.path.dirname(file_path)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name)
            default_content = '{}'
            if 'pending_actions' in file_path:
                default_content = '{"transactions": {}, "cashouts": {}}'
            elif any(x in file_path for x in ['user_data', 'challenge', 'guild_data']):
                default_content = '{}'
            else:
                default_content = '[]'
            
            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(default_content)
            return json.loads(default_content)
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                if not content:
                    return {"transactions": {}, "cashouts": {}} if 'pending_actions' in file_path else ({})
                return json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"Erreur lors du chargement de {file_path}: {e}")
            return {} if any(x in file_path for x in ['user_data', 'guild_data']) else []

    async def _save_json_data_async(self, file_path: str, data: any):
        async with self.data_lock:
            try:
                loop = asyncio.get_running_loop()
                json_string = await loop.run_in_executor(
                    None, lambda: json.dumps(data, indent=2, ensure_ascii=False)
                )
                async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                    await f.write(json_string)
            except Exception as e:
                print(f"Erreur lors de la sauvegarde de {file_path}: {e}")
    
    async def _load_all_data(self):
        tasks = {
            "config": self._load_json_data_async(self.CONFIG_FILE),
            "products": self._load_json_data_async(self.PRODUCTS_FILE),
            "achievements": self._load_json_data_async(self.ACHIEVEMENTS_FILE),
            "knowledge_base": self._load_json_data_async(self.KNOWLEDGE_BASE_FILE),
            "user_data": self._load_json_data_async(self.USER_DATA_FILE),
            "guild_data": self._load_json_data_async(self.GUILD_DATA_FILE),
            "current_challenge": self._load_json_data_async(self.CURRENT_CHALLENGE_FILE),
            "pending_actions": self._load_json_data_async(self.PENDING_ACTIONS_FILE)
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        results_dict = dict(zip(tasks.keys(), results))

        for name, result in results_dict.items():
            if isinstance(result, Exception):
                print(f"Erreur critique lors du chargement du fichier pour '{name}': {result}")
                default_val = []
                if name in ['user_data', 'guild_data', 'current_challenge', 'pending_actions', 'knowledge_base']:
                    default_val = {}
                setattr(self, name, default_val)
            else:
                 setattr(self, name, result)

        print("Toutes les données de configuration ont été chargées.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        xp_config = self.config.get("GAMIFICATION_CONFIG", {}).get("XP_SYSTEM", {})
        if len(message.content.split()) < xp_config.get("ANTI_FARM_MIN_WORDS", 0):
            return

        user_id_str = str(message.author.id)
        self.initialize_user_data(user_id_str)
        
        if xp_config.get("ENABLED", False):
            await self.grant_xp(message.author, "message", f"Message dans #{message.channel.name}")
        
        await self.update_mission_progress(message.author, "send_message", 1)


    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        
        unverified_role_name = self.config.get("ROLES", {}).get("UNVERIFIED")
        if unverified_role_name:
            role = discord.utils.get(member.guild.roles, name=unverified_role_name)
            if role:
                try:
                    await member.add_roles(role, reason="Nouveau membre")
                except discord.Forbidden:
                    print(f"Permissions manquantes pour assigner le rôle '{unverified_role_name}' à {member.name}")

        self.initialize_user_data(str(member.id))
        old_invites = self.invites_cache.get(member.guild.id, {})
        new_invites = await member.guild.invites()
        inviter = None
        for invite in new_invites:
            if invite.code in old_invites and invite.uses > old_invites[invite.code].uses:
                inviter = invite.inviter
                break
        if inviter and inviter.id != member.id:
            user_id_str = str(member.id)
            self.initialize_user_data(str(inviter.id))
            self.user_data[user_id_str]["referrer"] = str(inviter.id)
            
            await self.add_transaction(
                str(inviter.id),
                "referral_count", 1, f"Parrainage de {member.name}"
            )

            print(f"{member.name} a été invité par {inviter.name}")
            await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)

        await self._update_invite_cache(member.guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        await self._update_invite_cache(invite.guild)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        await self._update_invite_cache(invite.guild)

    def initialize_user_data(self, user_id: str):
        if user_id not in self.user_data:
            self.user_data[user_id] = {
                "xp": 0, "level": 1, "weekly_xp": 0, "last_message_timestamp": 0,
                "message_count": 0, "purchase_count": 0, "purchase_total_value": 0.0,
                "achievements": [], "store_credit": 0.0, "warnings": 0,
                "affiliate_sale_count": 0, "affiliate_earnings": 0.0, "referral_count": 0,
                "cashout_count": 0,
                "completed_challenges": [],
                "xp_gated": False,
                "current_prestige_challenge": None,
                "join_timestamp": datetime.now(timezone.utc).timestamp(),
                "weekly_affiliate_earnings": 0.0,
                "affiliate_booster": 0.0,
                "loyalty_commission_bonus": 0.0,
                "loyalty_xp_bonus": 0.0,
                "vip_premium": None,
                "affiliate_pro": None,
                "active_boosts": [],
                "guild_id": None,
                "transaction_log": [],
                "missions_opt_in": self.config.get("MISSION_SYSTEM", {}).get("OPT_IN_DEFAULT", True),
                "current_daily_mission": None,
                "current_weekly_mission": None
            }
            print(f"Nouvel utilisateur initialisé : {user_id}")
    
    async def add_transaction(self, user_id: str, type: str, amount: float, description: str):
        """Ajoute une transaction à l'historique de l'utilisateur et met à jour son solde."""
        self.initialize_user_data(user_id)
        user_data = self.user_data[user_id]
        
        if type in user_data:
            user_data[type] += amount
        else:
             user_data[type] = amount
        
        if "transaction_log" not in user_data:
            user_data["transaction_log"] = []
            
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": type,
            "amount": amount,
            "description": description
        }
        user_data["transaction_log"].append(log_entry)
        
        # Garder le log à une taille raisonnable
        max_log_size = self.config.get("TRANSACTION_LOG_CONFIG", {}).get("MAX_USER_LOG_SIZE", 50)
        if len(user_data["transaction_log"]) > max_log_size:
            user_data["transaction_log"] = user_data["transaction_log"][-max_log_size:]
            
    async def grant_xp(self, user: discord.Member, source: any, reason: str):
        user_id_str = str(user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]
        
        if isinstance(source, str) and source == "message" and user_data.get("xp_gated", False):
            return

        xp_config = self.config["GAMIFICATION_CONFIG"]["XP_SYSTEM"]
        now = datetime.now().timestamp()
        
        xp_to_add = 0
        if source == "message":
            cooldown = xp_config["ANTI_FARM_COOLDOWN_SECONDS"]
            if now - user_data.get("last_message_timestamp", 0) < cooldown: return
            xp_to_add = random.randint(*xp_config["XP_PER_MESSAGE"])
            user_data["last_message_timestamp"] = now
            await self.add_transaction(user_id_str, "message_count", 1, reason)
        elif isinstance(source, int): # Direct XP grant
            xp_to_add = source
        
        if xp_to_add == 0: return

        # --- Application des boosts d'XP ---
        total_boost = 1.0
        
        # Prestige boost
        prestige_config = self.config.get("GAMIFICATION_CONFIG", {}).get("PRESTIGE_LEVELS", {})
        prestige_bonus = 0.0
        for level_str, data in sorted(prestige_config.items(), key=lambda x: int(x[0]), reverse=True):
            if user_data['level'] >= int(level_str):
                prestige_bonus = data.get('xp_bonus', 0.0)
                break
        total_boost += prestige_bonus
        
        # VIP Premium boost
        if user_data.get("vip_premium") and user_data.get("vip_premium", {}).get("end_timestamp", 0) > now:
            vip_system = self.config.get("GAMIFICATION_CONFIG", {}).get("VIP_SYSTEM", {})
            consecutive_periods = user_data["vip_premium"].get("consecutive_periods", 1)
            for tier in sorted(vip_system.get("PREMIUM",{}).get("XP_BOOST_TIERS",[]), key=lambda x: x['consecutive_periods'], reverse=True):
                if consecutive_periods >= tier['consecutive_periods']:
                    total_boost += tier['boost']
                    break
        
        # Loyalty boost (permanent)
        total_boost += user_data.get("loyalty_xp_bonus", 0.0)

        # Active Boosters (from shop, cumulative)
        if user_data.get("active_boosts"):
            for boost in user_data["active_boosts"]:
                if boost.get("type") == "xp" and boost.get("expires_at", 0) > now:
                    total_boost += boost.get("rate", 0.0)
        
        final_xp = int(xp_to_add * total_boost)
        
        await self.add_transaction(user_id_str, "xp", final_xp, reason)
        await self.add_transaction(user_id_str, "weekly_xp", final_xp, f"Gain hebdomadaire: {reason}")

        # Add XP to guild if member of one
        guild_id = user_data.get("guild_id")
        if guild_id and str(guild_id) in self.guild_data:
            async with self.data_lock:
                self.guild_data[str(guild_id)]["total_xp"] = self.guild_data[str(guild_id)].get("total_xp", 0) + final_xp
                self.guild_data[str(guild_id)]["weekly_xp"] = self.guild_data[str(guild_id)].get("weekly_xp", 0) + final_xp
        
        await self.check_level_up(user)
        await self.check_achievements(user)
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        await self._save_json_data_async(self.GUILD_DATA_FILE, self.guild_data)


    async def check_referral_milestones(self, user: discord.Member):
        user_id_str = str(user.id)
        user_data = self.user_data[user_id_str]
        xp_config = self.config.get("GAMIFICATION_CONFIG", {}).get("XP_SYSTEM", {})

        if not user_data.get("referrer"): return
        
        referrer_id_str = user_data["referrer"]
        self.initialize_user_data(referrer_id_str)
        referrer = user.guild.get_member(int(referrer_id_str))
        if not referrer: return

        # Milestone: le filleul atteint le niveau 5
        if user_data["level"] >= 5 and not user_data.get("lvl5_milestone_rewarded"):
            join_ts = user_data.get("join_timestamp", 0)
            limit_days = xp_config.get("REFERRAL_LVL_5_DAYS_LIMIT", 7)
            if (datetime.now(timezone.utc).timestamp() - join_ts) < (limit_days * 86400):
                xp_gain = xp_config["XP_BONUS_REFERRAL_HITS_LVL_5"]
                await self.grant_xp(referrer, xp_gain, f"Filleul {user.display_name} a atteint le niveau 5")
                user_data["lvl5_milestone_rewarded"] = True
                try:
                    await referrer.send(f"🚀 Votre filleul {user.mention} a atteint le niveau 5 rapidement ! Vous gagnez **{xp_gain} XP** bonus !")
                except discord.Forbidden: pass

    async def check_level_up(self, user: discord.Member):
        user_id_str = str(user.id)
        user_data = self.user_data[user_id_str]

        if user_data.get("xp_gated", False): return

        xp_config = self.config["GAMIFICATION_CONFIG"]["XP_SYSTEM"]
        base_xp = xp_config["LEVEL_UP_FORMULA_BASE_XP"]
        multiplier = xp_config["LEVEL_UP_FORMULA_MULTIPLIER"]
        old_level = user_data["level"]
        
        target_level = old_level
        while user_data["xp"] >= int(base_xp * (multiplier ** (target_level - 1))):
            target_level += 1
        target_level -=1 # Go back to the level they actually reached

        if target_level == old_level: return

        prestige_config = self.config.get("GAMIFICATION_CONFIG", {}).get("PRESTIGE_LEVELS", {})
        hit_gate = False
        for level_str, challenge_data in sorted(prestige_config.items(), key=lambda x: int(x[0])):
            prestige_level = int(level_str)
            if old_level < prestige_level <= target_level:
                await self.add_transaction(user_id_str, "level", prestige_level - user_data["level"], f"Atteinte du palier de prestige {prestige_level}")
                user_data["xp_gated"] = True
                user_data["current_prestige_challenge"] = challenge_data
                
                dm_embed = discord.Embed(
                    title=f"🏆 Palier de Prestige Atteint : Niveau {prestige_level} !",
                    description=f"Félicitations {user.mention} ! Tu as atteint un jalon important. Pour continuer ta progression, tu dois accomplir un défi spécial.",
                    color=discord.Color.dark_gold()
                )
                dm_embed.add_field(
                    name="Ton Défi de Prestige",
                    value=challenge_data['description'] + "\n\nUtilise la commande `/prestige` pour revoir ce défi ou `/soumettre_defi` lorsque tu l'as complété.",
                    inline=False
                )
                try: await user.send(embed=dm_embed)
                except discord.Forbidden: pass
                hit_gate = True
                break
        
        if not hit_gate and target_level > old_level:
             await self.add_transaction(user_id_str, "level", target_level - old_level, "Montée de niveau")
            
        new_level = user_data["level"]
        
        await self.check_referral_milestones(user)

        channel_name = self.config["CHANNELS"]["LEVEL_UP_ANNOUNCEMENTS"]
        channel = discord.utils.get(user.guild.text_channels, name=channel_name)
        if channel:
            await channel.send(f"🎉 Bravo {user.mention}, tu as atteint le niveau **{new_level}** !")

        try:
            embed_dm = discord.Embed(
                title=f"🎉 Félicitations, tu as atteint le niveau {new_level} !",
                description="Ton activité a payé ! Voici tes récompenses et tes prochains objectifs.",
                color=discord.Color.gold()
            )
            
            reward_text = "Aucune nouvelle récompense de rôle pour ce niveau."
            level_rewards = self.config.get("GAMIFICATION_CONFIG", {}).get("LEVEL_REWARDS", {})
            for level_str, reward_data in level_rewards.items():
                if old_level < int(level_str) <= new_level:
                    if reward_data.get("type") == "role":
                        role_name = reward_data.get("value")
                        reward_text = f"Tu as obtenu le rôle **{role_name}** !"
                        role_to_add = discord.utils.get(user.guild.roles, name=role_name)
                        if role_to_add and role_to_add not in user.roles:
                            await user.add_roles(role_to_add, reason=f"Récompense de niveau {new_level}")
            embed_dm.add_field(name="🎁 Récompense de Rôle", value=reward_text, inline=False)
            
            next_aff_tier = next((t for t in sorted(self.config["GAMIFICATION_CONFIG"]["AFFILIATE_SYSTEM"]["COMMISSION_TIERS"], key=lambda x: x['level']) if new_level < t['level']), None)
            
            motivation_text = "Continue comme ça pour débloquer encore plus d'avantages !"
            if next_aff_tier:
                motivation_text += f"\n- **Au niveau {next_aff_tier['level']}** : Ta commission d'affiliation passera à **{next_aff_tier['rate']*100:.0f}%** !"

            embed_dm.add_field(name="🚀 Prochains Objectifs", value=motivation_text, inline=False)
            
            await user.send(embed=embed_dm)
        except (discord.Forbidden, Exception) as e:
            print(f"Erreur lors de l'envoi du DM de level up: {e}")

        await self.check_achievements(user)
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)


    async def check_achievements(self, user: discord.Member):
        user_id_str = str(user.id)
        user_stats = self.user_data[user_id_str]
        for achievement in self.achievements:
            if achievement["id"] in user_stats.get("achievements", []): continue
            trigger = achievement["trigger"]
            trigger_type = trigger["type"]
            trigger_value = trigger["value"]
            user_value = user_stats.get(trigger_type, 0)
            if user_value >= trigger_value:
                await self.grant_achievement(user, achievement)
    
    async def grant_achievement(self, user: discord.Member, achievement: dict):
        user_id_str = str(user.id)
        self.user_data[user_id_str]["achievements"].append(achievement["id"])
        
        xp_reward = achievement.get("reward_xp", 0)
        await self.grant_xp(user, xp_reward, f"Succès: {achievement['name']}")
        
        channel_name = self.config["CHANNELS"]["ACHIEVEMENT_ANNOUNCEMENTS"]
        channel = discord.utils.get(user.guild.text_channels, name=channel_name)
        if channel:
            embed = discord.Embed(title="🏆 Nouveau Succès Débloqué !", description=f"Félicitations {user.mention} pour avoir débloqué le succès **{achievement['name']}** !", color=discord.Color.gold())
            embed.add_field(name="Description", value=achievement['description'], inline=False)
            embed.add_field(name="Récompense", value=f"{xp_reward} XP", inline=False)
            await channel.send(embed=embed)
        print(f"Succès '{achievement['name']}' accordé à {user.name}")
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        
    async def record_purchase(self, user_id: int, product: dict, option: Optional[dict], credit_used: float, guild_id: int) -> tuple[bool, str]:
        user_id_str = str(user_id)
        self.initialize_user_data(user_id_str)
        guild = self.bot.get_guild(guild_id)
        if not guild: return False, "Guilde non trouvée."
        member = guild.get_member(user_id)
        if not member: return False, "Membre non trouvé."
        
        price = option['price'] if option else product.get('price', 0)
        product_display_name = product['name'] + (f" ({option['name']})" if option else "")

        # Handle Subscriptions
        if product.get("type") == "subscription":
            await self.handle_subscription_purchase(member, product)
            return True, "Abonnement enregistré."
        # Handle Boosters
        if product.get("type") == "booster":
            await self.handle_booster_purchase(member, product)
            return True, "Booster activé."


        await self.add_transaction(user_id_str, "purchase_count", 1, f"Achat: {product_display_name}")
        await self.add_transaction(user_id_str, "purchase_total_value", price, f"Achat: {product_display_name}")
        
        if credit_used > 0:
            await self.add_transaction(user_id_str, "store_credit", -credit_used, f"Achat avec crédit: {product_display_name}")
        
        xp_per_eur = self.config["GAMIFICATION_CONFIG"]["XP_SYSTEM"]["XP_PER_EURO_SPENT"]
        xp_from_purchase = int(price * xp_per_eur)
        await self.grant_xp(member, xp_from_purchase, f"Achat: {product_display_name}")
        
        await self.log_public_transaction(
            guild,
            f"🛒 **{member.display_name}** a acheté `{product_display_name}`.",
            f"**Valeur :** `{price:.2f} {product.get('currency', 'EUR')}`",
            discord.Color.blue()
        )
        
        referrer_id_str = self.user_data[user_id_str].get("referrer")
        if referrer_id_str:
            self.initialize_user_data(referrer_id_str)
            referrer = guild.get_member(int(referrer_id_str))
            if referrer:
                # Intelligent Commission
                purchase_cost = product.get('purchase_cost', 0.0)
                if option and 'purchase_cost' in option:
                    purchase_cost = option.get('purchase_cost', 0.0)

                commissionable_amount = price
                if product.get('margin_type') == 'net' and purchase_cost >= 0:
                    commissionable_amount = max(0, price - purchase_cost)
                
                # Commission Rate Calculation
                affiliate_config = self.config["GAMIFICATION_CONFIG"]["AFFILIATE_SYSTEM"]
                referrer_data = self.user_data[referrer_id_str]
                referrer_level = referrer_data.get('level', 1)
                
                # Base Rate
                base_rate = 0.0
                for tier in sorted(affiliate_config["COMMISSION_TIERS"], key=lambda x: x['level'], reverse=True):
                    if referrer_level >= tier['level']:
                        base_rate = tier['rate']
                        break
                
                # Get best temporary booster (non-cumulative)
                best_temp_booster_rate = 0.0
                weekly_booster = referrer_data.get('affiliate_booster', 0.0)
                
                active_shop_boosters = [
                    b['rate'] for b in referrer_data.get('active_boosts', []) 
                    if b.get('type') == 'commission' and b.get('expires_at', 0) > datetime.now(timezone.utc).timestamp()
                ]
                shop_booster = max(active_shop_boosters) if active_shop_boosters else 0.0
                
                best_temp_booster_rate = max(weekly_booster, shop_booster)

                total_rate = base_rate + best_temp_booster_rate
                
                # Loyalty Bonus (permanent)
                total_rate += referrer_data.get('loyalty_commission_bonus', 0.0)

                # VIP Premium Bonus
                if referrer_data.get("vip_premium") and referrer_data.get("vip_premium", {}).get("end_timestamp", 0) > datetime.now(timezone.utc).timestamp():
                     vip_system = self.config.get("GAMIFICATION_CONFIG", {}).get("VIP_SYSTEM", {})
                     consecutive_periods = referrer_data["vip_premium"].get("consecutive_periods", 1)
                     for tier in sorted(vip_system.get("PREMIUM",{}).get("COMMISSION_BONUS_TIERS",[]), key=lambda x: x['consecutive_periods'], reverse=True):
                         if consecutive_periods >= tier['consecutive_periods']:
                             total_rate += tier['bonus']
                             break

                commission_earned = commissionable_amount * total_rate
                await self.add_transaction(referrer_id_str, "store_credit", commission_earned, f"Commission sur achat de {member.display_name}")
                await self.add_transaction(referrer_id_str, "affiliate_earnings", commission_earned, f"Commission sur achat de {member.display_name}")
                await self.add_transaction(referrer_id_str, "weekly_affiliate_earnings", commission_earned, f"Commission sur achat de {member.display_name}")
                await self.add_transaction(referrer_id_str, "affiliate_sale_count", 1, f"Vente via {member.display_name}")
                
                await self.log_public_transaction(
                    guild,
                    f"🤝 **{referrer.display_name}** a gagné une commission d'affiliation !",
                    f"**Montant :** `{commission_earned:.2f}` crédits\n**Filleul :** `{member.display_name}`",
                    discord.Color.purple()
                )

                try:
                    await referrer.send(f"🎉 Bonne nouvelle ! Votre filleul {member.display_name} a fait un achat. Vous avez gagné **{commission_earned:.2f} crédits** (Taux: {total_rate*100:.1f}%)!")
                except discord.Forbidden: pass
                await self.check_achievements(referrer)
                await self.update_mission_progress(referrer, "affiliate_sale", 1)
                await self.update_mission_progress(referrer, "affiliate_earn", commission_earned)

        
        await self.check_achievements(member)
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        return True, "Achat enregistré avec succès."
    
    async def handle_booster_purchase(self, user: discord.Member, product: dict):
        user_id_str = str(user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]

        now = datetime.now(timezone.utc)
        duration = timedelta(hours=product.get("booster_duration_hours", 0))
        expires_at = now + duration

        new_booster = {
            "type": product.get("booster_type"),
            "rate": product.get("booster_rate"),
            "expires_at": expires_at.timestamp()
        }

        user_data["active_boosts"].append(new_booster)
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        
        try:
            await user.send(f"🚀 Booster activé ! Vous bénéficiez de **+{new_booster['rate']*100:.0f}%** de **{new_booster['type']}** jusqu'à <t:{int(expires_at.timestamp())}:F>.")
        except discord.Forbidden:
            pass


    async def handle_subscription_purchase(self, user: discord.Member, product: dict):
        user_id_str = str(user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]
        
        vip_config = self.config.get("GAMIFICATION_CONFIG", {}).get("VIP_SYSTEM", {}).get("PREMIUM", {})
        aff_pro_config = self.config.get("GAMIFICATION_CONFIG", {}).get("AFFILIATE_SYSTEM", {}).get("AFFILIATE_PRO_SYSTEM", {})
        
        sub_key = None
        role_name = None
        duration_days = 0
        
        if product.get('id') == vip_config.get("PRODUCT_ID"):
            sub_key = "vip_premium"
            role_name = vip_config.get("ROLE_NAME")
            duration_days = vip_config.get("SUBSCRIPTION_DURATION_DAYS", 7)
        elif product.get('id') == aff_pro_config.get("PRODUCT_ID"):
            sub_key = "affiliate_pro"
            role_name = aff_pro_config.get("ROLE_NAME")
            duration_days = aff_pro_config.get("SUBSCRIPTION_DURATION_DAYS", 30)

        if not sub_key:
            return

        role = discord.utils.get(user.guild.roles, name=role_name)
        if role:
            try: await user.add_roles(role, reason=f"Achat abonnement {product['name']}")
            except discord.Forbidden: print(f"Impossible d'ajouter le role {role_name} à {user.name}")
        
        now = datetime.now(timezone.utc)
        duration = timedelta(days=duration_days)
        
        current_sub_data = user_data.get(sub_key)
        consecutive_periods = 1
        
        if current_sub_data and current_sub_data.get("end_timestamp", 0) > now.timestamp():
            end_date = datetime.fromtimestamp(current_sub_data["end_timestamp"], tz=timezone.utc) + duration
            consecutive_periods = current_sub_data.get("consecutive_periods", 0) + 1
        else:
            end_date = now + duration
            consecutive_periods = 1
        
        user_data[sub_key] = {
            "end_timestamp": end_date.timestamp(),
            "consecutive_periods": consecutive_periods
        }
        
        # Grant XP to referrer if VIP purchase
        if sub_key == "vip_premium" and user_data.get("referrer"):
            referrer_id = user_data["referrer"]
            self.initialize_user_data(referrer_id)
            referrer = user.guild.get_member(int(referrer_id))
            if referrer:
                xp_bonus = self.config["GAMIFICATION_CONFIG"]["XP_SYSTEM"]["XP_BONUS_REFERRAL_BUYS_VIP"]
                await self.grant_xp(referrer, xp_bonus, f"Filleul {user.display_name} a acheté le VIP")
                try: await referrer.send(f"💎 Votre filleul {user.mention} a souscrit au VIP Premium ! Vous gagnez **{xp_bonus} XP** !")
                except discord.Forbidden: pass
        
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        
    async def handle_cashout_submission(self, interaction: discord.Interaction, amount_str: str, paypal_email: str):
        try: amount = float(amount_str)
        except ValueError: return await interaction.response.send_message("Le montant doit être un nombre.", ephemeral=True)
        user_id_str = str(interaction.user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]
        cashout_config = self.config["GAMIFICATION_CONFIG"]["CASHOUT_SYSTEM"]
        if not cashout_config["ENABLED"]: return await interaction.response.send_message("Le système de retrait est actuellement désactivé.", ephemeral=True)
        
        # Check account age and level
        if (datetime.now(timezone.utc).timestamp() - user_data.get("join_timestamp", 0)) < (cashout_config["MINIMUM_ACCOUNT_AGE_DAYS"] * 86400):
            return await interaction.response.send_message(f"Votre compte doit avoir au moins {cashout_config['MINIMUM_ACCOUNT_AGE_DAYS']} jours.", ephemeral=True)
        if user_data["level"] < cashout_config["MINIMUM_LEVEL"]:
             return await interaction.response.send_message(f"Vous devez être au moins niveau {cashout_config['MINIMUM_LEVEL']} pour faire un retrait.", ephemeral=True)

        # Check withdrawal threshold
        min_threshold = float('inf')
        for tier in sorted(cashout_config["WITHDRAWAL_THRESHOLDS"], key=lambda x: x['level'], reverse=True):
            if user_data['level'] >= tier['level']:
                min_threshold = tier['threshold']
                break
        if amount < min_threshold: return await interaction.response.send_message(f"Le montant minimum de retrait pour votre niveau est de {min_threshold} crédits.", ephemeral=True)
        
        if amount > user_data["store_credit"]: return await interaction.response.send_message("Vous n'avez pas assez de crédits.", ephemeral=True)
        
        euros_to_send = amount * cashout_config["CREDIT_TO_EUR_RATE"]
        
        await self.add_transaction(user_id_str, "store_credit", -amount, "Demande de retrait")
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        
        channel_name = self.config["CHANNELS"]["CASHOUT_REQUESTS"]
        channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
        if not channel: return await interaction.response.send_message("Erreur: Canal de requêtes de retrait non trouvé.", ephemeral=True)
        
        embed = discord.Embed(title="Nouvelle Demande de Retrait", color=discord.Color.blue(), timestamp=datetime.now())
        embed.add_field(name="Membre", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Montant (Crédit)", value=f"`{amount:.2f}`", inline=True)
        embed.add_field(name="Montant (EUR)", value=f"`{euros_to_send:.2f}`", inline=True)
        embed.add_field(name="Email PayPal", value=f"`{paypal_email}`", inline=False)
        
        msg = await channel.send(embed=embed, view=CashoutRequestView(self))

        async with self.data_lock:
            self.pending_actions['cashouts'][str(msg.id)] = {
                "user_id": interaction.user.id,
                "credit_to_deduct": amount,
                "euros_to_send": euros_to_send,
                "paypal_email": paypal_email
            }
            await self._save_json_data_async(self.PENDING_ACTIONS_FILE, self.pending_actions)

        await interaction.response.send_message("Votre demande de retrait a été envoyée au staff pour validation. Le crédit a été déduit de votre compte et sera remboursé si la demande est refusée.", ephemeral=True)

    @tasks.loop(hours=24)
    async def mission_assignment_task(self):
        """Assigns new daily and weekly missions to users."""
        if not self.config.get("MISSION_SYSTEM", {}).get("ENABLED"):
            return

        print("Début de la tâche d'assignation des missions...")
        guild = self.bot.get_guild(int(self.config["GUILD_ID"]))
        if not guild:
            return

        mission_config = self.config["MISSION_SYSTEM"]
        daily_templates = [m for m in mission_config.get("TEMPLATES", []) if m["type"] == "daily"]
        weekly_templates = [m for m in mission_config.get("TEMPLATES", []) if m["type"] == "weekly"]
        is_weekly_reset_day = datetime.now(timezone.utc).weekday() == 0  # Lundi

        for user_id_str, user_data in list(self.user_data.items()):
            if not user_data.get("missions_opt_in", False):
                continue
            
            member = guild.get_member(int(user_id_str))
            if not member or member.bot:
                continue

            # Assign Daily Mission
            if daily_templates:
                template = random.choice(daily_templates)
                target = random.randint(*template["target_range"])
                reward = random.randint(*template["reward_xp_range"])
                user_data["current_daily_mission"] = {
                    "id": template["id"],
                    "description": template["description"].format(target=target),
                    "target": target, "progress": 0, "reward_xp": reward, "completed": False
                }

            # Assign Weekly Mission
            if is_weekly_reset_day and weekly_templates:
                template = random.choice(weekly_templates)
                target = random.randint(*template["target_range"])
                reward = random.randint(*template["reward_xp_range"])
                user_data["current_weekly_mission"] = {
                    "id": template["id"],
                    "description": template["description"].format(target=target),
                    "target": target, "progress": 0, "reward_xp": reward, "completed": False
                }
            
            try:
                embed = discord.Embed(title="📜 Vos Nouvelles Missions", color=discord.Color.purple())
                if user_data.get("current_daily_mission"):
                    daily = user_data["current_daily_mission"]
                    embed.add_field(name="☀️ Mission Quotidienne", value=f"{daily['description']}\n**Récompense :** `{daily['reward_xp']}` XP", inline=False)
                if user_data.get("current_weekly_mission"):
                    weekly = user_data["current_weekly_mission"]
                    embed.add_field(name="📅 Mission Hebdomadaire", value=f"{weekly['description']}\n**Récompense :** `{weekly['reward_xp']}` XP", inline=False)
                
                embed.set_footer(text="Utilisez /missions pour voir votre progression ou désactiver ces messages.")
                await member.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                print(f"Impossible d'envoyer les missions en DM à {member.display_name}")

        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        print("Tâche d'assignation des missions terminée.")

    async def update_mission_progress(self, user: discord.Member, action_id: str, value: float):
        """Met à jour la progression des missions pour un utilisateur."""
        user_id_str = str(user.id)
        if user_id_str not in self.user_data: return

        missions_to_check = ["current_daily_mission", "current_weekly_mission"]
        for mission_key in missions_to_check:
            mission = self.user_data[user_id_str].get(mission_key)
            if mission and not mission.get("completed") and mission.get("id") == action_id:
                mission["progress"] = min(mission["progress"] + value, mission["target"])
                if mission["progress"] >= mission["target"]:
                    mission["completed"] = True
                    await self.grant_xp(user, mission["reward_xp"], f"Mission complétée: {mission['description']}")
                    try:
                        await user.send(f"🎉 **Mission accomplie !**\n> {mission['description']}\nVous avez gagné **{mission['reward_xp']}** XP !")
                    except discord.Forbidden: pass
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)

    @tasks.loop(hours=1)
    async def check_expired_boosts_task(self):
        """Cleans up expired boosters from user data."""
        now_ts = datetime.now(timezone.utc).timestamp()
        users_to_update = False

        async with self.data_lock:
            for user_id, user_data in self.user_data.items():
                if "active_boosts" in user_data and user_data["active_boosts"]:
                    active_boosts_before = len(user_data["active_boosts"])
                    user_data["active_boosts"] = [
                        b for b in user_data["active_boosts"]
                        if b.get("expires_at", 0) > now_ts
                    ]
                    if len(user_data["active_boosts"]) != active_boosts_before:
                        users_to_update = True
        
        if users_to_update:
            print("Nettoyage des boosters expirés...")
            await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
            print("Nettoyage terminé.")

    @tasks.loop(hours=1)
    async def check_expired_subscriptions_task(self):
        """Checks for expired subscriptions (VIP Premium, Affiliate Pro) and handles roles."""
        now_ts = datetime.now(timezone.utc).timestamp()
        guild_id_str = self.config.get("GUILD_ID")
        if not guild_id_str or guild_id_str == "VOTRE_VRAI_ID_DE_SERVEUR_ICI": return
            
        guild = self.bot.get_guild(int(guild_id_str))
        if not guild: return

        vip_config = self.config.get("GAMIFICATION_CONFIG", {}).get("VIP_SYSTEM", {}).get("PREMIUM", {})
        aff_pro_config = self.config.get("GAMIFICATION_CONFIG", {}).get("AFFILIATE_SYSTEM", {}).get("AFFILIATE_PRO_SYSTEM", {})
        roles_config = self.config.get("ROLES", {})
        
        vip_premium_role = discord.utils.get(guild.roles, name=roles_config.get("VIP_PREMIUM"))
        loyalty_bonus_role = discord.utils.get(guild.roles, name=roles_config.get("LOYALTY_BONUS"))
        affiliate_pro_role = discord.utils.get(guild.roles, name=roles_config.get("AFFILIATE_PRO"))

        users_to_update = {}
        for user_id_str, user_data in self.user_data.items():
            if user_data.get("vip_premium") and user_data["vip_premium"].get("end_timestamp", 0) < now_ts:
                users_to_update[user_id_str] = users_to_update.get(user_id_str, []) + ["vip_premium"]
            if user_data.get("affiliate_pro") and user_data["affiliate_pro"].get("end_timestamp", 0) < now_ts:
                users_to_update[user_id_str] = users_to_update.get(user_id_str, []) + ["affiliate_pro"]

        if not users_to_update: return
        
        print(f"Détection de {len(users_to_update)} abonnements expirés...")
        
        async with self.data_lock:
            for user_id_str, expired_subs in users_to_update.items():
                member = guild.get_member(int(user_id_str))
                
                if "vip_premium" in expired_subs:
                    vip_data = self.user_data[user_id_str]["vip_premium"]
                    consecutive_periods = vip_data.get("consecutive_periods", 1)
                    final_commission_bonus = 0.0
                    for tier in sorted(vip_config.get("COMMISSION_BONUS_TIERS", []), key=lambda x: x['consecutive_periods'], reverse=True):
                        if consecutive_periods >= tier['consecutive_periods']:
                            final_commission_bonus = tier['bonus']
                            break
                    final_xp_boost = 0.0
                    for tier in sorted(vip_config.get("XP_BOOST_TIERS", []), key=lambda x: x['consecutive_periods'], reverse=True):
                        if consecutive_periods >= tier['consecutive_periods']:
                            final_xp_boost = tier['boost']
                            break
                    self.user_data[user_id_str]["loyalty_commission_bonus"] = final_commission_bonus / 2
                    self.user_data[user_id_str]["loyalty_xp_bonus"] = final_xp_boost / 2
                    self.user_data[user_id_str]["vip_premium"] = None
                    if member and vip_premium_role and vip_premium_role in member.roles:
                        await member.remove_roles(vip_premium_role, reason="Abonnement VIP Premium expiré")
                    if member and loyalty_bonus_role and loyalty_bonus_role not in member.roles:
                        await member.add_roles(loyalty_bonus_role, reason="Prime de fidélité après abonnement")
                    print(f"Abonnement VIP Premium expiré et prime de fidélité accordée à {user_id_str}")

                if "affiliate_pro" in expired_subs:
                    self.user_data[user_id_str]["affiliate_pro"] = None
                    if member and affiliate_pro_role and affiliate_pro_role in member.roles:
                        await member.remove_roles(affiliate_pro_role, reason="Abonnement Parrain Pro expiré")
                    print(f"Abonnement Parrain Pro expiré pour {user_id_str}")
            
            await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        
        print("Mise à jour des abonnements expirés terminée.")

    @tasks.loop(hours=168)
    async def weekly_leaderboard_task(self):
        guild_id = int(self.config.get("GUILD_ID", 0))
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        
        print("Début de la tâche de classement hebdomadaire...")
        
        # --- XP Leaderboard ---
        xp_leaderboard_data = {uid: data['weekly_xp'] for uid, data in self.user_data.items() if data.get('weekly_xp', 0) > 0}
        sorted_xp_leaderboard = sorted(xp_leaderboard_data.items(), key=lambda item: item[1], reverse=True)
        
        roles_config = self.config.get("ROLES", {})
        top_xp_roles_names = {1: "LEADERBOARD_TOP_1_XP", 2: "LEADERBOARD_TOP_2_XP", 3: "LEADERBOARD_TOP_3_XP"}
        top_xp_roles = {rank: discord.utils.get(guild.roles, name=roles_config.get(role_name)) for rank, role_name in top_xp_roles_names.items()}
        all_top_xp_roles = [r for r in top_xp_roles.values() if r is not None]

        for member in guild.members:
            if any(role in member.roles for role in all_top_xp_roles):
                await member.remove_roles(*all_top_xp_roles, reason="Réinitialisation du classement hebdo XP")

        xp_winners_text = []
        for i, (user_id, xp) in enumerate(sorted_xp_leaderboard[:3]):
            rank = i + 1
            member = guild.get_member(int(user_id))
            if member:
                role_to_add = top_xp_roles.get(rank)
                if role_to_add: await member.add_roles(role_to_add, reason=f"Top {rank} XP hebdo")
                xp_winners_text.append(f"{'🥇🥈🥉'[rank-1]} **{member.display_name}** avec {int(xp)} XP")
        
        # --- Affiliate Leaderboard ---
        aff_config = self.config["GAMIFICATION_CONFIG"]["AFFILIATE_SYSTEM"]
        aff_winners_text = []
        if aff_config.get("WEEKLY_BOOSTERS", {}).get("ENABLED"):
            aff_leaderboard_data = {uid: data['weekly_affiliate_earnings'] for uid, data in self.user_data.items() if data.get('weekly_affiliate_earnings', 0) > 0}
            sorted_aff_leaderboard = sorted(aff_leaderboard_data.items(), key=lambda item: item[1], reverse=True)
            
            # Reset existing boosters
            for uid in self.user_data: self.user_data[uid]['affiliate_booster'] = 0.0

            boosters = {1: aff_config["WEEKLY_BOOSTERS"]["TOP_1_BOOST"], 2: aff_config["WEEKLY_BOOSTERS"]["TOP_2_BOOST"], 3: aff_config["WEEKLY_BOOSTERS"]["TOP_3_BOOST"]}
            for i, (user_id, earnings) in enumerate(sorted_aff_leaderboard[:3]):
                rank = i + 1
                self.user_data[user_id]['affiliate_booster'] = boosters[rank]
                member = guild.get_member(int(user_id))
                if member:
                     aff_winners_text.append(f"{'🥇🥈🥉'[rank-1]} **{member.display_name}** avec {earnings:.2f} crédits (boost de **+{boosters[rank]*100:.0f}%** pour la semaine)!")

        # --- Announcements ---
        channel_name = self.config["CHANNELS"]["WEEKLY_LEADERBOARD_ANNOUNCEMENTS"]
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel:
            embed = discord.Embed(title="🏆 Récompenses Hebdomadaires ! 🏆", description="Félicitations aux champions de la semaine !", color=discord.Color.gold())
            if xp_winners_text: embed.add_field(name="Podium XP", value="\n".join(xp_winners_text), inline=False)
            if aff_winners_text: embed.add_field(name="Podium Affiliation", value="\n".join(aff_winners_text), inline=False)
            if xp_winners_text or aff_winners_text: await channel.send(embed=embed)

        # --- Reset weekly stats ---
        for uid in self.user_data:
            self.user_data[uid]['weekly_xp'] = 0
            self.user_data[uid]['weekly_affiliate_earnings'] = 0
        
        for guild_id in self.guild_data:
            self.guild_data[guild_id]['weekly_xp'] = 0
            
        await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
        await self._save_json_data_async(self.GUILD_DATA_FILE, self.guild_data)
        print("Tâche de classement hebdomadaire terminée.")

    @weekly_leaderboard_task.before_loop
    @mission_assignment_task.before_loop
    @check_expired_subscriptions_task.before_loop
    @check_expired_boosts_task.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()
    
    async def _get_overwrites_from_config(self, guild: discord.Guild, perms_config: Dict[str, Any], roles_by_name: Dict[str, discord.Role]) -> Dict[discord.Role, discord.PermissionOverwrite]:
        overwrites = {}
        for role_name, perms in perms_config.items():
            target = None
            if role_name == "@everyone":
                target = guild.default_role
            else:
                target = roles_by_name.get(role_name)
            
            if target:
                overwrites[target] = discord.PermissionOverwrite(**perms)
        return overwrites
    
    @app_commands.command(name="setup", description="Crée les rôles et canaux par défaut définis dans config.json.")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        report = ["**Rapport de configuration du serveur :**"]
        
        try:
            report.append("\n**--- Rôles ---**")
            role_config = self.config.get("SERVER_SETUP_CONFIG", {}).get("ROLES", [])
            roles_by_name = {r.name: r for r in guild.roles}
            for role_data in role_config:
                if role_data["name"] not in roles_by_name:
                    try:
                        perms = discord.Permissions(**role_data.get("permissions", {}))
                        color_val = role_data.get("color", "0x000000")
                        color = discord.Color(int(color_val, 16))
                        new_role = await guild.create_role(
                            name=role_data["name"], 
                            permissions=perms, 
                            color=color, 
                            hoist=role_data.get("hoist", False),
                            reason="Configuration automatique du serveur"
                        )
                        roles_by_name[new_role.name] = new_role
                        report.append(f"✅ Rôle **{role_data['name']}** créé.")
                    except Exception as e:
                        report.append(f"❌ Erreur création rôle **{role_data['name']}**: {e}")
                else:
                    report.append(f"☑️ Rôle **{role_data['name']}** existe déjà.")
            
            await asyncio.sleep(1)
            
            report.append("\n**--- Catégories et Canaux ---**")
            category_config = self.config.get("SERVER_SETUP_CONFIG", {}).get("CATEGORIES", {})
            
            for cat_name, cat_data in category_config.items():
                try:
                    overwrites_conf = cat_data.get("permissions", {})
                    # Add staff roles to all category perms
                    for staff_role_name in self.config.get("ROLES", {}).get("STAFF", []):
                        if staff_role_name not in overwrites_conf:
                            overwrites_conf[staff_role_name] = {"view_channel": True}

                    overwrites = await self._get_overwrites_from_config(guild, overwrites_conf, roles_by_name)

                    category = discord.utils.get(guild.categories, name=cat_name)
                    if not category:
                        category = await guild.create_category(cat_name, overwrites=overwrites, reason="Configuration automatique")
                        report.append(f"✅ Catégorie **{cat_name}** créée avec permissions.")
                    else:
                        await category.edit(overwrites=overwrites, reason="Synchro configuration")
                        report.append(f"☑️ Catégorie **{cat_name}** synchronisée.")
                    
                    for chan_data in cat_data.get("channels", []):
                        chan_name = chan_data['name']
                        chan_type = chan_data.get('type', 'text')
                        if not discord.utils.get(guild.channels, name=chan_name):
                            chan_overwrites = await self._get_overwrites_from_config(guild, chan_data.get("permissions", {}), roles_by_name)
                            if chan_type == 'forum':
                                await category.create_forum(chan_name, overwrites=chan_overwrites, reason="Configuration automatique")
                            else:
                                await category.create_text_channel(chan_name, overwrites=chan_overwrites, reason="Configuration automatique")
                            report.append(f"  ✅ Canal **#{chan_name}** ({chan_type}) créé.")
                        else:
                            chan = discord.utils.get(guild.channels, name=chan_name)
                            if chan.category != category:
                                await chan.edit(category=category, sync_permissions=True, reason="Configuration automatique")
                                report.append(f"  ➡️ Canal **#{chan_name}** déplacé vers **{cat_name}**.")
                            else:
                                report.append(f"  ☑️ Canal **#{chan_name}** existe déjà.")

                except Exception as e:
                    report.append(f"❌ Erreur catégorie/canal **{cat_name}**: {e}")
                    traceback.print_exc()

            final_report = "\n".join(report)
            if len(final_report) > 1900: final_report = final_report[:1900] + "\n... (rapport tronqué)"
            await interaction.followup.send(f"Configuration terminée.\n```md\n{final_report}\n```", ephemeral=True)

        except Exception as e:
            report.append(f"\n\n❌ **ERREUR CRITIQUE PENDANT LE SETUP**: {e}")
            traceback.print_exc()
            final_report = "\n".join(report)
            if len(final_report) > 1900: final_report = final_report[:1900] + "\n... (rapport tronqué)"
            await interaction.followup.send(f"Une erreur est survenue.\n```md\n{final_report}\n```", ephemeral=True)

    @app_commands.command(name="sync_commandes", description="[Admin] Force la synchronisation des commandes slash avec Discord.")
    @app_commands.default_permissions(administrator=True)
    async def sync_commands(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(f"✅ Synchronisé {len(synced)} commande(s) avec succès.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur lors de la synchronisation : {e}", ephemeral=True)
            
    @app_commands.command(name="poster_verification", description="Poste le panneau de vérification dans ce canal.")
    @app_commands.default_permissions(administrator=True)
    async def post_verification_panel(self, interaction: discord.Interaction):
        config = self.config.get("VERIFICATION_SYSTEM", {})
        if not config.get("ENABLED"):
            return await interaction.response.send_message("Le système de vérification est désactivé.", ephemeral=True)
        rules_channel_name = self.config["CHANNELS"]["RULES"]
        rules_channel = discord.utils.get(interaction.guild.text_channels, name=rules_channel_name)
        embed = discord.Embed(
            title=config.get("WELCOME_MESSAGE_TITLE", "Bienvenue !"),
            description=config.get("WELCOME_MESSAGE_DESCRIPTION", "").format(rules_channel=rules_channel.mention if rules_channel else f"#{rules_channel_name}"),
            color=discord.Color.green()
        )
        view = VerificationView(self)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("Panneau de vérification posté.", ephemeral=True)
    
    @app_commands.command(name="poster_reglement", description="Poste le règlement du serveur dans ce canal.")
    @app_commands.default_permissions(administrator=True)
    async def poster_reglement(self, interaction: discord.Interaction):
        rules_config = self.config.get("SERVER_RULES")
        if not rules_config:
            return await interaction.response.send_message("Configuration des règles introuvable.", ephemeral=True)
        
        embed = discord.Embed(title=rules_config["TITLE"], description=rules_config["INTRODUCTION"], color=discord.Color.orange())
        rules_text = "\n\n".join(rules_config["RULES_LIST"])
        embed.add_field(name="Règles Générales", value=rules_text)
        embed.set_footer(text=rules_config["CONCLUSION"])

        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("Règlement posté.", ephemeral=True)

    @app_commands.command(name="poster_tickets", description="Poste le panneau de création de tickets dans ce canal.")
    @app_commands.default_permissions(administrator=True)
    async def post_ticket_panel(self, interaction: discord.Interaction):
        config = self.config.get("TICKET_SYSTEM", {})
        embed = discord.Embed(
            title="Support & Aide",
            description=config.get("TICKET_CREATION_MESSAGE", "Cliquez ci-dessous pour ouvrir un ticket."),
            color=discord.Color.blurple()
        )
        view = TicketCreationView(self)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("Panneau de tickets posté.", ephemeral=True)

    @app_commands.command(name="poster_regles_gamification", description="Affiche les règles du système de gamification.")
    @app_commands.default_permissions(administrator=True)
    async def post_gamification_rules(self, interaction: discord.Interaction):
        await interaction.response.defer()
        config = self.config["GAMIFICATION_CONFIG"]
        
        embed = discord.Embed(title="📜 L'Écosystème ResellBoost 📜", description="Ici, votre engagement est récompensé. Plus vous participez, plus vous gagnez d'XP, montez de niveau et débloquez des avantages exclusifs, y compris des gains financiers réels.", color=discord.Color.purple())
        
        # XP
        xp_config = config["XP_SYSTEM"]
        xp_text = (
            f"- **Messages :** `{xp_config['XP_PER_MESSAGE'][0]}-{xp_config['XP_PER_MESSAGE'][1]}` XP par message (cooldown de `{xp_config['ANTI_FARM_COOLDOWN_SECONDS']}`s, `{xp_config['ANTI_FARM_MIN_WORDS']}` mots min).\n"
            f"- **Parrainage :** `{xp_config['XP_PER_VERIFIED_INVITE']}` XP par invité, et des bonus si vos filleuls progressent ou achètent un VIP !\n"
            f"- **Achats :** `{xp_config['XP_PER_EURO_SPENT']}` XP par euro dépensé.\n"
            f"- **Missions & Défis :** La meilleure méthode pour gagner beaucoup d'XP !"
        )
        embed.add_field(name="📈 Comment Gagner de l'XP ?", value=xp_text, inline=False)
        
        # Affiliation
        aff_config = config["AFFILIATE_SYSTEM"]
        aff_tiers_text = "\n".join([f"**Niv. {t['level']}+ :** `{t['rate']*100:.0f}%`" for t in aff_config["COMMISSION_TIERS"]])
        
        aff_pro_config = aff_config.get("AFFILIATE_PRO_SYSTEM", {})
        if aff_pro_config.get("ENABLED"):
            aff_tiers_text += f"\n\n**✨ Parrain Pro :** Souscrivez à l'abonnement pour toucher **{aff_pro_config.get('COMMISSION_RATE', 0.1)*100:.0f}%** des gains de vos filleuls quand ils font un retrait !"

        embed.add_field(name="🤝 Le Système d'Affiliation", value=f"Invitez des membres et touchez une commission sur leurs achats ! Le taux de commission augmente avec votre niveau.\n{aff_tiers_text}\n*Des bonus VIP et hebdomadaires peuvent encore augmenter ce taux !*", inline=False)

        # Cashout
        cash_config = config["CASHOUT_SYSTEM"]
        cash_text = (
            f"Votre **Crédit Boutique** (1 crédit = {cash_config['CREDIT_TO_EUR_RATE']:.2f}€) peut être retiré via PayPal.\n"
            f"**Conditions :** Compte de plus de `{cash_config['MINIMUM_ACCOUNT_AGE_DAYS']}` jours et niveau `{cash_config['MINIMUM_LEVEL']}` minimum."
        )
        embed.add_field(name="💰 Cash Out : Convertir vos Crédits en Euros", value=cash_text, inline=False)
        
        # Prestige
        prestige_config = config["PRESTIGE_LEVELS"]
        prestige_text = f"Tous les 10 niveaux ({', '.join(prestige_config.keys())}), votre progression est bloquée. Accomplissez un **Défi de Prestige** pour continuer. C'est notre façon de valoriser la qualité sur la quantité. Utilisez `/prestige` !"
        embed.add_field(name="🏆 Les Paliers de Prestige", value=prestige_text, inline=False)

        await interaction.channel.send(embed=embed)
        await interaction.followup.send("Règles de gamification postées.", ephemeral=True)
    
    @app_commands.command(name="profil", description="Affiche votre profil de gamification (ou celui d'un autre membre).")
    @app_commands.describe(membre="Le membre dont vous voulez voir le profil.")
    async def profil(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None):
        if not IMAGING_AVAILABLE:
            return await self.profil_embed(interaction, membre)
        
        await interaction.response.defer()
        target_user = membre or interaction.user
        
        try:
            image_file = await self.generate_profile_card(target_user)
            await interaction.followup.send(file=image_file)
        except Exception as e:
            print(f"Erreur lors de la génération de la carte de profil : {e}")
            traceback.print_exc()
            await interaction.followup.send("Une erreur est survenue lors de la création de votre carte de profil. Affichage de la version texte.", ephemeral=True)
            await self.profil_embed(interaction, membre, followup=True)

    async def profil_embed(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None, followup: bool = False):
        target_user = membre or interaction.user
        user_id_str = str(target_user.id)
        self.initialize_user_data(user_id_str)
        data = self.user_data[user_id_str]
        
        embed = discord.Embed(title=f"Profil de {target_user.display_name}", color=target_user.color)
        embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.add_field(name="Niveau", value=data['level'], inline=True)
        embed.add_field(name="XP", value=int(data['xp']), inline=True)
        embed.add_field(name="Crédit Boutique", value=f"{data['store_credit']:.2f} crédits", inline=True)
        achievements_unlocked = len(data.get('achievements',[]))
        total_achievements = len(self.achievements)
        embed.add_field(name="Succès", value=f"{achievements_unlocked}/{total_achievements}", inline=True)
        
        if data.get("xp_gated", False):
            embed.add_field(name="⚠️ Progression Bloquée", value="Tu as atteint un palier de prestige ! Utilise `/prestige` pour voir ton défi.", inline=False)
        
        if followup:
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="prestige", description="Affiche votre défi de prestige actuel pour continuer à progresser.")
    async def prestige(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]

        if not user_data.get("xp_gated", False):
            return await interaction.response.send_message("Tu n'as pas de défi de prestige en attente. Continue de gagner de l'XP !", ephemeral=True)
        
        challenge = user_data.get("current_prestige_challenge")
        if not challenge:
            return await interaction.response.send_message("Erreur : Aucun défi de prestige n'est défini pour toi. Contacte un admin.", ephemeral=True)

        embed = discord.Embed(
            title=f"🏆 Ton Défi de Prestige (Niveau {user_data['level']})",
            description=challenge['description'],
            color=discord.Color.dark_gold()
        )
        embed.set_footer(text="Utilise /soumettre_defi pour valider ce défi.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="recherche", description="Recherche un produit par mots-clés dans le catalogue.")
    @app_commands.describe(mots_cles="Les mots-clés pour votre recherche (ex: 'compte fortnite rare')")
    async def recherche(self, interaction: discord.Interaction, mots_cles: str):
        await interaction.response.defer(ephemeral=True)

        query_words = set(re.findall(r'\w+', mots_cles.lower()))
        
        if not query_words:
            await interaction.followup.send("Veuillez fournir des mots-clés pour la recherche.", ephemeral=True)
            return

        scored_products = []
        for product in self.products:
            searchable_text = f"{product.get('name', '').lower()} {product.get('description', '').lower()} {' '.join(product.get('tags', []))}"
            product_words = set(re.findall(r'\w+', searchable_text))
            
            score = len(query_words.intersection(product_words))
            
            for word in query_words:
                if word in product.get('name', '').lower():
                    score += 2
            
            if score > 0:
                scored_products.append({'product': product, 'score': score})

        if not scored_products:
            await interaction.followup.send("Aucun produit ne correspond à votre recherche. Essayez d'autres mots-clés ou utilisez `/catalogue`.", ephemeral=True)
            return

        sorted_products = sorted(scored_products, key=lambda x: x['score'], reverse=True)
        
        top_results = [item['product'] for item in sorted_products[:5]]

        embed = discord.Embed(
            title=f"🔎 Résultats de recherche pour \"{mots_cles}\"",
            color=discord.Color.blue()
        )

        for product in top_results:
            embed.add_field(
                name=f"🛒 {product.get('name')}",
                value=f"**ID :** `{product.get('id')}`\n*Utilisez `/produit id:{product.get('id')}` pour voir les détails.*",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="cashout", description="Convertissez votre crédit boutique en argent réel.")
    async def cashout(self, interaction: discord.Interaction):
        modal = CashoutModal(self)
        await interaction.response.send_modal(modal)
        
    @app_commands.command(name="classement", description="Affiche le classement général ou celui d'un membre.")
    @app_commands.describe(membre="Le membre dont vous voulez voir le rang.", top="Affiche la page du top (ex: 20 pour voir les rangs 11-20).")
    async def classement(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None, top: Optional[int] = None):
        await interaction.response.defer()
        
        sorted_users = sorted(self.user_data.items(), key=lambda item: item[1].get('xp', 0), reverse=True)
        
        embed = discord.Embed(title="🏆 Classement d'XP 🏆", color=discord.Color.gold())

        if membre:
            try:
                rank = next(i for i, (uid, _) in enumerate(sorted_users) if uid == str(membre.id)) + 1
                user_data = self.user_data[str(membre.id)]
                embed.description = f"**{membre.display_name}** est au rang **#{rank}** avec **{int(user_data.get('xp', 0))}** XP."
            except StopIteration:
                embed.description = f"Le membre **{membre.display_name}** n'est pas encore classé."
        else:
            page_size = 10
            page = 0
            if top:
                page = (top - 1) // page_size
            
            start_index = page * page_size
            end_index = start_index + page_size
            
            paginated_users = sorted_users[start_index:end_index]
            
            if not paginated_users:
                embed.description = "Aucun utilisateur à afficher pour cette page du classement."
            else:
                leaderboard_text = ""
                for i, (uid, data) in enumerate(paginated_users):
                    rank = start_index + i + 1
                    user = interaction.guild.get_member(int(uid))
                    user_name = user.display_name if user else f"Utilisateur Inconnu ({uid})"
                    leaderboard_text += f"`#{rank: <3}` **{user_name}** - {int(data.get('xp', 0))} XP\n"
                
                embed.description = leaderboard_text
                total_pages = math.ceil(len(sorted_users) / page_size)
                embed.set_footer(text=f"Page {page + 1}/{total_pages}")
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="affiliation", description="Affiche le classement des meilleurs parrains.")
    async def affiliation(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        sorted_users = sorted(self.user_data.items(), key=lambda item: item[1].get('affiliate_earnings', 0), reverse=True)
        
        embed = discord.Embed(title="🤝 Classement d'Affiliation 🤝", description="Top des membres ayant gagné le plus de crédits grâce à leurs filleuls.", color=discord.Color.green())
        
        leaderboard_text = ""
        for i, (uid, data) in enumerate(sorted_users[:10]):
            if data.get('affiliate_earnings', 0) == 0: continue
            rank = i + 1
            user = interaction.guild.get_member(int(uid))
            user_name = user.display_name if user else f"Utilisateur Inconnu ({uid})"
            referral_count = data.get('referral_count', 0)
            leaderboard_text += f"`#{rank: <3}` **{user_name}** - {data.get('affiliate_earnings', 0):.2f} crédits ({referral_count} filleuls)\n"
        
        if not leaderboard_text:
            leaderboard_text = "Personne n'a encore gagné de crédit d'affiliation. Invitez vos amis !"
            
        embed.description = leaderboard_text
        await interaction.followup.send(embed=embed)
        
    @app_commands.command(name="acheter_xp", description="Accélérateur: Achetez l'XP manquant pour le prochain niveau.")
    async def buy_xp(self, interaction: discord.Interaction):
        xp_purchase_config = self.config["GAMIFICATION_CONFIG"].get("XP_SYSTEM", {}).get("XP_PURCHASE", {})
        if not xp_purchase_config.get("ENABLED"):
            return await interaction.response.send_message("L'achat d'XP est actuellement désactivé.", ephemeral=True)

        user_id_str = str(interaction.user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]
        
        if user_data.get("xp_gated"):
            return await interaction.response.send_message("Vous ne pouvez pas acheter d'XP tant que vous n'avez pas terminé votre défi de prestige.", ephemeral=True)
        
        xp_config = self.config["GAMIFICATION_CONFIG"]["XP_SYSTEM"]
        base_xp = xp_config["LEVEL_UP_FORMULA_BASE_XP"]
        multiplier = xp_config["LEVEL_UP_FORMULA_MULTIPLIER"]
        
        xp_for_next_level = int(base_xp * (multiplier ** (user_data["level"] -1)))
        xp_needed = xp_for_next_level - user_data["xp"]

        if xp_needed <= 0:
            return await interaction.response.send_message("Vous avez déjà assez d'XP pour le prochain niveau ! Patientez pour la mise à jour.", ephemeral=True)

        cost_per_xp = xp_purchase_config.get("COST_PER_XP_IN_EUR", 0.001)
        total_cost = xp_needed * cost_per_xp

        # VIP Discount
        vip_role = discord.utils.get(interaction.guild.roles, name=self.config["ROLES"].get("VIP"))
        if vip_role and vip_role in interaction.user.roles:
            total_cost *= (1 - xp_purchase_config.get("VIP_DISCOUNT", 0.5))

        embed = discord.Embed(title="🛒 Acheter de l'XP", description=f"Vous êtes sur le point d'acheter l'XP manquant pour passer au niveau **{user_data['level'] + 1}**.", color=discord.Color.blue())
        embed.add_field(name="XP Manquant", value=f"`{int(xp_needed)}` XP", inline=True)
        embed.add_field(name="Coût Total", value=f"`{total_cost:.2f} EUR`", inline=True)
        if vip_role and vip_role in interaction.user.roles:
            embed.set_footer(text=f"Une réduction VIP de {xp_purchase_config.get('VIP_DISCOUNT', 0.5)*100:.0f}% a été appliquée.")

        # This part should ideally reuse the payment flow from catalogue_cog.
        # For simplicity here, we'll just inform the user. A full implementation
        # would create a temporary product or a direct payment request.
        embed.add_field(name="Action", value="Cette fonctionnalité est en cours de finalisation. Pour l'instant, veuillez ouvrir un ticket pour procéder à l'achat.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(name="poster_defi_ia", description="Génère et poste un nouveau défi communautaire.")
    @app_commands.default_permissions(administrator=True)
    async def poster_defi_ia(self, interaction: discord.Interaction):
        if not self.model:
            return await interaction.response.send_message("Le service d'IA est indisponible.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        
        prompt = self.config["AI_PROCESSING_CONFIG"]["AI_CHALLENGE_GENERATION_PROMPT"]
        try:
            response = await self.model.generate_content_async(contents=prompt)
            json_str = response.text.strip().replace("```json", "").replace("```", "")
            challenge_data = json.loads(json_str)

            challenge_id = str(uuid.uuid4())
            self.current_challenge = {
                "id": challenge_id,
                "title": challenge_data["title"],
                "description": challenge_data["description"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await self._save_json_data_async(self.CURRENT_CHALLENGE_FILE, self.current_challenge)

            channel_name = self.config["CHANNELS"]["AI_CHALLENGE"]
            channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
            if channel:
                embed = discord.Embed(
                    title=f"💥 Nouveau Défi : {challenge_data['title']}",
                    description=challenge_data['description'],
                    color=discord.Color.random()
                )
                embed.set_footer(text="Utilisez /soumettre_defi pour participer !")
                await channel.send(embed=embed)
                await interaction.followup.send(f"Nouveau défi posté dans {channel.mention}.", ephemeral=True)
            else:
                await interaction.followup.send(f"Erreur: le canal des défis '{channel_name}' est introuvable.", ephemeral=True)

        except Exception as e:
            print(f"Erreur lors de la génération du défi IA: {e}")
            await interaction.followup.send("Une erreur est survenue lors de la création du défi.", ephemeral=True)


    @app_commands.command(name="soumettre_defi", description="Soumettez votre preuve pour le défi actuel.")
    async def soumettre_defi(self, interaction: discord.Interaction):
        if not self.model:
            return await interaction.response.send_message("Le service d'IA est indisponible pour le moment.", ephemeral=True)
        
        user_id_str = str(interaction.user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]

        challenge_type = "community"
        if user_data.get("xp_gated", False) and user_data.get("current_prestige_challenge"):
            challenge_type = "prestige"
        elif not self.current_challenge:
             return await interaction.response.send_message("Il n'y a pas de défi communautaire actif pour le moment.", ephemeral=True)
        
        if challenge_type == "community" and self.current_challenge['id'] in user_data.get("completed_challenges", []):
            return await interaction.response.send_message("Vous avez déjà complété le défi communautaire actuel !", ephemeral=True)

        modal = ChallengeSubmissionModal(self, challenge_type=challenge_type)
        await interaction.response.send_modal(modal)

    async def handle_challenge_submission(self, interaction: discord.Interaction, submission_text: str, challenge_type: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        user_id_str = str(interaction.user.id)
        user_data = self.user_data[user_id_str]

        if challenge_type == "prestige":
            challenge = user_data.get("current_prestige_challenge")
            challenge_desc = challenge['description'] if challenge else "N/A"
            challenge_id = f"prestige_{user_data['level']}"
        else:
            challenge = self.current_challenge
            challenge_desc = challenge['description']
            challenge_id = challenge['id']

        prompt_template = self.config["AI_PROCESSING_CONFIG"]["AI_CHALLENGE_SUBMISSION_EVALUATION_PROMPT"]
        prompt = prompt_template.format(
            challenge_description=challenge_desc,
            submission_text=submission_text
        )
        
        try:
            response = await self.model.generate_content_async(contents=prompt)
            json_str = response.text.strip().replace("```json", "").replace("```", "")
            eval_data = json.loads(json_str)

            is_valid = eval_data.get("is_valid", False)
            reason = eval_data.get("reason", "L'IA n'a pas pu évaluer votre soumission.")
            xp_reward = eval_data.get("xp_reward", 0)

            if is_valid:
                if challenge_type == "prestige":
                    embed = discord.Embed(title="🏆 Défi de Prestige Réussi ! 🏆", color=discord.Color.green())
                    embed.description = f"**Raison de l'IA :** {reason}\nTa progression est maintenant débloquée ! Continue de gagner de l'XP."
                    user_data["xp_gated"] = False
                    user_data["current_prestige_challenge"] = None
                    await self.check_level_up(interaction.user)
                else: # community challenge
                    await self.grant_xp(interaction.user, xp_reward, f"Défi communautaire: {challenge['title']}")
                    embed = discord.Embed(title="✅ Défi Validé !", color=discord.Color.green())
                    embed.description = f"**Raison de l'IA :** {reason}\n**Récompense :** Vous avez gagné **{xp_reward}** XP !"
                
                if "completed_challenges" not in user_data:
                    user_data["completed_challenges"] = []
                user_data["completed_challenges"].append(challenge_id)
                await self._save_json_data_async(self.USER_DATA_FILE, self.user_data)
            else:
                embed = discord.Embed(title="❌ Défi Refusé", color=discord.Color.red())
                embed.description = f"**Raison de l'IA :** {reason}"

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            print(f"Erreur lors de l'évaluation du défi : {e}")
            traceback.print_exc()
            await interaction.followup.send("Une erreur est survenue lors de l'évaluation de votre défi. Veuillez réessayer plus tard.", ephemeral=True)

    @app_commands.command(name="journal", description="Affiche votre historique personnel de transactions (XP et crédits).")
    async def journal(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id_str = str(interaction.user.id)
        self.initialize_user_data(user_id_str)
        
        log = self.user_data[user_id_str].get("transaction_log", [])
        if not log:
            return await interaction.followup.send("Vous n'avez encore aucune transaction.", ephemeral=True)
            
        embed = discord.Embed(title=f"📜 Journal de Transactions de {interaction.user.display_name}", color=interaction.user.color)
        
        log_text = ""
        for entry in reversed(log[-20:]): # Affiche les 20 plus récentes
            ts = datetime.fromisoformat(entry['timestamp']).strftime('%d/%m %H:%M')
            amount = entry['amount']
            desc = entry['description']
            type_icon = "💰" if entry['type'] == 'store_credit' else "✨"
            
            if amount > 0:
                log_text += f"**[{ts}]** {type_icon} `+{amount:,.0f}` - {desc}\n"
            else:
                log_text += f"**[{ts}]** {type_icon} `{amount:,.0f}` - {desc}\n"

        embed.description = log_text
        embed.set_footer(text="Affichage des 20 transactions les plus récentes.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="missions", description="Affiche vos missions actuelles et permet de gérer les notifications.")
    async def missions(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]

        embed = discord.Embed(title="🎯 Vos Missions Actuelles", color=discord.Color.purple())
        
        daily = user_data.get("current_daily_mission")
        if daily:
            progress_bar = self.create_progress_bar(daily['progress'], daily['target'])
            status = " (✅ Complétée)" if daily.get('completed') else ""
            embed.add_field(
                name="☀️ Mission Quotidienne",
                value=f"{daily['description']}{status}\n{progress_bar} `{int(daily['progress'])}/{int(daily['target'])}`\n**Récompense:** `{daily['reward_xp']} XP`",
                inline=False
            )
        else:
             embed.add_field(name="☀️ Mission Quotidienne", value="Aucune mission quotidienne active.", inline=False)

        weekly = user_data.get("current_weekly_mission")
        if weekly:
            progress_bar = self.create_progress_bar(weekly['progress'], weekly['target'])
            status = " (✅ Complétée)" if weekly.get('completed') else ""
            embed.add_field(
                name="📅 Mission Hebdomadaire",
                value=f"{weekly['description']}{status}\n{progress_bar} `{int(weekly['progress'])}/{int(weekly['target'])}`\n**Récompense:** `{weekly['reward_xp']} XP`",
                inline=False
            )
        else:
             embed.add_field(name="📅 Mission Hebdomadaire", value="Aucune mission hebdomadaire active.", inline=False)
        
        opt_in_status = "Activées" if user_data.get("missions_opt_in", True) else "Désactivées"
        embed.set_footer(text=f"Notifications par message privé : {opt_in_status}")

        await interaction.response.send_message(embed=embed, view=MissionView(self), ephemeral=True)

    async def _update_invite_cache(self, guild: discord.Guild):
        try:
            self.invites_cache[guild.id] = {invite.code: invite for invite in await guild.invites()}
        except discord.Forbidden:
            print(f"Permissions manquantes pour récupérer les invitations sur la guilde {guild.name}")
    
    def get_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.products if p.get('id') == product_id), None)

    def is_affiliate_pro_active(self, user_id_str: str) -> bool:
        """Vérifie si un utilisateur a un abonnement Parrain Pro actif."""
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]
        aff_pro_data = user_data.get("affiliate_pro")
        if not aff_pro_data:
            return False
        return aff_pro_data.get("end_timestamp", 0) > datetime.now(timezone.utc).timestamp()

    def get_product_display_price(self, product: Dict[str, Any], discount: float = 0.0) -> str:
        currency = product.get("currency", "EUR")
        if "options" in product and product.get("options"):
            try:
                prices = [opt['price'] for opt in product['options']]
                min_price = min(prices)
                min_price_disc = min_price * (1 - discount)
                return f"À partir de `{min_price_disc:.2f} {currency}`"
            except (ValueError, TypeError):
                return "`Prix variable`"
        elif "price_text" in product:
            return f"`{product['price_text']}`"
        else:
            price = product.get('price', 0.0)
            if price < 0: return "`Prix sur demande`"
            final_price = price * (1 - discount)
            return f"`{final_price:.2f} {currency}`"
    
    async def create_ticket(self, user: discord.Member, guild: discord.Guild, ticket_type: dict, initial_message: str) -> Optional[discord.TextChannel]:
        category_name = self.config.get("TICKET_SYSTEM", {}).get("TICKET_CATEGORY_NAME", "Tickets")
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            try: category = await guild.create_category(category_name)
            except discord.Forbidden: return None
        ticket_channel_name = f"ticket-{user.name}-{random.randint(1000,9999)}"
        support_roles = [discord.utils.get(guild.roles, name=r) for r in self.config["ROLES"]["SUPPORT"]]
        support_roles = [r for r in support_roles if r is not None]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True),
        }
        for role in support_roles:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        try:
            channel = await guild.create_text_channel(ticket_channel_name, category=category, overwrites=overwrites, topic=f"Ticket de {user.id} - Type: {ticket_type['label']}")
        except discord.Forbidden: return None
        ping_role_name = ticket_type.get("ping_role")
        ping_content = ""
        if ping_role_name:
            ping_role = discord.utils.get(guild.roles, name=ping_role_name)
            if ping_role: ping_content = ping_role.mention
        embed = discord.Embed(title=f"Ticket: {ticket_type['label']}", description=initial_message, color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        embed.set_author(name=f"Ouvert par {user.display_name}", icon_url=user.display_avatar.url)
        embed.set_footer(text=f"ID Utilisateur: {user.id}")
        await channel.send(content=f"{user.mention} {ping_content}".strip(), embed=embed, view=TicketCloseView(self))
        return channel

    async def log_ticket_closure(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self.model: return
        log_channel_name = self.config["CHANNELS"].get("TICKET_LOGS")
        if not log_channel_name: return
        log_channel = discord.utils.get(interaction.guild.text_channels, name=log_channel_name)
        if not log_channel: return
        try:
            messages = [message async for message in channel.history(limit=200, oldest_first=True)]
            transcript = "\n".join([f"[{msg.created_at.strftime('%H:%M')}] {msg.author.display_name}: {msg.content}" for msg in messages])
            prompt_template = self.config.get("TICKET_SYSTEM", {}).get("AI_SUMMARY_PROMPT")
            if not prompt_template: return
            prompt = prompt_template.format(transcript=transcript)
            
            response = await self.model.generate_content_async(contents=prompt)
            json_str = response.text
            match = re.search(r"```(?:json)?\s*({.*?})\s*```", json_str, re.DOTALL)
            if match: json_str = match.group(1)
            summary_data = json.loads(json_str)
            
            try:
                user_id_from_topic = int(channel.topic.split(" ")[2])
                ticket_creator = interaction.guild.get_member(user_id_from_topic)
            except (ValueError, IndexError):
                ticket_creator = None

            embed = discord.Embed(title=f"Log du Ticket: {channel.name}", color=discord.Color.dark_grey(), timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Utilisateur", value=ticket_creator.mention if ticket_creator else "Inconnu", inline=True)
            embed.add_field(name="Fermé par", value=interaction.user.mention, inline=True)
            embed.add_field(name="Sentiment Utilisateur", value=summary_data.get('user_sentiment', 'N/A'), inline=True)
            embed.add_field(name="Résumé du Problème", value=summary_data.get('summary', 'N/A'), inline=False)
            embed.add_field(name="Résolution", value=summary_data.get('resolution', 'N/A'), inline=False)
            embed.add_field(name="Mots-clés", value=", ".join(summary_data.get('keywords', [])), inline=False)
            await log_channel.send(embed=embed)
        except Exception as e:
            print(f"Erreur lors de la journalisation du ticket {channel.name}: {e}")
            await log_channel.send(f"⚠️ Erreur lors de la génération du résumé IA pour le ticket `{channel.name}`. Le transcript brut est archivé dans les logs du bot.")

    async def log_public_transaction(self, guild: discord.Guild, title: str, description: str, color: discord.Color):
        log_config = self.config.get("TRANSACTION_LOG_CONFIG", {})
        if not log_config.get("ENABLED"): return
        
        channel_name = log_config.get("CHANNEL_NAME")
        if not channel_name: return
        
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel: return
        
        embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
        await channel.send(embed=embed)

    def create_progress_bar(self, current, total, length=10):
        if total == 0: return f"[{'='*length}]"
        progress = int((current / total) * length)
        return f"[{'='*progress}{'-'*(length-progress)}]"

    async def generate_profile_card(self, user: discord.Member) -> discord.File:
        user_id_str = str(user.id)
        self.initialize_user_data(user_id_str)
        user_data = self.user_data[user_id_str]

        # --- Configuration ---
        config = self.config.get("PROFILE_CARD_CONFIG", {})
        level = user_data.get("level", 1)
        
        # Choisir la palette de couleurs et le badge en fonction du niveau
        palette = config.get("DEFAULT_PALETTE")
        for tier in sorted(config.get("LEVEL_PALETTES", []), key=lambda x: x['level'], reverse=True):
            if level >= tier['level']:
                palette = tier['palette']
                break
        
        badge_path = None
        for tier in sorted(config.get("LEVEL_BADGES", []), key=lambda x: x['level'], reverse=True):
            if level >= tier['level']:
                badge_path = tier['path']
                break

        W, H = 900, 300
        BG_COLOR = hex_to_rgb(palette['background'])
        
        # La surface peut être un dégradé (liste) ou une couleur unie (str)
        surface_colors = palette['surface']
        if isinstance(surface_colors, list) and len(surface_colors) > 1:
            SURFACE_COLOR_1 = hex_to_rgb(surface_colors[0])
            SURFACE_COLOR_2 = hex_to_rgb(surface_colors[1])
        else:
            solid_color = surface_colors[0] if isinstance(surface_colors, list) else surface_colors
            SURFACE_COLOR_1 = SURFACE_COLOR_2 = hex_to_rgb(solid_color)
            
        TEXT_COLOR = hex_to_rgb(palette['text'])
        ACCENT_COLOR = hex_to_rgb(palette['accent'])

        # --- Création de l'image ---
        # Image principale avec une couleur de fond pour la bordure
        img = Image.new('RGB', (W, H), BG_COLOR)
        
        # Créer le fond en dégradé
        surface_gradient = create_gradient(W - 30, H - 30, SURFACE_COLOR_1, SURFACE_COLOR_2)
        
        # Créer un masque arrondi pour le fond
        mask = Image.new('L', (W - 30, H - 30), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle((0, 0, W - 30, H - 30), radius=20, fill=255)
        
        # Coller le dégradé sur l'image principale en utilisant le masque
        img.paste(surface_gradient, (15, 15), mask)
        
        # Initialiser le dessin sur l'image finale
        draw = ImageDraw.Draw(img)

        # --- Polices ---
        try:
            font_bold = ImageFont.truetype("assets/Inter-Bold.ttf", 40)
            font_regular = ImageFont.truetype("assets/Inter-Regular.ttf", 22)
            font_small = ImageFont.truetype("assets/Inter-Regular.ttf", 18)
        except IOError:
            print("Police Inter non trouvée, utilisation de la police par défaut.")
            font_bold = ImageFont.load_default(size=40)
            font_regular = ImageFont.load_default(size=22)
            font_small = ImageFont.load_default(size=18)

        # --- Avatar ---
        avatar_size = 160
        avatar_pos = (50, (H - avatar_size) // 2)
        avatar_asset = user.display_avatar.with_size(256)
        avatar_data = await avatar_asset.read()
        avatar_img = Image.open(io.BytesIO(avatar_data)).convert("RGBA")
        
        # Masque circulaire pour l'avatar
        avatar_mask = Image.new('L', (avatar_size, avatar_size), 0)
        draw_mask = ImageDraw.Draw(avatar_mask)
        draw_mask.ellipse((0, 0, avatar_size, avatar_size), fill=255)
        
        avatar_img = avatar_img.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
        
        # Bordure autour de l'avatar
        border_size = 8
        draw.ellipse(
            (avatar_pos[0] - border_size//2, avatar_pos[1] - border_size//2, 
             avatar_pos[0] + avatar_size + border_size//2, avatar_pos[1] + avatar_size + border_size//2), 
            fill=ACCENT_COLOR
        )
        img.paste(avatar_img, avatar_pos, avatar_mask)

        # --- Textes ---
        text_x = 250
        # Nom de l'utilisateur
        if level >= config.get("GLOW_EFFECT_LEVEL", 999):
            glow_color = tuple(min(255, c + 50) for c in ACCENT_COLOR) # Couleur d'accent plus claire
            for offset in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
                draw.text((text_x + offset[0], 50 + offset[1]), user.display_name, font=font_bold, fill=glow_color)
        draw.text((text_x, 50), user.display_name, font=font_bold, fill=TEXT_COLOR)

        # Niveau
        draw.text((text_x, 105), f"NIVEAU {level}", font=font_regular, fill=ACCENT_COLOR)
        
        # Informations à droite
        info_x = W - 250
        rank = "N/A"
        try:
            sorted_users = sorted(self.user_data.items(), key=lambda item: item[1].get('xp', 0), reverse=True)
            user_rank = next(i for i, (uid, _) in enumerate(sorted_users) if uid == user_id_str) + 1
            rank = f"#{user_rank}"
        except StopIteration:
            pass

        draw.text((info_x, 55), "Classement", font=font_small, fill=TEXT_COLOR)
        draw.text((info_x, 80), rank, font=font_regular, fill=TEXT_COLOR)
        
        draw.text((info_x + 120, 55), "Crédits", font=font_small, fill=TEXT_COLOR)
        draw.text((info_x + 120, 80), f"{user_data.get('store_credit', 0):.2f}", font=font_regular, fill=TEXT_COLOR)

        # --- Barre d'XP ---
        xp_config = self.config["GAMIFICATION_CONFIG"]["XP_SYSTEM"]
        base_xp = xp_config["LEVEL_UP_FORMULA_BASE_XP"]
        multiplier = xp_config["LEVEL_UP_FORMULA_MULTIPLIER"]
        
        xp_for_current_level = int(base_xp * (multiplier ** (level - 2))) if level > 1 else 0
        xp_for_next_level = int(base_xp * (multiplier ** (level-1)))
        
        current_xp_in_level = user_data.get('xp', 0) - xp_for_current_level
        needed_xp_for_level = xp_for_next_level - xp_for_current_level
        
        xp_progress = current_xp_in_level / needed_xp_for_level if needed_xp_for_level > 0 else 1
        xp_progress = max(0, min(1, xp_progress))

        bar_x, bar_y, bar_w, bar_h = text_x, 190, W - text_x - 50, 30
        
        bar_bg_color = tuple(int(c * 0.5) for c in ACCENT_COLOR) # Couleur d'accent plus sombre
        draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=15, fill=bar_bg_color)
        if xp_progress > 0:
            draw.rounded_rectangle((bar_x, bar_y, bar_x + (bar_w * xp_progress), bar_y + bar_h), radius=15, fill=ACCENT_COLOR)

        xp_text = f"{int(current_xp_in_level)} / {int(needed_xp_for_level)} XP"
        text_bbox = draw.textbbox((0,0), xp_text, font=font_small)
        xp_text_width = text_bbox[2] - text_bbox[0]
        xp_text_height = text_bbox[3] - text_bbox[1]
        draw.text((bar_x + (bar_w - xp_text_width) / 2, bar_y + (bar_h - xp_text_height) / 2 - 2), xp_text, font=font_small, fill=TEXT_COLOR)

        # --- Badge ---
        if badge_path:
            try:
                badge_img = Image.open(badge_path).convert("RGBA")
                badge_size = 80
                badge_img = badge_img.resize((badge_size, badge_size), Image.Resampling.LANCZOS)
                badge_pos = (W - badge_size - 40, H - badge_size - 40)
                img.paste(badge_img, badge_pos, badge_img)
            except FileNotFoundError:
                print(f"Fichier de badge introuvable : {badge_path}")

        # --- Sauvegarde en mémoire ---
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        return discord.File(buffer, filename=f"profil_{user.id}.png")

async def setup(bot: commands.Bot):
    await bot.add_cog(ManagerCog(bot))
