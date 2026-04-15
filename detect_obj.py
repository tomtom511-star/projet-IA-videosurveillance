from ultralytics import YOLO #charge YOLO le modèle IA
import cv2 #openCV gère la caméra et vidéos
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

# CONFIGURATION GPU & MODÈLE 
model = YOLO("runs/detect/essai_surveillance_v113/weights/best.pt") 
model.to('cuda')

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
alert_ffmpeg_process = None # REMPLACE alert_video_writer pour le GPU
frames_to_record_after = 0
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

# diffusion live en mémoire (aucun fichier)
def generate_stream():
    global output_frame
    while True:
        time.sleep(0.1)  # Limite à ~10 images par seconde pour soulager Firefox et le CPU
        with frame_lock:
            if output_frame is None:
                continue
            frame = output_frame.copy()
        _, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b' \r\n')

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

# Mémoire (Tes variables d'origine)
suspect_disappearance = {} 
last_known_articles = {} 
object_hold_counter = {} 
active_objects = [] 
last_known_scores = {}        
hold_durations = {}    

# --- CONFIG CAPTURE PHOTOS ---
PHOTO_DIR = "frame_video"
os.makedirs(PHOTO_DIR, exist_ok=True)
PHOTO_INTERVAL = 4  # Secondes entre chaque photo
last_photo_time = time.time()

# Boucle infinie pour lire la vidéo image par image
while True:
    # --- LECTURE VIA LE PIPE GPU ---
    raw_frame = pipe_in.stdout.read(width * height * 3)
    if not raw_frame:
        print("Erreur flux... Reconnexion")
        pipe_in = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=10**8)
        continue
    
    frame = np.frombuffer(raw_frame, np.uint8).reshape((height, width, 3))

    # SAUVEGARDE PHOTO CHRONO 
    now = time.time()
    if now - last_photo_time >= PHOTO_INTERVAL:
        img_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        photo_path = os.path.join(PHOTO_DIR, f"photo_{img_ts}.jpg")
        cv2.imwrite(photo_path, frame) # Sauvegarde l'image BRUTE
        print(f"📸 [ARCHIVE] Image sauvegardée : {photo_path}")
        last_photo_time = now

    # Utilisation de .track() au GPU
    results = model.track(
        frame,
        persist=True,
        tracker="bytetrack.yaml",
        device=0,
        half=True,
        verbose=False
    )

    hands_pos = []
    bags_pos = []
    articles_pos = []
    persons_boxes = []

    # Vérifier si des objets sont détectés
    if results and results[0].boxes is not None and results[0].boxes.id is not None:
        boxes = results[0].boxes.xyxy.cpu().numpy()
        clss = results[0].boxes.cls.cpu().numpy()
        ids = results[0].boxes.id.cpu().numpy().astype(int)
        confs = results[0].boxes.conf.cpu().numpy()

        for box, cls, track_id, conf in zip(boxes, clss, ids, confs):
            name = model.names[int(cls)]
            center = get_center(box)

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

    # Dessiner les résultats
    if results and len(results) > 0:
        annotated_frame = results[0].plot(line_width=1, font_size=0.5)
    else:
        annotated_frame = frame.copy()

    # mise à jour stream live
    with frame_lock:
        output_frame = annotated_frame.copy()

    # On stocke dans le buffer
    video_buffer.append(annotated_frame)

    # Logique d'alerte (Tes variables)
    trigger_alert = False
    vol_type = ""
    alert_score = 0.0 
    current_active = [] 

    # --- SCÉNARIO 1 : OBJETS TENUS ---
    for h_center in hands_pos:
        for (a_center, a_id, a_conf) in articles_pos:
            distance = math.sqrt((h_center[0] - a_center[0])**2 + (h_center[1] - a_center[1])**2)

            if distance < 80:
                key = f"article_{a_id}"
                object_hold_counter[key] = object_hold_counter.get(key, 0) + 1

                if object_hold_counter[key] >= FRAME_THRESHOLD:
                    current_active.append((a_id, a_center, a_conf))
                    hold_durations[a_id] = hold_durations.get(a_id, 0) + 1
                    cv2.circle(annotated_frame, a_center, 15, (0,255,0), 2)
                    cv2.putText(annotated_frame, "OBJET TENU", a_center, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

    # --- SCÉNARIO 2 : VOL DANS LE SAC ---
    for (a_id, a_center, a_conf) in current_active:
        a_center = last_known_articles[a_id]
        for b_center in bags_pos:
            dist_sac = math.sqrt((a_center[0] - b_center[0])**2 + (a_center[1] - b_center[1])**2)
            if dist_sac < 100:
                if time.time() - last_alert_time > ALERT_COOLDOWN:
                    trigger_alert = True
                    vol_type = "SAC"
                    alert_score = float(a_conf)

    # --- SCÉNARIO 3 : VOL CORPOREL ---
    visible_ids = {a_id for (_, a_id, _) in articles_pos}

    for a_id in list(suspect_disappearance.keys()):
        if a_id in visible_ids:
            del suspect_disappearance[a_id]

    for key, count in object_hold_counter.items():
        a_id = int(key.split('_')[1])
        if count < FRAME_THRESHOLD: continue
        if a_id in visible_ids: continue

        last_pos = last_known_articles.get(a_id)
        if not last_pos: continue

        was_on_person = any(is_point_in_box(last_pos, p_box) for p_box in persons_boxes)
        if was_on_person and a_id not in suspect_disappearance:
            suspect_disappearance[a_id] = {
                "start_time": time.time(),
                "last_score": last_known_scores.get(a_id, 0.5),
                "hold_time": hold_durations.get(a_id, 0.0)
            }

    for a_id, data in list(suspect_disappearance.items()):
        elapsed = time.time() - data["start_time"]
        if elapsed < DISAPPEARANCE_TIMEOUT: continue
        if time.time() - last_alert_time < ALERT_COOLDOWN: continue

        yolo_score = data["last_score"]
        hold_score = min(1.0, data["hold_time"] / 2.0)
        time_score = min(1.0, elapsed / 5.0)

        alert_score = float(0.5 * yolo_score + 0.3 * hold_score + 0.2 * time_score)
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

    if is_recording_alert:
        # On écrit les octets bruts directement dans le pipe FFmpeg
        alert_ffmpeg_process.stdin.write(annotated_frame.tobytes())
        frames_to_record_after -= 1
        
        if frames_to_record_after <= 0:
            is_recording_alert = False
            alert_ffmpeg_process.stdin.close()
            alert_ffmpeg_process.wait()
            alert_ffmpeg_process = None
            print(" Fin de l'enregistrement du clip GPU.")

    object_hold_counter = {k: v-1 for k, v in object_hold_counter.items() if v > 1}

    cv2.imshow("YOLO Detection", annotated_frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cv2.destroyAllWindows()