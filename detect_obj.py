from ultralytics import YOLO #charge YOLO le modèle IA
import cv2 #openCV gère la caméra et vidéos
import os  # permet de gérer les dossiers
from datetime import datetime

model = YOLO("runs/detect/train/weights/best.pt") #charge notre model personalisé

cap = cv2.VideoCapture("vidéos/test.mp4")  #ouvre une vidéo 

# récupère infos vidéo (largeur, hauteur, FPS)
fps = cap.get(cv2.CAP_PROP_FPS)

# sécurité : si FPS est invalide
if fps == 0 or fps is None:
    fps = 30  # valeur par défaut

width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

# crée dossier sortie
output_dir = "resultats_video"
os.makedirs(output_dir, exist_ok=True)

# codec plus compatible (IMPORTANT)
fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # meilleur que mp4v sur certains systèmes

# Génère un horodatage (ex: 20231027_143005)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# Nom du fichier avec le timestamp
video_name = f"analyse_{timestamp}.mp4"
output_path = os.path.join(output_dir, video_name)

# Création du writer avec le nouveau chemin
out = cv2.VideoWriter(
    output_path,
    fourcc,
    fps,
    (width, height)
)

# vérifie que le writer fonctionne
if not out.isOpened():
    print(" Erreur: VideoWriter ne s'est pas ouvert")

cv2.namedWindow("YOLO Detection", cv2.WINDOW_NORMAL)#crée une fenêtre redimensionnable

cv2.resizeWindow("YOLO Detection", 1280, 720) # défini une taille initiale

# Boucle infinie pour lire la vidéo image par image
while True:

    # Lire une frame (image) de la vidéo
    ret, frame = cap.read()

    # Si on arrive à la fin de la vidéo → arrêter
    if not ret:
        break

    # Envoyer l'image à YOLO pour détecter les objets
    results = model(frame)

    # Dessiner les résultats (rectangles + noms des objets)
    annotated_frame = results[0].plot()

     # récupère taille actuelle de la fenêtre
    h, w = annotated_frame.shape[:2]

    # récupère taille écran de la fenêtre OpenCV
    _, _, win_w, win_h = cv2.getWindowImageRect("YOLO Detection")

    # resize dynamique de la frame vers la taille de la fenêtre
    resized_frame = cv2.resize(annotated_frame, (win_w, win_h))

    # Afficher la vidéo avec les détections
    cv2.imshow("YOLO Detection", annotated_frame)

    #écrit la frame dans le fichier MP4
    out.write(annotated_frame)

    # Si on appuie sur "q" → quitter le programme
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break


# Libére la vidéo (important pour éviter les bugs)
cap.release()

# Libère le writer vidéo
out.release()

# Fermer toutes les fenêtres ouvertes
cv2.destroyAllWindows()