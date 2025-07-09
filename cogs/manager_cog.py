import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
from PIL import Image, ImageDraw, ImageFont
import aiofiles # Important pour la lecture/écriture asynchrone
import traceback

class ManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_data_path = "data/user_data.json"
        self.users = {}
        self.bot.loop.create_task(self.load_data_on_startup())

    # --- FONCTIONS DE GESTION DE DONNÉES AVEC DÉBOGAGE ---

    async def _load_json_data_async(self, filename):
        print(f"--- DÉBOGAGE : Tentative de LECTURE du fichier {filename} ---")
        try:
            async with aiofiles.open(filename, 'r', encoding='utf-8') as f:
                content = await f.read()
                data = json.loads(content)
                print(f"--- DÉBOGAGE : LECTURE de {filename} RÉUSSIE. ---")
                return data
        except FileNotFoundError:
            print(f"--- DÉBOGAGE : AVERTISSEMENT - Fichier {filename} non trouvé. Un fichier vide sera créé/utilisé. ---")
            return {}
        except Exception as e:
            print(f"--- DÉBOGAGE : ERREUR CRITIQUE LORS DE LA LECTURE de {filename} ---")
            print(f"--- ERREUR : {e} ---")
            traceback.print_exc()
            return {}

    async def _save_json_data_async(self, data, filename):
        print(f"--- DÉBOGAGE : Tentative de SAUVEGARDE dans {filename} ---")
        try:
            # aiofiles est nécessaire pour les opérations de fichiers asynchrones
            async with aiofiles.open(filename, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=4))
            print(f"--- DÉBOGAGE : SAUVEGARDE dans {filename} RÉUSSIE. ---")
        except Exception as e:
            print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            print(f"--- DÉBOGAGE : ERREUR CRITIQUE LORS DE LA SAUVEGARDE DANS {filename} ---")
            print(f"--- ERREUR : {e} ---")
            print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            traceback.print_exc()

    async def load_data_on_startup(self):
        await self.bot.wait_until_ready()
        print("--- ManagerCog : Chargement des données utilisateur au démarrage ---")
        self.users = await self._load_json_data_async(self.user_data_path)
        print("--- ManagerCog : Données utilisateur chargées. ---")

    # ... (Le reste de votre code de ManagerCog reste ici, je ne l'inclus pas pour la clarté)
    # Assurez-vous que le reste de votre code original de ce fichier est bien présent en dessous.
    # Les seules fonctions à remplacer sont _load_json_data_async et _save_json_data_async
    # Si elles n'existaient pas, ajoutez-les.

    # Exemple de commande qui utilise la sauvegarde
    @app_commands.command(name="test_sauvegarde", description="Teste la sauvegarde des données.")
    async def test_sauvegarde(self, interaction: discord.Interaction):
        """Une commande simple pour forcer une sauvegarde et voir les logs."""
        await interaction.response.send_message("Lancement du test de sauvegarde... Vérifiez les logs du serveur.", ephemeral=True)
        user_id = str(interaction.user.id)
        if user_id not in self.users:
            self.users[user_id] = {"test_data": 1}
        else:
            self.users[user_id]["test_data"] = self.users[user_id].get("test_data", 0) + 1
        
        await self._save_json_data_async(self.users, self.user_data_path)
        print(f"Données pour l'utilisateur {user_id} mises à jour.")

async def setup(bot):
    await bot.add_cog(ManagerCog(bot))


