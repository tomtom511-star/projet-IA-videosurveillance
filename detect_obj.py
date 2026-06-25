#=========LOGIQUE DU CODE (le cerveau)============

import os  # Gestion des variables d'environnement et des chemins de fichiers (dossiers, existence de fichiers, stats disque)

# ==========================================
# CONFIGURATION GPU
# ==========================================
os.environ["CUDA_VISIBLE_DEVICES"] = "0"   # Force PyTorch et YOLO à n'utiliser que le GPU 0 (la carte NVIDIA du Z2)
os.environ["YOLO_VERBOSE"] = "False"        # Désactive les logs verbeux d'Ultralytics à chaque inférence (évite de polluer la console)

from ultralytics import YOLO   # Framework de détection d'objets — charge et exécute les modèles radar et spécialiste (.pt)
import cv2                     # OpenCV — lecture/écriture d'images, dessin des bounding boxes, encodage JPEG pour le flux MJPEG
import math                    # Fonctions mathématiques (hypot pour les distances pixel, ceil pour la purge disque)
import json                    # Sérialisation/désérialisation JSON — lecture et écriture des fichiers alerts.jsonl et logs.jsonl
import signal                  # Capture du signal SIGINT (Ctrl+C) pour déclencher l'arrêt propre des enregistrements FFmpeg
import numpy as np             # Tableaux numériques — conversion des frames brutes FFmpeg en matrices image, génération des sons pygame
import subprocess              # Lancement des processus FFmpeg en sous-processus (lecture RTSP et encodage des clips d'alerte)
from datetime import datetime  # Horodatage des alertes, logs et noms de fichiers de clips
import time                    # Timestamps flottants (time.time()) pour mesurer les durées (elapsed, cooldown, timeouts)
import torch                   # PyTorch — backend GPU pour les inférences YOLO, vérification CUDA, optimisation cudnn
from collections import deque  # File circulaire — pré-buffer vidéo (BEFORE_ALERT_SECS frames), historique positions et mains
import queue                   # Queues thread-safe — communication entre les threads caméra, GPU et les workers (result_queues, _sound_q)
from flask import Flask, Response, request, jsonify  # Serveur web léger — expose les flux MJPEG, les endpoints /alerts /logs /suspicions /sound
import threading               # Threads Python — un thread par caméra (reader + worker), thread GPU centralisé, thread son, watchdog FFmpeg



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
# Délai minimum (en secondes) entre deux alertes pour le même événement.
# Évite les alertes en rafale sur un même vol.
ALERT_COOLDOWN = 60

# Durée max (en secondes) pendant laquelle un article peut avoir disparu
# avant qu'on déclenche l'alerte CORPS.
DISAPPEARANCE_TIMEOUT = 9.0

# Nombre minimum de frames consécutives pour considérer un article comme "vu".
FRAME_THRESHOLD = 8

# Durée de présence (en secondes) d'une personne avant de la signaler
# comme flâneur suspect.
LOITERING_THRESHOLD = 180.0

# Durée d'affichage (en secondes) du texte d'alerte sur la frame vidéo.
DISPLAY_TEXT_DURATION = 4.0

# Secondes de vidéo conservées AVANT le déclenchement de l'alerte (buffer circulaire).
BEFORE_ALERT_SECS = 13

# Secondes de vidéo enregistrées APRÈS le déclenchement de l'alerte.
AFTER_ALERT_SECS = 7

# Nombre de frames pendant lesquelles le tracker peut "rater" une détection
# sans perdre l'identifiant d'un article (tolérance aux occlusions courtes).
TRACKER_MISS_TOLERANCE = 134

# Même principe que TRACKER_MISS_TOLERANCE, mais pour les personnes.
PERSON_MISS_TOLERANCE = 24


# ── PARAMÈTRES CONTACT MAIN ──
# Nombre de frames mémorisées pour l'historique des positions de mains.
# Permet de vérifier rétroactivement si une main était proche d'un article.
HAND_MEMORY_FRAMES = 45

# Distance en pixels en dessous de laquelle on considère qu'une main
# touche un article (valeur de base, adaptée dynamiquement selon la taille de la personne).
HAND_ARTICLE_DIST = 60

# Nombre de frames utilisées pour calculer la corrélation de mouvement
# entre un article et une personne.
MOVEMENT_HISTORY_FRAMES = 6

# Corrélation minimale (entre -1 et 1) pour qu'un article soit considéré
# comme "porté" par une personne (mouvement synchronisé).
MOVEMENT_CORRELATION_MIN = 0.6


# ── STREAK "TENU" ──
# Nombre de frames consécutives de mouvement synchronisé personne/article
# pour valider qu'un article est activement "tenu".
HOLD_STREAK_THRESHOLD = 20

# Tolérance : nombre de frames sans synchronisation avant de réinitialiser
# le compteur de "tenu".
HOLD_STREAK_MISS_MAX = 10

# Nombre de frames consécutives de détection d'un article dans la bbox
# d'une personne pour le déclarer tenu par détection directe.
ARTICLE_DETECTED_HOLD_THRESHOLD = 12

# Nombre de frames consécutives sans détection avant de réinitialiser
# le compteur de présence d'un article.
CONSECUTIVE_MISS_MAX = 8


# ── ANTI-FAUX-POSITIFS  ──
# Confiance YOLO moyenne minimale sur l'historique pour qu'un article
# soit pris en compte dans la logique de vol.
HOLD_CONF_MIN = 0.25

# Taille de la fenêtre glissante de confiances YOLO mémorisées par article.
HOLD_CONF_HISTORY_LEN = 20

# Nombre minimum de frames pendant lesquelles un article doit être absent
# avant qu'on ouvre une suspicion de vol corporel.
MIN_DISAPPEARANCE_FRAMES = 24

# Score de suspicion minimum pour déclencher une vraie alerte.
# En dessous, la suspicion est loguée mais n'enregistre pas de clip.
ALERT_SCORE_MIN = 0.3

# Nombre minimum de frames de réapparition consécutives pour annuler
# une suspicion (évite les annulations sur une détection fugace).
REAPPEARANCE_FRAMES_MIN = 3

