from ultralytics import YOLO # charge YOLO le modèle IA
import cv2 # openCV gère la caméra et vidéos
import os  # permet de gérer les dossiers
import math # permet de calculer les distances
import json # permet de sauvegarder les alertes
import numpy as np
import subprocess
from datetime import datetime
import time
import torch # pour forcer l'utilisation du GPU
from collections import deque # Pour le buffer circulaire

#streaming live (sans stockage disque)
from flask import Flask, Response
import threading

os.environ["CUDA_VISIBLE_DEVICES"] = "0"
os.environ["YOLO_VERBOSE"] = "False"
app = Flask(__name__)

print("CUDA available:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")
output_frame = None
frame_lock = threading.Lock()

# ==========================================
# CONFIGURATION GPU & MODÈLES (L'ARCHITECTURE DOUBLE)
# ==========================================
# MODÈLE 1 : LE RADAR (Cherche uniquement les personnes dans le magasin)
model_radar = YOLO("runs/detect/radar_global_v1/weights/best.pt") 
model_radar.to('cuda')

# MODÈLE 2 : LE SPÉCIALISTE (Cherche les mains, sacs et articles sur les personnes zoomées)
model_specialist = YOLO("runs/detect/radar_specialiste_v2/weights/best.pt") 
model_specialist.to('cuda')

# VARIABLES DE GESTION DES ALERTES 
last_alert_time = 0  # timestamp de la dernière alerte
ALERT_COOLDOWN = 20 # temps minimum entre 2 alertes (évite spam)
DISAPPEARANCE_TIMEOUT = 12.0 # temps avant de considérer un objet "disparu"
FRAME_THRESHOLD = 8 # nombre de frames pour valider un objet tenu
LOITERING_THRESHOLD = 90.0 # Temps en secondes avant qu'une personne soit considérée suspecte

# TIMER POUR L'AFFICHAGE DU TEXTE
alert_text_to_show = ""
alert_text_timer = 0
DISPLAY_TEXT_DURATION = 4.0 # Le texte restera affiché 4 secondes à l'écran

# GESTION DES CLIPS VIDÉO 
BEFORE_ALERT_SECS = 5  # secondes AVANT alerte
AFTER_ALERT_SECS = 5   # secondes APRÈS alerte
is_recording_alert = False
alert_ffmpeg_process = None
raw_ffmpeg_process = None
frames_to_record_after = 0
# Buffer circulaire → garde les dernières frames
video_buffer = None
zoom_target_id = None
smooth_center = None

# Connexion au flux RTSP de la caméra
rtsp_url = "rtsp://leclerc:LecOli%2545@10.21.9.21:554/cam/realmonitor?channel=1&subtype=1"

# --- VERSION STABLE POUR QUADRO P2200 ---
width, height, fps = 704, 576, 12  # BIEN VERIFIER CES VALEURS
command = [
    'ffmpeg',
    '-rtsp_transport', 'tcp',
    '-i', rtsp_url,
    '-vf', f'scale={width}:{height}',
    '-f', 'image2pipe',
    '-pix_fmt', 'bgr24',
    '-vcodec', 'rawvideo', '-'
]
pipe_in = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=10**8)

# Initialisation du buffer (5 secondes * FPS)
video_buffer = deque(maxlen=int(BEFORE_ALERT_SECS * fps))
video_buffer_raw = deque(maxlen=int(BEFORE_ALERT_SECS * fps)) # Pour Roboflow

# Dossier uniquement pour les clips d'alerte
alert_vid_dir = "alert_clips"
os.makedirs(alert_vid_dir, exist_ok=True)

raw_dir = os.path.join(alert_vid_dir, "raw")
os.makedirs(raw_dir, exist_ok=True)

# fichier JSON pour communiquer avec l'interface
ALERT_FILE = "alerts.json"

# initialise le fichier JSON
if not os.path.exists(ALERT_FILE):
    with open(ALERT_FILE, "w") as f:
        json.dump([], f)

cv2.namedWindow("YOLO Detection", cv2.WINDOW_NORMAL)

def get_center(box):
    """Calcule le point central d'un rectangle (x1, y1, x2, y2)"""
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int((y1 + y2) / 2))

def is_point_in_box(point, box):
    """Vérifie si un point (x,y) est à l'intérieur d'un rectangle [x1, y1, x2, y2]"""
    px, py = point
    x1, y1, x2, y2 = box
    return x1 <= px <= x2 and y1 <= py <= y2

