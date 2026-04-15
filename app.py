import streamlit as st  # Interface web Streamlit
import json  # Lecture / écriture JSON (alertes)
import os  # Gestion fichiers système
from datetime import datetime, timedelta  # Gestion des heures
from streamlit_cookies_manager import EncryptedCookieManager  # Cookies persistants sécurisés

# IDENTIFIANTS ADMIN (À PROTÉGER EN PRODUCTION)

ADMIN_USER = "admin"  # Identifiant de connexion
ADMIN_PASSWORD = "admin"  # Mot de passe (à changer en prod)

# INITIALISATION COOKIES (PERSISTANCE AUTH)

cookies = EncryptedCookieManager(
    prefix="leclerc_security_",  # Préfixe des cookies (évite conflits)
    password="CHANGE_THIS_SECRET_KEY"  # Clé de chiffrement obligatoire
)

# On bloque l'app tant que les cookies ne sont pas prêts
if not cookies.ready():
    st.stop()

# CONFIGURATION PAGE STREAMLIT

st.set_page_config(
    page_title="E.Leclerc - Sécurité IA",  # Titre onglet navigateur
    layout="wide",  # Layout large
    page_icon="🛡️"  # Icône
)

# STYLE CSS GLOBAL
st.markdown("""
<style>
    .stApp { background-color: white !important; color: #0066b2 !important; }

    [data-testid="stWidgetLabel"] p {
        color: black !important;  /* Labels en noir */
        font-weight: bold !important;
        font-size: 1.05rem !important;
    }

    [data-testid="stSidebar"] {
        background-color: #0066b2 !important;  /* Sidebar bleue */
    }

    [data-testid="stSidebar"] * {
        color: white !important;  /* Texte sidebar blanc */
    }

    .header {
        background-color: #0066b2;  /* Bleu Leclerc */
        color: white;  /* Texte blanc */
        padding: 18px;  /* Espacement interne */
        border-bottom: 6px solid #f39200;  /* Bande orange */
        text-align: center;  /* Centrage */
        border-radius: 0 0 12px 12px;  /* Coins arrondis bas */
        margin-bottom: 20px;  /* Espace en dessous */
    }

    .card {
        background-color: #f8f9fa;  /* Fond gris clair */
        border-left: 6px solid red;  /* Bord rouge alerte */
        padding: 12px;  /* Espacement interne */
        border-radius: 10px;  /* Coins arrondis */
        margin-bottom: 10px;  /* Espace entre cartes */
        color: #333
    }
    /* Bouton refresh normal */
    .stButton > button {
        background-color: white;
        color: #0066b2;
        border-radius: 8px;
        border: none;
    }

    /* Hover inversé */
    .stButton > button:hover {
        background-color: #0066b2;
        color: white;
        border: 2px solid #0066b2;
    }
    /* Bouton refresh normal */
    .st.sidebar.button > button {
        background-color: #0066b2;
        color: white;
        border-radius: 8px;
        border: none;
    }

    /* Hover inversé */
    .st.sidebar.button > button:hover {
        background-color: white;
        color: #0066b2;
        border: 2px solid #0066b2;
    }
</style>
""", unsafe_allow_html=True)

# CHARGEMENT DES ALERTES

def load_alerts():
    """Charge les alertes depuis fichier JSON"""

    if not os.path.exists("alerts.json"):
        return []

    try:
        with open("alerts.json", "r") as f:
            data = json.load(f)

        # sécurité: garantir compatibilité multi-cam
        for a in data:
            if "cam" not in a:
                a["cam"] = "CAM_INCONNUE"

        return data

    except:
        return []

# SUPPRESSION D'ALERTE

def delete_alert(index_to_remove, video_path):
    """Supprime alerte + vidéo associée"""

    if video_path and os.path.exists(video_path):  # Vérifie vidéo existe
        try:
            os.remove(video_path)  # Supprime fichier vidéo
        except:
            pass  # Ignore erreur suppression

    alerts = load_alerts()  # Recharge toutes alertes

    if 0 <= index_to_remove < len(alerts):  # Vérifie index valide
        alerts.pop(index_to_remove)  # Supprime alerte

        with open("alerts.json", "w") as f:  # Réécrit JSON
            json.dump(alerts, f, indent=4)

    st.rerun()  # Recharge interface

# VERIFICATION AUTHENTIFICATION

def is_authenticated():
    """Vérifie si utilisateur est connecté via cookie"""

    return cookies.get("auth") == "true"  # True si cookie actif