# Nombre de frames de présence consécutives nécessaires pour remettre
# à zéro le compteur d'absence d'un article.
PRESENCE_FRAMES_FOR_ABSENCE_RESET = 3


# ── GPU ──
# Délai maximum (en secondes) pendant lequel le worker GPU attend
# des frames des caméras avant de traiter le batch partiel.
BATCH_TIMEOUT_SECS = 0.060

# Durée de vie (en secondes) d'une suspicion visible dans /suspicions
# avant qu'elle expire automatiquement.
SUSPICION_TTL = 30

# Taille maximale de la queue d'envoi au GPU.
# Limitée à 2× le nombre de caméras pour éviter la saturation RAM.
BATCH_QUEUE_MAXSIZE = len(CAMERAS) * 2


# ── SCÉNARIO SAC ──
# Nombre minimum de frames pendant lesquelles un article doit avoir
# été proche d'un sac pour valider la phase de "rapprochement".
SAC_PROXIMITY_FRAMES_MIN = 8

# Distance en pixels (de base) entre un article et un sac pour
# considérer qu'ils sont proches (adaptée dynamiquement à la taille de la personne).
SAC_PROXIMITY_DIST = 40

# Délai (en secondes) après lequel on abandonne un rapprochement article/sac
# si l'article ne disparaît pas.
SAC_DISAPPEARANCE_TIMEOUT = 2.0

