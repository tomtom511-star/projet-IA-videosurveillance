from ultralytics import YOLO #charge YOLO le modèle IA
import cv2 #openCV gère la caméra et vidéos
import os  # permet de gérer les dossiers
import math # permet de calculer les distances
from datetime import datetime

# charge notre modèle personnalisé (celui avec hand, bag, person, article)
model = YOLO("runs/detect/train/weights/best.pt") 

cap = cv2.VideoCapture("vidéos/test3.mp4")  #ouvre une vidéo 

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
fourcc = cv2.VideoWriter_fourcc(*"mp4v") 

# Génère un horodatage (ex: 20231027_143005)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# Nom du fichier avec le timestamp
video_name = f"analyse_vol_{timestamp}.mp4"
output_path = os.path.join(output_dir, video_name)

# Création du writer avec le nouveau chemin
out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

# vérifie que le writer fonctionne
if not out.isOpened():
    print(" Erreur: VideoWriter ne s'est pas ouvert")

cv2.namedWindow("YOLO Detection", cv2.WINDOW_NORMAL)#crée une fenêtre redimensionnable
cv2.resizeWindow("YOLO Detection", 1280, 720) # défini une taille initiale

def get_center(box):
    """Calcule le point central d'un rectangle (x1, y1, x2, y2)"""
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int((y1 + y2) / 2))

# Boucle infinie pour lire la vidéo image par image
while True:
    # Lire une frame (image) de la vidéo
    ret, frame = cap.read()

    # Si on arrive à la fin de la vidéo → arrêter
    if not ret:
        break

    # Utilisation de .track() au lieu de .predict()
    # persist=True permet de garder les mêmes IDs entre les images
    results = model.track(frame, persist=True, tracker="botsort.yaml")

    # Listes pour stocker les positions des mains et des sacs sur cette image
    hands_pos = []
    bags_pos = []

    # Vérifier si des objets sont détectés et s'ils ont un ID
    if results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy() # coordonnées
        clss = results[0].boxes.cls.cpu().numpy()   # classes
        
        for box, cls in zip(boxes, clss):
            name = model.names[int(cls)]
            center = get_center(box)

            # On stocke les centres des mains et des sacs
            if name == "hands":
                hands_pos.append(center)
            elif name == "bags":
                bags_pos.append(center)

    # Dessiner les résultats (rectangles + noms des objets)
    annotated_frame = results[0].plot()

    # LOGIQUE DE VOL : Calculer la distance main <-> sac
    for h_center in hands_pos:
        for b_center in bags_pos:
            # Distance mathématique (Pythagore) entre les deux points
            distance = math.sqrt((h_center[0] - b_center[0])**2 + (h_center[1] - b_center[1])**2)
            
            # Si la main est à moins de 80 pixels du sac (seuil à ajuster)
            if distance < 80:
                cv2.putText(annotated_frame, "ALERTE : MOUVEMENT SUSPECT", (50, 80), 
                            cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 4)
                # On peut aussi dessiner une ligne entre les deux pour voir l'interaction
                cv2.line(annotated_frame, h_center, b_center, (0, 0, 255), 2)

    # Afficher la vidéo avec les détections et alertes
    cv2.imshow("YOLO Detection", annotated_frame)

    # Par (Sécurité de taille) : écrit la frame dans le fichier MP4
    frame_to_save = cv2.resize(annotated_frame, (width, height))
    out.write(frame_to_save)

    # Si on appuie sur "q" → quitter le programme
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Libére la vidéo (important pour éviter les bugs)
cap.release()
# Libère le writer vidéo
out.release()
# Fermer toutes les fenêtres ouvertes
cv2.destroyAllWindows()