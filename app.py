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
    }
</style>
""", unsafe_allow_html=True)

# CHARGEMENT DES ALERTES

def load_alerts():
    """Charge les alertes depuis fichier JSON"""

    if not os.path.exists("alerts.json"):  # Si fichier absent
        return []  # Retourne liste vide

    try:
        with open("alerts.json", "r") as f:  # Ouvre fichier lecture
            return json.load(f)  # Convertit JSON → Python list

    except:
        return []  # En cas d'erreur, liste vide

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

# PAGE LIVE

if page == "📺 LIVE":

    st.subheader("📡 Caméra en direct")

    col1, col2 = st.columns([3, 1])  # Layout 2 colonnes

    with col1:
        st.image(
            "https://img.freepik.com/vecteurs-libre/fond-ecran-videosurveillance-numerique-moderne_23-2148332164.jpg",
            use_container_width=True
        )  # Image caméra

    with col2:
        st.metric("Statut IA", "EN COURS")  # Statut IA
        st.metric("Heure", datetime.now().strftime("%H:%M:%S"))  # Heure réelle

        if st.button("🔄 Refresh"):  # Bouton refresh
            st.rerun()

# PAGE ALERTES

elif page == "🚨 ALERTES":

    st.subheader("🚨 Historique des alertes")

    if not alerts:  # Si aucune alerte
        st.info("Aucune alerte")  # Message info
        st.stop()  # Stop affichage

    # Filtres UI
    type_filter = st.selectbox("Type", ["Tous", "SAC", "CORPS"])
    time_filter = st.selectbox("Période", ["Toutes", "Dernière heure"])

    now = datetime.now()  # Heure actuelle

    filtered = []  # Liste filtrée

    # FILTRAGE ALERTES

    for alert in alerts:

        if type_filter != "Tous" and alert.get("type") != type_filter:
            continue  # Skip si type différent

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

        original_index = alerts.index(alert)  # index réel

        st.markdown(f"""
        <div class="card">
            ⚠️ <b>{alert.get('type')}</b> |
            🕒 {alert.get('time')} |
            🎯 {int(alert.get('score', 0) * 100)}%
        </div>
        """, unsafe_allow_html=True)

        col1, col2 = st.columns([3, 1])

        with col1:
            video_path = alert.get("video_clip", "")

            if video_path and os.path.exists(video_path):
                st.video(video_path)  # vidéo
            else:
                st.warning("Vidéo indisponible")

        with col2:

            # bouton suppression
            if st.button("🗑️ Supprimer", key=f"del_{i}"):
                delete_alert(original_index, video_path)

            # téléchargement vidéo
            if video_path and os.path.exists(video_path):
                with open(video_path, "rb") as f:
                    st.download_button(
                        "📥 Télécharger",
                        f,
                        file_name=f"alert_{alert['time']}.webm"
                    )

# RGPD
st.markdown("""
<div style="font-size:0.8rem;color:gray;margin-top:30px">
<b>RGPD :</b> traitement local des données vidéo pour sécurité uniquement.
Aucune reconnaissance faciale.
</div>
""", unsafe_allow_html=True)