# Fenêtre de patience (en secondes) avant de confirmer la disparition
# dans le sac, similaire au timeout CORPS.
SAC_DISAPPEARANCE_PATIENCE = 3.0



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
    Générateur MJPEG pour le flux vidéo Flask d'une caméra.
    
    Envoie en continu les frames annotées au format multipart/x-mixed-replace.
    Utilise last_sent_id (id mémoire de l'objet frame) pour ne pas renvoyer
    deux fois la même frame — évite les doublons sans polling agressif.
    
    Si aucune nouvelle frame n'est disponible, attend 20ms avant de réessayer
    plutôt que de boucler à vide (limite l'usage CPU).
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
    """
    Endpoint POST /snapshot — enregistre une capture brute (sans annotations)
    de la caméra demandée dans le dossier snapshots/.
    
    Corps JSON attendu : { "cam_id": "CAM_21" }
    Utilise raw_frames (frame sans bounding boxes) pour avoir une image propre.
    Retourne le chemin du fichier créé ou une erreur 500 si aucune frame disponible.
    """
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
    """
    Endpoint GET /alerts — retourne la liste des alertes enregistrées
    dans alerts.jsonl, du plus ancien au plus récent.
    
    Paramètre optionnel : ?last=N → retourne uniquement les N dernières alertes.
    Chaque alerte est un objet JSON avec cam, type, score, time, date,
    video_clip et video_raw.
    """
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
    """
    Endpoint GET /suspicions — retourne les suspicions actives en temps réel,
    c'est-à-dire les situations où le système surveille un comportement
    suspect sans avoir encore déclenché d'alerte.
    
    Les suspicions expirées (> SUSPICION_TTL secondes) sont purgées
    à chaque appel avant de retourner la réponse.
    Retourne un dict { cam_id: { time, score, type } }.
    """
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

# ── SSE clients ──
_sse_clients      = []
_sse_clients_lock = threading.Lock()

def _push_sse_event(data: dict):
    msg = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_clients_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)

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
    """
    Fonction de logging centralisée pour tout le système.
    
    Écrit simultanément :
    - Dans log_buffer (deque en RAM, maxlen=2000) pour l'endpoint /logs.
    - Dans logs.jsonl sur disque pour la persistance entre redémarrages.
    
    Rotation automatique du fichier disque au-delà de LOGS_MAX_LINES lignes
    (conserve les 50 000 dernières lignes).
    Si DEBUG_LOGS est True, affiche aussi en console via print().
    """
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
    if level in ("ALERT", "INFO"):
        _push_sse_event({"type": "log", "entry": entry})
    if DEBUG_LOGS:
        print(f"[{cam_id}] [{level}] {message}")

@app.route("/logs")
def get_logs():
    """
    Endpoint GET /logs — retourne les logs récents depuis le buffer RAM.
    
    Paramètres optionnels :
    - ?cam=CAM_21   → filtre sur une caméra spécifique (ou ALL pour tout)
    - ?level=DEBUG  → filtre sur un niveau de log (DEBUG, INFO, ALERT, ERROR)
    - ?last=200     → nombre de lignes retournées (défaut : 200)
    
    Utile pour le debug en production sans accès SSH au serveur.
    """
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



# ==========================================
# SYSTÈME SONORE (corrigé v12)
# ==========================================
import pygame as _pygame
import queue as _sound_queue_module

sound_enabled = True
sound_lock    = threading.Lock()

# Queue dédiée — le son est joué dans UN thread unique
# évite les crashs PulseAudio depuis les threads worker GPU/caméra
_sound_q    = _sound_queue_module.Queue(maxsize=8)
_PYGAME_OK  = False
_sounds     = {}   # lazy-init : créés au premier appel


def _init_pygame_audio():
    """
    Initialise pygame.mixer en stéréo (channels=2 obligatoire pour
    que sndarray.make_sound() fonctionne correctement avec numpy).
    Appelé une seule fois depuis le thread son dédié.
    """
    global _PYGAME_OK
    try:
        _pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        _PYGAME_OK = True
        _log("SYSTEM", "INFO", "pygame.mixer initialisé (stéréo 44100Hz)")
    except Exception as e:
        _log("SYSTEM", "ERROR", f"Son désactivé (pygame non dispo) : {e}")
        _PYGAME_OK = False


def _make_sound(freqs_durations: list, volume: float = 1.0):
    """
    Génère un son composite à partir d'une liste (fréquence_hz, durée_ms).
    Retourne un objet pygame.Sound ou None.

    CORRECTIF CLÉ : le tableau numpy doit être shape (N, 2) — stéréo —
    et dtype int16. Avec channels=1 (ancienne config), make_sound()
    acceptait le tableau mais produisait un son vide ou corrompu.
    """
    if not _PYGAME_OK:
        return None
    import numpy as np
    sample_rate = 44100
    segments    = []
    for freq, dur_ms in freqs_durations:
        n_samples = int(sample_rate * dur_ms / 1000)
        if freq == 0:
            mono = np.zeros(n_samples, dtype=np.float32)
        else:
            t       = np.linspace(0, dur_ms / 1000, n_samples, endpoint=False)
            mono    = np.sin(2 * np.pi * freq * t).astype(np.float32)
            attack  = min(int(sample_rate * 0.005), n_samples // 4)
            release = min(int(sample_rate * 0.030), n_samples // 4)
            mono[:attack]   *= np.linspace(0, 1, attack)
            mono[-release:] *= np.linspace(1, 0, release)
        segments.append(mono)

    mono_full = np.concatenate(segments) * volume
    # Stéréo obligatoire : shape (N, 2), dtype int16
    stereo    = np.column_stack([mono_full, mono_full])
    wave_16   = (stereo * 32767).astype(np.int16)
    # make_sound attend un tableau C-contigu
    wave_16   = np.ascontiguousarray(wave_16)
    try:
        return _pygame.sndarray.make_sound(wave_16)
    except Exception as e:
        _log("SYSTEM", "ERROR", f"make_sound() échoué : {e}")
        return None


def _ensure_sounds():
    """
    Initialisation lazy des sons — appelée depuis le thread son dédié,
    après que pygame.mixer soit prêt. Évite la création au top-level
    où le device audio peut ne pas être accessible.
    """
    if _sounds:
        return
    alert_sound = _make_sound([
        (1046, 120), (0, 40),
        (880,  120), (0, 40),
        (698,  250),
    ], volume=0.9)
    suspicion_sound = _make_sound([
        (1800, 12), (0, 8), (2200, 80), (0, 15), (1800, 40),
], volume=0.55)
    _sounds["alert"]     = alert_sound
    _sounds["suspicion"] = suspicion_sound
    if alert_sound and suspicion_sound:
        _log("SYSTEM", "INFO", "Sons synthétisés OK (alert + suspicion)")
    else:
        _log("SYSTEM", "ERROR", "Échec synthèse sons — vérifie PulseAudio/ALSA")


def _sound_worker():
    """
    Thread daemon dédié à la lecture audio.

    POURQUOI UN THREAD DÉDIÉ :
    Sur Linux, PulseAudio/ALSA peut rejeter les appels play() depuis des
    threads sans contexte audio propre (workers GPU, workers caméra).
    Ce thread unique initialise pygame depuis son propre contexte,
    puis consomme la queue _sound_q indéfiniment.

    Lazy-init ici (pas au top-level) pour que pygame soit initialisé
    APRÈS que l'OS ait fini de démarrer les services audio.
    """
    _init_pygame_audio()
    if _PYGAME_OK:
        _ensure_sounds()
    while True:
        try:
            kind = _sound_q.get(timeout=1.0)
        except _sound_queue_module.Empty:
            continue
        if not _PYGAME_OK:
            continue
        with sound_lock:
            enabled = sound_enabled
        if not enabled:
            continue
        sound = _sounds.get(kind)
        if sound is not None:
            try:
                sound.play()
            except Exception as e:
                _log("SYSTEM", "ERROR", f"sound.play() échoué : {e}")


def play_sound(kind: str):
    """
    Empile une demande de son dans la queue — non bloquant, thread-safe.
    kind : "alert" | "suspicion"
    Si la queue est pleine (8 sons en attente), on ignore silencieusement.
    """
    with sound_lock:
        enabled = sound_enabled
    if not enabled:
        return
    try:
        _sound_q.put_nowait(kind)
    except _sound_queue_module.Full:
        pass


def toggle_sound() -> bool:
    """Bascule l'état du son et retourne le nouvel état (True = activé)."""
    global sound_enabled
    with sound_lock:
        sound_enabled = not sound_enabled
        state = sound_enabled
    _log("SYSTEM", "INFO", f"Son {'activé' if state else 'désactivé'} (Ctrl+B)")
    return state


@app.route("/sound/status")
def sound_status():
    """Endpoint GET /sound/status — retourne l'état courant du son."""
    with sound_lock:
        state = sound_enabled
    return jsonify({"enabled": state})
    
@app.route("/sound/toggle", methods=["POST"])
def sound_toggle():
    state = toggle_sound()
    return jsonify({"enabled": state})

@app.route("/stream")
def sse_stream():
    import queue as _q
    def generate():
        q = _q.Queue(maxsize=50)
        with _sse_clients_lock:
            _sse_clients.append(q)
        try:
            # État initial
            try:
                with open(ALERT_FILE, "r") as f:
                    pending = sum(
                        1 for line in f
                        if line.strip() and
                        json.loads(line).get("label") not in ("VP", "FP")
                    )
            except Exception:
                pending = 0
            yield f"data: {json.dumps({'type':'init','pending_alerts':pending})}\n\n"
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except _q.Empty:
                    yield "data: {\"type\":\"heartbeat\"}\n\n"
        except GeneratorExit:
            with _sse_clients_lock:
                try:
                    _sse_clients.remove(q)
                except ValueError:
                    pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


def start_server():
    """
    Lance le serveur Flask sur toutes les interfaces réseau (0.0.0.0:5000)
    dans un thread daemon séparé.
    Les logs werkzeug sont réduits au niveau ERROR pour ne pas polluer
    la console avec les requêtes HTTP normales.
    """
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)


# ==========================================
# FONCTIONS UTILITAIRES
# ==========================================
def get_center(box):
    """Retourne le centre (cx, cy) d'une bounding box [x1, y1, x2, y2]."""
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int((y1 + y2) / 2))


def is_point_in_box(point, box):
    """Retourne True si le point (px, py) est à l'intérieur de la box [x1, y1, x2, y2], bornes incluses."""
    px, py = point
    x1, y1, x2, y2 = box
    return x1 <= px <= x2 and y1 <= py <= y2


def read_exactly(pipe, n_bytes):
    """
    Lit exactement n_bytes octets depuis un pipe, en plusieurs lectures
    si nécessaire (un read() peut retourner moins que demandé).
    
    Retourne None si le pipe est fermé avant d'avoir lu tous les octets
    (signal de fin de flux FFmpeg → reconnexion RTSP déclenchée).
    """
    buf = bytearray()
    while len(buf) < n_bytes:
        remaining = n_bytes - len(buf)
        chunk = pipe.read(remaining)
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def drain_stderr(process, cam_id: str, stop_event: threading.Event):
    """
    Thread dédié à la lecture continue du stderr de FFmpeg.
    
    Sans ce drain, le pipe stderr se remplit et bloque FFmpeg quand
    il tente d'écrire un warning ou une erreur → gel du flux vidéo.
    
    Rate-limiting à 1 log/seconde pour éviter de noyer les logs du système
    avec les messages répétitifs de FFmpeg (perte de paquets, etc.).
    Seules les lignes contenant "error" sont loggées.
    """
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
    """
    Ajoute une alerte au fichier alerts.jsonl de façon thread-safe
    (verrou alerts_file_lock partagé avec l'endpoint /alerts).
    Chaque alerte est écrite sur une ligne JSON distincte (format JSONL).
    """
    with alerts_file_lock:
        with open(ALERT_FILE, "a") as f:
            f.write(json.dumps(alert_dict, ensure_ascii=False) + "\n")
            _push_sse_event({"type": "new_alert", "alert": alert_dict})


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
    """
    Libération d'urgence d'espace disque quand le seuil critique est atteint.
    Supprime les 30% de clips les plus anciens (tri par date de modification)
    dans alert_clips/ et alert_clips/raw/.
    Retourne True si l'espace libre après suppression dépasse DISK_MIN_FREE_GB.
    """

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
    """
    Vérifie que l'espace disque disponible dépasse DISK_MIN_FREE_GB.
    Si l'espace est insuffisant, tente une libération d'urgence via
    emergency_free_space(). Retourne True si l'espace est suffisant
    après éventuelle libération, False sinon.
    Appelée avant chaque démarrage d'enregistrement de clip.
    """
    free_gb = _get_free_gb(path)
    if free_gb < 0:
        return True
    if free_gb >= DISK_MIN_FREE_GB:
        return True
    print(f"⚠️  ESPACE DISQUE BAS : {free_gb:.2f} Go libres. Tentative de libération...")
    return emergency_free_space()


def purge_old_clips():
    """
    Purge planifiée des clips anciens, exécutée toutes les PURGE_INTERVAL_SECS
    secondes par purge_worker().
    Supprime tous les fichiers .mp4 dont la date de modification dépasse
    CLIP_RETENTION_DAYS jours dans les dossiers alert_clips/ et raw/.
    """
    cutoff = time.time() - (CLIP_RETENTION_DAYS * 86400)
    total_deleted = total_freed = 0
    archive_dir     = os.path.join("alert_clips", "archives")
    archive_raw_dir = os.path.join("alert_clips", "archives", "raw")
    for folder in [alert_vid_dir, raw_dir, archive_dir, archive_raw_dir]:
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
    """
    Thread GPU centralisé (unique pour toutes les caméras).
    
    Collecte les frames en attente dans gpu_pending_frames pendant
    BATCH_TIMEOUT_SECS, puis déclenche une inférence en batch sur
    les deux modèles YOLO :
    
    1. model_radar : détecte les personnes sur les frames complètes.
    2. model_specialist : détecte mains, sacs et articles sur les crops
       de chaque personne détectée (inférence en batch sur tous les crops
       de toutes les caméras simultanément).
    
    Les résultats sont poussés dans result_queues[cam_id] pour être
    consommés par chaque CameraWorker indépendamment.
    
    Ce design évite les conflits GPU entre threads et maximise l'utilisation
    du GPU en regroupant les inférences.
    """
    cycle_count = 0
    while True:
        cycle_start = time.time()
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
        collect_duration = time.time() - cycle_start

        if not batch:
            continue

        cam_ids = list(batch.keys())
        frames  = [batch[c] for c in cam_ids]

        all_crops  = []
        radar_data = {}

        try:
            with torch.no_grad():
                t0 = time.time()
                radar_results = model_radar.predict(frames, verbose=False, conf=0.15, imgsz=416, half=True)
                radar_time = time.time() - t0
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
        spec_time   = 0.0
        if all_crops:
            try:
                with torch.no_grad():
                    t1 = time.time()
                    spec_results = model_specialist.predict(all_crops, verbose=False, conf=0.15, half=True)
                    spec_time = time.time() - t1
                for idx, res in enumerate(spec_results):
                    spec_by_idx[idx] = res
            except Exception as e:
                print(f"[GPU] ❌ Erreur Spécialiste : {e}")

        cycle_count += 1
        total_cycle = time.time() - cycle_start


        #======================
        #    LOG GPU
        #======================

        #if cycle_count % 50 == 0:
            #_log("GPU", "DEBUG",
                #f"[Cycle GPU #{cycle_count}] "
                #f"Caméras dans le batch : {len(batch)}/{len(CAMERAS)} | "
                #f"Collecte des frames : {collect_duration*1000:.0f} ms | "
                #f"Détection personnes (modèle Radar) : {radar_time*1000:.0f} ms | "
                #f"Détection objets/mains/sacs (modèle Spécialiste) : {spec_time*1000:.0f} ms "
                #f"sur {len(all_crops)} zones découpées | "
                #f"Durée totale du cycle GPU : {total_cycle*1000:.0f} ms")

        for i, cam_id in enumerate(cam_ids):
            persons = radar_data.get(cam_id, [])
            for p in persons:
                p["spec_result"] = spec_by_idx.get(p["crop_idx"])
            _put_result(cam_id, persons, frames[i])


def _put_result(cam_id: str, persons: list, frame: np.ndarray):
    """
    Pousse le résultat d'inférence GPU dans la queue de résultats
    de la caméra concernée (result_queues[cam_id], taille max 1).
    
    Si la queue est pleine (le CameraWorker n'a pas encore consommé
    le résultat précédent), l'ancien résultat est éjecté avant l'insertion
    pour toujours avoir le résultat le plus récent disponible.
    """
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
            ["ffmpeg", "-loglevel", "warning",
            "-rtsp_transport", "tcp",
            "-rtsp_flags", "prefer_tcp",
            "-timeout", "5000000",
            "-max_delay", "500000",
            "-i", self.rtsp_url,
            "-vf", f"scale={self.width}:{self.height}",
            "-f", "image2pipe", "-pix_fmt", "bgr24", "-vcodec", "rawvideo", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=self._bufsize,
        )

    def run(self):
        """
        Boucle principale du lecteur RTSP pour une caméra.
        
        Lance FFmpeg en subprocess avec sortie rawvideo BGR24 sur stdout,
        lit les frames octet par octet via read_exactly(), et les pousse
        dans self.queue (taille 1 — on ne garde que la frame la plus récente).
        
        Mécanismes de robustesse :
        - Watchdog : thread séparé qui tue FFmpeg si aucune frame n'arrive
        depuis 5 secondes, forçant une reconnexion.
        - drain_stderr : thread séparé qui vide le pipe stderr de FFmpeg
        pour éviter tout blocage.
        - Reconnexion automatique : en cas de coupure, attend 3s et relance
        FFmpeg. Lève reconnect_event pour que CameraWorker purge son état.
        - select() avec timeout 2s : évite le blocage infini sur stdout.
        """
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
                        _log(self.cam_id, "ERROR", "Caméra ne répond plus depuis 5s — reconnexion forcée")
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
                        _log(self.cam_id, "ERROR", "Flux vidéo interrompu — reconnexion en cours...")
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
                _log(self.cam_id, "INFO", "Caméra déconnectée — nouvelle tentative dans 3s...")
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
        self.gpu_miss_count = 0

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
        self.article_raw_presence:       dict = {}
        self.article_conf_history:       dict = {}
        self.article_holder: dict = {} 

        # ── SAC ──
        self.article_near_bag: dict = {}

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
        """
        Calcule la distance maximale de matching article↔tracker de façon dynamique.
        
        Plus une personne est proche de la caméra (bbox grande), plus la tolérance
        est grande. Cela évite de perdre le suivi d'articles tenus par des personnes
        proches, tout en restant strict pour les personnes éloignées.
        
        Retourne une distance en pixels (min 60px).
        """
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
        """
        Vérifie que l'article et la personne se déplacent dans la même direction
        (corrélation vectorielle sur les N dernières frames).
        
        Retourne True si :
        - L'historique est insuffisant (bénéfice du doute),
        - La personne est quasi-immobile (article potentiellement posé sur elle),
        - La corrélation de direction dépasse MOVEMENT_CORRELATION_MIN.
        
        Retourne False si l'article est immobile mais la personne bouge
        (article posé sur une étagère, pas tenu).
        """
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
        """
        Vérifie rétroactivement si une main était proche d'un article
        dans les HAND_MEMORY_FRAMES dernières frames.
        
        C'est le filtre principal anti-faux-positifs pour les scénarios CORPS et SAC :
        un article ne peut être signalé volé que si une main l'a approché.
        
        La distance de tolérance est proportionnelle à la taille de la personne
        à l'écran (30% de sa hauteur, min 40px) pour rester pertinente quelle
        que soit la distance à la caméra.
        
        Si p_id est fourni, on ne consulte que l'historique des mains de cette
        personne. Sinon, on cherche dans toutes les personnes suivies.
        """
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
                return False
        else:
            # S'il n'y a pas de p_id fourni (ex: vol dans un sac sans suspect identifié)
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
        """
        [FIX H v12] Enregistre la signature visuelle (histogramme couleur HSV
        + ratio largeur/hauteur) d'un article tenu, pour pouvoir le reconnaître
        même s'il change d'identifiant tracker après une occlusion.
        
        Conserve un historique glissant de 10 signatures.
        """
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
        """
        Compare la signature visuelle d'un article suspect à une nouvelle
        détection pour déterminer si c'est le même objet physique.
        
        Critères de match :
        - Ratio largeur/hauteur similaire (tolérance 35%)
        - Corrélation d'histogramme HSV ≥ 0.70 avec au moins une signature mémorisée
        
        Utilisé pour éviter de déclencher une alerte quand l'article réapparaît
        sous un nouvel identifiant tracker (pivot, réocclusion brève).
        """
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
        return best_corr >= 0.82

    def _is_duplicate_alert(self, a_id: int, frame: np.ndarray, current_time: float) -> bool:
        """
        [FIX I v12] Vérifie si l'alerte sur cet article est un doublon
        d'une alerte déjà déclenchée récemment (dans la fenêtre ALERT_COOLDOWN).
        
        Double critère de déduplication :
        1. Position : si la dernière position connue est à moins de 100px d'une
        alerte récente → doublon probable.
        2. Visuel : si l'histogramme HSV et le ratio de forme correspondent
        à une alerte récente (corrélation ≥ 0.65) → doublon confirmé.
        
        Évite qu'un même article déclenche plusieurs alertes successives
        à cause d'une réapparition/disparition cyclique.
        """
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
            if best_corr >= 0.70:
                return True
        return False


    # ======================================================================
    # MINI-TRACKER ARTICLES
    # ======================================================================
    def _track_articles_custom(self, current_articles_centers, frame: np.ndarray):
        """
        Mini-tracker maison pour les articles détectés.
        
        Algorithme en deux passes :
        - Passe 1 (stricte) : associe chaque détection au track le plus proche
        dans un rayon adaptatif (proportionnel à la taille de la personne).
        - Passe 2 (élargie) : si aucun match strict, cherche parmi les tracks
        en "miss" dans un rayon 2.5× plus large, avec vérification visuelle
        si une signature existe (évite les rattachements erronés après pivot).
        
        Les tracks sans détection incrémentent leur compteur "miss".
        Ils sont supprimés au-delà de TRACKER_MISS_TOLERANCE frames sans détection.
        
        Retourne la liste des articles trackés sous la forme (center, id, conf).
        """
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
                extended_dist = adaptive_dist * 2.5
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
                                    f"[SUIVI] Article #{a_id} — objet visuellement différent, pas de rattachement de suivi")
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
        """
        Mini-tracker maison pour les personnes détectées par le modèle radar.
        
        Fonctionne comme _track_articles_custom mais avec une distance adaptive
        basée sur la hauteur de la bbox personne (40% de sa hauteur, min 60px).
        
        Les personnes sans détection sont conservées jusqu'à
        PERSON_MISS_TOLERANCE frames (plus court que pour les articles car
        une personne qui quitte le champ doit être oubliée rapidement).
        """
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
        """
        Lance deux processus FFmpeg en parallèle pour enregistrer :
        - Un clip annoté (avec les bounding boxes et textes d'alerte).
        - Un clip brut (frame originale sans annotations).
        
        Le clip inclut BEFORE_ALERT_SECS secondes de pré-buffer (frames déjà
        capturées en mémoire) + AFTER_ALERT_SECS secondes après l'alerte.
        
        Le pré-buffer est écrit dans un thread séparé pour ne pas bloquer
        la boucle principale. L'événement _pre_alert_done signale la fin
        de cette écriture avant de commencer les frames live.
        
        Vérifie l'espace disque avant de démarrer — annule l'enregistrement
        si l'espace disponible est insuffisant (< DISK_MIN_FREE_GB).
        
        Journalise l'alerte dans alerts.jsonl avec métadonnées et chemins de clips.
        """
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

    # ======================================================================
    # SUSPICION
    # ======================================================================
    def _notify_suspicion(self, article_id: int, type_vol: str, score: float):
        """
        Enregistre une suspicion active dans le dictionnaire global
        active_suspicions (visible via GET /suspicions).
        
        Les types CORPS et SAC sont loggés mais pas publiés dans /suspicions
        (ils passent directement en alerte si confirmés).
        Les autres types (FLÂNERIE) sont publiés et expireront après SUSPICION_TTL.
        """
        with suspicions_lock:
            active_suspicions[self.cam_id] = {
                "time":       datetime.now().strftime("%H:%M:%S"),
                "score":      round(score, 2),
                "type":       type_vol,
                "expires_at": time.time() + SUSPICION_TTL,
            }
        if not self._suspicion_logged.get(article_id, False):
            play_sound("suspicion")
        self._suspicion_logged[article_id] = True


    def _clear_suspicion(self, article_id: int = None):
        """
        Supprime la suspicion active pour cette caméra du dictionnaire global.
        Si article_id est fourni, supprime aussi le flag _suspicion_logged
        pour cet article spécifique.
        """
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
        """
        Lissage exponentiel de la position caméra entre deux frames.
        
        Au lieu de sauter brutalement vers la nouvelle position du suspect,
        on interpole entre la position mémorisée (smooth_center) et la nouvelle,
        avec alpha comme facteur de réactivité :
        - alpha proche de 0 → mouvement très lent, très stable
        - alpha proche de 1 → suit immédiatement la nouvelle position
        
        Évite l'effet "caméra tremblante" quand la bbox du suspect oscille.
        """
        if self.smooth_center is None:
            self.smooth_center = new_center
        else:
            self.smooth_center = (
                int(self.smooth_center[0] * (1 - alpha) + new_center[0] * alpha),
                int(self.smooth_center[1] * (1 - alpha) + new_center[1] * alpha),
            )
        return self.smooth_center

    # ======================================================================
    # RESET TRACKING (reconnexion RTSP)
    # ======================================================================
    def _reset_tracking_state(self):
        """
        Purge complète de l'état de tracking après une reconnexion RTSP.
        
        Sans ce reset, les compteurs d'absence (article_absence_frames)
        et les suspicions (suspect_disappearance) continuent d'accumuler
        pendant la coupure vidéo, ce qui provoquerait des fausses alertes
        dès le retour du flux.
        
        On conserve intentionnellement person_tracking et last_known_person_boxes :
        une reconnexion courte peut retrouver les mêmes personnes en vue,
        et perdre leur historique causerait des faux positifs de flânerie.
        """
        self.suspect_disappearance.clear()
        self.active_article_tracks.clear()
        self.article_absence_frames.clear()
        self.article_consecutive_frames.clear()
        self.article_consecutive_miss.clear()
        self.article_presence_streak.clear()
        self.article_raw_presence.clear()
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
        self.article_holder.clear()
        self.hands_history.clear()
        self._suspicion_logged.clear()
        self._clear_suspicion()
        _log(self.cam_id, "INFO", "Reconnexion réussie — remise à zéro du suivi")

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
                gpu_result = result_queues[self.cam_id].get(timeout=0.20)
                        
                if isinstance(gpu_result, tuple) and len(gpu_result) == 2:
                    persons_data, synced_frame = gpu_result
                    # synced_frame sert UNIQUEMENT pour la détection visuelle (signatures)
                    # annotated_frame et clean_frame restent sur la frame live capturée en haut de boucle
                    detection_frame = synced_frame
                else:
                    persons_data    = gpu_result
                    detection_frame = clean_frame
            except queue.Empty:
                self.gpu_miss_count += 1
                if self.frames_processed % 200 == 0 and self.frames_processed > 0:
                    miss_pct = 100 * self.gpu_miss_count / self.frames_processed
                    _log(self.cam_id, "DEBUG", f"GPU miss: {self.gpu_miss_count}/{self.frames_processed} frames ({miss_pct:.1f}%)")
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
                    self.article_raw_presence.pop(a_id, None) 
                    self.article_presence_streak.pop(a_id, None)
                    self.article_position_history.pop(a_id, None)
                    self.article_conf_history.pop(a_id, None)
                    self.article_alert_time.pop(a_id, None)
                    self.article_near_bag.pop(a_id, None)
                    self.article_visual_signature.pop(a_id, None)
                    self.article_holder.pop(a_id, None)  
                    self.article_last_bbox.pop(a_id, None)

            # ==========================================================
            # 🛡️ NOUVEAU FILTRE ANTI-FANTÔMES (STABILITÉ)
            # ==========================================================
            # ── Tracking articles ──
            articles_pos = self._track_articles_custom(raw_articles_pos, detection_frame)

            # 1. On incrémente la présence brute de TOUT ce que le tracker voit
            for (a_center, a_id, a_conf) in articles_pos:
                self.article_raw_presence[a_id] = self.article_raw_presence.get(a_id, 0) + 1

            # IDs réellement détectés par YOLO cette frame (centres bruts → IDs tracker)
            # CRITIQUE : on utilise les détections YOLO brutes, PAS ce que le tracker
            # maintient vivant artificiellement (TRACKER_MISS_TOLERANCE).
            # Un article absent de YOLO mais maintenu par le tracker ne doit PAS
            # bloquer l'incrémentation de article_absence_frames.
            raw_centers_this_frame = {item[0] for item in raw_articles_pos}
            yolo_seen_ids = {
                a_id for (a_center, a_id, _) in articles_pos
                if a_center in raw_centers_this_frame
            }
            tracker_seen_ids = yolo_seen_ids  # garde le nom pour compatibilité avec le reste

            # 2. On filtre sur cette présence brute
            stable_articles_pos = []
            for (a_center, a_id, a_conf) in articles_pos:
                if self.article_raw_presence[a_id] >= 2:
                    stable_articles_pos.append((a_center, a_id, a_conf))
                else:
                    if DEBUG_LOGS and self.frames_processed % 30 == 0:
                        _log(self.cam_id, "DEBUG", f"[FILTRE] Article #{a_id} — détection trop brève, probable faux positif YOLO")

            # On remplace la liste brute par la liste stabilisée
            articles_pos = stable_articles_pos
            # ==========================================================

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
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)

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

                        if article_held_by_detection and article_held_by_streak and not self._was_hand_near_article(a_center, p_id=p_id):
                            if DEBUG_LOGS:
                                _log(self.cam_id, "DEBUG", f"[FILTRE] Article #{a_id} tenu mais aucune main détectée au contact — pas de suspicion")

                        is_held = (
                            article_held_by_detection
                            and article_held_by_streak
                            and self._was_hand_near_article(a_center, p_id=p_id)
                        )

                        if is_held:
                            self.article_holder[a_id] = p_id 
                            conf_history = self.article_conf_history.get(a_id, deque())
                            mean_conf = sum(conf_history) / len(conf_history) if conf_history else 0.0
                            if mean_conf < HOLD_CONF_MIN:
                                if DEBUG_LOGS and self.frames_processed % 30 == 0:
                                    _log(self.cam_id, "DEBUG", f"[FILTRE] Article #{a_id} — confiance YOLO trop faible ({mean_conf:.0%}), ignoré")
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
            # On utilise tracker_seen_ids (avant filtre fantôme) pour ne pas traiter
            # comme "disparu" un article qui vient juste d'apparaître (raw_presence=1).
            # Utiliser articles_pos filtré ici causait Absent=135 sur des articles visibles.
            already_handled = tracker_seen_ids
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
            if DEBUG_LOGS and self.frames_processed % 30 == 0:
                for (_, a_id, _) in articles_pos:
                    streak    = self.hold_streak.get(a_id, 0)
                    consec    = self.article_consecutive_frames.get(a_id, 0)
                    hold_dur  = self.hold_durations.get(a_id, 0)
                    absent    = self.article_absence_frames.get(a_id, 0)
                    conf_hist = self.article_conf_history.get(a_id, deque())
                    mean_conf = sum(conf_hist) / len(conf_hist) if conf_hist else 0.0
                    if absent >= MIN_DISAPPEARANCE_FRAMES:
                        _log(self.cam_id, "DEBUG",
                            f"Article #{a_id} : disparu depuis {absent} images — sous surveillance active")
                    elif hold_dur >= 12:
                        _log(self.cam_id, "DEBUG",
                            f"Article #{a_id} : tenu par une personne depuis {hold_dur} images (confiance: {mean_conf:.0%})")
                    elif mean_conf >= HOLD_CONF_MIN:
                        _log(self.cam_id, "DEBUG",
                            f"Article #{a_id} : article en main détecté (confiance: {mean_conf:.0%}) — en attente de confirmation ({hold_dur}/12 frames)")
                for a_id, data in self.suspect_disappearance.items():
                    elapsed   = current_time - data["start_time"]
                    hold_snap = self.hold_durations_snapshot.get(a_id, data["hold_frames"])
                    _log(self.cam_id, "DEBUG",
                        f"SUSPICION CORPS — article #{a_id} caché depuis {elapsed:.0f}s "
                        f"(alerte dans {max(0, DISAPPEARANCE_TIMEOUT - elapsed):.0f}s si pas de réapparition, "
                        f"tenu {hold_snap} images)")

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
                    if self.object_hold_counter.get(f"article_{a_id}", 0) < FRAME_THRESHOLD:
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
                        _log(self.cam_id, "DEBUG", f"[FILTRE SAC] Article #{a_id} — rapprochement trop long sans disparition, suivi annulé")
                    del self.article_near_bag[a_id]

            # Phase 2 : article disparu → alerte SAC
            for a_id, data in list(self.article_near_bag.items()):
                if a_id in visible_article_ids:
                    data["frames_gone"] = 0
                    data.pop("gone_since", None)  # reset patience si réapparu
                    continue

                data["frames_gone"] = data.get("frames_gone", 0) + 1

                if data["frames_gone"] < 12:
                    continue

                if data["frames_near_bag"] < SAC_PROXIMITY_FRAMES_MIN:
                    del self.article_near_bag[a_id]
                    continue

                # [v9] _was_hand_near_article() présent dans SAC
                # Valide qu'il y avait bien un contact de main avant l'insertion
                last_pos = self.last_known_articles.get(a_id)
                if last_pos and not self._was_hand_near_article(last_pos, p_id=data["p_id"]):
                    if DEBUG_LOGS:
                        _log(self.cam_id, "DEBUG", f"[FILTRE SAC] Article #{a_id} disparu sans contact de main préalable — pas de suspicion")
                    del self.article_near_bag[a_id]
                    continue

                if self.object_hold_counter.get(f"article_{a_id}", 0) == 0:
                    if DEBUG_LOGS:
                        _log(self.cam_id, "DEBUG", f"[FILTRE SAC] Article #{a_id} — jamais tenu par une personne, ignoré")
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
                        _log(self.cam_id, "INFO", f"Article #{a_id} réapparu normalement — suspicion levée, aucun vol détecté")
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
                            _log(self.cam_id, "INFO", f"Article #{a_id} repositionné — suspicion levée, aucun vol détecté")
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
                            _log(self.cam_id, "INFO", f"Article #{a_id} retrouvé — suspicion levée, aucun vol détecté")
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
                known_holder = self.article_holder.get(a_id)   # ← AJOUT
                for p_id, p_box in self.last_known_person_boxes.items():
                    if current_time - self.person_last_seen.get(p_id, 0) > 2.0:
                        continue
                    if known_holder is not None and p_id != known_holder:   # ← AJOUT
                        continue
                    if is_point_in_box(last_pos, p_box):
                        p_w   = p_box[2] - p_box[0]
                        p_h   = p_box[3] - p_box[1]
                        rel_x = (last_pos[0] - p_box[0]) / p_w if p_w > 0 else 0.5
                        rel_y = (last_pos[1] - p_box[1]) / p_h if p_h > 0 else 0.5
                        if 0.25 <= rel_y <= 0.85 and 0.20 <= rel_x <= 0.80:
                            if current_time - self.person_tracking[p_id]["first_seen"] < 3.0:
                                continue
                            p_cx_rel = 0.5
                            p_cy_rel = 0.55
                            dist_to_center = math.hypot(rel_x - p_cx_rel, rel_y - p_cy_rel)
                            if dist_to_center > 0.7:
                                if DEBUG_LOGS:
                                    _log(self.cam_id, "DEBUG",
                                        f"[FILTRE CORPS] Article #{a_id} en bordure de personne — probablement porté à bout de bras, ignoré")
                                continue
                            is_suspect_zone   = True
                            local_target_p_id = p_id
                            break

                if not is_suspect_zone:
                    continue

                # Filtre main APRÈS avoir trouvé la personne — maintenant p_id est connu
                if not self._was_hand_near_article(last_pos, p_id=local_target_p_id):
                    if DEBUG_LOGS and self.frames_processed % 60 == 0:
                        _log(self.cam_id, "DEBUG", f"[FILTRE CORPS] Article #{a_id} disparu sans contact de main préalable — pas de suspicion")
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
                            # [AJUSTEMENT] Passé de +6 à +4 (environ 0.3s à 12 FPS).
                            # Permet d'accepter la double détection de YOLO pendant qu'un produit pivote,
                            # tout en évitant qu'un objet voisin bloque une vraie suspicion de vol.
                            if new_id_age > frames_absent + 4:
                                # Ce track existait bien avant la disparition de a_id → article différent
                                continue
                            # Vérif visuelle : même objet ?
                            if self._is_same_article_visual(a_id, detection_frame, new_bbox):
                                article_reappeared_as_same = True
                                if DEBUG_LOGS:
                                    _log(self.cam_id, "DEBUG", f"[SUIVI] Article #{a_id} — simple pivotement de l'objet, pas de suspicion")
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

                if trigger_alert:   # une alerte a déjà été décidée cette frame → on skip
                    continue
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
                        f"[FILTRE CORPS] Article #{a_id} : suspect et article sortis ensemble de la zone — surveillance terminée")
                    del self.suspect_disappearance[a_id]
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
                                        f"[FILTRE CORPS] Article #{a_id} — confiance trop basse pour déclencher une alerte ({alert_score:.0%})")
                            else:
                                # [v12 FIX I] Anti-doublon visuel
                                if self._is_duplicate_alert(a_id, clean_frame, current_time):
                                    _log(self.cam_id, "INFO",
                                        f"[FILTRE CORPS] Article #{a_id} — doublon d'une alerte récente, ignoré")
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
                                        _log(self.cam_id, "ALERT", f"🚨 ALERTE VOL CORPS — suspect sorti de la zone avec l'article (confiance: {alert_score:.0%})")

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
                _log(self.cam_id, "ALERT", f"🚨 ALERTE VOL {vol_type} — (confiance IA: {alert_score:.0%})")
                play_sound("alert")
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


            # ── Indicateur son (overlay bas droite) ──
            with sound_lock:
                _snd_on = sound_enabled
            _snd_label = "SON ON" if _snd_on else "SON OFF"
            _snd_color = (0, 200, 80) if _snd_on else (80, 80, 80)
            cv2.putText(
                annotated_frame, _snd_label,
                (self.width - 90, self.height - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, _snd_color, 1, cv2.LINE_AA
            )

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



def keyboard_listener():
    import sys, tty, termios, select
    if not sys.stdin.isatty():
        print("[SON] Pas de terminal interactif — utilisez POST /sound/toggle")
        return
    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.2)
            if r:
                ch = sys.stdin.read(1)
                if ch == '\x02':        # Ctrl+B
                    state = toggle_sound()
                    status = "ON" if state else "OFF"
                    sys.stdout.write(f"\r[SON] {status}            \r")
                    sys.stdout.flush()
                elif ch == '\x03':      # Ctrl+C
                    import signal as _sig
                    _sig.raise_signal(_sig.SIGINT)
                    break
    except Exception:
        pass
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass


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
    threading.Thread(target=_sound_worker, daemon=True, name="sound_worker").start()
    print("🔊 Thread son démarré")

    threading.Thread(target=purge_worker, daemon=True, name="purge_worker").start()
    threading.Thread(target=keyboard_listener, daemon=True, name="keyboard_listener").start()
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