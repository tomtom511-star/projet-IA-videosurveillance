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

# AJOUT : streaming live (sans stockage disque)
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
model_specialist = YOLO("runs/detect/radar_specialiste_v1/weights/best.pt") 
model_specialist.to('cuda')

# VARIABLES DE GESTION DES ALERTES 
last_alert_time = 0  # timestamp de la dernière alerte
ALERT_COOLDOWN = 20 # temps minimum entre 2 alertes (évite spam)
DISAPPEARANCE_TIMEOUT = 12.0 # temps avant de considérer un objet "disparu"
FRAME_THRESHOLD = 8 # nombre de frames pour valider un objet tenu

# TIMER POUR L'AFFICHAGE DU TEXTE
alert_text_to_show = ""
alert_text_timer = 0
DISPLAY_TEXT_DURATION = 4.0 # Le texte restera affiché 4 secondes à l'écran

# GESTION DES CLIPS VIDÉO 
BEFORE_ALERT_SECS = 5  # secondes AVANT alerte
AFTER_ALERT_SECS = 5   # secondes APRÈS alerte
is_recording_alert = False
alert_ffmpeg_process = None
frames_to_record_after = 0
# Buffer circulaire → garde les dernières frames
video_buffer = None

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

# Dossier uniquement pour les clips d'alerte
alert_vid_dir = "alert_clips"
os.makedirs(alert_vid_dir, exist_ok=True)

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

# --- BLOC GPU : ENCODAGE MATÉRIEL (NVENC) ---
def start_alert_video(type_vol, score):
    """Initialise l'enregistrement du clip d'alerte via le GPU"""
    global is_recording_alert, alert_ffmpeg_process, frames_to_record_after
    
    timestamp = datetime.now().strftime("%H%M%S")
    vid_name = f"Vole_{type_vol}_{timestamp}.mp4"
    vid_path = os.path.abspath(os.path.join(alert_vid_dir, vid_name))
    
    # Commande FFmpeg pour utiliser NVENC (encodeur NVIDIA)
    # Cela libère 100% de la charge CPU lors de l'enregistrement
    cmd_out = [
        'ffmpeg', '-y', '-f', 'rawvideo', '-vcodec', 'rawvideo',
        '-s', f'{width}x{height}', '-pix_fmt', 'bgr24', '-r', str(fps),
        '-i', '-', '-vcodec', 'h264_nvenc', '-preset', 'fast', '-b:v', '2M', vid_path
    ]
    alert_ffmpeg_process = subprocess.Popen(cmd_out, stdin=subprocess.PIPE)
    
    # 1. On écrit tout ce qu'il y a dans le buffer (les 5s passées)
    for f in video_buffer:
        alert_ffmpeg_process.stdin.write(f.tobytes())
        
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

# Mémoire (Tes variables d'origine pour la logique d'alerte)
suspect_disappearance = {} 
last_known_articles = {} 
object_hold_counter = {} 
last_known_scores = {}        
hold_durations = {}  
last_known_person_boxes = {}  

