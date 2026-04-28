import streamlit as st  # Interface web Streamlit
import json  # Lecture / écriture JSON (alertes)
import os  # Gestion fichiers système
from datetime import datetime, timedelta  # Gestion des heures
from streamlit_cookies_manager import EncryptedCookieManager  # Cookies persistants sécurisés
import requests

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

# STYLE CSS GLOBAL (MIS À JOUR POUR LE HOVER SIDEBAR ET L'ESPACEMENT)
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
    
    /* DESIGN DES BOUTONS */
    div[data-testid="stButton"] > button {
        background-color: white !important;
        color: #0066b2 !important;
        border: 2px solid #0066b2 !important;
        border-radius: 8px !important;
        font-weight: bold !important;
        transition: all 0.2s ease-in-out !important;
    }

    div[data-testid="stButton"] > button:hover,
    div[data-testid="stButton"] > button:hover * {
        background-color: #0066b2 !important;
        color: white !important;
        transform: scale(1.02) !important;
    }
    
    div[data-testid="stDownloadButton"] > button {
        background-color: white !important;
        color: #0066b2 !important;
        border: 2px solid #0066b2 !important;
        border-radius: 8px !important;
        font-weight: bold !important;
        transition: all 0.2s ease-in-out !important;
    }

    div[data-testid="stDownloadButton"] > button:hover,
    div[data-testid="stDownloadButton"] > button:hover * {
        background-color: #0066b2 !important;
        color: white !important;
        transform: scale(1.02) !important;
    }

    /* BOUTONS DANS LA SIDEBAR (CORRECTION DU BLANC SUR BLANC) */
    [data-testid="stSidebar"] div[data-testid="stButton"] > button {
        background-color: #0066b2 !important;
        color: white !important;
        border: 2px solid white !important;
    }

    [data-testid="stSidebar"] div[data-testid="stButton"] > button:hover,
    [data-testid="stSidebar"] div[data-testid="stButton"] > button:hover * {
        background-color: white !important;
        color: #0066b2 !important;  /* Forcer le texte en bleu au survol */
    }
    /* CIBLE EXACTE DU HEADER EXPANDER */
    div[data-testid="stExpander"] > details > summary {
        background-color: #f39200 !important; /* ORANGE */
        color: #0066b2 !important; /* BLEU */
        border-radius: 10px !important;
        padding: 10px 15px !important;
        font-weight: bold !important;
        transition: all 0.2s ease-in-out !important;
    }

    /* TEXTE À L'INTÉRIEUR */
    div[data-testid="stExpander"] > details > summary * {
        color: #0066b2 !important;
    }

    /* HOVER */
    div[data-testid="stExpander"] > details > summary:hover {
        background-color: #0066b2 !important; /* BLEU */
    }

    /* TEXTE HOVER */
    div[data-testid="stExpander"] > details > summary:hover * {
        color: white !important;
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

def delete_alert(index_to_remove, video_path, raw_path=None):
    """Supprime alerte + vidéos associées (IA et RAW)"""

    # Suppression de la vidéo IA
    if video_path and os.path.exists(video_path):  
        try:
            os.remove(video_path)  
        except:
            pass  

    # Suppression de la vidéo RAW sans IA si elle existe
    if raw_path and os.path.exists(raw_path):
        try:
            os.remove(raw_path)
        except:
            pass

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

page = st.sidebar.radio("MENU", ["📺 LIVE", "🚨 ALERTES", "📘 GUIDE D'AMÉLIORATION"])  # Navigation

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
        "🍾 Alcool": [
            {"id": "CAM_01", "name": "🥃​ Rayon alcool fort", "url": "http://192.168.0.97:5000/video/CAM_01"},
            {"id": "CAM_02", "name": "🍷 Vins", "url": "http://192.168.0.97:5000/video/CAM_02"},
            {"id": "CAM_03", "name": "🥂 Champagnes", "url": "http://192.168.0.97:5002/video/CAM_03"},
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

    # --- CORRECTION DE L'INJECTION JAVASCRIPT ---
    # Récupération de l'ordre linéaire de toutes les URLS pour la navigation au clavier
    all_cam_urls = []
    for zone, cams in cameras.items():
        for cam in cams:
            all_cam_urls.append(cam["url"])
    
    js_urls_array = "[" + ",".join([f"'{url}'" for url in all_cam_urls]) + "]"

    # Code Javascript sur UNE SEULE LIGNE (minifié) pour éviter que Streamlit l'affiche comme du Markdown
    js_code = (
        "window.camUrls=" + js_urls_array + ";"
        "window.handleKey=function(e,imgElem){"
        "if(e.key==='ArrowRight'){"
        "let idx=parseInt(imgElem.getAttribute('data-index'));idx=(idx+1)%window.camUrls.length;"
        "imgElem.setAttribute('data-index',idx);imgElem.src=window.camUrls[idx];"
        "}else if(e.key==='ArrowLeft'){"
        "let idx=parseInt(imgElem.getAttribute('data-index'));idx=(idx-1+window.camUrls.length)%window.camUrls.length;"
        "imgElem.setAttribute('data-index',idx);imgElem.src=window.camUrls[idx];"
        "}"
        "};"
        
        # FIX FULLSCREEN
        "window.openFS=function(id){"
        "let el=document.getElementById('container_'+id);"
        "if(!el)return;"
        "if(el.requestFullscreen)el.requestFullscreen();"
        "else if(el.webkitRequestFullscreen)el.webkitRequestFullscreen();"
        "else if(el.msRequestFullscreen)el.msRequestFullscreen();"
        "};"
    )

    # Injection sans sauts de ligne
    st.markdown(f'<img src="dummy" style="display:none;" onerror="{js_code}">', unsafe_allow_html=True)
    # -------------------------------------------------------------

    for zone, cams in cameras.items():
        with st.expander(f"📍 {zone}", expanded=True): # Utiliser expander réduit la charge CPU si fermé
            for i in range(0, len(cams), 2): # 2 caméras par ligne pour plus de stabilité
                cols = st.columns(2)
                for j in range(2):
                    if i + j < len(cams):
                        cam = cams[i + j]
                        global_cam_index = all_cam_urls.index(cam["url"]) # Index global pour le JS
                        with cols[j]:
                            # On simplifie le HTML pour ne garder que le Fullscreen
                            st.markdown(f"""
                                <div id="container_img_{cam['id']}">
                                    <div style="background-color:#0066b2; color:#f39200; padding:5px 10px; border-radius:10px 10px 0 0; font-weight:bold; display: flex; justify-content: space-between; align-items: center;">
                                        <span>🎥 {cam['name']}</span>
                                        <button onclick="window.openFS('img_{cam['id']}')" style="background:none; border:none; color:white; cursor:pointer; font-size:1.2rem;">⛶</button>
                                    </div>
                                    <div style="border: 4px solid #0066b2; border-radius: 0 0 10px 10px; overflow: hidden; background-color: #000;">
                                        <img id="img_{cam['id']}" data-index="{global_cam_index}" src="{cam['url']}" tabindex="0" onkeydown="window.handleKey(event, this)" style="width: 100%; display: block; aspect-ratio: 16/9; object-fit: contain;">
                                    </div>
                                </div>
                            """, unsafe_allow_html=True)
                            st.caption(f"ID: {cam['id']} | Flux Temps Réel")

                            # 👉 BOUTON PYTHON (backend)
                            if st.button(f"📸 Prendre une capture {cam['id']}", key=f"snap_{cam['id']}"):
                                try:
                                    response = requests.post(
                                        "http://192.168.0.97:5000/snapshot",
                                        json={"cam_id": cam["id"]},
                                        timeout=2
                                    )

                                    if response.status_code == 200:
                                        st.success(f"Capture enregistrée 📸 ({cam['id']})")
                                    else:
                                        st.error("Erreur snapshot")

                                except Exception as e:
                                    st.error(f"Erreur connexion caméra : {e}")

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

        # EXTRACTION DE LA DATE
        # Si la date n'est pas dans le JSON, on regarde la date de modification du fichier mp4
        vid_clip = alert.get("video_clip", "")
        vid_raw = alert.get("video_raw", "")
        
        alert_date_str = "Date inconnue"
        if "date" in alert:
            alert_date_str = alert["date"]
        elif vid_clip and os.path.exists(vid_clip):
            timestamp_creation = os.path.getmtime(vid_clip)
            alert_date_str = datetime.fromtimestamp(timestamp_creation).strftime("%d/%m/%Y")

        # ON OUVRE LE CONTENEUR DE L'ALERTE (pour la marge)
        st.markdown('<div style="margin-bottom: 25px;">', unsafe_allow_html=True)

        # 1. LE HEADER DE COULEUR FUSIONNÉ (Ajout de la date)
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
                <span>⚠️ ALERTE VOL {alert.get('type')}</span>
                <div style="color:white; font-size:1.1rem; padding: 2px 10px;">📅 {alert_date_str} - 🕒 {alert.get("time")}</div>
                <span style="background: rgba(255,255,255,0.3); padding: 2px 8px; border-radius: 20px;">
                    {status_text} | {score_percent}%
                </span>
            </div>
        """, unsafe_allow_html=True)

        # 2. LE BLOC VIDÉO AVEC BORDURE ÉPAISSE
        with st.container():
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
            
            # GESTION DU TOGGLE IA / RAW DANS LE SESSION_STATE
            toggle_key = f"toggle_raw_{i}"
            if toggle_key not in st.session_state:
                st.session_state[toggle_key] = False # Par défaut: Vue IA

            is_raw_view = st.session_state[toggle_key]
            
            # Si le mode raw est actif et que la vidéo existe, on la prend, sinon on rabat sur clip
            active_video_path = vid_raw if (is_raw_view and vid_raw and os.path.exists(vid_raw)) else vid_clip

            with col_video:
                if active_video_path and os.path.exists(active_video_path):
                    st.video(active_video_path)
                else:
                    st.warning("Flux vidéo indisponible sur le disque")

            with col_actions:
                # Espace initial pour centrer les boutons par rapport à la vidéo
                st.markdown('<div style="margin-top: 15px;"></div>', unsafe_allow_html=True)
                
                # BOUTON : Toggle Vue IA / Vue Nette
                btn_text = "📹​ Voir la vue naturelle" if not is_raw_view else "🧠​ Voir la vue intelligente "
                if st.button(btn_text, key=f"btn_toggle_{i}", use_container_width=True):
                    st.session_state[toggle_key] = not is_raw_view
                    st.rerun()

                # Bouton suppression (Modifié pour supprimer IA + RAW)
                if st.button("🗑️ Supprimer", key=f"del_{i}", use_container_width=True):
                    delete_alert(original_index, vid_clip, vid_raw)

                # Bouton téléchargement (Télécharge la vidéo affichée à l'écran : IA ou RAW)
                if active_video_path and os.path.exists(active_video_path):
                    with open(active_video_path, "rb") as f:
                        file_suffix = "RAW" if is_raw_view else "IA"
                        st.download_button(
                            "📥 Télécharger",
                            f,
                            file_name=f"alert_{file_suffix}_{alert['time'].replace(':', '')}.mp4",
                            key=f"dl_{i}",
                            use_container_width=True
                        )

elif page == "📘 GUIDE D'AMÉLIORATION":

    st.markdown(
        '<div class="header"><h1>🔄 Guide d\'amélioration continue</h1></div>',
        unsafe_allow_html=True
    )

    st.markdown("""
    <style>
    .step-card {
        background: white;
        border-radius: 14px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 6px 15px rgba(0,0,0,0.06);
        border-left: 6px solid #0066b2;
    }

    .step-title {
        font-size: 1.4rem;
        font-weight: bold;
        color: #0066b2;
        margin-bottom: 10px;
    }

    .step-sub {
        font-size: 1.05rem;
        color: #333;
        margin-bottom: 10px;
    }

    .code-box {
        background: #f4f4f4;
        padding: 10px;
        border-radius: 8px;
        font-family: monospace;
        margin-top: 8px;
        margin-bottom: 8px;
    }

    .warning {
        color: #d00000;
        font-weight: bold;
    }

    .badge {
        display:inline-block;
        background:#f39200;
        color:white;
        padding:3px 8px;
        border-radius:8px;
        font-size:0.8rem;
        margin-left:5px;
    }
    </style>
    """, unsafe_allow_html=True)

    # =========================
    # ÉTAPE 1
    # =========================
    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🧪 ÉTAPE 1 : Identification et Extraction</div>'

            '<div class="step-sub">'
                '<b>Repérage :</b> On analyse les vidéos dans <code>alert_clips/</code><br>'
                '<b>Récupération :</b> <code>alert_clips/raw/</code>'
            '</div>'

            '<div class="step-sub">'
                'Extraction :'
                '<ul>'
                    '<li>1 image / seconde</li>'
                    '<li>uniquement les erreurs visibles</li>'
                '</ul>'
            '</div>'

            '<div class="step-sub">'
                'Script utilisé : <b>frame.py</b><br>'
                '<span class="warning">ATTENTION :</span> bien changer la ligne 14 par le bon chemin de la vidéo'
            '</div>'

            '<div class="code-box">python3 frame.py</div>'
        '</div>',
    unsafe_allow_html=True
    )

    # =========================
    # ÉTAPE 2
    # =========================
    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🧠 ÉTAPE 2 : Mise à jour Dataset Global (Radar)</div>'

            '<div class="step-sub">'
            'Upload des images sur <b>Roboflow Global</b>'
            '</div>'

            '<ul>'
                '<li><b>Upload :</b> Envoi ces images dans ton projet Roboflow Global </li>'
                '<li><b>Correction :</b> Corrige ou ajoute les labels (ID 3 pour la personne, et les autres pour les mains/sacs/articles)</li>'
                '<li><b>Génération :</b> Créé une Nouvelle Version sur Roboflow. Garde tes paramètres d\'augmentation (Blur, Noise, Light) pour que le modèle reste robuste</li>'
                '<li><b>Export :</b> Télécharge le nouveau data.yaml et les images et renomme le Data_global_vX avec X la version du dataset</li>'
                '<li><b>Transport :</b> Déplace le dans le dossier du projet</li>'
            '</ul>'
        '</div>',
    unsafe_allow_html=True)

    # =========================
    # ÉTAPE 3
    # =========================
    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🔧 ÉTAPE 3 : Radar Global</div>'

            '<div class="step-sub">'
            'Aucun besoin d\'amélioration actuellement<br>'
            '<b>Les personnes sont suffisamment bien détectées</b>'
            '</div>'
        '</div>', 
    unsafe_allow_html=True)

    # =========================
    # ÉTAPE 4
    # =========================
    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">✂️ ÉTAPE 4 : Préparation du Spécialiste</div>'

            '<div class="step-sub">'
            'Relancer le script de découpe sur tes nouvelles images en adaptant le script <b>decoupe.py </b>:'
            'Il faut changer les ligne 6 et 7 en ajoutant les version (ex: Data_global_vX ou bien Dataset_specialiste_vX)'
            'On lance '
            '</div>'
            '<div class="code-box">python3 decoupe.py</div>'

            '<div class="step-sub">'
            'Sur vs code, sur le dossier créé par le script de découpe on fait clic droit new file : data.yaml => ici c\'est le meme que la version du radar spécialiste antérieur (sauf si ajout ou suppression de classes) donc on copie colle.'
            '<br>'
            '<span class="warning">ATTENTION :</span> vérifier chemin ligne 1'
            '</div>'

            '<div class="step-sub">'
            'Pour tester que cela fonctionne on lance le script verif.py'
            '<br>'
            '<span class="warning">ATTENTION :</span> changer les lignes 7 et 8'
            '</div>'
            '<div class="code-box">python3 verif.py</div>'

            '<div class="step-sub">'
            'Ensuite va dans le dossier du modèle spécialiste:'
            '</div>'
            '<div class="code-box">cd Dataset_Specialiste_vX</div>'

            '<div class="step-sub">'
            'Puis on crée les dossiers pour séparer les données (valid et train):'
            '</div>'
            '<div class="code-box">mkdir -p images/train images/val labels/train labels/val</div>'

            '<div class="step-sub">'
            'Split : Lancement du script de séparation pour isoler 80% pour le train et 20% pour le valid:'
            '<br>'
            '<span class="warning">ATTENTION :</span> Faire gaffe aux lignes 6 et 7 avec le chemin'
            '</div>'
            '<div class="code-box">python3 split.py</div>'
        '</div>',
     unsafe_allow_html=True)

    # =========================
    # ÉTAPE 5
    # =========================
    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🚀 ÉTAPE 5 : Ré-entraînement du Spécialiste</div>'

            '<div class="code-box">'
            'yolo task=detect mode=train model=runs/detect/radar_specialiste_v(X-1)/weights/best.pt data=Dataset_Specialiste_vX/data.yaml epochs=200 patience=50 imgsz=640 batch=-1 mosaic=1.0 mixup=0.2 cos_lr=True close_mosaic=10 name=specialiste_final_vX'
            '</div>'

            '<div class="step-sub">'
            '<b>best.pt</b> <span class="badge">MEILLEUR</span><br>'
            'C\'est la version qui a eu les meilleurs scores de précision lors des tests de validation.'
            'Pour le Fine-Tuning. C\'est le cerveau le plus "brillant" que l\'on a produit. C\'est la base pour devenir encore meilleur.'
            '</div>'

            '<div class="step-sub">'
            '<b>last.pt</b> <span class="badge">REPRISE</span><br>'
            'C\'est l\'image exacte du modèle à la toute dernière époque de l\'entraînement.'
            'Pour la reprise après crash. Si l\'entraînement a duré 20h et que le PC a planté, on reprend le last.pt pour finir les époques restantes.'
            '</div>'
        '</div>',
    unsafe_allow_html=True)

    # =========================
    # ÉTAPE 6
    # =========================
    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🔄 ÉTAPE 6 : Mise à jour detect_obj.py</div>'

            '<div class="step-sub">'
            'Remplacer simplement dans le script detect_obj.py:'
            '<ul>'
                '<li>model_radar</li>'
                '<li>model_specialiste</li>'
            '</ul>'
            '</div>'

            '<div class="step-sub">'
            '→ vers les nouveaux <b>best.pt</b>'
            '</div>'
        '</div>', 
    unsafe_allow_html=True)

# RGPD
st.markdown("""
<div style="font-size:0.8rem;color:gray;margin-top:30px">
<b>RGPD :</b> traitement local des données vidéo pour sécurité uniquement.
Aucune reconnaissance faciale.
</div>
""", unsafe_allow_html=True)