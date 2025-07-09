import os
import discord
from discord.ext import commands
import traceback

# Le token est lu depuis les variables d'environnement
BOT_TOKEN = os.environ.get("DISCORD_TOKEN")

# Configuration des intents (permissions) du bot
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

# Liste des cogs à tester
COGS_TO_LOAD = [
    'cogs.manager_cog',
    'cogs.catalogue_cog',
    'cogs.assistant_cog',
    'cogs.moderator_cog',
    'cogs.giveaway_cog',
    'cogs.guild_cog'
]

class DebugBot(commands.Bot):
    async def setup_hook(self):
        print("--- DÉBUT DU TEST DE CHARGEMENT DES COGS ---")
        for cog_name in COGS_TO_LOAD:
            print(f"Tentative de chargement de : {cog_name}...")
            # On utilise un try...except pour voir l'erreur exacte
            try:
                await self.load_extension(cog_name)
                print(f"✅ SUCCÈS : {cog_name} chargé.")
            except Exception as e:
                print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                print(f"❌ ERREUR CRITIQUE DANS LE COG : {cog_name}")
                print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
                traceback.print_exc() # Affiche l'erreur complète
                # On arrête le bot pour que l'erreur soit visible
                await self.close()
                return
        
        print("--- FIN DU TEST : Tous les cogs ont été chargés sans erreur critique. ---")

    async def on_ready(self):
        print(f"Le bot de débogage est connecté. Si vous voyez ce message, tous les cogs sont OK.")
        print("Vous pouvez maintenant remettre l'ancien code main.py.")
        await self.close() # On l'arrête car ce n'est qu'un test

# Point d'entrée
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERREUR : Le token du bot n'est pas défini.")
    else:
        bot = DebugBot(command_prefix="!", intents=intents)
        bot.run(BOT_TOKEN)

