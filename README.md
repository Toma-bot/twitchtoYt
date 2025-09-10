# 🎥 twitchtoYt

twitchtoYt est un projet open-source qui automatise le processus complet :
de Twitch → à YouTube, en passant par le découpage, la création de miniatures, et la génération de métadonnées optimisées.

Objectif : Gagner du temps et transformer automatiquement les VODs Twitch en vidéos YouTube prêtes à être publiées.


## 🚀 Fonctionnalités
- 🔑 Authentification OAuth **Twitch** et **YouTube**
- ⏬ Téléchargement automatique des VODs Twitch récentes (moins de 48h)
- ✂️ Découpage intelligent des parties de League of Legends grâce à l’OCR du chrono
- 📝 Génération automatique de **titres, descriptions, tags et hashtags**
- 📸 Miniatures générées automatiquement
- 📤 Upload sur **YouTube**
- 🔁 Pipeline complet `run_pipeline.py` orchestrant toutes les étapes

## ⚙️ Installation
### 1. Cloner le repo et créer un environnement virtuel
- git clone https://github.com/Toma-bot/twitchtoYt.git
- cd twitchtoYt
- python -m venv venv
- source venv/bin/activate      # Linux/Mac
- venv\Scripts\activate         # Windows

### 2. Installer les dépendances
pip install -r requirements.txt

### 3. Installer les binaires externes
ffmpeg (requis pour le découpage et l’upload vidéo)
Tesseract OCR (requis pour la détection du chrono)

## 🔑 Configuration

### 1. Twitch
1. Connecte-toi sur Twitch Developer Console
2. Clique sur Register Your Application
3. Remplis le formulaire : 
    - OAuth Redirect URL : http://localhost:3000/callback
    - Category : Application Integration.
4. Récupère et place dans config/.env:
    - Client ID → à placer dans TWITCH_CLIENT_ID
    - New Secret → à placer dans TWITCH_CLIENT_SECRET

### 2. Youtube
1. Va sur Google Cloud Console
2. Crée un projet et active l’API YouTube Data API v3
3. Crée un identifiant OAuth 2.0
4. Télécharge le fichier JSON → renomme-le client_secret_<profile>.json
5. Place le dans config/

Au premier upload, le script ouvrira ton navigateur pour choisir le compte YouTube où publier.

# ▶️ Utilisation
Pour exécuter toutes les étapes : python run_pipeline.py