# PAGE LOGIN
def login_page():

    st.markdown(
        '<div class="header"><h2>🔐 Accès Sécurisé</h2></div>',
        unsafe_allow_html=True
    )

    col1, col2, col3 = st.columns([1, 2, 1])  # Centrage UI

    with col2:
        user = st.text_input("Identifiant")  # Champ user
        password = st.text_input("Mot de passe", type="password")  # Champ password

        if st.button("Connexion", use_container_width=True):

            # Vérification credentials
            if user == ADMIN_USER and password == ADMIN_PASSWORD:

                cookies["auth"] = "true"  # Stocke cookie login
                cookies.save()  # Sauvegarde persistante

                st.success("Connexion réussie")  # Message succès
                st.rerun()  # Recharge app

            else:
                st.error("Identifiants incorrects")  # Erreur login

    st.stop()  # Bloque accès page principale


# GATE D'ACCÈS GLOBAL (IMPORTANT)

if not is_authenticated():  # Si pas connecté
    login_page()  # Affiche login

# HEADER PRINCIPAL APP

st.markdown(
    '<div class="header"><h1>🛡️ E.Leclerc - Surveillance IA</h1></div>',
    unsafe_allow_html=True
)

# CHARGEMENT DONNÉES

alerts = load_alerts()  # Liste des alertes

# SIDEBAR MENU

st.sidebar.title("📊 Menu")  # Titre sidebar

st.sidebar.metric("Alertes", len(alerts))  # Nombre alertes

page = st.sidebar.radio("MENU", ["📺 LIVE", "🚨 ALERTES"])  # Navigation

# DÉCONNEXION

if st.sidebar.button("🚪 Déconnexion"):

    cookies["auth"] = "false"  # Supprime auth
    cookies.save()  # Sauvegarde cookie

    st.rerun()  # Recharge app


# 📺 PAGE LIVE (MULTI CAMÉRAS PRO)

if page == "📺 LIVE":
    st.subheader("🎥 Surveillance en direct")
    # On utilise des colonnes pour économiser de l'espace
    col_refresh, col_info = st.columns([1, 4])
    with col_refresh:
        if st.button("🔄 Actualiser"):
            st.rerun()
    with col_info:
        st.info("Note : Les flux sont optimisés pour le GPU. Si une image ne s'affiche pas, vérifiez que le script IA tourne.")

    
    # 📍 DÉFINITION DES CAMÉRAS PAR ZONES
    
    cameras = {
        "🏬 Magasin central": [
            {"id": "CAM_01", "name": "Entrée principale", "url": "http://192.168.0.97:5000/video"},
            {"id": "CAM_02", "name": "Rayon alimentaire", "url": "http://192.168.0.97:5001/video"},
            {"id": "CAM_03", "name": "Caisses Automatiques", "url": "http://192.168.0.97:5002/video"},
        ],
        "🌍 Espace culturel": [
            {"id": "CAM_04", "name": "Zone jeux vidéo", "url": "http://192.168.0.97:5003/video"},
            {"id": "CAM_05", "name": "Librairie", "url": "http://192.168.0.97:5004/video"},
            {"id": "CAM_06", "name": "Caisse", "url": "http://192.168.0.97:5005/video"},
        ],
        "🏪 Galerie": [
            {"id": "CAM_07", "name": "Fleuriste", "url": "http://192.168.0.97:5006/video"},
            {"id": "CAM_08", "name": "Bijoux", "url": "http://192.168.0.97:5007/video"},
            {"id": "CAM_09", "name": "Adopt", "url": "http://192.168.0.97:5008/video"},
        ],
        "🚪 Zones sécurisées": [
            {"id": "CAM_10", "name": "Sortie secours", "url": "http://192.168.0.97:5009/video"},
            {"id": "CAM_11", "name": "Réserve", "url": "http://192.168.0.97:5010/video"},
            {"id": "CAM_12", "name": "Personnel", "url": "http://192.168.0.97:5011/video"},
        
        ]
    }  
    for zone, cams in cameras.items():
        with st.expander(f"📍 {zone}", expanded=True): # Utiliser expander réduit la charge CPU si fermé
            for i in range(0, len(cams), 2): # 2 caméras par ligne pour plus de stabilité
                cols = st.columns(2)
                for j in range(2):
                    if i + j < len(cams):
                        cam = cams[i + j]
                        with cols[j]:
                            # CADRE DESIGN LECLERC
                            st.markdown(f"""
                                <div style="background-color:#0066b2; color:white; padding:5px 10px; border-radius:10px 10px 0 0; font-weight:bold;">
                                    🎥 {cam['name']}
                                </div>
                                <div style="border: 4px solid #0066b2; border-radius: 0 0 10px 10px; overflow: hidden;">
                                    <img src="{cam['url']}" style="width: 100%; display: block;" 
                                         onerror="this.onerror=null;this.src='https://via.placeholder.com/1280x720?text=Flux+Indisponible';">
                                </div>
                            """, unsafe_allow_html=True)
                            st.caption(f"ID: {cam['id']} | Flux Temps Réel (NVDEC Accelerated)")

