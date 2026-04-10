import streamlit as st # Framework pour créer l'interface web
import json # Pour lire et modifier le fichier de données alerts.json
import os # Pour manipuler les fichiers (vérifier l'existence, supprimer)
import time # Pour la gestion du temps et les rafraîchissements

st.set_page_config(page_title="Surveillance IA - Leclerc", layout="wide", page_icon="🛡️")

# CSS personnalisé pour améliorer le look
# CSS personnalisé pour améliorer le look
st.markdown("""
    <style>
    .main {
        background-color: #f5f5f5;
    }
    .stButton>button { 
        color: white; background-color: #ff4b4b; border-radius: 5px; 
    }
    .stAlert {
        border-radius: 10px;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ Dashboard Surveillance IA - Preuves Vidéo")

# Fonction pour charger les alertes
def load_alerts():
    if not os.path.exists("alerts.json"):
        return []
    try:
        with open("alerts.json", "r") as f:
            return json.load(f)
    except:
        return []

# FONCTION DE SUPPRESSION 
def delete_alert(index_to_remove, video_path):
    # 1. Supprimer le fichier du PC
    try:
        if os.path.exists(video_path):
            os.remove(video_path)
            st.toast(f"Fichier supprimé : {os.path.basename(video_path)}")
    except Exception as e:
        st.error(f"Erreur lors de la suppression du fichier : {e}")

    # 2. Supprimer de l'historique JSON
    alerts = load_alerts()
    if 0 <= index_to_remove < len(alerts):
        alerts.pop(index_to_remove)
        with open("alerts.json", "w") as f:
            json.dump(alerts, f, indent=4)
    
    # 3. Rafraîchir la page
    st.rerun()

# Menu latéral
page = st.sidebar.radio("Navigation", ["Dashboard Live", "Historique des Alertes"])

# PAGE DASHBOARD (Lien avec ton script principal)
if page == "Dashboard Live":
    st.subheader("📺 Surveillance en cours")
    st.info("Le système analyse actuellement le flux de la caméra CAM_01.")
    st.write("Les preuves vidéo apparaîtront automatiquement dans l'onglet 'Historique' dès qu'une anomalie est détectée.")

# PAGE ALERTES (C'est ici que le gros changement opère)
if page == "Historique des Alertes":
    st.subheader("🚨 Preuves Vidéo ")

    # On utilise un conteneur pour rafraîchir la page
    placeholder = st.empty()

    alerts = load_alerts()
    
    if not alerts:
        st.write("Aucune alerte pour le moment.")
    else:
        # On ne met pas de "while True" ici pour que les boutons Streamlit fonctionnent bien
        # On affiche les alertes du plus récent au plus ancien
        # On utilise enumerate pour garder l'index original pour la suppression
        for i, alert in enumerate(reversed(alerts)):
            # Calcul de l'index réel dans la liste originale (non inversée)
            real_index = len(alerts) - 1 - i
            
            with st.expander(f"🔴 VOL {alert['type']} détecté à {alert['time']}", expanded=True):
                col1, col2 = st.columns([1, 2])
                
                with col1:
                    st.write(f"**Caméra :** {alert['cam']}")
                    st.write(f"**Heure :** {alert['time']}")
                    st.write(f"**Type :** {alert['type']}")
                    
                    # --- BOUTON POUBELLE ---
                    if st.button(f"🗑️ Supprimer l'alerte", key=f"del_{real_index}"):
                        delete_alert(real_index, alert['video_clip'])
                
                with col2:
                    if "video_clip" in alert and os.path.exists(alert["video_clip"]):
                        st.video(alert["video_clip"])
                        
                        # Bouton de secours pour ouvrir le fichier
                        with open(alert["video_clip"], "rb") as f:
                            st.download_button(
                                label="📥 Télécharger",
                                data=f,
                                file_name=os.path.basename(alert["video_clip"]),
                                mime="video/webm",
                                key=f"down_{real_index}"
                            )
                    else:
                        st.warning("Fichier vidéo introuvable sur le PC.")

    # actualisation auto toutes les 5s
    time.sleep(5)