# ==========================================
# MINI-TRACKER SPATIAL (NOUVEAU)
# ==========================================
# YOLO ne peut pas tracker des objets qui sont dans des "crops" (découpes) différents.
# Ce mini-tracker donne un ID unique aux articles en fonction de leur position (distance) 
# d'une image à l'autre pour que le calcul des vols continue de fonctionner.
next_article_id = 0
active_article_tracks = {} 

def track_articles_custom(current_articles_centers, max_distance=60):
    global next_article_id, active_article_tracks
    new_tracks = {}
    tracked_articles = [] 

    for (center, conf) in current_articles_centers:
        best_id = None
        best_dist = max_distance
        for a_id, last_center in active_article_tracks.items():
            dist = math.hypot(center[0]-last_center[0], center[1]-last_center[1])
            if dist < best_dist:
                best_dist = dist
                best_id = a_id

        if best_id is not None:
            new_tracks[best_id] = center
            tracked_articles.append((center, best_id, conf))
            del active_article_tracks[best_id] 
        else:
            new_tracks[next_article_id] = center
            tracked_articles.append((center, next_article_id, conf))
            next_article_id += 1

    active_article_tracks = new_tracks
    return tracked_articles

# ==========================================
# SERVEUR FLASK STREAMING
# ==========================================
# diffusion live en mémoire (aucun fichier)
def generate_stream():
    global output_frame
    while True:
        time.sleep(0.04)  # Limite à ~10 images par seconde pour soulager Firefox et le CPU
        with frame_lock:
            if output_frame is None:
                continue
            # On encode en qualité JPG moyenne (80) pour alléger le réseau
            _, buffer = cv2.imencode('.jpg', output_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

@app.route('/video')
def video():
    return Response(
        generate_stream(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

def start_server():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

threading.Thread(target=start_server, daemon=True).start()

def zoom_tracking(frame, box, zoom_factor=1.3):
    global smooth_center

    h, w = frame.shape[:2]

    x1, y1, x2, y2 = map(int, box)

    # centre réel
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2

    # centre lissé (ANTI TREMBLEMENT)
    cx, cy = smooth_position((cx, cy))

    bw = (x2 - x1)
    bh = (y2 - y1)

    new_w = int(bw * zoom_factor)
    new_h = int(bh * zoom_factor)

    # clamp
    new_w = min(new_w, w)
    new_h = min(new_h, h)

    x1 = max(0, cx - new_w // 2)
    y1 = max(0, cy - new_h // 2)
    x2 = min(w, cx + new_w // 2)
    y2 = min(h, cy + new_h // 2)

    crop = frame[y1:y2, x1:x2]

    return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)

def smooth_position(new_center, alpha=0.2):
    global smooth_center

    if smooth_center is None:
        smooth_center = new_center
    else:
        smooth_center = (
            int(smooth_center[0] * (1 - alpha) + new_center[0] * alpha),
            int(smooth_center[1] * (1 - alpha) + new_center[1] * alpha)
        )

    return smooth_center

# --- BLOC GPU : ENCODAGE MATÉRIEL (NVENC) ---
def start_alert_video(type_vol, score):
    """Initialise l'enregistrement du clip d'alerte via le GPU"""
    global is_recording_alert, alert_ffmpeg_process, raw_ffmpeg_process, frames_to_record_after
    
    timestamp = datetime.now().strftime("%H%M%S")
    vid_name = f"Vole_{type_vol}_{timestamp}.mp4"
    raw_name = f"RAW_{type_vol}_{timestamp}.mp4"
    vid_path = os.path.abspath(os.path.join(alert_vid_dir, vid_name))

    raw_path = os.path.abspath(os.path.join(raw_dir, raw_name))
    # Commande FFmpeg pour utiliser NVENC (encodeur NVIDIA)
    # Cela libère 100% de la charge CPU lors de l'enregistrement
    def get_cmd(path):
        return [
            'ffmpeg', '-y', '-f', 'rawvideo', '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}', '-pix_fmt', 'bgr24', '-r', str(fps),
            '-i', '-', '-vcodec', 'h264_nvenc', '-preset', 'fast', '-b:v', '1M', path
        ]
    alert_ffmpeg_process = subprocess.Popen(get_cmd(vid_path), stdin=subprocess.PIPE)
    raw_ffmpeg_process = subprocess.Popen(get_cmd(raw_path), stdin=subprocess.PIPE)
    
    # 1. On écrit tout ce qu'il y a dans le buffer (les 5s passées)
    for f in video_buffer:
        alert_ffmpeg_process.stdin.write(f.tobytes())
    for f in video_buffer_raw:
        raw_ffmpeg_process.stdin.write(f.tobytes())
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
        "video_clip": vid_path,
        "video_raw": raw_path
    })
    with open(ALERT_FILE, "w") as f: json.dump(data, f, indent=4)
    
    return vid_path

# Mémoire (Tes variables d'origine pour la logique d'alerte)
suspect_disappearance = {} 
last_known_articles = {} 
object_hold_counter = {} 
last_known_scores = {}        
hold_durations = {}  
last_known_person_boxes = {}  
person_tracking = {} # Dictionnaire pour traquer le temps de présence

# Boucle infinie pour lire la vidéo image par image
while True:
    # --- LECTURE VIA LE PIPE GPU ---
    raw_frame = pipe_in.stdout.read(width * height * 3)
    if not raw_frame:
        print("Erreur flux... Reconnexion")
        pipe_in = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=10**8)
        continue
    
    frame = np.frombuffer(raw_frame, np.uint8).reshape((height, width, 3))
    clean_frame = frame.copy()
    annotated_frame = frame.copy() # L'image sur laquelle on va dessiner
    current_time = time.time()

    # ==========================================
    # ÉTAPE 1 : LE RADAR (DÉTECTION DES PERSONNES)
    # ==========================================
    # On cherche d'abord les personnes sur l'image globale et on les track
    results_radar = model_radar.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)
    
    hands_pos = []
    bags_pos = []
    raw_articles_pos = [] 
    persons_boxes = []

    if results_radar and results_radar[0].boxes is not None:
        r_boxes = results_radar[0].boxes.xyxy.cpu().numpy()
        r_clss = results_radar[0].boxes.cls.cpu().numpy()
        r_ids = results_radar[0].boxes.id.cpu().numpy().astype(int) if results_radar[0].boxes.id is not None else []
        
        for i, (box, cls) in enumerate(zip(r_boxes, r_clss)):
            name = model_radar.names[int(cls)]
            
            if name == "person":
                persons_boxes.append(box)
                # On sauvegarde la boîte pour l'analyse anatomique
                if i < len(r_ids):
                    last_known_person_boxes[r_ids[i]] = box
                
                x1, y1, x2, y2 = map(int, box)
                is_loitering = False
                presence_time = 0
                
                if i < len(r_ids):
                    p_id = r_ids[i]
                    last_known_person_boxes[p_id] = box
                    
                    # On met à jour le chrono de la personne
                    if p_id not in person_tracking:
                        person_tracking[p_id] = {"first_seen": current_time, "last_seen": current_time}
                    else:
                        person_tracking[p_id]["last_seen"] = current_time
                    
                    # Calcul du temps passé à l'écran
                    presence_time = current_time - person_tracking[p_id]["first_seen"]
                    if presence_time > LOITERING_THRESHOLD:
                        is_loitering = True

                # Affichage visuel (Orange si suspect, Bleu sinon)
                if is_loitering:
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 160, 255), 2)
                    cv2.putText(annotated_frame, f"SUSPECT: {int(presence_time)}s", (x1, y1 - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 2)
                else:
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 0, 0), 1)
                
                # ==========================================
                # ÉTAPE 2 : LA DÉCOUPE (CROP)
                # ==========================================
                # On découpe l'image globale autour de la personne (avec une marge de 20px)
                padding = 20
                x1_pad = max(0, x1 - padding)
                y1_pad = max(0, y1 - padding)
                x2_pad = min(width, x2 + padding)
                y2_pad = min(height, y2 + padding)
                
                crop = frame[y1_pad:y2_pad, x1_pad:x2_pad]
                if crop.size == 0: continue
                
                # ==========================================
                # ÉTAPE 3 : LE SPÉCIALISTE (ANALYSE DU CROP)
                # ==========================================
                # On envoie le petit bout d'image (le crop) au Modèle 2
                results_spec = model_specialist.predict(crop, verbose=False, conf=0.15)
                
                if results_spec and results_spec[0].boxes is not None:
                    s_boxes = results_spec[0].boxes.xyxy.cpu().numpy()
                    s_clss = results_spec[0].boxes.cls.cpu().numpy()
                    s_confs = results_spec[0].boxes.conf.cpu().numpy()
                    
                    for s_box, s_cls, s_conf in zip(s_boxes, s_clss, s_confs):
                        s_name = model_specialist.names[int(s_cls)]
                        
                        # ==========================================
                        # ÉTAPE 4 : REMAPPING (RECALCUL DES COORDONNÉES)
                        # ==========================================
                        # Les coordonnées du Spécialiste partent de zéro (coin du crop).
                        # Il faut les additionner à la position du crop (x1_pad, y1_pad) 
                        # pour les replacer sur l'image globale de la caméra.
                        g_x1 = int(s_box[0] + x1_pad)
                        g_y1 = int(s_box[1] + y1_pad)
                        g_x2 = int(s_box[2] + x1_pad)
                        g_y2 = int(s_box[3] + y1_pad)
                        g_center = get_center([g_x1, g_y1, g_x2, g_y2])
                        
                        if s_name == "hands" and s_conf>0.5:
                            hands_pos.append(g_center)
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 255, 255), 1) # Main = Jaune
                            
                        elif s_name == "bags" and s_conf>0.22:
                            bags_pos.append(g_center)
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 0, 255), 2) # Sac = Rouge
                            
                        elif s_name == "article" and s_conf>0.20:
                            raw_articles_pos.append((g_center, s_conf))
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (255, 0, 255), 2) # Article = Violet

    # Nettoyage de la mémoire pour les personnes parties
    # Si on n'a pas vu la personne depuis 30 secondes, on réinitialise son chrono
    keys_to_delete = [pid for pid, data in person_tracking.items() if current_time - data["last_seen"] > 30.0]
    for pid in keys_to_delete:
        del person_tracking[pid]

    # ÉTAPE 5 : ASSIGNATION DES IDs AUX ARTICLES 
    # On passe les articles détectés dans le mini-tracker pour suivre leurs déplacements
    articles_pos = track_articles_custom(raw_articles_pos)
    
    for (a_center, a_id, a_conf) in articles_pos:
        last_known_articles[a_id] = a_center 
        last_known_scores[a_id] = a_conf
        cv2.putText(annotated_frame, f"ID:{a_id}", (a_center[0]-10, a_center[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,0,255), 1)

    # Logique d'alerte (Tes variables)
    trigger_alert = False
    vol_type = ""
    alert_score = 0.0 
    current_active = [] 

    # --- SCÉNARIO 1 : OBJETS TENUS (VERSION SIMPLIFIÉE) ---
    for p_id, p_box in last_known_person_boxes.items():
        for (a_center, a_id, a_conf) in articles_pos:
            # Si l'article est dans la boîte de la personne, il est "TENU"
            if is_point_in_box(a_center, p_box):
                key = f"article_{a_id}"
                object_hold_counter[key] = object_hold_counter.get(key, 0) + 1
                
                # On considère l'objet tenu sans condition de "main"
                if object_hold_counter[key] >= FRAME_THRESHOLD:
                    current_active.append((a_id, a_center, a_conf))
                    hold_durations[a_id] = hold_durations.get(a_id, 0) + 1
                    
                    # Affichage visuel pour debug
                    cv2.circle(annotated_frame, a_center, 10, (0, 255, 0), 2)
                    cv2.putText(annotated_frame, "TENU", (a_center[0]+10, a_center[1]), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    # --- SCÉNARIO 2 : VOL DANS LE SAC ---
    for (a_id, a_center, a_conf) in current_active:
        a_center = last_known_articles[a_id]
        for b_center in bags_pos:
            dist_sac = math.hypot(a_center[0] - b_center[0], a_center[1] - b_center[1])
            if dist_sac < 35:
                if time.time() - last_alert_time > ALERT_COOLDOWN:
                    trigger_alert = True
                    vol_type = "SAC"
                    alert_score = float(a_conf)


    # --- SCÉNARIO 3 : VOL CORPOREL (LOGIQUE ANATOMIQUE RÉALISTE) ---
    visible_ids = {a_id for (_, a_id, _) in articles_pos}

    # 1. On nettoie les suspects qui réapparaissent
    for a_id in list(suspect_disappearance.keys()):
        if a_id in visible_ids:
            print(f"Angle mort terminé pour l'objet {a_id}, suspicion annulée.")
            del suspect_disappearance[a_id]

    # 2. On analyse les disparitions
    for key, count in object_hold_counter.items():
        a_id = int(key.split('_')[1])
        # Si l'objet était tenu mais n'est plus visible
        if count >= FRAME_THRESHOLD and a_id not in visible_ids:
            last_pos = last_known_articles.get(a_id)
            if not last_pos: continue

            # --- LE FILTRE CRUCIAL : ANTI-ERREUR DE LABEL ---
            # Si l'article disparaît au profit d'un "Sac" au même endroit, on ignore.
            is_label_swap = False
            for b_center in bags_pos:
                if math.hypot(last_pos[0] - b_center[0], last_pos[1] - b_center[1]) < 30:
                    is_label_swap = True
                    break
            if is_label_swap: continue 

            # Filtre Bord écran (On ignore les sorties de champ)
            margin = 45
            if not (margin < last_pos[0] < width - margin and margin < last_pos[1] < height - margin):
                continue

            # --- FILTRE GÉOMÉTRIQUE AVANCÉ (ANTI-RAYON) ---
            is_suspect_zone = False
            target_p_id = None

            for p_id, p_box in last_known_person_boxes.items():
                if is_point_in_box(last_pos, p_box):
                    # On calcule les dimensions de la personne
                    p_w = p_box[2] - p_box[0]
                    p_h = p_box[3] - p_box[1]
                    
                    # Position relative en X (largeur) et Y (hauteur)
                    rel_x = (last_pos[0] - p_box[0]) / p_w if p_w > 0 else 0.5
                    rel_y = (last_pos[1] - p_box[1]) / p_h if p_h > 0 else 0.5
                    
                    # CONDITION DE VOL RÉALISTE :
                    # 1. Hauteur (rel_y) : Entre 35% et 85% (Torse, taille, poches)
                    # 2. Centralité (rel_x) : Entre 25% et 75% (Pas à bout de bras)
                    
                    hauteur_suspecte = 0.35 <= rel_y <= 0.85
                    centralite_suspecte = 0.25 <= rel_x <= 0.75

                    if hauteur_suspecte and centralite_suspecte:
                        is_suspect_zone = True
                        target_p_id = p_id
                        break
                    else:
                        # Ici, l'objet a disparu soit trop haut, soit trop bas,
                        # soit (surtout) trop sur les côtés (bras tendu vers le rayon).
                        # On ne crée pas d'entrée dans suspect_disappearance.
                        pass

            if is_suspect_zone and a_id not in suspect_disappearance:
                suspect_disappearance[a_id] = {
                    "start_time": current_time, # On déclenche le chrono
                    "last_score": last_known_scores.get(a_id, 0.5),
                    "hold_time": hold_durations.get(a_id, 0.0),
                    "p_id": target_p_id
                }

    # 3. Validation finale par la patience (12 secondes réelles)
    for a_id, data in list(suspect_disappearance.items()):
        elapsed = current_time - data["start_time"]
        target_p_id = data["p_id"]
        
        # --- SÉCURITÉ SORTIE DE CHAMP ---
        # On vérifie si la personne qui a caché l'objet a quitté l'image
        personne_partie = False
        if target_p_id in person_tracking:
            # Si le radar ne l'a pas vue depuis plus de 2 secondes
            if current_time - person_tracking[target_p_id]["last_seen"] > 2.5:
                personne_partie = True
            else:
                personne_partie = False 
        
        # CONDITION D'ALERTE : Les 12s sont passées OU le suspect a fui
        if elapsed >= 12.0 or personne_partie:
        
            # Si après 12s l'objet n'est toujours pas revenu
            if current_time - last_alert_time > ALERT_COOLDOWN:
                # On vérifie que l'objet a été manipulé assez longtemps pour être crédible
                if data["hold_time"] > 30: 
                    
                    # FLÂNERIE : Impact sur le score
                    loitering_bonus = 0.0
                    if target_p_id in person_tracking:
                        p_time = current_time - person_tracking[target_p_id]["first_seen"]
                        if p_time > LOITERING_THRESHOLD:
                            loitering_bonus = 0.25 # Boost énorme si le mec est suspect

                    base_score = float(0.4 * data["last_score"] + 0.6 * min(1.0, data["hold_time"] / 20.0))
                    alert_score = min(1.0, base_score + loitering_bonus) # On plafonne à 1.0 (100%)
                    
                    trigger_alert = True
                    vol_type = "CORPS"

                    # Si c'est une fuite, on le précise dans le print console
                    if personne_partie and elapsed < 12.0:
                        print(f"!!! ALERTE ANTICIPÉE : Suspect {target_p_id} sorti avec l'objet {a_id}")
            
            # On nettoie pour éviter les boucles d'alertes
            del suspect_disappearance[a_id]
            if a_id in hold_durations: del hold_durations[a_id]
    
    # --- ENREGISTREMENT ---
    if trigger_alert and not is_recording_alert:
        zoom_target_id = target_p_id
        os.system("paplay /usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga &")
        print(f" ALERTE DÉCLENCHÉE : Enregistrement du clip {vol_type}...")
        start_alert_video(vol_type, alert_score)
        last_alert_time = current_time
        alert_text_to_show = f" ALERTE : VOL {vol_type} POTENTIEL "
        alert_text_timer = current_time + DISPLAY_TEXT_DURATION
    if current_time < alert_text_timer:
        blink = int(time.time() * 2) % 2  # clignote toutes les 0.5 sec

        if blink == 1:
            color = (0, 0, 255)  # rouge
            thickness = 2
            corner_length = 40  # taille des coins
            # TOP LEFT
            cv2.line(annotated_frame, (0, 0), (corner_length, 0), color, thickness)
            cv2.line(annotated_frame, (0, 0), (0, corner_length), color, thickness)
            # TOP RIGHT
            cv2.line(annotated_frame, (width, 0), (width - corner_length, 0), color, thickness)
            cv2.line(annotated_frame, (width, 0), (width, corner_length), color, thickness)
            # BOTTOM LEFT
            cv2.line(annotated_frame, (0, height), (corner_length, height), color, thickness)
            cv2.line(annotated_frame, (0, height), (0, height - corner_length), color, thickness)
            # BOTTOM RIGHT
            cv2.line(annotated_frame, (width, height), (width - corner_length, height), color, thickness)
            cv2.line(annotated_frame, (width, height), (width, height - corner_length), color, thickness)

        font_scale = 0.5  # plus petit (avant 0.8)
        thickness = 1
        text_size = cv2.getTextSize(alert_text_to_show, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)[0]
        text_x = 10  # marge gauche
        text_y = 30  # marge haut

        # fond noir derrière le texte (plus petit aussi)
        cv2.rectangle(
            annotated_frame,
            (text_x - 5, text_y - text_size[1] - 5),
            (text_x + text_size[0] + 5, text_y + 5),
            (0, 0, 0),
            -1
        )
        cv2.putText(
            annotated_frame,
            alert_text_to_show,
            (text_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 255),
            thickness
        )

    # 1. Mise à jour du flux Live pour Streamlit (Maintenant que TOUT est dessiné)
    with frame_lock:
        output_frame = annotated_frame.copy()
    # 2. Stockage dans le buffer pour FFmpeg (Vidéo avec dessins)
    video_buffer.append(annotated_frame)
    video_buffer_raw.append(clean_frame.copy())

    frame_to_record = annotated_frame.copy()
    frame_raw_to_record = clean_frame.copy()

    # zoom uniquement si on a une cible
    if zoom_target_id in last_known_person_boxes:
        box = last_known_person_boxes[zoom_target_id]

        frame_to_record = zoom_tracking(frame_to_record, box, zoom_factor=1.3)
        frame_raw_to_record = zoom_tracking(frame_raw_to_record, box, zoom_factor=1.3)

    if is_recording_alert:
        try:
            if alert_ffmpeg_process and alert_ffmpeg_process.stdin:
                alert_ffmpeg_process.stdin.write(frame_to_record.tobytes())  # AVEC overlay + zoom

            if raw_ffmpeg_process and raw_ffmpeg_process.stdin:
                raw_ffmpeg_process.stdin.write(frame_raw_to_record.tobytes())  # SANS overlay + zoom
            frames_to_record_after -= 1
            
            if frames_to_record_after <= 0:
                is_recording_alert = False
                zoom_target_id = None
                smooth_center = None
                alert_ffmpeg_process.stdin.close()
                alert_ffmpeg_process.wait()
                alert_ffmpeg_process = None
                if raw_ffmpeg_process:
                    raw_ffmpeg_process.stdin.close()
                    raw_ffmpeg_process.wait()
                    raw_ffmpeg_process = None
                print(" Fin de l'enregistrement du clip GPU.")
        except Exception as e:
            print(f"Erreur enregistrement: {e}")
            is_recording_alert = False

    object_hold_counter = {k: v-1 for k, v in object_hold_counter.items() if v > 1}

    # Pour éviter le freeze "Ne répond pas", on commente cv2.imshow
    # cv2.imshow("YOLO Detection", annotated_frame)

    # Empêche le système de croire que le script est planté
    cv2.waitKey(1)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

pipe_in.terminate() # Ferme proprement la connexion à la caméra
cv2.destroyAllWindows()