# PAGE ALERTES

elif page == "🚨 ALERTES":

    st.subheader("🚨 Historique des alertes")

    if not alerts:  # Si aucune alerte
        st.info("Aucune alerte")  # Message info
        st.stop()  # Stop affichage

    cams_available = sorted(list(set(a.get("cam", "CAM_INCONNUE") for a in alerts)))
    cams_available.insert(0, "Toutes")

    # Filtres UI
    type_filter = st.selectbox("Type", ["Tous", "SAC", "CORPS"])
    time_filter = st.selectbox("Période", ["Toutes", "Dernière heure"])
    cam_filter = st.selectbox("Caméra", cams_available)

    now = datetime.now()  # Heure actuelle

    filtered = []  # Liste filtrée

    # FILTRAGE ALERTES

    for alert in alerts:

        if type_filter != "Tous" and alert.get("type") != type_filter:
            continue  # Skip si type différent

        if cam_filter != "Toutes" and alert.get("cam") != cam_filter:
            continue

        if time_filter == "Dernière heure":
            try:
                t = datetime.strptime(alert["time"], "%H:%M:%S").replace(
                    year=now.year,
                    month=now.month,
                    day=now.day
                )

                if now - t > timedelta(hours=1):
                    continue  # Skip si trop ancien

            except:
                pass

        filtered.append(alert)  # Ajout si valide

    st.write(f"**{len(filtered)} alertes**")  # compteur


    # AFFICHAGE ALERTES
    for i, alert in enumerate(reversed(filtered)):
        original_index = alerts.index(alert)
        score_percent = int(alert.get('score', 0) * 100)
        
        # Choix des couleurs et du texte de statut selon les critères
        if score_percent < 60:
            main_color, status_text = "#FFD700", "DOUTE IA" # Jaune
        elif score_percent < 85:
            main_color, status_text = "#FF8C00", "CERTITUDE MOYENNE" # Orange
        else:
            main_color, status_text = "#FF0000", "CERTITUDE HAUTE" # Rouge

        # ON OUVRE LE CONTENEUR DE L'ALERTE (pour la marge)
        st.markdown('<div style="margin-bottom: 25px;">', unsafe_allow_html=True)

        # 1. LE HEADER DE COULEUR FUSIONNÉ
        # Un div transparent de couleur qui contient l'info, au-dessus du bloc vidéo
        st.markdown(f"""
            <div style="
                background-color: {main_color};
                color: white; 
                padding: 10px 15px; 
                border-radius: 10px 10px 0 0; 
                font-size: 1.1rem; 
                font-weight: bold; 
                display: flex; 
                justify-content: space-between; 
                align-items: center;
            ">
                <span>⚠️ ALERTE  VOL {alert.get('type')}</span>
                <div style="color:white; font-size:1.1rem; padding: 2px 10px;">🕒 Heure : {alert.get("time")}</div>
                <span style="background: rgba(255,255,255,0.3); padding: 2px 8px; border-radius: 20px;">
                    {status_text} | {score_percent}%
                </span>
            </div>
        """, unsafe_allow_html=True)

        # 2. LE BLOC VIDÉO AVEC BORDURE ÉPAISSE
        # On enferme la vidéo dans un div qui prend la couleur du score
        with st.container():
            # OUVERTURE DU CADRE VIDÉO
            st.markdown(f"""
                <div style="
                    border: 6px solid {main_color}; 
                    border-top: none; 
                    border-radius: 0 0 10px 10px; 
                    background-color: #fcfcfc;
                    box-shadow: 0 4px 10px rgba(0,0,0,0.1);
                ">
            """, unsafe_allow_html=True)

            col_video, col_actions = st.columns([3, 1])
            
            with col_video:
                video_path = alert.get("video_clip", "")
                if video_path and os.path.exists(video_path):
                    st.video(video_path)
                else:
                    st.warning("Flux vidéo indisponible")

            with col_actions:
                st.write("---") # Séparateur
                # Bouton suppression (avec tes keys)
                if st.button("🗑️ Supprimer", key=f"del_{i}"):
                    delete_alert(original_index, video_path)

                # Bouton téléchargement (avec tes keys)
                if video_path and os.path.exists(video_path):
                    with open(video_path, "rb") as f:
                        st.download_button(
                            "📥 Télécharger",
                            f,
                            file_name=f"alert_{alert['time']}.mp4",
                            key=f"dl_{i}"
                        )
                        #L'argument key permet de donner un "nom de famille" unique à chaque bouton pour que Streamlit puisse les différencier.

# RGPD
st.markdown("""
<div style="font-size:0.8rem;color:gray;margin-top:30px">
<b>RGPD :</b> traitement local des données vidéo pour sécurité uniquement.
Aucune reconnaissance faciale.
</div>
""", unsafe_allow_html=True)