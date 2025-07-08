
import discord
from discord.ext import commands
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import os
import re

# Importation de ManagerCog pour l'autocomplétion
from .manager_cog import ManagerCog

# Importation de la librairie Gemini
try:
    import google.generativeai as genai
    from google.generativeai.types import GenerationConfig
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

class ModeratorCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.manager: Optional[ManagerCog] = None
        self.model: Optional[genai.GenerativeModel] = None

    async def cog_load(self):
        # Cette méthode est appelée lors du chargement du cog.
        self.manager = self.bot.get_cog('ManagerCog')
        if not self.manager:
            return print("ERREUR CRITIQUE: ModeratorCog n'a pas pu trouver le ManagerCog.")
        
        if AI_AVAILABLE and self.manager.model:
            self.model = self.manager.model
            print("✅ Moderator Cog: Modèle Gemini partagé par ManagerCog chargé.")
        else:
            print("⚠️ ATTENTION: ModeratorCog désactivé car aucun modèle AI n'est disponible.")

    async def _parse_gemini_json_response(self, text: str) -> Optional[Dict[str, Any]]:
        """Analyse de manière robuste une réponse JSON potentiellement mal formatée de l'IA."""
        match = re.search(r'```(?:json)?\s*({.*?})\s*```', text, re.DOTALL)
        json_str = match.group(1) if match else text
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"Erreur de décodage JSON dans ModeratorCog: {e}\nTexte reçu: {text}")
            return None

    async def query_gemini_moderation(self, message: discord.Message) -> Optional[Dict[str, Any]]:
        if not self.model or not self.manager: return None
        
        mod_config = self.manager.config.get("MODERATION_CONFIG", {})
        prompt_template = mod_config.get("AI_MODERATION_PROMPT")
        if not prompt_template:
             print("ATTENTION: Le prompt de modération IA est manquant dans config.json")
             return {"action": "PASS", "reason": "Configuration IA manquante."}
             
        prompt = prompt_template.format(
            user_message=message.content,
            channel_name=message.channel.name
        )

        try:
            generation_config = GenerationConfig(
                response_mime_type="application/json"
            )
            response = await self.model.generate_content_async(
                contents=prompt,
                generation_config=generation_config
            )
            return await self._parse_gemini_json_response(response.text)
        except Exception as e:
            print(f"Erreur Gemini (Modération): {e}")
            return {"action": "PASS", "reason": f"Erreur d'analyse IA."}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None: return

        mod_config = self.manager.config.get("MODERATION_CONFIG", {})
        if not self.manager or not mod_config.get("ENABLED", False):
            return
        
        # Ignorer les canaux où la promotion est autorisée
        promo_channel = self.manager.config.get("CHANNELS", {}).get("PUBLIC_PROMO")
        marketplace_channel = self.manager.config.get("CHANNELS", {}).get("MARKETPLACE")
        if message.channel.name in [promo_channel, marketplace_channel]:
            return

        # Ignorer le staff
        staff_role_names = self.manager.config.get("ROLES", {}).get("STAFF", [])
        author_roles = [role.name for role in message.author.roles]
        if any(role_name in author_roles for role_name in staff_role_names):
            return

        result = await self.query_gemini_moderation(message)
        if not result: return
        
        action = result.get("action", "PASS")
        reason = result.get("reason", "Aucune raison spécifiée.")

        if action == "PASS": return
        
        action_handlers = {
            "DELETE_AND_WARN": self.handle_delete_and_warn,
            "DELETE_AND_TIMEOUT": self.handle_delete_and_timeout,
            "CREATE_SUPPORT_TICKET": self.handle_create_support_ticket,
            "WARN_PERSONAL_INFO_SHARING": self.handle_warn_personal_info,
            "WARN": self.handle_warn,
            "LOG_MINOR_TOXICITY": self.handle_log_minor_toxicity,
            "NOTIFY_STAFF": self.handle_notify_staff
        }
        
        handler = action_handlers.get(action)
        if handler:
            await handler(message, reason)

    async def handle_delete_and_warn(self, message: discord.Message, reason: str):
        try: await message.delete()
        except discord.NotFound: pass
        await self.apply_warning(message.author, reason, message.jump_url)

    async def handle_delete_and_timeout(self, message: discord.Message, reason: str):
        try: await message.delete()
        except discord.NotFound: pass
        try:
            await message.author.timeout(timedelta(hours=1), reason=f"Modération IA: {reason}")
            await self.notify_staff(message.author.guild, f"L'utilisateur {message.author.mention} a été mis en silencieux pour 1 heure.", f"Raison : {reason}\nMessage original supprimé.")
        except discord.Forbidden:
            await self.notify_staff(message.author.guild, f"ERREUR: Tentative de Mute sur {message.author.mention} a échoué (permissions).", f"Raison : {reason}\nMessage original supprimé.")

    async def handle_create_support_ticket(self, message: discord.Message, reason: str):
        if not self.manager: return
        
        ticket_types = self.manager.config.get("TICKET_SYSTEM", {}).get("TICKET_TYPES", [])
        ticket_type = next((tt for tt in ticket_types if "Signaler" in tt["label"]), ticket_types[0] if ticket_types else None)
        
        if ticket_type:
            initial_message = f"Ticket créé automatiquement par IA.\n**Raison IA :** {reason}\n**Message original de l'utilisateur :**\n> {message.content}"
            ticket_channel = await self.manager.create_ticket(message.author, message.guild, ticket_type, initial_message)
            if ticket_channel:
                await message.reply(f"{message.author.mention}, un ticket de support a été automatiquement créé pour vous. Rendez-vous dans {ticket_channel.mention}.", delete_after=20)
        else:
            await self.notify_staff(message.guild, "L'IA a tenté de créer un ticket, mais aucun type de ticket pour signalement n'est configuré.", f"Message original : [cliquer ici]({message.jump_url})")

    async def handle_warn_personal_info(self, message: discord.Message, reason: str):
        await self.apply_warning(message.author, reason, message.jump_url)
        try: await message.delete()
        except discord.NotFound: pass

    async def handle_warn(self, message: discord.Message, reason: str):
         await self.apply_warning(message.author, reason, message.jump_url)

    async def handle_log_minor_toxicity(self, message: discord.Message, reason: str):
        await self.notify_staff(message.guild, f"Toxicité mineure détectée (surveillance).", f"Raison : {reason}\nMessage de {message.author.mention}: [cliquer ici]({message.jump_url})")

    async def handle_notify_staff(self, message: discord.Message, reason: str):
        await self.notify_staff(message.guild, f"Notification IA.", f"Raison : {reason}\nMessage de {message.author.mention}: [cliquer ici]({message.jump_url})")

    async def notify_staff(self, guild: discord.Guild, title: str, description: str):
        if not self.manager: return
        channel_name = self.manager.config.get("CHANNELS", {}).get("MOD_ALERTS")
        if not channel_name: return
        mod_channel = discord.utils.get(guild.text_channels, name=channel_name)
        if mod_channel:
            embed = discord.Embed(title=f"🚨 {title}", description=description, color=discord.Color.orange())
            await mod_channel.send(embed=embed)

    async def apply_warning(self, member: discord.Member, reason: str, jump_url: str):
        if not self.manager: return
        user_id_str = str(member.id)
        self.manager.initialize_user_data(user_id_str)
        self.manager.user_data[user_id_str]["warnings"] = self.manager.user_data[user_id_str].get("warnings", 0) + 1
        await self.manager._save_json_data_async(self.manager.USER_DATA_FILE, self.manager.user_data)
        
        warning_count = self.manager.user_data[user_id_str]["warnings"]
        threshold = self.manager.config.get("MODERATION_CONFIG", {}).get("WARNING_THRESHOLD", 3)

        try:
            await member.send(f"Vous avez reçu un avertissement sur le serveur **{member.guild.name}** pour la raison suivante : **{reason}**. C'est votre avertissement n°{warning_count}.")
        except discord.Forbidden:
            pass # L'utilisateur a bloqué le bot ou désactivé les DMs

        await self.notify_staff(member.guild, f"Avertissement appliqué à {member.mention}", f"Raison : {reason}\nTotal d'avertissements : **{warning_count}/{threshold}**\n[Lien vers le message]({jump_url})")
        
        if warning_count >= threshold:
            try:
                await member.timeout(timedelta(days=1), reason=f"Seuil d'avertissement ({threshold}) atteint.")
                await self.notify_staff(member.guild, f"Seuil d'avertissement atteint pour {member.mention}", "L'utilisateur a été mis en silencieux pour 24h.")
                self.manager.user_data[user_id_str]["warnings"] = 0 # reset warnings after timeout
                await self.manager._save_json_data_async(self.manager.USER_DATA_FILE, self.manager.user_data)
            except discord.Forbidden:
                 await self.notify_staff(member.guild, f"ERREUR: Tentative de Mute sur {member.mention} a échoué (permissions).", "Seuil d'avertissement atteint.")

async def setup(bot: commands.Bot):
    await bot.add_cog(ModeratorCog(bot))
