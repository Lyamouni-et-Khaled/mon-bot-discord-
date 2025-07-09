import os
import asyncio
import discord
from discord.ext import commands
import json
import traceback
from flask import Flask
from threading import Thread

# --- Configuration Globale ---
COGS_TO_LOAD = [
    'cogs.manager_cog',
    'cogs.catalogue_cog',
    'cogs.assistant_cog',
    'cogs.moderator_cog',
    'cogs.giveaway_cog',
    'cogs.guild_cog'
]

# Le token est lu depuis les variables d'environnement, ce qui est sécurisé.
BOT_TOKEN = os.environ.get("DISCORD_TOKEN")


class ResellBoostBot(commands.Bot):
    """
    Classe personnalisée pour le bot, utilisant setup_hook pour un chargement robuste.
    """
    def __init__(self):
        # Configuration des intents (permissions) du bot
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.reactions = True
        intents.guilds = True
        intents.invites = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """
        Hook spécial appelé par discord.py pour la configuration asynchrone.
        C'est l'endroit idéal pour charger les extensions et synchroniser les commandes.
        """
        print("--- Démarrage du setup_hook ---")
        
        # 1. Charger tous les cogs
        for cog_name in COGS_TO_LOAD:
            try:
                await self.load_extension(cog_name)
                print(f"✅ Cog '{cog_name}' chargé avec succès.")
            except Exception as e:
                print(f"❌ Erreur lors du chargement du cog '{cog_name}': {e}")
                traceback.print_exc()

        # 2. Lire la configuration pour l'ID du serveur
        config = {}
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"AVERTISSEMENT: Impossible de lire config.json. La synchronisation est annulée. {e}")
            return

        guild_id_str = config.get("GUILD_ID")
        if not guild_id_str or guild_id_str == "VOTRE_VRAI_ID_DE_SERVEUR_ICI":
            print("ERREUR CRITIQUE: GUILD_ID n'est pas défini dans config.json. Les commandes slash ne seront pas synchronisées.")
            return

        # 3. Synchroniser les commandes pour la guilde spécifique
        try:
            guild_id = int(guild_id_str)
            guild = discord.Object(id=guild_id)
            synced = await self.tree.sync(guild=guild)
            print(f"✅ Synchronisé {len(synced)} commande(s) pour la guilde : {guild_id_str}.")
        except Exception as e:
            print(f"❌ Erreur lors de la synchronisation des commandes pour la guilde {guild_id_str}: {e}")

    async def on_ready(self):
        """Événement appelé lorsque le bot est connecté et prêt."""
        print("-" * 50)
        print(f"Connecté en tant que {self.user} (ID: {self.user.id})")
        print(f"Le bot est prêt et en ligne sur {len(self.guilds)} serveur(s).")
        print("-" * 50)


async def main_bot_logic():
    """Point d'entrée principal pour la logique du bot."""
    if not BOT_TOKEN:
        print("ERREUR CRITIQUE: Le token du bot (DISCORD_TOKEN) n'est pas défini dans l'environnement.")
        return

    # Initialisation et démarrage du bot
    bot = ResellBoostBot()
    await bot.start(BOT_TOKEN)

# --- Bloc pour le serveur web (pour Cloud Run) ---
app = Flask('')

@app.route('/')
def home():
    # Cette page simple répond à Cloud Run pour lui dire que le service est en vie.
    return "Le bot est en ligne."

def run_flask():
  # Le port est fourni par Cloud Run via la variable d'environnement PORT.
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)

# --- Point d'entrée principal du script ---
if __name__ == "__main__":
    print("Lancement du service...")

    # 1. Lance le serveur web dans un thread séparé.
    # C'est ce qui permet de répondre aux "health checks" de Cloud Run.
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    print("Serveur web pour le health check démarré.")

    # 2. Lance le bot Discord.
    # Le code du bot s'exécute dans le thread principal.
    print("Lancement du bot Discord...")
    try:
        asyncio.run(main_bot_logic())
    except KeyboardInterrupt:
        print("\nArrêt du bot.")
    except Exception as e:
        print(f"Une erreur inattendue est survenue lors du lancement du bot: {e}")
        traceback.print_exc()

