import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
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

BOT_TOKEN = os.environ.get("DISCORD_TOKEN")

class ResellBoostBot(commands.Bot):
    """
    Classe personnalisée pour le bot.
    """
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.reactions = True
        intents.guilds = True
        intents.invites = True
        super().__init__(command_prefix="!", intents=intents)
        # On garde une trace pour ne synchroniser qu'une seule fois
        self.synced = False

    async def setup_hook(self):
        """
        Charge les extensions (cogs) au démarrage.
        """
        print("--- Démarrage du setup_hook ---")
        for cog_name in COGS_TO_LOAD:
            try:
                await self.load_extension(cog_name)
                print(f"✅ Cog '{cog_name}' chargé avec succès.")
            except Exception as e:
                print(f"❌ Erreur lors du chargement du cog '{cog_name}': {e}")
                traceback.print_exc()

    async def on_ready(self):
        """
        Événement appelé lorsque le bot est connecté et prêt.
        C'EST LE MEILLEUR ENDROIT POUR SYNCHRONISER.
        """
        print("-" * 50)
        print(f"Connecté en tant que {self.user} (ID: {self.user.id})")
        print(f"Le bot est prêt et en ligne sur {len(self.guilds)} serveur(s).")
        
        # --- SYNCHRONISATION FORCÉE ---
        if not self.synced:
            print("Tentative de synchronisation des commandes slash...")
            try:
                # Synchronise pour toutes les guildes où le bot se trouve.
                # C'est plus lent mais beaucoup plus fiable.
                synced_commands = await self.tree.sync()
                print(f"✅ Synchronisé {len(synced_commands)} commande(s) globalement.")
                self.synced = True
            except Exception as e:
                print(f"❌ Erreur critique lors de la synchronisation globale : {e}")
                traceback.print_exc()
        
        print("-" * 50)

# --- Bloc pour le serveur web (pour Cloud Run) ---
app = Flask('')
@app.route('/')
def home():
    return "Le bot est en ligne."

def run_flask():
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)

# --- Point d'entrée principal du script ---
if __name__ == "__main__":
    print("Lancement du service...")

    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    print("Serveur web pour le health check démarré.")
    
    bot = ResellBoostBot()

    print("Lancement du bot Discord...")
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"Une erreur inattendue est survenue lors du lancement du bot: {e}")
        traceback.print_exc()
