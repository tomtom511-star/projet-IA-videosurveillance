from ultralytics import YOLO #charge YOLO le modèle IA
import cv2 #openCV gère la caméra et vidéos
import os  # permet de gérer les dossiers
import math # permet de calculer les distances
import json # permet de sauvegarder les alertes
from datetime import datetime
import time
import torch # pour forcer l'utilisation du GPU
from collections import deque # Pour le buffer circulaire

# CONFIGURATION GPU & MODÈLE 
device = 'cuda' if torch.cuda.is_available() else 'cpu'
model = YOLO("runs/detect/essai_surveillance_v113/weights/best.pt").to(device) 

# VARIABLES DE GESTION DES ALERTES 
last_alert_time = 0
ALERT_COOLDOWN = 12 # On passe à 12s (10s de clip + 2s de marge)
DISAPPEARANCE_TIMEOUT = 3.0 # Délai de 3 secondes pour ignorer un demi-tour
FRAME_THRESHOLD = 4

#  TIMER POUR L'AFFICHAGE DU TEXTE
alert_text_to_show = ""
alert_text_timer = 0
DISPLAY_TEXT_DURATION = 4.0 # Le texte restera affiché 4 secondes à l'écran

# GESTION DES CLIPS VIDÉO 
BEFORE_ALERT_SECS = 5
AFTER_ALERT_SECS = 5
is_recording_alert = False
alert_video_writer = None
frames_to_record_after = 0
# Le buffer stockera les images brutes ou annotées selon ton choix
video_buffer = None

cap = cv2.VideoCapture("vidéos/test3.mp4")  #ouvre une vidéo 

# récupère infos vidéo (largeur, hauteur, FPS)
fps = cap.get(cv2.CAP_PROP_FPS)

# sécurité : si FPS est invalide
fps = fps if fps > 1 else 30


width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

# Initialisation du buffer (5 secondes * FPS)
video_buffer = deque(maxlen=int(BEFORE_ALERT_SECS * fps))

# crée dossier sortie
output_dir = "resultats_video"
alert_vid_dir = os.path.join(output_dir, "alert_clips")
os.makedirs(output_dir, exist_ok=True)
os.makedirs(alert_vid_dir, exist_ok=True) # On s'assure que le dossier des clips existe

# fichier JSON pour communiquer avec l'interface
ALERT_FILE = "alerts.json"

# initialise le fichier JSON
if not os.path.exists(ALERT_FILE):
    with open(ALERT_FILE, "w") as f:
        json.dump([], f)

# codec plus compatible (IMPORTANT)
fourcc = cv2.VideoWriter_fourcc(*"VP80") # On utilise VP80 (WebM), c'est l'alternative qui marche partout sur internet

# Génère un horodatage (ex: 20231027_143005)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# Nom du fichier avec le timestamp
video_name = f"analyse_vol_{timestamp}.webm"
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

def is_point_in_box(point, box):
    """Vérifie si un point (x,y) est à l'intérieur d'un rectangle [x1, y1, x2, y2]"""
    px, py = point
    x1, y1, x2, y2 = box
    return x1 <= px <= x2 and y1 <= py <= y2

def start_alert_video(type_vol, score):
    """Initialise l'enregistrement du clip d'alerte"""
    global is_recording_alert, alert_video_writer, frames_to_record_after
    
    timestamp = datetime.now().strftime("%H%M%S")
    vid_name = f"Vole_{type_vol}_{timestamp}.webm"
    vid_path = os.path.abspath(os.path.join(alert_vid_dir, vid_name)) # Chemin complet
    
    # Création du writer pour le clip
    alert_video_writer = cv2.VideoWriter(vid_path, fourcc, fps, (width, height))
    
    # 1. On écrit tout ce qu'il y a dans le buffer (les 5s passées)
    for f in video_buffer:
        alert_video_writer.write(f)
        
    # 2. On prépare l'enregistrement des 5s futures
    is_recording_alert = True
    frames_to_record_after = int(AFTER_ALERT_SECS * fps)
    
    # 3. Mise à jour du JSON
    with open(ALERT_FILE, "r") as f: data = json.load(f)
    data.append({
        "cam": "CAM_01",
        "type": type_vol,
        "score": score, 
        "time": datetime.now().strftime("%H:%M:%S"),
        "video_clip": vid_path
    })
    with open(ALERT_FILE, "w") as f: json.dump(data, f, indent=4)
    
    return vid_path


