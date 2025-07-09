import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands # Nécéssaire pour les décorateurs de commandes
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

    async def setup_hook(self):
        """
        Hook pour la configuration asynchrone.
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
        """Événement appelé lorsque le bot est connecté et prêt."""
        print("-" * 50)
        print(f"Connecté en tant que {self.user} (ID: {self.user.id})")
        print(f"Le bot est prêt et en ligne sur {len(self.guilds)} serveur(s).")
        print("-" * 50)


async def main_bot_logic(bot):
    """Point d'entrée principal pour la logique du bot."""
    if not BOT_TOKEN:
        print("ERREUR CRITIQUE: Le token du bot (DISCORD_TOKEN) n'est pas défini dans l'environnement.")
        return
    await bot.start(BOT_TOKEN)

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
    
    # On instancie le bot ici
    bot = ResellBoostBot()

    # --- COMMANDE DE SYNCHRONISATION MANUELLE ---
    @bot.tree.command(name="sync", description="[Admin] Forcer la synchronisation des commandes slash.")
    @app_commands.default_permissions(administrator=True)
    async def sync(interaction: discord.Interaction):
        """
        Commande spéciale pour forcer la mise à jour des commandes sur le serveur.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            # On synchronise pour la guilde actuelle
            synced = await bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(f"✅ Synchronisé {len(synced)} commande(s) avec succès.", ephemeral=True)
            print(f"Synchronisation manuelle réussie pour la guilde {interaction.guild.id}. {len(synced)} commandes synchronisées.")
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur lors de la synchronisation : {e}", ephemeral=True)
            print(f"Erreur de synchronisation manuelle : {e}")


    print("Lancement du bot Discord...")
    try:
        # On passe l'instance du bot à la logique principale
        asyncio.run(main_bot_logic(bot))
    except KeyboardInterrupt:
        print("\nArrêt du bot.")
    except Exception as e:
        print(f"Une erreur inattendue est survenue lors du lancement du bot: {e}")
        traceback.print_exc()