# Boucle infinie pour lire la vidéo image par image
while True:
    # --- LECTURE VIA LE PIPE GPU ---
    raw_frame = pipe_in.stdout.read(width * height * 3)
    if not raw_frame:
        print("Erreur flux... Reconnexion")
        pipe_in = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=10**8)
        continue
    
    frame = np.frombuffer(raw_frame, np.uint8).reshape((height, width, 3))
    annotated_frame = frame.copy() # L'image sur laquelle on va dessiner

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
                results_spec = model_specialist.predict(crop, verbose=False, conf=0.20)
                
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
                            
                        elif s_name == "bags" and s_conf>0.25:
                            bags_pos.append(g_center)
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 165, 255), 2) # Sac = Orange
                            
                        elif s_name == "article" and s_conf>0.22:
                            raw_articles_pos.append((g_center, s_conf))
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (255, 0, 255), 2) # Article = Violet

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
                    cv2.circle(annotated_frame, a_center, 15, (0, 255, 0), 2)
                    cv2.putText(annotated_frame, "TENU", (a_center[0]+15, a_center[1]), 
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
            del suspect_disappearance[a_id]

    # 2. On analyse les disparitions
    for key, count in object_hold_counter.items():
        a_id = int(key.split('_')[1])
        if count < FRAME_THRESHOLD or a_id in visible_ids: continue

        last_pos = last_known_articles.get(a_id)
        if not last_pos: continue

        # Filtre Bord écran (On ignore si le client sort de l'image)
        margin = 40
        if last_pos[0] < margin or last_pos[0] > (width - margin) or \
           last_pos[1] < margin or last_pos[1] > (height - margin):
            continue

        # FILTRE ANATOMIQUE (Bras levé / Rayon)
        is_suspect_zone = False
        target_p_id = None
        
        for p_id, p_box in last_known_person_boxes.items():
            if is_point_in_box(last_pos, p_box):
                p_h = p_box[3] - p_box[1]
                rel_y = (last_pos[1] - p_box[1]) / p_h if p_h > 0 else 0.5
                
                # On ne suspecte que la zone Milieu (Torse/Poches)
                if 0.35 <= rel_y <= 0.85:
                    is_suspect_zone = True
                    target_p_id = p_id
                break

        if is_suspect_zone and a_id not in suspect_disappearance:
            suspect_disappearance[a_id] = {
                "start_time": time.time(),
                "last_score": last_known_scores.get(a_id, 0.5),
                "hold_time": hold_durations.get(a_id, 0.0),
                "p_id": target_p_id
            }

    # 3. Validation après patience (10 secondes)
    for a_id, data in list(suspect_disappearance.items()):
        elapsed = time.time() - data["start_time"]
        if elapsed < 10.0: continue 
        
        if time.time() - last_alert_time > ALERT_COOLDOWN:
            # Score basé sur le temps tenu et la confiance IA
            hold_score = min(1.0, data["hold_time"] / 20.0)
            if hold_score < 0.3: # Si trop peu tenu, c'est une erreur de détection
                del suspect_disappearance[a_id]
                continue

            alert_score = float(0.4 * data["last_score"] + 0.6 * hold_score)
            trigger_alert = True
            vol_type = "CORPS"
            del suspect_disappearance[a_id]
            hold_durations.pop(a_id, None)
    
    # --- ENREGISTREMENT ---
    if trigger_alert and not is_recording_alert:
        print(f" ALERTE DÉCLENCHÉE : Enregistrement du clip {vol_type}...")
        start_alert_video(vol_type, alert_score)
        last_alert_time = time.time()
        alert_text_to_show = f" ALERTE : VOL {vol_type} DETECTE "
        alert_text_timer = time.time() + DISPLAY_TEXT_DURATION
    
    if time.time() < alert_text_timer:
        text_size = cv2.getTextSize(alert_text_to_show, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
        text_x = (width - text_size[0]) // 2
        cv2.rectangle(annotated_frame, (text_x - 10, 60), (text_x + text_size[0] + 10, 110), (0,0,0), -1)
        cv2.putText(annotated_frame, alert_text_to_show, (text_x, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)


    # 1. Mise à jour du flux Live pour Streamlit (Maintenant que TOUT est dessiné)
    with frame_lock:
        output_frame = annotated_frame.copy()
    # 2. Stockage dans le buffer pour FFmpeg (Vidéo avec dessins)
    video_buffer.append(annotated_frame)

    if is_recording_alert:
        try: # Try/Except pour éviter que FFmpeg fasse planter la boucle
            alert_ffmpeg_process.stdin.write(annotated_frame.tobytes())
            frames_to_record_after -= 1
            
            if frames_to_record_after <= 0:
                is_recording_alert = False
                alert_ffmpeg_process.stdin.close()
                alert_ffmpeg_process.wait()
                alert_ffmpeg_process = None
                print(" Fin de l'enregistrement du clip GPU.")
        except:
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