#Mémoire
suspect_disappearance = {} # Mémoire des objets disparus sur le corps {id_article: timestamp}
last_known_articles = {} # Stocke la dernière position connue de chaque ID d'article
object_hold_counter = {} # mémoire des objets avec compteur de frames
active_objects = [] #mémoire des objets "pris"
last_known_scores = {}        # score YOLO
hold_durations = {}           # durée pendant laquelle l'objet est tenu

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

    # Listes pour stocker les positions
    hands_pos = []
    bags_pos = []
    articles_pos = []
    persons_boxes = []

    # Vérifier si des objets sont détectés
    if results and results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        clss = results[0].boxes.cls.cpu().numpy()
        ids = results[0].boxes.id.cpu().numpy().astype(int)
        confs = results[0].boxes.conf.cpu().numpy()  #  LES SCORES

        for box, cls, track_id, conf in zip(boxes, clss, ids, confs):
            name = model.names[int(cls)]
            center = get_center(box)

            # On stocke les centres des mains, sacs et  articles
            if name == "hands":
                hands_pos.append(center)
            elif name == "bags":
                bags_pos.append(center)
            elif name == "article":
                articles_pos.append((center, track_id, conf))
                last_known_articles[track_id] = center 
                last_known_scores[track_id] = conf
            elif name == "person": 
                persons_boxes.append(box)

    # Dessiner les résultats (lignes plus fines, texte plus petit))
    annotated_frame = results[0].plot(line_width=1, font_size=0.5)

    # On stocke la frame annotée pour que le clip montre les détections
    video_buffer.append(annotated_frame.copy()) #copy évite que le texte de l'alerte n'apparaisse "dans le passé" sur les 5s précédentes

    # Variables pour savoir si on doit déclencher la vidéo 
    trigger_alert = False
    vol_type = ""
    alert_score = 0.0  # score réel de l'alerte

    current_active = []  # (id, center, score)

    for h_center in hands_pos:
        for (a_center, a_id, a_conf) in articles_pos:
            distance = math.sqrt((h_center[0] - a_center[0])**2 + (h_center[1] - a_center[1])**2)

            if distance < 80:
                key = f"article_{a_id}"
                object_hold_counter[key] = object_hold_counter.get(key, 0) + 1

                if object_hold_counter[key] >= FRAME_THRESHOLD:
                    current_active.append((a_id, a_center, a_conf))
                    # 🔥 on accumule la durée de tenue
                    hold_durations[a_id] = hold_durations.get(a_id, 0) + 1
                    cv2.circle(annotated_frame, a_center, 15, (0,255,0), 2)
                    cv2.putText(annotated_frame, "OBJET TENU", a_center, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

    #  2. SCÉNARIO VOL DANS LE SAC 
    for (a_id, a_center, a_conf) in current_active:
        # On récupère le centre de cet article spécifique
        a_center = last_known_articles[a_id]
        for b_center in bags_pos:
            dist_sac = math.sqrt((a_center[0] - b_center[0])**2 + (a_center[1] - b_center[1])**2)
            if dist_sac < 100:
                if time.time() - last_alert_time > ALERT_COOLDOWN:
                    trigger_alert = True
                    vol_type = "SAC"
                    alert_score = float(a_conf)

    # SCÉNARIO VOL CORPOREL

    visible_ids = {a_id for (_, a_id, _) in articles_pos}

    # 1. Nettoyage des objets réapparus
    for a_id in list(suspect_disappearance.keys()):
        if a_id in visible_ids:
            del suspect_disappearance[a_id]

    # 2. Détection disparition suspecte
    for key, count in object_hold_counter.items():
        a_id = int(key.split('_')[1])

        if count < FRAME_THRESHOLD:
            continue

        if a_id in visible_ids:
            continue

        last_pos = last_known_articles.get(a_id)
        if not last_pos:
            continue

        # Vérifie si l’objet était sur une personne
        was_on_person = any(is_point_in_box(last_pos, p_box) for p_box in persons_boxes)

        if was_on_person and a_id not in suspect_disappearance:
            suspect_disappearance[a_id] = {
                "start_time": time.time(),
                "last_score": last_known_scores.get(a_id, 0.5),
                "hold_time": hold_durations.get(a_id, 0.0)
            }

    # 3. Validation
    for a_id, data in list(suspect_disappearance.items()):

        elapsed = time.time() - data["start_time"]

        if elapsed < DISAPPEARANCE_TIMEOUT:
            continue

        if time.time() - last_alert_time < ALERT_COOLDOWN:
            continue

        yolo_score = data["last_score"]
        hold_score = min(1.0, data["hold_time"] / 2.0)
        time_score = min(1.0, elapsed / 5.0)

        alert_score = float(
            0.5 * yolo_score +
            0.3 * hold_score +
            0.2 * time_score
        )

        trigger_alert = True
        vol_type = "CORPS"

        del suspect_disappearance[a_id]
        hold_durations.pop(a_id, None)
    
    #  LE MOTEUR D'ENREGISTREMENT VIDÉO 
    # Si on a détecté un vol et qu'on n'est pas déjà en train d'enregistrer
    if trigger_alert and not is_recording_alert:
        print(f" ALERTE DÉCLENCHÉE : Enregistrement du clip {vol_type}...")
        start_alert_video(vol_type, alert_score)
        last_alert_time = time.time()

        # On paramètre le texte et le chrono
        alert_text_to_show = f" ALERTE : VOL {vol_type} DETECTE "
        alert_text_timer = time.time() + DISPLAY_TEXT_DURATION
    
    # Si le chrono tourne, on affiche le message au centre
    if time.time() < alert_text_timer:
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.8
        thickness = 2
        color = (0, 0, 255) # Rouge
        
        # Calcul pour centrer le texte
        text_size = cv2.getTextSize(alert_text_to_show, font, scale, thickness)[0]
        text_x = (width - text_size[0]) // 2  # Milieu horizontal
        text_y = 100 # Position verticale (en haut)
        
        # Optionnel : Ajouter un petit rectangle noir derrière pour la lisibilité
        cv2.rectangle(annotated_frame, (text_x - 10, text_y - 40), 
                      (text_x + text_size[0] + 10, text_y + 10), (0,0,0), -1)
        
        cv2.putText(annotated_frame, alert_text_to_show, (text_x, text_y), 
                    font, scale, color, thickness)
        

    # Si un enregistrement est en cours, on écrit la frame et on décompte
    if is_recording_alert:
        alert_video_writer.write(annotated_frame)
        frames_to_record_after -= 1
        
        # Quand on a fini de filmer les 5 secondes du futur
        if frames_to_record_after <= 0:
            is_recording_alert = False
            alert_video_writer.release() # On ferme proprement le fichier 
            alert_video_writer = None
            print(" Fin de l'enregistrement du clip. Vidéo sauvegardée !")

    # Reset des compteurs si l'objet n'est plus tenu
    object_hold_counter = {k: v-1 for k, v in object_hold_counter.items() if v > 1}

    # AFFICHAGE

    cv2.imshow("YOLO Detection", annotated_frame)

    # Sauvegarde vidéo
    out.write(annotated_frame)

    # Quitter avec "q"
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Libérations
cap.release()
out.release()
if alert_video_writer is not None:
    alert_video_writer.release() # Sécurité si on coupe pendant une alerte
cv2.destroyAllWindows()