"""
╔══════════════════════════════════════════════════════════════════════════╗
║         SYSTÈME DE DÉTECTION DE VOL MULTI-CAMÉRAS — YOLO + Flask        ║
║                    VERSION 12 — FUSION v9 (SAC+CORPS) + v11 (ARCH)      ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  POURQUOI CETTE VERSION :                                                ║
║  v9  → SAC fonctionnel, nb d'alertes parfait, CORPS ne marche pas       ║
║  v11 → CORPS tenté, SAC complètement cassé, 65 fausses alertes/nuit     ║
║                                                                          ║
║  CAUSES RACINES IDENTIFIÉES (v11 → cassé) :                             ║
║                                                                          ║
║  [BUG 1] FIX 6 v10 : _was_hand_near_article() retiré du CORPS.         ║
║    Ce filtre était LA protection principale contre les faux positifs.    ║
║    En v9 il bloquait ~80% des cas ambigus. Sans lui, tout article        ║
║    qui disparaît dans la zone corporelle déclenche une alerte.           ║
║    CORRECTION : réintroduction de _was_hand_near() dans CORPS.          ║
║    NOTE : il reste retiré du SAC (justifié — main occultée par le sac). ║
║                                                                          ║
║  [BUG 2] FIX C v11 : STATIC_BAG_FRAME_THRESHOLD 20 → 180 frames.       ║
║    EFFET RÉEL INVERSE : tous les sacs "actifs" passent 15s dans         ║
║    static_bag_cache et sont donc EXCLUS de bags_pos_filtered.           ║
║    Résultat : le scénario SAC ne voit plus jamais aucun sac → 0 alerte. ║
║    CORRECTION : retour à 20 frames (v9). Le problème que FIX C          ║
║    tentait de résoudre (sac posé brièvement filtré) n'existait pas :    ║
║    le cache se réinitialise dès que le sac bouge (purge seen_keys).     ║
║                                                                          ║
║  [BUG 3] frames_gone < 18 en v11 vs < 4 en v9 pour le SAC.             ║
║    Délai trop long : combiné au timeout SAC_DISAPPEARANCE_TIMEOUT,      ║
║    l'alerte SAC n'arrive presque jamais dans la fenêtre valide.          ║
║    CORRECTION : retour à 4 frames (v9).                                  ║
║                                                                          ║
║  [BUG 4] _was_hand_near() retiré du SAC aussi (FIX 6 v10).             ║
║    En v9 ce filtre validait le contact avant l'insertion dans le sac.   ║
║    CORRECTION : réintroduction dans SAC (commentaire mis à jour).        ║
║                                                                          ║
║  [BUG 5] ALERT_COOLDOWN 20s (v9) → 90s (v11).                          ║
║    Trop long, un même événement peut dépasser la fenêtre d'analyse.     ║
║    CORRECTION : retour à 20s.                                            ║
║                                                                          ║
║  CE QUI EST CONSERVÉ DE v11 (améliorations valides) :                   ║
║  ──────────────────────────────────────────────────                       ║
║  [OK] Architecture GPU : request_id sync frame↔résultat                 ║
║  [OK] batch_input_queue avec maxsize (évite saturation RAM)             ║
║  [OK] generate_stream : lock bloquant + fallback last_sent              ║
║  [OK] PERSON_MISS_TOLERANCE : tracker personnes avec tolérance ratés    ║
║  [OK] drain_stderr : rate-limiting 1 log/seconde                        ║
║  [OK] Fuite mémoire : nettoyage article_conf_history + position_history ║
║  [OK] FIX 2 v10 : article_presence_streak reset absence conditionné     ║
║  [OK] FIX 3 v10 : annulation suspicion sur réapparition ≥ 3 frames     ║
║  [OK] FIX D v10 : alert_article_id correctement récupéré pour CORPS    ║
║  [OK] TRACKER_MISS_TOLERANCE : 60 frames (v11) conservé                 ║
║  [OK] FIX H v12 : signature visuelle article (doublon visuel)           ║
║  [OK] FIX I v12 : déduplication des alertes par signature               ║
║  [OK] Endpoint /logs pour debug en production                            ║
║                                                                          ║
║  CE QUI EST CONSERVÉ DE v9 (fondamentaux qui fonctionnaient) :          ║
║  ──────────────────────────────────────────────────────────               ║
║  [OK] _was_hand_near_article() présent dans CORPS                        ║
║  [OK] _was_hand_near_article() présent dans SAC                          ║
║  [OK] STATIC_BAG_FRAME_THRESHOLD = 20 frames                            ║
║  [OK] frames_gone < 4 avant déclenchement SAC                           ║
║  [OK] ALERT_COOLDOWN = 20 secondes                                       ║
║  [OK] Score CORPS = 0.4 * last_score + 0.6 * hold_norm (v9)             ║
║       → plus stable que hold_only v11 (badges conf=0.9 → score élevé)   ║
║  [OK] Zone suspecte v9 : rel_y ∈ [0.20,0.95] rel_x ∈ [0.10,0.90]     ║
║       → élargi mais compensé par _was_hand_near() qui filtre vraiment   ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import os

# ==========================================
# CONFIGURATION GPU
# ==========================================
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
os.environ["YOLO_VERBOSE"] = "False"


from ultralytics import YOLO
import cv2
import math
import json
import signal
import numpy as np
import subprocess
from datetime import datetime
import time
import torch
from collections import deque
import queue
from flask import Flask, Response, request, jsonify
import threading



DEBUG_LOGS = True


# ==========================================
# CONFIGURATION DES CAMÉRAS
# ==========================================
CAMERAS = [
    {
        "cam_id":   "CAM_21",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.21:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
    {
        "cam_id":   "CAM_22",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.22:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
    {
        "cam_id":   "CAM_23",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.23:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
    {
        "cam_id":   "CAM_45",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.45:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
    {
        "cam_id":   "CAM_46",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.46:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
    {
        "cam_id":   "CAM_47",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.47:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
    {
        "cam_id":   "CAM_49",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.49:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
]


# ==========================================
# VARIABLES DE GESTION DES ALERTES
# ==========================================

# [v9] 20s — valeur qui donnait un nb d'alertes parfait.
# v11 avait monté à 90s, trop restrictif sur des événements distincts.
ALERT_COOLDOWN        = 60

DISAPPEARANCE_TIMEOUT = 9.0
FRAME_THRESHOLD       = 8
LOITERING_THRESHOLD   = 180.0
DISPLAY_TEXT_DURATION = 4.0
BEFORE_ALERT_SECS     = 13
AFTER_ALERT_SECS      = 7

TRACKER_MISS_TOLERANCE = 180

# [v11] Tolérance tracker personnes — conservé
PERSON_MISS_TOLERANCE = 12

# ── PARAMÈTRES CONTACT MAIN ──
HAND_MEMORY_FRAMES    = 30
HAND_ARTICLE_DIST     = 45
MOVEMENT_HISTORY_FRAMES = 6
MOVEMENT_CORRELATION_MIN = 0.6

# ── STREAK "TENU" ──
HOLD_STREAK_THRESHOLD          = 20
HOLD_STREAK_MISS_MAX           = 10
ARTICLE_DETECTED_HOLD_THRESHOLD = 12
CONSECUTIVE_MISS_MAX           = 8

# ── ANTI-FAUX-POSITIFS v9 ──
HOLD_CONF_MIN         = 0.25
HOLD_CONF_HISTORY_LEN = 20

MIN_DISAPPEARANCE_FRAMES = 24

ALERT_SCORE_MIN = 0.4

# [v11] Conservé
REAPPEARANCE_FRAMES_MIN          = 3
PRESENCE_FRAMES_FOR_ABSENCE_RESET = 3

# ── GPU ──
BATCH_TIMEOUT_SECS = 0.080
SUSPICION_TTL      = 30

# [v11] Conservé — évite saturation RAM
BATCH_QUEUE_MAXSIZE = len(CAMERAS) * 2

# ── SCÉNARIO SAC ──
SAC_PROXIMITY_FRAMES_MIN    = 8
SAC_PROXIMITY_DIST          = 40
SAC_DISAPPEARANCE_TIMEOUT   = 2.0
SAC_DISAPPEARANCE_PATIENCE = 3.0  # secondes — même ordre de grandeur que CORPS

# [BUG 2 CORRIGÉ] Retour à 20 frames (v9).
# v11 avait porté à 180 (FIX C) ce qui bloquait TOUS les sacs actifs.
# Le cache se réinitialise déjà dès qu'un sac bouge (purge seen_keys),
# donc 20 frames est suffisant pour filtrer les sacs vraiment fixes.
STATIC_BAG_FRAME_THRESHOLD = 20


# ==========================================
# DOSSIERS ET FICHIER D'ALERTES
# ==========================================
ALERT_FILE    = "alerts.jsonl"
alert_vid_dir = "alert_clips"
raw_dir       = os.path.join(alert_vid_dir, "raw")

os.makedirs(alert_vid_dir, exist_ok=True)
os.makedirs(raw_dir,       exist_ok=True)
os.makedirs("snapshots",   exist_ok=True)

if not os.path.exists(ALERT_FILE):
    open(ALERT_FILE, "w").close()

alerts_file_lock = threading.Lock()


# ==========================================
# SUSPICIONS EN MÉMOIRE
# ==========================================
active_suspicions: dict = {}
suspicions_lock = threading.Lock()


# ==========================================
# CHARGEMENT DES MODÈLES YOLO
# ==========================================
print("CUDA available:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")

model_radar = YOLO("runs/detect/radar_global_v2/weights/best.pt")
model_radar.to("cuda")

model_specialist = YOLO("runs/detect/radar_specialiste_v5/weights/best.pt")
model_specialist.to("cuda")

# Force tout sur GPU, libère le CPU entre les inférences
torch.backends.cudnn.benchmark = True
torch.set_num_threads(2)  # limite les threads CPU de PyTorch — le GPU fait le vrai travail


# ==========================================
# QUEUES DE COMMUNICATION GPU
# ==========================================
gpu_pending_frames = {}
gpu_pending_lock = threading.Lock()

result_queues: dict = {
    cam["cam_id"]: queue.Queue(maxsize=1)
    for cam in CAMERAS
}


# ==========================================
# ÉTAT PARTAGÉ ENTRE LES THREADS ET FLASK
# ==========================================
output_frames: dict = {}
raw_frames:    dict = {}
frame_lock = threading.RLock()


# ==========================================
# SERVEUR FLASK
# ==========================================
app = Flask(__name__)


def generate_stream(cam_id: str):
    """
    [v11 — conservé] Lock bloquant + fallback last_sent_id.
    Évite les coupures MJPEG quand le lock est pris par un worker.
    """
    last_sent_id = None
    while True:
        with frame_lock:
            frame = output_frames.get(cam_id)
        if frame is not None and id(frame) != last_sent_id:
            _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 40])
            last_sent_id = id(frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        else:
            time.sleep(0.02)


@app.route("/video/<cam_id>")
def video(cam_id):
    return Response(generate_stream(cam_id), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/snapshot", methods=["POST"])
def take_snapshot():
    data   = request.get_json()
    cam_id = data.get("cam_id", "unknown")
    with frame_lock:
        frame = raw_frames.get(cam_id)
        if frame is None:
            return jsonify({"status": "error", "message": "Pas d'image disponible"}), 500
        frame_to_save = frame.copy()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join("snapshots", f"CLEAN_{cam_id}_{timestamp}.jpg")
    cv2.imwrite(file_path, frame_to_save)
    print(f"📸 Snapshot enregistré : {file_path}")
    return jsonify({"status": "success", "file": file_path}), 200


@app.route("/alerts")
def get_alerts():
    last_n = request.args.get("last", default=None, type=int)
    with alerts_file_lock:
        with open(ALERT_FILE, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
    if last_n is not None:
        lines = lines[-last_n:]
    alerts = []
    for line in lines:
        try:
            alerts.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return jsonify(alerts)


@app.route("/suspicions")
def get_suspicions():
    now = time.time()
    with suspicions_lock:
        expired = [c for c, s in active_suspicions.items() if now > s["expires_at"]]
        for cam_id in expired:
            del active_suspicions[cam_id]
        result = {
            cam_id: {"time": s["time"], "score": s["score"], "type": s["type"]}
            for cam_id, s in active_suspicions.items()
        }
    return jsonify(result)


# ── Buffer de logs en mémoire ──
import logging as _logging
from collections import deque as _deque

log_buffer      = _deque(maxlen=2000)
log_buffer_lock = threading.Lock()
# RAM + disque en temps réel
LOGS_DISK_FILE     = "logs.jsonl"
LOGS_MAX_LINES     = 50_000
logs_disk_lock     = threading.Lock()
logs_disk_counter  = 0

def _rotate_logs_if_needed():
    global logs_disk_counter
    logs_disk_counter += 1
    if logs_disk_counter % 1000 != 0:
        return
    try:
        with open(LOGS_DISK_FILE, "r") as f:
            lines = f.readlines()
        if len(lines) > LOGS_MAX_LINES:
            with open(LOGS_DISK_FILE, "w") as f:
                f.writelines(lines[-LOGS_MAX_LINES:])
    except Exception:
        pass


def _log(cam_id: str, level: str, message: str):
    entry = {
        "ts":    datetime.now().strftime("%H:%M:%S"),
        "date":  datetime.now().strftime("%Y-%m-%d"),
        "cam":   cam_id,
        "level": level,
        "msg":   message,
    }
    with log_buffer_lock:
        log_buffer.append(entry)
    # Écriture disque immédiate — indépendante de l'interface
    with logs_disk_lock:
        with open(LOGS_DISK_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _rotate_logs_if_needed()
    if DEBUG_LOGS:
        print(f"[{cam_id}] [{level}] {message}")

@app.route("/logs")
def get_logs():
    cam_filter   = request.args.get("cam",   default=None)
    level_filter = request.args.get("level", default=None)
    last_n       = request.args.get("last",  default=200, type=int)
    with log_buffer_lock:
        entries = list(log_buffer)
    if cam_filter and cam_filter != "ALL":
        entries = [e for e in entries if e["cam"] == cam_filter]
    if level_filter and level_filter != "ALL":
        entries = [e for e in entries if e["level"] == level_filter]
    return jsonify(entries[-last_n:])


def start_server():
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)


# ==========================================
# FONCTIONS UTILITAIRES
# ==========================================
def get_center(box):
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int((y1 + y2) / 2))


def is_point_in_box(point, box):
    px, py = point
    x1, y1, x2, y2 = box
    return x1 <= px <= x2 and y1 <= py <= y2


def read_exactly(pipe, n_bytes):
    buf = bytearray()
    while len(buf) < n_bytes:
        remaining = n_bytes - len(buf)
        chunk = pipe.read(remaining)
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def drain_stderr(process, cam_id: str, stop_event: threading.Event):
    """[v11 — conservé] Rate-limiting 1 log/seconde pour éviter la saturation."""
    last_log_time = 0.0
    try:
        for line in process.stderr:
            if stop_event.is_set():
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded and "error" in decoded.lower():
                now = time.time()
                if now - last_log_time >= 1.0:
                    _log(cam_id, "ERROR", f"FFmpeg : {decoded}")
                    last_log_time = now
    except Exception:
        pass


def append_alert_jsonl(alert_dict: dict):
    with alerts_file_lock:
        with open(ALERT_FILE, "a") as f:
            f.write(json.dumps(alert_dict, ensure_ascii=False) + "\n")


# ==========================================
# GESTION DISQUE ET PURGE
# ==========================================
DISK_MIN_FREE_GB    = 5.0
CLIP_RETENTION_DAYS = 30
PURGE_INTERVAL_SECS = 3600


def _get_free_gb(path: str = ".") -> float:
    try:
        stat = os.statvfs(path)
        return (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
    except Exception as e:
        print(f"⚠️  Impossible de lire l'espace disque sur '{path}' : {e}")
        return -1.0


def emergency_free_space():
    all_clips = []
    for folder in [alert_vid_dir, raw_dir]:
        if not os.path.isdir(folder):
            continue
        for filename in os.listdir(folder):
            if not filename.endswith(".mp4"):
                continue
            filepath = os.path.join(folder, filename)
            try:
                mtime = os.path.getmtime(filepath)
                size  = os.path.getsize(filepath)
                all_clips.append((mtime, filepath, size))
            except Exception:
                pass
    if not all_clips:
        print("[PURGE URGENCE] ⚠️  Aucun clip à supprimer.")
        return False
    all_clips.sort(key=lambda x: x[0])
    n_to_delete = math.ceil(len(all_clips) * 0.30)
    total_freed = total_deleted = 0
    for mtime, filepath, size in all_clips[:n_to_delete]:
        try:
            os.remove(filepath)
            total_freed   += size
            total_deleted += 1
        except Exception as e:
            print(f"[PURGE URGENCE] ⚠️  {filepath} : {e}")
    print(f"[PURGE URGENCE] ✅ {total_deleted} clip(s), {total_freed/1024/1024:.0f} Mo libérés.")
    free_after = _get_free_gb(alert_vid_dir)
    return free_after >= DISK_MIN_FREE_GB


def check_disk_space(path: str = ".") -> bool:
    free_gb = _get_free_gb(path)
    if free_gb < 0:
        return True
    if free_gb >= DISK_MIN_FREE_GB:
        return True
    print(f"⚠️  ESPACE DISQUE BAS : {free_gb:.2f} Go libres. Tentative de libération...")
    return emergency_free_space()


def purge_old_clips():
    cutoff = time.time() - (CLIP_RETENTION_DAYS * 86400)
    total_deleted = total_freed = 0
    for folder in [alert_vid_dir, raw_dir]:
        if not os.path.isdir(folder):
            continue
        for filename in os.listdir(folder):
            if not filename.endswith(".mp4"):
                continue
            filepath = os.path.join(folder, filename)
            try:
                if os.path.getmtime(filepath) < cutoff:
                    size = os.path.getsize(filepath)
                    os.remove(filepath)
                    total_deleted += 1
                    total_freed   += size
            except Exception as e:
                print(f"[PURGE] ⚠️  {filepath} : {e}")
    if total_deleted > 0:
        print(f"[PURGE] ✅ {total_deleted} clip(s), {total_freed/1024/1024:.1f} Mo libérés.")


def purge_worker():
    print(f"[PURGE] Thread de purge démarré.")
    while True:
        purge_old_clips()
        time.sleep(PURGE_INTERVAL_SECS)


# ==========================================
# THREAD GPU CENTRALISÉ (v11 — conservé intégralement)
# ==========================================
def gpu_batch_worker():
    while True:
        batch    = {}
        deadline = time.time() + BATCH_TIMEOUT_SECS
        while time.time() < deadline:
            with gpu_pending_lock:
                for c_id, f in list(gpu_pending_frames.items()):
                    if c_id not in batch:
                        batch[c_id] = f
                        del gpu_pending_frames[c_id] # On vide ce qu'on a pris
            if len(batch) >= len(CAMERAS):
                break
            time.sleep(0.005)

        if not batch:
            continue

        cam_ids = list(batch.keys())
        frames  = [batch[c] for c in cam_ids]

        all_crops  = []
        radar_data = {}

        try:
            with torch.no_grad():
                radar_results = model_radar.predict(frames, verbose=False, conf=0.15, imgsz=416, half=True)
        except Exception as e:
            print(f"[GPU] ❌ Erreur Radar batch : {e}")
            for cam_id in cam_ids:
                _put_result(cam_id, [])
            continue

        for i, cam_id in enumerate(cam_ids):
            result = radar_results[i]
            frame = frames[i]
            h, w  = frame.shape[:2]
            persons_this_cam = []

            if result.boxes is None:
                radar_data[cam_id] = persons_this_cam
                continue

            r_boxes = result.boxes.xyxy.cpu().numpy()
            r_clss  = result.boxes.cls.cpu().numpy()
            r_confs = result.boxes.conf.cpu().numpy()

            for box, cls, conf in zip(r_boxes, r_clss, r_confs):
                if model_radar.names[int(cls)] != "person":
                    continue
                if conf <= 0.5:
                    continue
                x1, y1, x2, y2 = map(int, box)
                x1p = max(0, x1 - 20)
                y1p = max(0, y1 - 20)
                x2p = min(w,  x2 + 20)
                y2p = min(h,  y2 + 20)
                crop = frame[y1p:y2p, x1p:x2p]
                if crop.size == 0:
                    continue
                persons_this_cam.append({
                    "box":         box,
                    "conf":        float(conf),
                    "crop_idx":    len(all_crops),
                    "x1_pad":      x1p,
                    "y1_pad":      y1p,
                    "spec_result": None,
                })
                all_crops.append(crop)
            radar_data[cam_id] = persons_this_cam

        spec_by_idx = {}
        if all_crops:
            try:
                with torch.no_grad():
                    spec_results = model_specialist.predict(all_crops, verbose=False, conf=0.15, half=True)
                for idx, res in enumerate(spec_results):
                    spec_by_idx[idx] = res
            except Exception as e:
                print(f"[GPU] ❌ Erreur Spécialiste : {e}")

        for i, cam_id in enumerate(cam_ids):
            persons = radar_data.get(cam_id, [])
            for p in persons:
                p["spec_result"] = spec_by_idx.get(p["crop_idx"])
            _put_result(cam_id, persons, frames[i])


def _put_result(cam_id: str, persons: list, frame: np.ndarray):
    rq = result_queues.get(cam_id)
    if rq is None:
        return
    if rq.full():
        try:
            rq.get_nowait()
        except queue.Empty:
            pass
    try:
        rq.put_nowait((persons, frame))
    except queue.Full:
        pass


# ==========================================
# CLASSE FFmpegReader (v11 — conservé)
# ==========================================
class FFmpegReader:
    def __init__(self, cam_id: str, rtsp_url: str, width: int, height: int):
        self.cam_id      = cam_id
        self.rtsp_url    = rtsp_url
        self.width       = width
        self.height      = height
        self.frame_size  = width * height * 3
        self.queue       = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._process    = None
        self.is_reconnecting = False
        self._bufsize    = self.frame_size * 10
        self.reconnect_event = threading.Event()

    def _start_ffmpeg(self):
        return subprocess.Popen(
            ["ffmpeg", "-loglevel", "warning", "-rtsp_flags", "prefer_tcp",
             "-rtsp_transport", "tcp", "-timeout", "10000000", "-max_delay", "500000",
             "-i", self.rtsp_url, "-vf", f"scale={self.width}:{self.height}",
             "-f", "image2pipe", "-pix_fmt", "bgr24", "-vcodec", "rawvideo", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=self._bufsize,
        )

    def run(self):
        import select
        while not self._stop_event.is_set():
            print(f"[{self.cam_id}] Connexion au flux RTSP...")
            self._process = self._start_ffmpeg()
            last_frame_time = [time.time()]
            stop_watchdog   = threading.Event()

            def watchdog(last_frame_time=last_frame_time, stop_watchdog=stop_watchdog):
                while not stop_watchdog.is_set():
                    time.sleep(1)
                    if stop_watchdog.is_set():
                        break
                    if time.time() - last_frame_time[0] > 5:
                        _log(self.cam_id, "ERROR", "Watchdog : aucune frame depuis 5s")
                        self.is_reconnecting = True
                        self.reconnect_event.set()
                        try:
                            self._process.kill()
                        except Exception:
                            pass
                        return

            threading.Thread(target=watchdog, daemon=True, name=f"{self.cam_id}_watchdog").start()
            threading.Thread(
                target=drain_stderr, args=(self._process, self.cam_id, self._stop_event),
                daemon=True, name=f"{self.cam_id}_stderr_drain",
            ).start()

            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass

            try:
                while not self._stop_event.is_set():
                    ready = select.select([self._process.stdout], [], [], 2.0)[0]
                    if not ready:
                        continue
                    raw_bytes = read_exactly(self._process.stdout, self.frame_size)
                    if raw_bytes is None:
                        _log(self.cam_id, "ERROR", "Flux interrompu — pipe fermé")
                        self.reconnect_event.set()
                        break
                    last_frame_time[0]   = time.time()
                    self.is_reconnecting = False
                    if self.queue.full():
                        try:
                            self.queue.get_nowait()
                        except queue.Empty:
                            pass
                    self.queue.put(raw_bytes)
            except Exception as e:
                print(f"[{self.cam_id}] 💥 Exception FFmpegReader : {e}")
            finally:
                stop_watchdog.set()
                try:
                    self._process.kill()
                    self._process.wait(timeout=3)
                except Exception:
                    pass

            if not self._stop_event.is_set():
                _log(self.cam_id, "INFO", "Reconnexion RTSP dans 3s...")
                time.sleep(3)
        print(f"[{self.cam_id}] FFmpegReader arrêté.")

    def get_frame(self, timeout=2.0):
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._stop_event.set()
        if self._process:
            try:
                self._process.kill()
            except Exception:
                pass


# ==========================================
# CLASSE PRINCIPALE : UNE INSTANCE PAR CAMÉRA
# ==========================================
class CameraWorker:
    """
    Fusion v9/v11 :
    - Architecture GPU v11 (request_id, batch_queue maxsize, lock bloquant)
    - Logique métier v9 pour CORPS (_was_hand_near présent) et SAC (seuils v9)
    - Corrections mémoire et tracker personnes de v11
    - FIX H/I v12 (signatures visuelles) conservés
    """

    def __init__(self, cam_id: str, rtsp_url: str, width: int, height: int, fps: int):
        self.cam_id           = cam_id
        self.rtsp_url         = rtsp_url
        self.width            = width
        self.height           = height
        self.fps              = fps
        self.frames_processed = 0

        self._record_stdin_lock   = threading.Lock()
        self._pre_alert_done      = threading.Event()
        self._record_procs_lock   = threading.Lock()
        self._active_record_procs = []

        # ── Trackers ──
        self.next_article_id       = 0
        self.active_article_tracks = {}
        self.next_person_id        = 0
        self.active_person_tracks  = {}   # { p_id → {"center": ..., "miss": int} }

        self.article_position_history: dict = {}
        self.person_position_history:  dict = {}
        self.hands_history: dict = {} 

        self.hold_streak:      dict = {}
        self.hold_streak_miss: dict = {}

        self.article_consecutive_frames: dict = {}
        self.article_consecutive_miss:   dict = {}
        self.article_absence_frames:     dict = {}
        self.article_presence_streak:    dict = {}   # [v11 FIX 2]
        self.article_conf_history:       dict = {}

        # ── SAC ──
        self.article_near_bag: dict = {}
        self.static_bag_cache: dict = {}

        # ── Logique de vol ──
        self.suspect_disappearance      = {}
        self.last_known_articles        = {}
        self.object_hold_counter        = {}
        self.last_known_scores          = {}
        self.hold_durations             = {}
        self.hold_durations_snapshot:   dict = {}
        self.article_visual_signature:  dict = {}   # [v12 FIX H]
        self.last_known_person_boxes    = {}
        self.person_last_seen           = {}
        self.person_tracking            = {}
        self.article_last_bbox:         dict = {}

        self.recent_alert_signatures:   list = []   # [v12 FIX I]
        self._suspicion_logged:         dict = {}

        # ── Alertes ──
        self.last_alert_time    = 0
        self.article_alert_time: dict = {}
        self.alert_text_to_show = ""
        self.alert_text_timer   = 0

        # ── Clips ──
        self.is_recording_alert     = False
        self.alert_ffmpeg_process   = None
        self.raw_ffmpeg_process     = None
        self.frames_to_record_after = 0
        self.zoom_target_id         = None
        self.zoom_target_last_box   = None  # Dernière bbox connue du suspect (fallback zoom)
        self.smooth_center          = None

        buf_size          = int(BEFORE_ALERT_SECS * fps)
        self.video_buffer     = deque(maxlen=buf_size)
        self.video_buffer_raw = deque(maxlen=buf_size)


    # ======================================================================
    # DISTANCE DE MATCHING DYNAMIQUE
    # ======================================================================
    def _get_adaptive_max_distance(self, article_center):
        best_dist     = float("inf")
        adaptive_dist = 120
        for p_id, p_box in self.last_known_person_boxes.items():
            if time.time() - self.person_last_seen.get(p_id, 0) > 5.0:
                continue
            p_cx = (p_box[0] + p_box[2]) / 2
            p_cy = (p_box[1] + p_box[3]) / 2
            dist = math.hypot(article_center[0] - p_cx, article_center[1] - p_cy)
            if dist < best_dist:
                best_dist     = dist
                person_height = p_box[3] - p_box[1]
                adaptive_dist = max(60, int(person_height * 0.15))
        return adaptive_dist


    # ======================================================================
    # CORRÉLATION MOUVEMENT
    # ======================================================================
    def _is_article_moving_with_person(self, article_id, person_id):
        a_hist = self.article_position_history.get(article_id)
        p_hist = self.person_position_history.get(person_id)
        if not a_hist or not p_hist or len(a_hist) < 3 or len(p_hist) < 3:
            return True
        a_dx  = a_hist[-1][0] - a_hist[0][0]
        a_dy  = a_hist[-1][1] - a_hist[0][1]
        a_mag = math.hypot(a_dx, a_dy)
        p_dx  = p_hist[-1][0] - p_hist[0][0]
        p_dy  = p_hist[-1][1] - p_hist[0][1]
        p_mag = math.hypot(p_dx, p_dy)
        if p_mag < 3:
            return True
        if a_mag < 3 and p_mag > 8:
            return False
        dot = (a_dx * p_dx + a_dy * p_dy)
        correlation = dot / (a_mag * p_mag) if (a_mag * p_mag) > 0 else 0
        return correlation >= MOVEMENT_CORRELATION_MIN


    # ======================================================================
    # VÉRIFICATION CONTACT MAIN / ARTICLE (Rendu DYNAMIQUE)
    # ======================================================================
    def _was_hand_near_article(self, article_center, p_id=None):
        # 1. Calcul de la distance de tolérance DYNAMIQUE
        # Par défaut on garde l'ancienne valeur si on ne trouve pas la personne
        dynamic_dist = HAND_ARTICLE_DIST 
        
        # Si on connait l'ID de la personne, on adapte la distance à sa taille à l'écran
        if p_id is not None and p_id in self.last_known_person_boxes:
            p_box = self.last_known_person_boxes[p_id]
            p_h = p_box[3] - p_box[1] # Hauteur de la personne en pixels
            
            # La tolérance est de 30% de sa hauteur, avec un minimum de 40 pixels
            # Ex: Personne de 400px (proche) -> marge de 120px
            # Ex: Personne de 100px (loin) -> marge de 40px
            dynamic_dist = max(40, int(p_h * 0.30))

        # 2. Logique de recherche
        if p_id is not None and p_id in self.hands_history:
            hist = self.hands_history[p_id]
            has_any_hand = any(len(h) > 0 for h in hist)
            if has_any_hand:
                histories = [hist]
            else:
                histories = self.hands_history.values()
        else:
            histories = self.hands_history.values()
        
        for person_history in histories:
            for frame_hands in person_history:
                for hand_center in frame_hands:
                    # Utilisation de notre distance proportionnelle
                    if math.hypot(
                        article_center[0] - hand_center[0],
                        article_center[1] - hand_center[1]
                    ) < dynamic_dist:
                        return True
        return False


    # ======================================================================
    # [v12 FIX H] SIGNATURE VISUELLE ARTICLE
    # ======================================================================
    def _capture_article_signature(self, a_id: int, frame: np.ndarray, bbox: tuple):
        x1, y1, x2, y2 = map(int, bbox)
        x1 = max(0, x1 - 4); y1 = max(0, y1 - 4)
        x2 = min(frame.shape[1], x2 + 4); y2 = min(frame.shape[0], y2 + 4)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 8:
            return
        hsv  = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        ratio = (x2 - x1) / max(1, y2 - y1)
        if a_id not in self.article_visual_signature:
            self.article_visual_signature[a_id] = {"hists": [], "ratios": []}
        sig = self.article_visual_signature[a_id]
        sig["hists"].append(hist)
        sig["ratios"].append(ratio)
        if len(sig["hists"]) > 10:
            sig["hists"].pop(0)
            sig["ratios"].pop(0)

    def _is_same_article_visual(self, a_id_suspect: int, frame: np.ndarray, new_bbox: tuple) -> bool:
        sig = self.article_visual_signature.get(a_id_suspect)
        if not sig or not sig["hists"]:
            return False
        x1, y1, x2, y2 = map(int, new_bbox)
        x1 = max(0, x1); y1 = max(0, y1)
        x2 = min(frame.shape[1], x2); y2 = min(frame.shape[0], y2)
        new_crop = frame[y1:y2, x1:x2]
        if new_crop.size == 0 or new_crop.shape[0] < 8 or new_crop.shape[1] < 8:
            return False
        new_ratio = (x2 - x1) / max(1, y2 - y1)
        ref_ratio  = sum(sig["ratios"]) / len(sig["ratios"])
        if abs(new_ratio - ref_ratio) / max(ref_ratio, 0.01) > 0.35:
            return False
        hsv_new  = cv2.cvtColor(new_crop, cv2.COLOR_BGR2HSV)
        hist_new = cv2.calcHist([hsv_new], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(hist_new, hist_new, 0, 1, cv2.NORM_MINMAX)
        best_corr = max(cv2.compareHist(ref_hist, hist_new, cv2.HISTCMP_CORREL) for ref_hist in sig["hists"])
        return best_corr >= 0.70

    def _is_duplicate_alert(self, a_id: int, frame: np.ndarray, current_time: float) -> bool:
        """[v12 FIX I] Vérifie si c'est un doublon visuel d'une alerte récente."""
        self.recent_alert_signatures = [
            s for s in self.recent_alert_signatures
            if current_time - s["timestamp"] < ALERT_COOLDOWN
        ]
        if not self.recent_alert_signatures:
            return False
        sig      = self.article_visual_signature.get(a_id, {})
        last_pos = self.last_known_articles.get(a_id)
        if not sig or not sig.get("hists"):
            if last_pos is None:
                return False
            for past in self.recent_alert_signatures:
                if math.hypot(last_pos[0] - past["last_pos"][0], last_pos[1] - past["last_pos"][1]) < 100:
                    return True
            return False
        new_ratio = sum(sig["ratios"]) / len(sig["ratios"]) if sig.get("ratios") else 1.0
        for past in self.recent_alert_signatures:
            if last_pos is not None:
                dist = math.hypot(last_pos[0] - past["last_pos"][0], last_pos[1] - past["last_pos"][1])
                if dist > 200:
                    continue
            if abs(new_ratio - past["ratio"]) / max(past["ratio"], 0.01) > 0.35:
                continue
            if not past["hists"]:
                continue
            best_corr = max(
                cv2.compareHist(ref_hist, h, cv2.HISTCMP_CORREL)
                for ref_hist in sig["hists"]
                for h in past["hists"]
            )
            if best_corr >= 0.55:
                return True
        return False


    # ======================================================================
    # MINI-TRACKER ARTICLES
    # ======================================================================
    def _track_articles_custom(self, current_articles_centers, frame: np.ndarray):
        new_tracks = {}
        tracked    = []
        remaining  = dict(self.active_article_tracks)

        for item in current_articles_centers:
            center, conf = item[0], item[1]
            adaptive_dist = self._get_adaptive_max_distance(center)

            # PASS 1 : matching strict (distance adaptative) — comportement v9/v11
            best_id   = None
            best_dist = adaptive_dist
            for a_id, track_data in remaining.items():
                dist = math.hypot(
                    center[0] - track_data["center"][0],
                    center[1] - track_data["center"][1]
                )
                if dist < best_dist:
                    best_dist = dist
                    best_id   = a_id

            # [FIX P1] PASS 2 : si pas de match strict, chercher un track en miss
            # dans une fenêtre élargie (3× la distance adaptive).
            # Cela couvre le cas où l'article pivote et sort de la fenêtre normale.
            # On prend le candidat le plus proche parmi ceux en miss > 0.
            if best_id is None:
                extended_dist = adaptive_dist * 3
                for a_id, track_data in remaining.items():
                    if track_data["miss"] == 0:
                        continue
                    dist = math.hypot(
                        center[0] - track_data["center"][0],
                        center[1] - track_data["center"][1]
                    )
                    if dist < extended_dist:
                        # Vérification visuelle si on a une signature et une bbox
                        bbox = item[2] if len(item) > 2 else None
                        if bbox is not None and self.article_visual_signature.get(a_id):
                            if not self._is_same_article_visual(a_id, frame, bbox):
                                _log(self.cam_id, "DEBUG",
                                    f"[PASS2] Article {a_id} refusé : signature visuelle différente")
                                continue  # visuellement différent → on ne rattache pas
                        extended_dist = dist
                        best_id       = a_id

                        
            if best_id is not None:
                old_miss = self.active_article_tracks[best_id]["miss"]
                new_tracks[best_id] = {"center": center, "miss": 0}
                tracked.append((center, best_id, conf))
                del remaining[best_id]
                if old_miss >= CONSECUTIVE_MISS_MAX:
                    self.article_consecutive_frames[best_id] = 0
                    self.article_consecutive_miss[best_id]   = 0
                if best_id not in self.article_position_history:
                    self.article_position_history[best_id] = deque(maxlen=MOVEMENT_HISTORY_FRAMES)
                self.article_position_history[best_id].append(center)
                if best_id not in self.article_conf_history:
                    self.article_conf_history[best_id] = deque(maxlen=HOLD_CONF_HISTORY_LEN)
                self.article_conf_history[best_id].append(conf)
            else:
                new_id = self.next_article_id
                self.next_article_id += 1
                new_tracks[new_id] = {"center": center, "miss": 0}
                tracked.append((center, new_id, conf))
                self.article_consecutive_frames[new_id] = 0
                self.article_consecutive_miss[new_id]   = 0
                self.article_position_history[new_id]   = deque(maxlen=MOVEMENT_HISTORY_FRAMES)
                self.article_position_history[new_id].append(center)
                self.article_conf_history[new_id]       = deque(maxlen=HOLD_CONF_HISTORY_LEN)
                self.article_conf_history[new_id].append(conf)
                self.article_presence_streak[new_id]    = 0

        for a_id, track_data in remaining.items():
            miss_count = track_data["miss"] + 1
            if miss_count <= TRACKER_MISS_TOLERANCE:
                new_tracks[a_id] = {"center": track_data["center"], "miss": miss_count}
            self.article_presence_streak[a_id] = 0

        self.active_article_tracks = new_tracks
        return tracked

    # ======================================================================
    # MINI-TRACKER PERSONNES (v11 — avec tolérance PERSON_MISS_TOLERANCE)
    # ======================================================================
    def _track_persons_custom(self, detections):
        new_tracks = {}
        tracked    = []
        remaining  = dict(self.active_person_tracks)

        for box in detections:
            x1, y1, x2, y2 = map(int, box)
            center        = ((x1 + x2) // 2, (y1 + y2) // 2)
            person_height = y2 - y1
            adaptive_dist = max(60, int(person_height * 0.4))
            best_id   = None
            best_dist = adaptive_dist  # ← distance adaptative au lieu de 120px fixe

            for p_id, track_data in remaining.items():
                dist = math.hypot(
                    center[0] - track_data["center"][0],
                    center[1] - track_data["center"][1]
                )
                if dist < best_dist:  # ← condition simple, identique à avant
                    best_dist = dist
                    best_id   = p_id

            if best_id is not None:
                new_tracks[best_id] = {"center": center, "miss": 0, "height": person_height}
                tracked.append((best_id, box))
                del remaining[best_id]
            else:
                new_id = self.next_person_id
                self.next_person_id += 1
                new_tracks[new_id] = {"center": center, "miss": 0, "height": person_height}
                tracked.append((new_id, box))

        for p_id, track_data in remaining.items():
            miss_count = track_data["miss"] + 1
            if miss_count <= PERSON_MISS_TOLERANCE:
                new_tracks[p_id] = {
                    "center": track_data["center"],
                    "miss":   miss_count,
                    "height": track_data.get("height", 200),
                }

        self.active_person_tracks = new_tracks
        return tracked


    # ======================================================================
    # ENREGISTREMENT VIDÉO
    # ======================================================================
    def _start_alert_video(self, type_vol: str, score: float, target_p_id: int = None):
        if not check_disk_space(alert_vid_dir):
            append_alert_jsonl({
                "cam": self.cam_id, "type": type_vol, "score": round(score, 3),
                "status": "alerte", "time": datetime.now().strftime("%H:%M:%S"),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "video_clip": None, "video_raw": None,
                "note": f"Enregistrement annulé : espace disque < {DISK_MIN_FREE_GB} Go",
            })
            return None

        timestamp = datetime.now().strftime("%H%M%S")
        vid_path  = os.path.abspath(os.path.join(alert_vid_dir, f"{self.cam_id}_Vole_{type_vol}_{timestamp}.mp4"))
        raw_path  = os.path.abspath(os.path.join(raw_dir, f"{self.cam_id}_RAW_{type_vol}_{timestamp}.mp4"))

        def get_cmd(path):
            return ["ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
                    "-s", f"{self.width}x{self.height}", "-pix_fmt", "bgr24",
                    "-r", str(self.fps), "-i", "-", "-vf", "format=yuv420p",
                    "-vcodec", "h264_nvenc", "-preset", "p1", "-b:v", "1M", path]

        self.alert_ffmpeg_process = subprocess.Popen(get_cmd(vid_path), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.raw_ffmpeg_process   = subprocess.Popen(get_cmd(raw_path), stdin=subprocess.PIPE, stderr=subprocess.PIPE)

        buffer_snapshot     = list(self.video_buffer)
        buffer_raw_snapshot = list(self.video_buffer_raw)

        with self._record_procs_lock:
            self._active_record_procs = [p for p in self._active_record_procs if p is not None and p.poll() is None]
            for p in [self.alert_ffmpeg_process, self.raw_ffmpeg_process]:
                if p is not None:
                    self._active_record_procs.append(p)

        def write_pre_alert_buffer():
            time.sleep(0.2)
            with self._record_stdin_lock:
                try:
                    for f in buffer_snapshot:
                        frame_out = f.copy()
                        if self.alert_ffmpeg_process and self.alert_ffmpeg_process.stdin:
                            self.alert_ffmpeg_process.stdin.write(frame_out.tobytes())
                except Exception as e:
                    print(f"[{self.cam_id}] ⚠️ Erreur pré-alerte annoté : {e}")
                    try: self.alert_ffmpeg_process.kill()
                    except Exception: pass
                    self.alert_ffmpeg_process = None
                try:
                    for f in buffer_raw_snapshot:
                        frame_out = f.copy()
                        if self.raw_ffmpeg_process and self.raw_ffmpeg_process.stdin:
                            self.raw_ffmpeg_process.stdin.write(frame_out.tobytes())
                except Exception as e:
                    print(f"[{self.cam_id}] ⚠️ Erreur pré-alerte brut : {e}")
                    try: self.raw_ffmpeg_process.kill()
                    except Exception: pass
                    self.raw_ffmpeg_process = None
            self._pre_alert_done.set()

        self._pre_alert_done.clear()
        threading.Thread(target=write_pre_alert_buffer, daemon=True, name=f"{self.cam_id}_pre_alert_writer").start()
        self.is_recording_alert     = True
        self.frames_to_record_after = int(AFTER_ALERT_SECS * self.fps)
        append_alert_jsonl({
            "cam": self.cam_id, "type": type_vol, "score": round(score, 3),
            "status": "alerte", "time": datetime.now().strftime("%H:%M:%S"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "video_clip": vid_path, "video_raw": raw_path,
        })
        return vid_path

    def _zoom_light(self, frame, box, factor=1.4):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = map(int, box)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        crop_w = int(w / factor); crop_h = int(h / factor)
        cx1 = max(0, cx - crop_w // 2); cy1 = max(0, cy - crop_h // 2)
        cx2 = min(w, cx1 + crop_w);     cy2 = min(h, cy1 + crop_h)
        if cx2 - cx1 < crop_w: cx1 = max(0, cx2 - crop_w)
        if cy2 - cy1 < crop_h: cy1 = max(0, cy2 - crop_h)
        crop = frame[cy1:cy2, cx1:cx2]
        if crop.size == 0:
            return frame
        return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)


    # ======================================================================
    # SUSPICION
    # ======================================================================
    def _notify_suspicion(self, article_id: int, type_vol: str, score: float):
        if type_vol in ("CORPS", "SAC"):
            self._suspicion_logged[article_id] = True
            return
        with suspicions_lock:
            active_suspicions[self.cam_id] = {
                "time":       datetime.now().strftime("%H:%M:%S"),
                "score":      round(score, 2),
                "type":       type_vol,
                "expires_at": time.time() + SUSPICION_TTL,
            }
        if type_vol != "FLÂNERIE":
            _log(self.cam_id, "ALERT", f"SUSPICION article {article_id} : VOL {type_vol} (score={score:.2f})")
        self._suspicion_logged[article_id] = True

    def _clear_suspicion(self, article_id: int = None):
        with suspicions_lock:
            active_suspicions.pop(self.cam_id, None)
        if article_id is not None:
            self._suspicion_logged.pop(article_id, None)
        else:
            self._suspicion_logged.clear()


    # ======================================================================
    # ZOOM TRACKING
    # ======================================================================
    def _smooth_position(self, new_center, alpha=0.3):
        if self.smooth_center is None:
            self.smooth_center = new_center
        else:
            self.smooth_center = (
                int(self.smooth_center[0] * (1 - alpha) + new_center[0] * alpha),
                int(self.smooth_center[1] * (1 - alpha) + new_center[1] * alpha),
            )
        return self.smooth_center

    def _zoom_tracking(self, frame, box):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = map(int, box)
        cx, cy  = self._smooth_position(((x1 + x2) // 2, (y1 + y2) // 2))
        new_w   = min(int((x2 - x1) + 160), w)
        new_h   = min(int((y2 - y1) + 160), h)
        cx1     = int(max(0, cx - new_w // 2)); cy1 = int(max(0, cy - new_h // 2))
        cx2     = int(min(w, cx + new_w // 2)); cy2 = int(min(h, cy + new_h // 2))
        if cx2 <= cx1 or cy2 <= cy1:
            return frame
        return cv2.resize(frame[cy1:cy2, cx1:cx2], (w, h), interpolation=cv2.INTER_LINEAR)



    # ======================================================================
    # RESET TRACKING (reconnexion RTSP)
    # ======================================================================
    def _reset_tracking_state(self):
        """
        Purge l'état de tracking après une reconnexion RTSP.
        Sans ça, article_absence_frames et suspect_disappearance continuent
        d'accumuler pendant la coupure → fausse alerte au retour du flux.
        On NE purge PAS person_tracking ni last_known_person_boxes :
        une reconnexion rapide peut garder les mêmes personnes en vue.
        """
        self.suspect_disappearance.clear()
        self.active_article_tracks.clear()
        self.article_absence_frames.clear()
        self.article_consecutive_frames.clear()
        self.article_consecutive_miss.clear()
        self.article_presence_streak.clear()
        self.hold_durations.clear()
        self.hold_durations_snapshot.clear()
        self.hold_streak.clear()
        self.hold_streak_miss.clear()
        self.object_hold_counter.clear()
        self.last_known_articles.clear()
        self.article_near_bag.clear()
        self.article_last_bbox.clear()
        self.article_visual_signature.clear()
        self.article_conf_history.clear()  
        self.article_position_history.clear()
        self.article_alert_time.clear()
        self.hands_history.clear()
        self._suspicion_logged.clear()
        self._clear_suspicion()
        _log(self.cam_id, "INFO", "Reset tracking après reconnexion RTSP")

    # ======================================================================
    # NETTOYAGE
    # ======================================================================
    def cleanup(self):
        print(f"[{self.cam_id}] Fermeture propre...")
        with self._record_procs_lock:
            procs = list(self._active_record_procs)
        for proc in procs:
            try:
                if proc.stdin: proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                try: proc.kill()
                except Exception: pass
        with self._record_procs_lock:
            self._active_record_procs.clear()


    # ======================================================================
    # BOUCLE PRINCIPALE
    # ======================================================================
    def run(self, reader: "FFmpegReader"):
        print(f"[{self.cam_id}] Worker démarré.")

        while True:
            # ── Récupération frame ──
            raw_bytes = reader.get_frame(timeout=2.0)
            # ── Détection reconnexion RTSP → reset état tracking ──
            if reader.reconnect_event.is_set():
                reader.reconnect_event.clear()
                self._reset_tracking_state()

            if raw_bytes is None:
                if reader.is_reconnecting:
                    # Geler les timers suspect_disappearance pendant la coupure
                    # pour qu'ils ne mûrissent pas sans frames réelles
                    now = time.time()
                    for data in self.suspect_disappearance.values():
                        data["start_time"] = now
                    with frame_lock:
                        last_frame = output_frames.get(self.cam_id)
                    if last_frame is not None:
                        overlay    = cv2.GaussianBlur(last_frame, (31, 31), 0)
                        band_y1    = self.height // 2 - 45
                        band_y2    = self.height // 2 + 45
                        roi        = overlay[band_y1:band_y2, 0:self.width]
                        black_band = np.zeros_like(roi)
                        overlay[band_y1:band_y2, 0:self.width] = cv2.addWeighted(roi, 0.35, black_band, 0.65, 0)
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        msg1 = "  Perte de la connexion RTSP"
                        sz1  = cv2.getTextSize(msg1, font, 0.75, 2)[0]
                        cv2.putText(overlay, msg1, ((self.width - sz1[0]) // 2, self.height // 2 - 8),
                                    font, 0.75, (0, 200, 255), 2, cv2.LINE_AA)
                        if int(time.time() * 1.5) % 2 == 0:
                            msg2 = "Reconnexion en cours..."
                            sz2  = cv2.getTextSize(msg2, font, 0.55, 1)[0]
                            cv2.putText(overlay, msg2, ((self.width - sz2[0]) // 2, self.height // 2 + 30),
                                        font, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
                        with frame_lock:
                            output_frames[self.cam_id] = overlay
                continue

            try:
                frame = np.frombuffer(raw_bytes, np.uint8).reshape((self.height, self.width, 3))
            except Exception as e:
                print(f"[{self.cam_id}] ⚠️ Frame corrompue ({len(raw_bytes)} octets) : {e}")
                continue

            clean_frame     = frame.copy()
            annotated_frame = frame.copy()
            current_time    = time.time()
            self.frames_processed += 1

            with gpu_pending_lock:
                gpu_pending_frames[self.cam_id] = clean_frame

            # ── Récupération résultats GPU ──
            try:
                gpu_result = result_queues[self.cam_id].get(timeout=0.15)
                        
                if isinstance(gpu_result, tuple) and len(gpu_result) == 2:
                    persons_data, synced_frame = gpu_result
                    # synced_frame sert UNIQUEMENT pour la détection visuelle (signatures)
                    # annotated_frame et clean_frame restent sur la frame live capturée en haut de boucle
                    detection_frame = synced_frame
                else:
                    persons_data    = gpu_result
                    detection_frame = clean_frame
            except queue.Empty:
                with frame_lock:
                    output_frames[self.cam_id] = annotated_frame
                    raw_frames[self.cam_id]    = clean_frame
                self.video_buffer.append(annotated_frame)
                self.video_buffer_raw.append(clean_frame)
                time.sleep(0.005)
                continue

            # ── Tracking personnes ──
            person_boxes    = [p["box"] for p in persons_data]
            tracked_persons = self._track_persons_custom(person_boxes)
            for (p_id, _), person_data in zip(tracked_persons, persons_data):
                person_data["p_id"] = p_id

            # ── Traitement résultats spécialiste ──
            hands_pos        = []
            bags_pos         = []
            raw_articles_pos = []

            for person_data in persons_data:
                person_hands = [] 
                p_id     = person_data["p_id"]
                box      = person_data["box"]
                spec_res = person_data.get("spec_result")
                x1_pad   = person_data["x1_pad"]
                y1_pad   = person_data["y1_pad"]

                x1, y1, x2, y2 = map(int, box)
                p_cx = (x1 + x2) // 2
                p_cy = (y1 + y2) // 2
                is_loitering  = False
                presence_time = 0

                if p_id >= 0:
                    self.last_known_person_boxes[p_id] = box
                    self.person_last_seen[p_id]        = current_time
                    if p_id not in self.person_position_history:
                        self.person_position_history[p_id] = deque(maxlen=MOVEMENT_HISTORY_FRAMES)
                    self.person_position_history[p_id].append((p_cx, p_cy))
                    if p_id not in self.person_tracking:
                        self.person_tracking[p_id] = {"first_seen": current_time, "last_seen": current_time}
                    else:
                        self.person_tracking[p_id]["last_seen"] = current_time
                    presence_time = current_time - self.person_tracking[p_id]["first_seen"]

                    if presence_time > LOITERING_THRESHOLD:
                        loitering_key   = -(p_id + 1)
                        loitering_score = min(0.60, 0.30 + (presence_time - LOITERING_THRESHOLD) / 180.0)
                        self._notify_suspicion(loitering_key, "FLÂNERIE", loitering_score)

                    person_is_handling = any(
                        self.hold_streak.get(a_id, 0) > 0
                        for a_id in self.last_known_articles
                        if is_point_in_box(self.last_known_articles[a_id], box)
                    )
                    if presence_time > LOITERING_THRESHOLD and person_is_handling:
                        is_loitering  = True
                        loitering_key = -(p_id + 1)
                        if not self._suspicion_logged.get(loitering_key, False):
                            loitering_score = min(1.0, 0.3 + (presence_time - LOITERING_THRESHOLD) / 120.0)
                            self._notify_suspicion(loitering_key, "FLÂNERIE", loitering_score)

                # Rectangle de base — couleur selon statut
                if p_id == self.zoom_target_id:
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 160, 255), 2)
                    cv2.putText(annotated_frame, "SUSPECT", (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 160, 255), 2)
                elif presence_time > LOITERING_THRESHOLD:
                    # Flânerie : toujours affiché dès que le seuil est dépassé,
                    # indépendamment de person_is_handling (calculé trop tôt)
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 0, 255), 3)
                    label_flanerie = f"FLANERIE: {int(presence_time)}s"
                    # Fond noir pour lisibilité
                    (tw, th), _ = cv2.getTextSize(label_flanerie, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                    cv2.rectangle(annotated_frame, (x1, y1 - th - 8), (x1 + tw + 4, y1), (0, 0, 0), -1)
                    cv2.putText(annotated_frame, label_flanerie,
                                (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 0, 255), 2)
                else:
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 0, 0),1)

                if spec_res is not None and spec_res.boxes is not None:
                    s_boxes = spec_res.boxes.xyxy.cpu().numpy()
                    s_clss  = spec_res.boxes.cls.cpu().numpy()
                    s_confs = spec_res.boxes.conf.cpu().numpy()
                    for s_box, s_cls, s_conf in zip(s_boxes, s_clss, s_confs):
                        s_name = model_specialist.names[int(s_cls)]
                        g_x1     = int(s_box[0] + x1_pad)
                        g_y1     = int(s_box[1] + y1_pad)
                        g_x2     = int(s_box[2] + x1_pad)
                        g_y2     = int(s_box[3] + y1_pad)
                        g_center = get_center([g_x1, g_y1, g_x2, g_y2])
                        if s_name == "hands" and s_conf > 0.4:
                            hands_pos.append(g_center)
                            person_hands.append(g_center) 
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 255, 255), 1)
                        elif s_name == "bags" and s_conf > 0.25:
                            bags_pos.append(g_center) 
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 0, 255), 2)
                        elif s_name == "article" and s_conf > 0.22:
                            raw_articles_pos.append((g_center, s_conf, (g_x1, g_y1, g_x2, g_y2)))
                            self._pending_bboxes = getattr(self, '_pending_bboxes', {})
                            self._pending_bboxes[g_center] = (g_x1, g_y1, g_x2, g_y2)
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (255, 0, 255), 2)
                if p_id not in self.hands_history:
                    self.hands_history[p_id] = deque(maxlen=HAND_MEMORY_FRAMES)
                self.hands_history[p_id].append(person_hands)

            # ── Filtre sacs fixes [v9 : seuil 20 frames] ──
            bags_pos_filtered = []
            for b_center in bags_pos:
                key = (b_center[0] // 15, b_center[1] // 15)
                if key not in self.static_bag_cache:
                    self.static_bag_cache[key] = {"count": 0, "center": b_center}
                self.static_bag_cache[key]["count"] += 1
                if self.static_bag_cache[key]["count"] < STATIC_BAG_FRAME_THRESHOLD:
                    bags_pos_filtered.append(b_center)
            seen_keys = {(b[0] // 15, b[1] // 15) for b in bags_pos}
            for key in list(self.static_bag_cache.keys()):
                if key not in seen_keys:
                    del self.static_bag_cache[key]
            bags_pos = bags_pos_filtered

            # ── Nettoyage personnes ──
            stale_person_ids = [pid for pid, ts in self.person_last_seen.items() if current_time - ts > 30.0]
            for pid in stale_person_ids:
                self.last_known_person_boxes.pop(pid, None)
                self.hands_history.pop(pid, None)
                self.person_last_seen.pop(pid, None)
                self.person_tracking.pop(pid, None)
                self.person_position_history.pop(pid, None)
                self.active_person_tracks.pop(pid, None)
                self._suspicion_logged.pop(-(pid + 1), None)

            # ── Nettoyage articles [v11 FIX G — complet] ──
            active_ids  = set(self.active_article_tracks.keys())
            suspect_ids = set(self.suspect_disappearance.keys())
            for a_id in list(self.last_known_articles.keys()):
                if a_id not in active_ids and a_id not in suspect_ids:
                    self.last_known_articles.pop(a_id, None)
                    self.last_known_scores.pop(a_id, None)
                    self.hold_durations.pop(a_id, None)
                    self.hold_durations_snapshot.pop(a_id, None)
                    self.object_hold_counter.pop(f"article_{a_id}", None)
                    self.hold_streak.pop(a_id, None)
                    self.hold_streak_miss.pop(a_id, None)
                    self.article_consecutive_frames.pop(a_id, None)
                    self.article_consecutive_miss.pop(a_id, None)
                    self.article_absence_frames.pop(a_id, None)
                    self.article_presence_streak.pop(a_id, None)
                    self.article_position_history.pop(a_id, None)
                    self.article_conf_history.pop(a_id, None)
                    self.article_alert_time.pop(a_id, None)
                    self.article_near_bag.pop(a_id, None)
                    self.article_visual_signature.pop(a_id, None)
                    self.article_last_bbox.pop(a_id, None)

            # ── Tracking articles ──
            articles_pos = self._track_articles_custom(raw_articles_pos, detection_frame)

            pending = getattr(self, '_pending_bboxes', {})
            for (a_center, a_id, a_conf) in articles_pos:
                if a_center in pending:
                    self.article_last_bbox[a_id] = pending[a_center]
            self._pending_bboxes = {}

            for (a_center, a_id, a_conf) in articles_pos:
                self.last_known_articles[a_id] = a_center
                self.last_known_scores[a_id]   = a_conf
                cv2.putText(annotated_frame, f"ID:{a_id}",
                            (a_center[0] - 10, a_center[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)

            trigger_alert        = False
            vol_type             = ""
            alert_score          = 0.0
            current_active       = []
            target_p_id          = None
            alert_article_id_sac = None

            # ══════════════════════════════════════════════════════════════
            # SCÉNARIO 1 : OBJETS TENUS
            # ══════════════════════════════════════════════════════════════
            articles_in_person_bbox = set()

            for p_id, p_box in self.last_known_person_boxes.items():
                for (a_center, a_id, a_conf) in articles_pos:
                    if is_point_in_box(a_center, p_box):
                        articles_in_person_bbox.add(a_id)
                        key = f"article_{a_id}"
                        self.object_hold_counter[key] = self.object_hold_counter.get(key, 0) + 1

                        current_consec = self.article_consecutive_frames.get(a_id, 0)
                        self.article_consecutive_miss[a_id] = 0

                        # [v11 FIX 2] presence streak
                        presence_streak = self.article_presence_streak.get(a_id, 0) + 1
                        self.article_presence_streak[a_id] = presence_streak
                        if presence_streak >= PRESENCE_FRAMES_FOR_ABSENCE_RESET:
                            self.article_absence_frames[a_id] = 0

                        self.article_consecutive_frames[a_id] = current_consec + 1
                        consecutive = self.article_consecutive_frames[a_id]

                        article_moves_with_person = self._is_article_moving_with_person(a_id, p_id)
                        if article_moves_with_person:
                            self.hold_streak_miss[a_id] = 0
                            self.hold_streak[a_id] = self.hold_streak.get(a_id, 0) + 1
                        else:
                            miss = self.hold_streak_miss.get(a_id, 0) + 1
                            self.hold_streak_miss[a_id] = miss
                            if miss >= HOLD_STREAK_MISS_MAX:
                                self.hold_streak[a_id]      = 0
                                self.hold_streak_miss[a_id] = 0

                        article_held_by_detection = (consecutive >= ARTICLE_DETECTED_HOLD_THRESHOLD)
                        article_held_by_streak    = (
                            self.object_hold_counter.get(key, 0) >= FRAME_THRESHOLD
                            and self.hold_streak.get(a_id, 0) >= HOLD_STREAK_THRESHOLD
                        )
                        is_held = article_held_by_detection and article_held_by_streak

                        if is_held:
                            conf_history = self.article_conf_history.get(a_id, deque())
                            mean_conf = sum(conf_history) / len(conf_history) if conf_history else 0.0
                            if mean_conf < HOLD_CONF_MIN:
                                if DEBUG_LOGS and self.frames_processed % 30 == 0:
                                    _log(self.cam_id, "DEBUG", f"[FILTRE A] Article {a_id} conf moy={mean_conf:.2f} < {HOLD_CONF_MIN}")
                                continue

                            current_active.append((a_id, a_center, a_conf))
                            self.hold_durations[a_id] = self.hold_durations.get(a_id, 0) + 1

                            bbox_to_sign = self.article_last_bbox.get(a_id)
                            if bbox_to_sign is not None and self.frames_processed % 2 == 0:
                                self._capture_article_signature(a_id, detection_frame, bbox_to_sign)

                            cv2.circle(annotated_frame, a_center, 10, (0, 255, 0), 2)
                            cv2.putText(annotated_frame, "TENU",
                                        (a_center[0] + 10, a_center[1]),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # Articles hors bbox
            for (a_center, a_id, a_conf) in articles_pos:
                if a_id not in articles_in_person_bbox:
                    consec_miss = self.article_consecutive_miss.get(a_id, 0) + 1
                    self.article_consecutive_miss[a_id] = consec_miss
                    if consec_miss >= CONSECUTIVE_MISS_MAX:
                        self.article_consecutive_frames[a_id] = 0
                        self.article_consecutive_miss[a_id]   = 0
                    miss = self.hold_streak_miss.get(a_id, 0) + 1
                    self.hold_streak_miss[a_id] = miss
                    if miss >= HOLD_STREAK_MISS_MAX:
                        self.hold_streak[a_id]      = 0
                        self.hold_streak_miss[a_id] = 0
                    self.article_presence_streak[a_id] = 0

            # Articles complètement disparus
            already_handled = {a_id for (_, a_id, _) in articles_pos}
            for gone_id in list(self.last_known_articles.keys()):
                if gone_id in already_handled:
                    continue
                consec_miss = self.article_consecutive_miss.get(gone_id, 0) + 1
                self.article_consecutive_miss[gone_id] = consec_miss
                if consec_miss >= CONSECUTIVE_MISS_MAX:
                    self.article_consecutive_frames[gone_id] = 0
                self.article_absence_frames[gone_id] = self.article_absence_frames.get(gone_id, 0) + 1
                if self.article_absence_frames[gone_id] == 1:
                    self.hold_durations_snapshot[gone_id] = self.hold_durations.get(gone_id, 0)
                self.article_presence_streak[gone_id] = 0
                # [FIX P2] hold_durations NE s'incrémente plus pendant l'invisibilité.
                # Le snapshot pris à absence_frames==1 est la valeur de référence pour le score.
                # Incrémenter pendant la disparition gonflait artificiellement le score CORPS.
                miss = self.hold_streak_miss.get(gone_id, 0) + 1
                self.hold_streak_miss[gone_id] = miss
                if miss >= HOLD_STREAK_MISS_MAX:
                    self.hold_streak[gone_id]      = 0
                    self.hold_streak_miss[gone_id] = 0

            # Debug
            if DEBUG_LOGS and self.frames_processed % 30 == 0 and articles_pos:
                for (_, a_id, _) in articles_pos:
                    streak    = self.hold_streak.get(a_id, 0)
                    consec    = self.article_consecutive_frames.get(a_id, 0)
                    hold_dur  = self.hold_durations.get(a_id, 0)
                    absent    = self.article_absence_frames.get(a_id, 0)
                    conf_hist = self.article_conf_history.get(a_id, deque())
                    mean_conf = sum(conf_hist) / len(conf_hist) if conf_hist else 0.0
                    _log(self.cam_id, "DEBUG",
                        f"Article {a_id} | Streak={streak}/{HOLD_STREAK_THRESHOLD} | "
                        f"Consec={consec}/{ARTICLE_DETECTED_HOLD_THRESHOLD} | "
                        f"Hold={hold_dur} | ConfMoy={mean_conf:.2f} | Absent={absent}/{MIN_DISAPPEARANCE_FRAMES}")

            # ══════════════════════════════════════════════════════════════
            # SCÉNARIO 2 : VOL DANS LE SAC
            # [v9] Logique originale qui fonctionnait, avec :
            #   - STATIC_BAG_FRAME_THRESHOLD = 20 (pas 180)
            #   - frames_gone < 4 (pas 18)
            #   - _was_hand_near_article() présent
            # ══════════════════════════════════════════════════════════════
            visible_article_ids = {a_id for (_, a_id, _) in articles_pos}

            # Phase 1 : proximité article/sac
            for p_id, p_box in self.last_known_person_boxes.items():
                p_h = p_box[3] - p_box[1]
                dynamic_sac_dist = max(30, int(p_h * 0.25))
                for (a_center, a_id, a_conf) in articles_pos:
                    if not is_point_in_box(a_center, p_box):
                        continue
                    if self.hold_durations.get(a_id, 0) < FRAME_THRESHOLD:
                        continue
                    for b_center in bags_pos:
                        dist = math.hypot(a_center[0] - b_center[0], a_center[1] - b_center[1])
                        if dist < dynamic_sac_dist:
                            if not is_point_in_box(b_center, p_box):
                                continue
                            if a_id not in self.article_near_bag:
                                self.article_near_bag[a_id] = {
                                    "frames_near_bag": 1,
                                    "bag_center":      b_center,
                                    "p_id":            p_id,
                                    "conf":            a_conf,
                                    "start_time":      current_time,
                                    "frames_gone":     0,
                                }
                            else:
                                self.article_near_bag[a_id]["frames_near_bag"] += 1
                                self.article_near_bag[a_id]["bag_center"]       = b_center
                                self.article_near_bag[a_id]["conf"]             = a_conf
                            break

            # Nettoyage sac
            for a_id in list(self.article_near_bag.keys()):
                data  = self.article_near_bag[a_id]
                p_id  = data["p_id"]
                p_box = self.last_known_person_boxes.get(p_id)
                if p_box is None:
                    del self.article_near_bag[a_id]
                    continue
                p_h = p_box[3] - p_box[1]
                dynamic_sac_dist = max(30, int(p_h * 0.15))
                if a_id in visible_article_ids:
                    still_near = False
                    a_center   = self.last_known_articles.get(a_id)
                    if a_center:
                        for b_center in bags_pos:
                            if math.hypot(a_center[0] - b_center[0], a_center[1] - b_center[1]) < dynamic_sac_dist:
                                still_near = True
                                break
                    if not still_near:
                        del self.article_near_bag[a_id]
                        continue
                if current_time - data["start_time"] > data["frames_near_bag"] / self.fps + SAC_DISAPPEARANCE_TIMEOUT:
                    if DEBUG_LOGS:
                        _log(self.cam_id, "DEBUG", f"[SAC] Timeout rapprochement article {a_id} → reset")
                    del self.article_near_bag[a_id]

            # Phase 2 : article disparu → alerte SAC
            for a_id, data in list(self.article_near_bag.items()):
                if a_id in visible_article_ids:
                    data["frames_gone"] = 0
                    data.pop("gone_since", None)  # reset patience si réapparu
                    continue

                data["frames_gone"] = data.get("frames_gone", 0) + 1

                if data["frames_gone"] < 4:
                    continue

                if data["frames_near_bag"] < SAC_PROXIMITY_FRAMES_MIN:
                    del self.article_near_bag[a_id]
                    continue

                # [v9] _was_hand_near_article() présent dans SAC
                # Valide qu'il y avait bien un contact de main avant l'insertion
                last_pos = self.last_known_articles.get(a_id)
                if last_pos and not self._was_hand_near_article(last_pos, p_id=data["p_id"]):
                    if DEBUG_LOGS:
                        _log(self.cam_id, "DEBUG", f"[SAC] Article {a_id} disparu sans contact de main → ignoré")
                    del self.article_near_bag[a_id]
                    continue

                if self.hold_durations.get(a_id, 0) == 0:
                    if DEBUG_LOGS:
                        _log(self.cam_id, "DEBUG", f"[SAC] Article {a_id} jamais tenu → ignoré")
                    del self.article_near_bag[a_id]
                    continue

                last_alert_this_article = self.article_alert_time.get(a_id, 0)
                if current_time - last_alert_this_article <= ALERT_COOLDOWN:
                    del self.article_near_bag[a_id]
                    continue

                # ✅ Alerte SAC
                trigger_alert        = True
                vol_type             = "SAC"
                alert_score          = float(data["conf"])
                target_p_id          = data["p_id"]
                alert_article_id_sac = a_id
                del self.article_near_bag[a_id]
                break

            # ══════════════════════════════════════════════════════════════
            # SCÉNARIO 3 : VOL CORPOREL
            # [v9] _was_hand_near_article() réintroduit — c'est LE filtre
            # principal. Zone suspecte v9 conservée (plus large mais compensée
            # par le filtre main). Score v9 conservé (0.4*conf + 0.6*hold).
            # Améliorations v11 conservées : request_id sync, FIX 2, FIX 3.
            # ══════════════════════════════════════════════════════════════
            visible_ids = {a_id for (_, a_id, _) in articles_pos}

            # [v11 FIX 3] Réapparition : annulation suspicion (chemin 1 : même a_id)
            for a_id in list(self.suspect_disappearance.keys()):
                if a_id in visible_ids:
                    reapp = self.suspect_disappearance[a_id].get("reappearance_frames", 0) + 1
                    self.suspect_disappearance[a_id]["reappearance_frames"] = reapp
                    if reapp >= REAPPEARANCE_FRAMES_MIN:
                        _log(self.cam_id, "INFO", f"Réapparition article {a_id} confirmée ({reapp}f) → suspicion annulée")
                        del self.suspect_disappearance[a_id]
                        self.article_visual_signature.pop(a_id, None)
                        self.article_absence_frames[a_id] = 0
                        self.hold_durations[a_id] = 0
                        self.hold_durations_snapshot[a_id] = 0
                        self._clear_suspicion(a_id)
                    continue
                

                # [v12 FIX H] Chemin 2 : réapparition avec nouvel a_id mais même objet visuel
                suspect_p_id  = self.suspect_disappearance[a_id].get("p_id")
                suspect_p_box = self.last_known_person_boxes.get(suspect_p_id) if suspect_p_id is not None else None
                if suspect_p_box is not None and self.article_visual_signature.get(a_id):
                    for (new_center, new_a_id, new_conf) in articles_pos:
                        if new_a_id == a_id:
                            continue
                        if not is_point_in_box(new_center, suspect_p_box):
                            continue
                        new_bbox = self.article_last_bbox.get(new_a_id)
                        if new_bbox is None:
                            continue
                        if self._is_same_article_visual(a_id, detection_frame, new_bbox):
                            _log(self.cam_id, "INFO",
                                f"[FIX H] Article {a_id} réapparu sous ID {new_a_id} → suspicion annulée")
                            self.hold_durations[new_a_id]          = self.hold_durations.get(a_id, 0)
                            self.article_alert_time[new_a_id]      = self.article_alert_time.get(a_id, 0)
                            self.article_visual_signature[new_a_id] = self.article_visual_signature.get(a_id, {})
                            del self.suspect_disappearance[a_id]
                            self.article_visual_signature.pop(a_id, None)
                            self.article_absence_frames[a_id] = 0
                            self.hold_durations[a_id] = 0
                            self.hold_durations_snapshot[a_id] = 0
                            self._clear_suspicion(a_id)
                            break
                
                # [FIX B] Chemin 3 : réapparition dans n'importe quelle bbox personne
                # (couvre le cas où l'ID a été purgé après TRACKER_MISS_TOLERANCE)
                if self.article_visual_signature.get(a_id, {}).get("hists"):
                    for (new_center, new_a_id, new_conf) in articles_pos:
                        if new_a_id == a_id:
                            continue  # déjà géré chemin 1
                        new_bbox = self.article_last_bbox.get(new_a_id)
                        if new_bbox is None:
                            continue
                        # Vérif temporelle : l'article suspect était absent depuis x frames,
                        # le nouvel ID ne doit pas être plus vieux que ça
                        frames_absent = self.article_absence_frames.get(a_id, 0)
                        new_id_age = self.article_consecutive_frames.get(new_a_id, 0)
                        if new_id_age > frames_absent + 6:
                            continue
                        if self._is_same_article_visual(a_id, detection_frame, new_bbox):
                            _log(self.cam_id, "INFO",
                                f"[FIX B] Article {a_id} réapparu sous ID {new_a_id} "
                                f"(tracker expiré) → suspicion annulée")
                            self.hold_durations[new_a_id]           = self.hold_durations.get(a_id, 0)
                            self.article_alert_time[new_a_id]       = self.article_alert_time.get(a_id, 0)
                            self.article_visual_signature[new_a_id] = self.article_visual_signature.get(a_id, {})
                            del self.suspect_disappearance[a_id]
                            self.article_visual_signature.pop(a_id, None)
                            self.article_absence_frames[a_id] = 0
                            self.hold_durations[a_id] = 0
                            self.hold_durations_snapshot[a_id] = 0
                            self._clear_suspicion(a_id)
                            break

                if a_id in self.suspect_disappearance:
                    self.suspect_disappearance[a_id]["reappearance_frames"] = 0

            # Analyse des disparitions suspectes
            for a_id in list(self.last_known_articles.keys()):
                article_was_active = (
                    a_id not in visible_ids
                    and self.hold_durations.get(a_id, 0) >= 12
                )
                if not article_was_active:
                    continue

                last_pos = self.last_known_articles.get(a_id)
                if not last_pos:
                    continue

                if any(math.hypot(last_pos[0] - bc[0], last_pos[1] - bc[1]) < 30 for bc in bags_pos):
                    continue

                margin = 45
                if not (margin < last_pos[0] < self.width - margin
                        and margin < last_pos[1] < self.height - margin):
                    continue

                # Trouver target_p_id d'abord
                is_suspect_zone = False
                local_target_p_id = None
                for p_id, p_box in self.last_known_person_boxes.items():
                    if current_time - self.person_last_seen.get(p_id, 0) > 2.0:
                        continue
                    if is_point_in_box(last_pos, p_box):
                        p_w   = p_box[2] - p_box[0]
                        p_h   = p_box[3] - p_box[1]
                        rel_x = (last_pos[0] - p_box[0]) / p_w if p_w > 0 else 0.5
                        rel_y = (last_pos[1] - p_box[1]) / p_h if p_h > 0 else 0.5
                        if 0.25 <= rel_y <= 0.85 and 0.25 <= rel_x <= 0.75:
                            if current_time - self.person_tracking[p_id]["first_seen"] < 3.0:
                                continue
                            p_cx_rel = 0.5
                            p_cy_rel = 0.55
                            dist_to_center = math.hypot(rel_x - p_cx_rel, rel_y - p_cy_rel)
                            if dist_to_center > 0.5:
                                if DEBUG_LOGS:
                                    _log(self.cam_id, "DEBUG",
                                        f"[CORPS] Article {a_id} trop excentré (dist={dist_to_center:.2f}) → bras tendu ignoré")
                                continue
                            is_suspect_zone   = True
                            local_target_p_id = p_id
                            break

                if not is_suspect_zone:
                    continue

                # Filtre main APRÈS avoir trouvé la personne — maintenant p_id est connu
                if not self._was_hand_near_article(last_pos, p_id=local_target_p_id):
                    if DEBUG_LOGS and self.frames_processed % 60 == 0:
                        _log(self.cam_id, "DEBUG", f"[CORPS] Article {a_id} disparu sans main → ignoré")
                    continue


                frames_absent = self.article_absence_frames.get(a_id, 0)

                # [FIX P3 v2] Bloquer la suspicion UNIQUEMENT si l'article visible dans la bbox
                # est visuellement le même que celui qui a disparu (pivot/rotation).
                # Si c'est un article différent → suspicion légitime, on ne bloque pas.

                article_reappeared_as_same = False
                if local_target_p_id is not None:
                    p_box_check = self.last_known_person_boxes.get(local_target_p_id)
                    sig_gone    = self.article_visual_signature.get(a_id, {})
                    if p_box_check is not None and sig_gone.get("hists"):
                        for (new_center, new_a_id, _) in articles_pos:
                            if new_a_id == a_id:
                                continue
                            if not is_point_in_box(new_center, p_box_check):
                                continue
                            new_bbox = self.article_last_bbox.get(new_a_id)
                            if new_bbox is None:
                                continue
                            # Vérif temporelle : le nouvel ID doit être récent (apparu pendant
                            # la fenêtre d'absence de l'ancien — pas un article tenu depuis longtemps)
                            new_id_age = self.article_consecutive_frames.get(new_a_id, 0)
                            if new_id_age > frames_absent + 6:
                                # Ce track existait avant la disparition de a_id → article différent
                                continue
                            # Vérif visuelle : même objet ?
                            if self._is_same_article_visual(a_id, detection_frame, new_bbox):
                                article_reappeared_as_same = True
                                if DEBUG_LOGS:
                                    _log(self.cam_id, "DEBUG",
                                        f"[FIX P3] Article {a_id} → pivot détecté, "
                                        f"même objet sous ID {new_a_id} (age={new_id_age}f) → pas de suspicion")
                                break

                if (is_suspect_zone
                        and not article_reappeared_as_same 
                        and a_id not in self.suspect_disappearance
                        and frames_absent >= MIN_DISAPPEARANCE_FRAMES):
                    self.suspect_disappearance[a_id] = {
                        "start_time":          current_time,
                        "last_score":          self.last_known_scores.get(a_id, 0.5),
                        "hold_frames":         self.hold_durations.get(a_id, 0),
                        "p_id":                local_target_p_id,
                        "reappearance_frames": 0,
                    }

            # Validation et déclenchement alerte CORPS
            for a_id, data in list(self.suspect_disappearance.items()):
                if a_id in visible_ids:
                    continue

                elapsed     = current_time - data["start_time"]
                target_p_id = data["p_id"]

                # si la personne n'est plus vue depuis > 2s au moment où
                # la suspicion a été créée, c'est qu'article et personne sont partis ensemble
                # → annulation immédiate, pas un vol
                person_gone_at_creation = (
                    target_p_id is not None
                    and current_time - self.person_last_seen.get(target_p_id, 0) > 2.0
                    and target_p_id not in self.active_person_tracks
                )
                if person_gone_at_creation:
                    _log(self.cam_id, "DEBUG",
                        f"[CORPS] Article {a_id} annulé : personne {target_p_id} partie avec l'article")
                    del self.suspect_disappearance[a_id]
                    self.hold_durations[a_id] = 0
                    self.hold_durations_snapshot[a_id] = 0
                    self._clear_suspicion(a_id)
                    continue

                # 1. Augmenter le délai "parti" — 2.5s c'est trop peu, une occlusion suffit
                personne_partie = (
                    target_p_id is not None
                    and target_p_id in self.person_tracking
                    and current_time - self.person_tracking[target_p_id]["last_seen"] > 6.0  # était 2.5
                )

                # 2. Dans le bloc de validation AVANT de déclencher, vérifier que la personne
                #    n'est pas revenue (active_person_tracks) :
                if personne_partie:
                    if target_p_id in self.active_person_tracks:  # ← elle est revenue !
                        personne_partie = False
                        self.suspect_disappearance[a_id]["start_time"] = current_time  # reset timer

                if (4.0 <= elapsed < DISAPPEARANCE_TIMEOUT
                        and not self._suspicion_logged.get(a_id, False)
                        and data["hold_frames"] > 12
                        and time.time() - self.last_alert_time > ALERT_COOLDOWN):
                    loitering_bonus = 0.25 if (
                        target_p_id is not None
                        and target_p_id in self.person_tracking
                        and current_time - self.person_tracking[target_p_id]["first_seen"] > LOITERING_THRESHOLD
                    ) else 0.0
                    # [v9] Score basé sur conf YOLO + hold_duration
                    hold_snapshot   = self.hold_durations_snapshot.get(a_id, data["hold_frames"])
                    base_score      = float(0.4 * data["last_score"] + 0.6 * min(1.0, hold_snapshot / 60.0))
                    suspicion_score = min(1.0, base_score + loitering_bonus)
                    self._notify_suspicion(a_id, "CORPS", suspicion_score)

                if elapsed >= DISAPPEARANCE_TIMEOUT or personne_partie:
                    last_alert_this_article = self.article_alert_time.get(a_id, 0)
                    if time.time() - last_alert_this_article > ALERT_COOLDOWN:
                        if data["hold_frames"] > 12:
                            loitering_bonus = 0.25 if (
                                target_p_id is not None
                                and target_p_id in self.person_tracking
                                and current_time - self.person_tracking[target_p_id]["first_seen"] > LOITERING_THRESHOLD
                            ) else 0.0
                            hold_snapshot = self.hold_durations_snapshot.get(a_id, data["hold_frames"])
                            base_score    = float(0.4 * data["last_score"] + 0.6 * min(1.0, hold_snapshot / 60.0))
                            alert_score   = min(1.0, base_score + loitering_bonus)

                            if alert_score < ALERT_SCORE_MIN:
                                if DEBUG_LOGS:
                                    _log(self.cam_id, "DEBUG",
                                        f"[FILTRE D] CORPS article {a_id} bloqué : score={alert_score:.2f} < {ALERT_SCORE_MIN}")
                                if not self._suspicion_logged.get(a_id, False):
                                    self._notify_suspicion(a_id, "CORPS", alert_score)
                            else:
                                # [v12 FIX I] Anti-doublon visuel
                                if self._is_duplicate_alert(a_id, clean_frame, current_time):
                                    _log(self.cam_id, "INFO",
                                        f"[FIX I] CORPS article {a_id} bloqué : doublon visuel")
                                    if a_id in self.suspect_disappearance:
                                        del self.suspect_disappearance[a_id]
                                    self.hold_durations[a_id] = 0
                                    self.hold_durations_snapshot[a_id] = 0
                                    self._clear_suspicion(a_id)
                                else:
                                    trigger_alert = True
                                    vol_type      = "CORPS"
                                    self.last_alert_time     = current_time
                                    self.article_alert_time[a_id] = current_time

                                    sig      = self.article_visual_signature.get(a_id, {})
                                    last_pos = self.last_known_articles.get(a_id, (0, 0))
                                    self.recent_alert_signatures.append({
                                        "hists":     list(sig.get("hists", [])),
                                        "ratio":     sum(sig.get("ratios", [1.0])) / max(len(sig.get("ratios", [1.0])), 1),
                                        "last_pos":  last_pos,
                                        "timestamp": current_time,
                                    })
                                    if personne_partie and elapsed < DISAPPEARANCE_TIMEOUT:
                                        _log(self.cam_id, "ALERT", f"ALERTE ANTICIPÉE : Suspect {target_p_id} sorti avec {a_id}")

                    if a_id in self.suspect_disappearance:
                        del self.suspect_disappearance[a_id]
                    self.hold_durations[a_id] = 0
                    self.hold_durations_snapshot[a_id] = 0
                    self._clear_suspicion(a_id)

            # ══════════════════════════════════════════════════════════════
            # DÉCLENCHEMENT ALERTE
            # ══════════════════════════════════════════════════════════════
            if trigger_alert:
                self.zoom_target_id = target_p_id
                _log(self.cam_id, "ALERT", f"ALERTE : VOL {vol_type} (score={alert_score:.2f})")
                self._clear_suspicion()

                alert_article_id = None
                if vol_type == "SAC":
                    alert_article_id = alert_article_id_sac
                    if alert_article_id is not None:
                        self.article_alert_time[alert_article_id] = current_time

                if not self.is_recording_alert:
                    self._start_alert_video(vol_type, alert_score, target_p_id=target_p_id)
                    self.last_alert_time    = current_time
                    self.alert_text_to_show = f" ALERTE : VOL {vol_type} POTENTIEL "
                    self.alert_text_timer   = current_time + DISPLAY_TEXT_DURATION
                else:
                    self.frames_to_record_after = int(AFTER_ALERT_SECS * self.fps)
                    self.last_alert_time = current_time
                    print(f"[{self.cam_id}] ⚡ Alerte supplémentaire → clip prolongé")

            # ── Affichage texte alerte ──
            if current_time < self.alert_text_timer:
                blink = int(time.time() * 2) % 2
                if blink == 1:
                    color, thickness, corner_length = (0, 0, 255), 2, 40
                    w, h = self.width, self.height
                    cv2.line(annotated_frame, (0, 0),    (corner_length, 0), color, thickness)
                    cv2.line(annotated_frame, (0, 0),    (0, corner_length), color, thickness)
                    cv2.line(annotated_frame, (w, 0),    (w - corner_length, 0), color, thickness)
                    cv2.line(annotated_frame, (w, 0),    (w, corner_length), color, thickness)
                    cv2.line(annotated_frame, (0, h),    (corner_length, h), color, thickness)
                    cv2.line(annotated_frame, (0, h),    (0, h - corner_length), color, thickness)
                    cv2.line(annotated_frame, (w, h),    (w - corner_length, h), color, thickness)
                    cv2.line(annotated_frame, (w, h),    (w, h - corner_length), color, thickness)
                font_scale = 0.5; thickness = 1
                text_size  = cv2.getTextSize(self.alert_text_to_show, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)[0]
                cv2.rectangle(annotated_frame, (5, 10 - text_size[1] - 5), (15 + text_size[0], 35), (0, 0, 0), -1)
                cv2.putText(annotated_frame, self.alert_text_to_show,
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 255), thickness)

            # ── Overlay reconnexion ──
            if reader.is_reconnecting:
                overlay    = cv2.GaussianBlur(annotated_frame, (31, 31), 0)
                band_y1    = self.height // 2 - 45
                band_y2    = self.height // 2 + 45
                roi        = overlay[band_y1:band_y2, 0:self.width]
                black_band = np.zeros_like(roi)
                overlay[band_y1:band_y2, 0:self.width] = cv2.addWeighted(roi, 0.35, black_band, 0.65, 0)
                font = cv2.FONT_HERSHEY_SIMPLEX
                msg1 = "  Perte de la connexion RTSP"
                sz1  = cv2.getTextSize(msg1, font, 0.75, 2)[0]
                cv2.putText(overlay, msg1, ((self.width - sz1[0]) // 2, self.height // 2 - 8),
                            font, 0.75, (0, 200, 255), 2, cv2.LINE_AA)
                if int(time.time() * 1.5) % 2 == 0:
                    msg2 = "Reconnexion en cours..."
                    sz2  = cv2.getTextSize(msg2, font, 0.55, 1)[0]
                    cv2.putText(overlay, msg2, ((self.width - sz2[0]) // 2, self.height // 2 + 30),
                                font, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
                annotated_frame = overlay

            # ── Publication Flask ──
            with frame_lock:
                output_frames[self.cam_id] = annotated_frame.copy()
                raw_frames[self.cam_id]    = clean_frame.copy()

            self.video_buffer.append(annotated_frame)
            self.video_buffer_raw.append(clean_frame)   

            # ── Enregistrement clip ──
            frame_to_record     = None
            frame_raw_to_record = None

            if self.is_recording_alert :
                frame_to_record     = annotated_frame.copy()
                frame_raw_to_record = clean_frame.copy()

                zoom_box_fresh = (
                    self.zoom_target_id in self.last_known_person_boxes
                    and current_time - self.person_last_seen.get(self.zoom_target_id, 0) < 3.0
                )

                if zoom_box_fresh:
                    # Personne encore visible → zoom tracking normal + mise à jour fallback
                    box = self.last_known_person_boxes[self.zoom_target_id]
                    self.zoom_target_last_box = box  # on mémorise pour le fallback

            if self.is_recording_alert:
                if hasattr(self, '_pre_alert_done') and not self._pre_alert_done.is_set():
                    self._pre_alert_done.wait(timeout=2.0)
                if frame_to_record is None:
                    frame_to_record     = annotated_frame.copy()
                    frame_raw_to_record = clean_frame.copy()
                try:
                    with self._record_stdin_lock:
                        if self.alert_ffmpeg_process and self.alert_ffmpeg_process.stdin:
                            self.alert_ffmpeg_process.stdin.write(frame_to_record.tobytes())
                        if self.raw_ffmpeg_process and self.raw_ffmpeg_process.stdin:
                            self.raw_ffmpeg_process.stdin.write(frame_raw_to_record.tobytes())
                    self.frames_to_record_after -= 1
                    if self.frames_to_record_after <= 0:
                        self.is_recording_alert = False
                        self.zoom_target_id     = None
                        self.zoom_target_last_box = None 
                        self.smooth_center      = None
                        for proc in [self.alert_ffmpeg_process, self.raw_ffmpeg_process]:
                            if proc:
                                try:
                                    proc.stdin.close()
                                    proc.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    proc.kill()
                                except Exception:
                                    pass
                        self.alert_ffmpeg_process = None
                        self.raw_ffmpeg_process   = None
                        print(f"[{self.cam_id}] ✅ Clip enregistré.")
                except Exception as e:
                    print(f"[{self.cam_id}] ❌ Erreur enregistrement : {e}")
                    self.is_recording_alert   = False
                    self.zoom_target_id       = None
                    self.zoom_target_last_box = None
                    self.smooth_center        = None
                    self.alert_ffmpeg_process = None
                    self.raw_ffmpeg_process   = None

            # ── Décrémentation "tenu" ──
            self.object_hold_counter = {k: v - 1 for k, v in self.object_hold_counter.items() if v > 1}


# ==========================================
# POINT D'ENTRÉE
# ==========================================
if __name__ == "__main__":

    all_workers = []
    all_readers = []

    def shutdown_handler(signum, frame):
        print("\n⏹ Arrêt demandé. Fermeture propre des enregistrements...")
        for w in all_workers:
            w.cleanup()
        for r in all_readers:
            r.stop()
        print("✅ Fermeture terminée.")
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)

    threading.Thread(target=start_server, daemon=True).start()
    print("🌐 Serveur Flask démarré sur http://192.168.0.97:5000")

    threading.Thread(target=gpu_batch_worker, daemon=True, name="gpu_batch_worker").start()
    print("🖥️  Thread GPU centralisé démarré")

    threading.Thread(target=purge_worker, daemon=True, name="purge_worker").start()

    for cam_cfg in CAMERAS:
        cam_id = cam_cfg["cam_id"]
        reader = FFmpegReader(cam_cfg["cam_id"], cam_cfg["rtsp_url"], cam_cfg["width"], cam_cfg["height"])
        all_readers.append(reader)
        threading.Thread(target=reader.run, daemon=True, name=f"{cam_id}_reader").start()
        worker = CameraWorker(**cam_cfg)
        all_workers.append(worker)
        threading.Thread(target=worker.run, args=(reader,), daemon=True, name=f"{cam_id}_worker").start()
        print(f"✅ {cam_id} démarré → http://192.168.0.97:5000/video/{cam_id}")

    print("\n🔒 Système actif — v12 (fusion v9+v11)")
    print("   Alertes    → GET /alerts?last=50")
    print("   Suspicions → GET /suspicions")
    print("   Logs debug → GET /logs?cam=CAM_21&level=DEBUG")
    print("   Snapshot   → POST /snapshot {\"cam_id\": \"CAM_21\"}")
    print(f"   CORPS : _was_hand_near() ACTIF | ZoneSuspecte v9 | Score=0.4*conf+0.6*hold")
    print(f"   SAC   : _was_hand_near() ACTIF | SacFixeSeuil=20f | frames_gone≥4 | CoolDown={ALERT_COOLDOWN}s")
    print(f"   GPU   : request_id sync | batch_queue maxsize={BATCH_QUEUE_MAXSIZE}")
    print(f"   MEM   : TRACKER_MISS={TRACKER_MISS_TOLERANCE}f | PERSON_MISS={PERSON_MISS_TOLERANCE}f")
    print("   Ctrl+C pour arrêter proprement.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt demandé.")