import cv2
import os
from datetime import datetime

# --- CONFIGURATION DU DOSSIER DE SORTIE ---
# Dossier où les images (frames) seront enregistrées
output_frames_dir = "frame_video"

# Crée le dossier s'il n'existe pas déjà
os.makedirs(output_frames_dir, exist_ok=True)

# --- OUVERTURE DE LA VIDÉO ---
# Remplace par le chemin de ta vidéo
video_path = "vidéos/test4.mp4"
cap = cv2.VideoCapture(video_path)

# Vérifie que la vidéo s'est bien ouverte
if not cap.isOpened():
    print("Erreur : impossible d'ouvrir la vidéo.")
    exit()

# --- VARIABLES ---
frame_count = 0  # Compteur de frames
timestamp_session = datetime.now().strftime("%Y%m%d_%H%M%S")

# --- BOUCLE DE LECTURE VIDÉO ---
while True:
    # Lecture d'une frame
    ret, frame = cap.read()

    # Si la vidéo est terminée, on sort de la boucle
    if not ret:
        break

    # Incrément du compteur de frames
    frame_count += 1

    # --- SAUVEGARDE DES FRAMES ---
    # Ici : on sauvegarde 1 frame toutes les 60 frames (~2 secondes si 30 fps)
    if frame_count % 60 == 0:

        # Nom du fichier image
        img_name = f"frame_{timestamp_session}_{frame_count}.jpg"

        # Chemin complet de sauvegarde
        save_path = os.path.join(output_frames_dir, img_name)

        # Sauvegarde de l'image
        cv2.imwrite(save_path, frame)

        print(f"Frame sauvegardée : {img_name}")

# --- LIBÉRATION DES RESSOURCES ---
cap.release()
print("Extraction terminée.")