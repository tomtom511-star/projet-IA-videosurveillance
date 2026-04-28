"""
╔══════════════════════════════════════════════════════════════════════════╗
║         SYSTÈME DE DÉTECTION DE VOL MULTI-CAMÉRAS — YOLO + Flask        ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ARCHITECTURE GÉNÉRALE :                                                 ║
║  ┌──────────┐    ┌──────────────────┐    ┌──────────────────────────┐  ║
║  │ CAM RTSP │───▶│  FFmpegReader    │───▶│  frame_queue (maxsize=1) │  ║
║  │ CAM RTSP │───▶│  (thread dédié)  │    │  (drop des vieilles      │  ║
║  │   ...    │    │  1 par caméra    │    │   frames automatique)    │  ║
║  └──────────┘    └──────────────────┘    └────────────┬─────────────┘  ║
║                                                        │                 ║
║                                               ┌────────▼──────────┐     ║
║                                               │  CameraWorker     │     ║
║                                               │  (thread analyse) │     ║
║                                               │  YOLO + logique   │     ║
║                                               └────────┬──────────┘     ║
║                                                        │                 ║
║                                          ┌─────────────▼──────────────┐ ║
║                                          │  output_frames[cam_id]     │ ║
║                                          │  raw_frames[cam_id]        │ ║
║                                          │  (dict partagé + RLock)    │ ║
║                                          └─────────────┬──────────────┘ ║
║                                                        │                 ║
║                                          Flask /video/<cam_id>           ║
║                                                                          ║
║  POURQUOI 2 THREADS PAR CAMÉRA ?                                        ║
║  Le thread FFmpegReader lit le flux RTSP EN CONTINU et ne garde que     ║
║  la dernière frame disponible (queue de taille 1). Cela empêche         ║
║  l'accumulation dans le buffer qui causait le freeze après ~2 minutes.  ║
║  Le thread CameraWorker consomme ces frames à son propre rythme         ║
║  (limité par le GPU) sans jamais bloquer la lecture réseau.             ║
║                                                                          ║
║  CORRECTIFS v2 (freeze "200" résolu) :                                  ║
║  ─────────────────────────────────────                                  ║
║  BUG 1 — stderr bloquant :                                              ║
║    subprocess.PIPE sur stderr sans lecture → le buffer OS (~64 Ko) se  ║
║    remplit en silence, FFmpeg se bloque en écriture stderr, ce qui      ║
║    bloque AUSSI stdout → freeze total. Fix : stderr=DEVNULL ou thread   ║
║    dédié de drainage.                                                    ║
║                                                                          ║
║  BUG 2 — bufsize trop petit :                                           ║
║    bufsize=10**6 (1 Mo) < taille d'une frame (704×576×3 = 1.16 Mo).    ║
║    Le BufferedReader Python ne pouvait pas buffériser une frame entière  ║
║    → read_exactly() faisait des dizaines de read() par frame, ce qui    ║
║    introduisait une latence croissante jusqu'au freeze. Fix : bufsize=  ║
║    10×frame_size pour être largement au-dessus.                         ║
║                                                                          ║
║  BUG 3 — reconnexion sans drain de la queue :                           ║
║    Après une reconnexion FFmpeg, la queue pouvait contenir une frame    ║
║    de l'ancienne session. Fix : on vide la queue à chaque reconnexion.  ║
║                                                                          ║
║  POUR AJOUTER UNE CAMÉRA : ajouter une entrée dans la liste CAMERAS     ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from ultralytics import YOLO       # Charge YOLO, le modèle IA de détection d'objets
import cv2                         # OpenCV : gère les images et les flux vidéo
import os                          # Permet de gérer les dossiers et variables d'environnement
import math                        # Permet de calculer les distances (math.hypot)
import json                        # Permet de sauvegarder les alertes dans un fichier JSON
import signal                      # Permet d'intercepter Ctrl+C pour fermer proprement FFmpeg
import numpy as np                 # Manipulation des tableaux de pixels (images)
import subprocess                  # Lance des processus externes (FFmpeg)
from datetime import datetime      # Pour horodater les alertes et noms de fichiers
import time                        # Pour les délais et timestamps
import torch                       # PyTorch : force l'utilisation du GPU NVIDIA
from collections import deque      # Buffer circulaire : garde les N dernières frames en mémoire
import queue                       # queue.Queue : communication thread-safe entre FFmpegReader et Worker
from flask import Flask, Response, request, jsonify  # Serveur web pour streamer la vidéo
import threading                   # Gestion des threads (2 threads = 1 caméra)


# ==========================================
# CONFIGURATION GPU
# ==========================================
# Force l'utilisation du premier GPU NVIDIA (index 0)
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
# Désactive les logs verbeux de YOLO pour ne pas polluer la console
os.environ["YOLO_VERBOSE"] = "False"


# ==========================================
# CONFIGURATION DES CAMÉRAS
# ==========================================
# Liste de toutes les caméras à surveiller.
# Pour ajouter une caméra, copier/coller un bloc et changer cam_id + rtsp_url.
CAMERAS = [
    {
        "cam_id":   "CAM_01",           # Identifiant unique (utilisé dans les alertes et URLs)
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.21:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,                # Largeur en pixels (doit correspondre au flux réel)
        "height":   576,                # Hauteur en pixels (doit correspondre au flux réel)
        "fps":      12,                 # FPS réel du flux caméra — BIEN VÉRIFIER CETTE VALEUR
    },
    # Décommentez et adaptez pour ajouter d'autres caméras :
    {
        "cam_id":   "CAM_02",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.22:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
    {
        "cam_id":   "CAM_03",
        "rtsp_url": "rtsp://leclerc:LecOli%2545@10.21.9.23:554/cam/realmonitor?channel=1&subtype=1",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
]


# ==========================================
# VARIABLES DE GESTION DES ALERTES
# ==========================================
ALERT_COOLDOWN        = 20    # Temps minimum (en secondes) entre 2 alertes (évite le spam)
DISAPPEARANCE_TIMEOUT = 12.0  # Temps (en secondes) avant de considérer un objet "disparu sous les vêtements"
FRAME_THRESHOLD       = 8     # Nombre de frames consécutives pour valider qu'un objet est bien "tenu"
LOITERING_THRESHOLD   = 90.0  # Temps (en secondes) avant qu'une personne soit considérée suspecte (flânerie)

# Durée d'affichage du texte d'alerte à l'écran
DISPLAY_TEXT_DURATION = 4.0   # Le texte d'alerte reste affiché 4 secondes

# Durées du clip vidéo enregistré lors d'une alerte
BEFORE_ALERT_SECS = 5         # Secondes AVANT l'alerte (grâce au buffer circulaire)
AFTER_ALERT_SECS  = 5         # Secondes APRÈS l'alerte (enregistrement en direct)

# Tolérance du mini-tracker : nombre de frames pendant lesquelles un article
# peut disparaître (occlusion, raté YOLO) avant d'être définitivement perdu.
# Sans ça, un seul raté YOLO réinitialisait le compteur "tenu" à zéro.
TRACKER_MISS_TOLERANCE = 5


# ==========================================
# DOSSIERS ET FICHIER JSON D'ALERTES
# ==========================================
ALERT_FILE    = "alerts.json"                          # Fichier de communication avec l'interface web
alert_vid_dir = "alert_clips"                          # Dossier des clips annotés (avec dessins)
raw_dir       = os.path.join(alert_vid_dir, "raw")     # Sous-dossier des clips bruts (sans dessins)

os.makedirs(alert_vid_dir, exist_ok=True)
os.makedirs(raw_dir,       exist_ok=True)
os.makedirs("snapshots",   exist_ok=True)              # Dossier pour les captures manuelles

# Initialise le fichier JSON s'il n'existe pas encore
if not os.path.exists(ALERT_FILE):
    with open(ALERT_FILE, "w") as f:
        json.dump([], f)

# Verrou dédié à l'écriture du fichier alerts.json.
# Sans ce verrou, Flask (route /alerts) peut lire le fichier exactement pendant
# qu'un worker l'écrit → fichier tronqué → crash JSON côté interface web.
alerts_file_lock = threading.Lock()


# ==========================================
# CHARGEMENT DES MODÈLES YOLO (ARCHITECTURE DOUBLE)
# ==========================================
# Les deux modèles sont chargés UNE SEULE FOIS et partagés entre tous les threads.
# Charger un modèle par thread serait beaucoup trop gourmand en VRAM.

print("CUDA available:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")

# MODÈLE 1 : LE RADAR
# Cherche uniquement les personnes sur l'image globale de la caméra.
# Utilise le tracking ByteTrack pour donner un ID unique à chaque personne.
model_radar = YOLO("runs/detect/radar_global_v1/weights/best.pt")
model_radar.to("cuda")

# MODÈLE 2 : LE SPÉCIALISTE
# Reçoit un "crop" (découpe) autour d'une personne et cherche :
# les mains, les sacs et les articles de magasin.
model_specialist = YOLO("runs/detect/radar_specialiste_v2/weights/best.pt")
model_specialist.to("cuda")

# VERROU GPU : empêche deux threads d'utiliser le GPU en même temps.
# Sans ce verrou, les appels YOLO simultanés feraient planter CUDA.
# Les inférences sont donc sérialisées (l'une attend la fin de l'autre).
gpu_lock = threading.Lock()


# ==========================================
# ÉTAT PARTAGÉ ENTRE LES THREADS ET FLASK
# ==========================================
# Ces deux dictionnaires stockent la dernière frame de chaque caméra.
# Flask les lit pour construire les flux vidéo en direct.
output_frames: dict = {}   # cam_id → frame annotée (avec les rectangles et textes YOLO)
raw_frames:    dict = {}   # cam_id → frame propre (sans annotations, pour les snapshots)

# Verrou pour protéger la lecture/écriture des frames entre les threads et Flask.
# On utilise RLock (re-entrant) car Flask peut appeler plusieurs routes en même temps.
frame_lock = threading.RLock()


# ==========================================
# SERVEUR FLASK — STREAMING LIVE MULTI-CAMÉRAS
# ==========================================
# Flask expose une URL par caméra : http://ip:5000/video/CAM_01
# Le streaming se fait en MJPEG (multipart), sans écriture sur disque.
app = Flask(__name__)


def generate_stream(cam_id: str):
    """Générateur infini de frames MJPEG pour une caméra donnée."""
    while True:
        # Lecture non-bloquante : on essaie de prendre le verrou sans attendre
        got_lock = frame_lock.acquire(blocking=False)
        frame = None
        try:
            if got_lock:
                frame = output_frames.get(cam_id)
        finally:
            if got_lock:
                frame_lock.release()

        if frame is not None:
            _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

        # Petite pause pour ne pas saturer le CPU sur ce générateur
        time.sleep(0.04)


@app.route("/video/<cam_id>")
def video(cam_id):
    """
    Route Flask : diffuse le flux live de la caméra cam_id en MJPEG.
    Exemple d'accès : http://10.21.9.x:5000/video/CAM_01
    """
    return Response(
        generate_stream(cam_id),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/snapshot", methods=["POST"])
def take_snapshot():
    """
    Route Flask : capture et sauvegarde l'image brute (sans annotations)
    de la caméra demandée.
    Corps JSON attendu : { "cam_id": "CAM_01" }
    """
    data   = request.get_json()
    cam_id = data.get("cam_id", "unknown")

    # Récupération thread-safe de la frame propre
    with frame_lock:
        frame = raw_frames.get(cam_id)
        if frame is None:
            return jsonify({"status": "error", "message": "Pas d'image disponible"}), 500
        frame_to_save = frame.copy()

    # Sauvegarde dans le dossier snapshots/
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"CLEAN_{cam_id}_{timestamp}.jpg"
    file_path = os.path.join("snapshots", file_name)
    cv2.imwrite(file_path, frame_to_save)
    print(f"📸 Snapshot enregistré : {file_path}")
    return jsonify({"status": "success", "file": file_path}), 200


@app.route("/alerts")
def get_alerts():
    """Route Flask : retourne toutes les alertes enregistrées au format JSON."""
    # Protégé par le même verrou que l'écriture pour éviter une lecture partielle
    with alerts_file_lock:
        with open(ALERT_FILE, "r") as f:
            return jsonify(json.load(f))


def start_server():
    """Lance le serveur Flask dans un thread dédié (daemon = s'arrête avec le process principal)."""
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)


# ==========================================
# FONCTIONS UTILITAIRES
# ==========================================

def get_center(box):
    """Calcule le point central d'un rectangle (x1, y1, x2, y2)."""
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int((y1 + y2) / 2))


def is_point_in_box(point, box):
    """
    Vérifie si un point (px, py) est à l'intérieur d'un rectangle [x1, y1, x2, y2].
    Utilisé pour savoir si un article est dans la zone corporelle d'une personne.
    """
    px, py = point
    x1, y1, x2, y2 = box
    return x1 <= px <= x2 and y1 <= py <= y2


def read_exactly(pipe, n_bytes):
    """
    ╔══════════════════════════════════════════════════════════════════╗
    ║  LECTURE EXACTE DE n_bytes OCTETS DEPUIS UN PIPE                ║
    ╠══════════════════════════════════════════════════════════════════╣
    ║                                                                  ║
    ║  PROBLÈME :                                                      ║
    ║  pipe.read(n) sur un pipe OS ne garantit PAS de retourner       ║
    ║  exactement n octets. Il retourne dès qu'il y a QUELQUE CHOSE   ║
    ║  de disponible dans le buffer kernel.                            ║
    ║                                                                  ║
    ║  SOLUTION :                                                      ║
    ║  Lire en boucle et accumuler les chunks jusqu'à avoir           ║
    ║  exactement le nombre d'octets voulu.                           ║
    ║                                                                  ║
    ║  Retourne :                                                      ║
    ║    - bytes de taille exactement n_bytes si tout va bien         ║
    ║    - None si le pipe est fermé (flux vraiment coupé)            ║
    ╚══════════════════════════════════════════════════════════════════╝
    """
    buf = bytearray()
    while len(buf) < n_bytes:
        remaining = n_bytes - len(buf)
        chunk = pipe.read(remaining)

        if not chunk:
            # pipe.read() a retourné zéro octet → pipe fermé, FFmpeg a quitté
            return None

        buf.extend(chunk)

    return bytes(buf)


def drain_stderr(process, cam_id: str, stop_event: threading.Event):
    """
    ╔══════════════════════════════════════════════════════════════════╗
    ║  CORRECTIF BUG 1 — DRAINAGE CONTINU DE STDERR                  ║
    ╠══════════════════════════════════════════════════════════════════╣
    ║                                                                  ║
    ║  PROBLÈME :                                                      ║
    ║  FFmpeg écrit ses logs (warnings, stats, erreurs) sur stderr.   ║
    ║  Si on utilise stderr=subprocess.PIPE sans JAMAIS lire ce pipe, ║
    ║  le buffer OS (~64 Ko sur Linux) se remplit en quelques minutes. ║
    ║  Quand le buffer est plein, FFmpeg se bloque en écriture sur    ║
    ║  stderr. Comme stdout et stderr partagent le même process,      ║
    ║  FFmpeg arrête d'écrire sur stdout aussi → FREEZE TOTAL.        ║
    ║                                                                  ║
    ║  SYMPTÔME OBSERVÉ :                                              ║
    ║  Tout fonctionne ~2 minutes, puis le flux se fige sans erreur   ║
    ║  Python visible. Flask répond toujours 200 mais plus de frames. ║
    ║                                                                  ║
    ║  SOLUTION :                                                      ║
    ║  Ce thread tourne en parallèle et lit stderr en continu,        ║
    ║  vidant le buffer OS avant qu'il ne se remplisse.               ║
    ║  On n'affiche que les lignes contenant "error" pour ne pas      ║
    ║  polluer la console avec les stats FFmpeg normales.             ║
    ║                                                                  ║
    ║  ALTERNATIVE PLUS SIMPLE : stderr=subprocess.DEVNULL            ║
    ║  Utiliser DEVNULL si vous ne voulez pas voir les erreurs FFmpeg.║
    ║  Ce thread est préférable car il permet de logger les vraies    ║
    ║  erreurs réseau/codec sans bloquer le flux vidéo.               ║
    ╚══════════════════════════════════════════════════════════════════╝
    """
    try:
        for line in process.stderr:
            if stop_event.is_set():
                break
            # On ne log que les vraies erreurs (pas les stats de progression FFmpeg)
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded and "error" in decoded.lower():
                print(f"[{cam_id}] FFmpeg stderr: {decoded}")
    except Exception:
        # Le pipe stderr peut se fermer normalement quand FFmpeg quitte
        pass


# ==========================================
# CLASSE FFmpegReader — THREAD DE LECTURE RTSP DÉDIÉ
# ==========================================
class FFmpegReader:
    """
    Thread indépendant qui lit le flux RTSP via FFmpeg EN CONTINU
    et ne conserve que la dernière frame dans une queue de taille 1.

    POURQUOI CETTE CLASSE EXISTE (la cause du freeze) :
    ─────────────────────────────────────────────────────
    Avant, la lecture FFmpeg et l'analyse YOLO se faisaient dans la MÊME boucle.
    YOLO prend ~80-200ms par frame sur GPU. FFmpeg, lui, produit une frame toutes
    les ~83ms (12 FPS). Résultat : FFmpeg remplissait son buffer interne de 10 Mo
    plus vite qu'on ne le vidait. Au bout de ~2 minutes, le buffer était plein,
    FFmpeg se bloquait, et la boucle principale freezait.

    SOLUTION :
    ──────────
    Ce thread lit FFmpeg aussi vite que possible (pas de YOLO ici, juste des octets)
    et met chaque frame dans une queue de taille MAXIMALE 1.
    → Si la queue est déjà pleine (worker trop lent), on jette la vieille frame
      et on met la nouvelle. Le flux reste toujours en temps réel.
    → Le worker consomme ces frames à son rythme sans jamais bloquer la lecture.

    RECONNEXION AUTOMATIQUE :
    ─────────────────────────
    Si FFmpeg plante ou si le flux RTSP est interrompu, le thread attend 3 secondes
    puis relance FFmpeg automatiquement. Cela assure le fonctionnement 24/7.
    """

    def __init__(self, cam_id: str, rtsp_url: str, width: int, height: int):
        self.cam_id     = cam_id
        self.rtsp_url   = rtsp_url
        self.width      = width
        self.height     = height
        self.frame_size = width * height * 3  # Taille exacte en octets d'une frame BGR brute

        # Queue de taille 1 : ne stocke que la frame la plus récente.
        # Si le worker est lent, les vieilles frames sont automatiquement écrasées.
        self.queue = queue.Queue(maxsize=1)

        # Flag d'arrêt : mettre à True depuis l'extérieur pour stopper proprement le thread
        self._stop_event = threading.Event()

        # Référence au processus FFmpeg en cours (pour pouvoir le tuer si besoin)
        self._process = None

        # ── CORRECTIF BUG 2 ──
        # bufsize doit être PLUS GRAND que frame_size pour que le BufferedReader
        # Python puisse lire une frame entière en un seul appel interne.
        # On prend 10× la taille d'une frame pour avoir de la marge.
        # Avec bufsize < frame_size (l'ancienne valeur de 10**6 = 1 Mo),
        # read_exactly() devait faire des dizaines de read() par frame,
        # introduisant une latence croissante jusqu'au freeze.
        self._bufsize = self.frame_size * 10

    def _start_ffmpeg(self):
        return subprocess.Popen(
            [
                "ffmpeg",
                "-loglevel",       "error",          # N'affiche que les erreurs réelles
                "-rtsp_transport", "tcp",             # TCP plus fiable qu'UDP pour RTSP longue durée
                # Timeouts réseau explicites pour éviter un blocage silencieux
                # si la caméra ne répond plus sans fermer la connexion TCP
                "-timeout",        "5000000",         # Timeout de connexion RTSP : 5 secondes (en µs)
                "-i",              self.rtsp_url,     # URL du flux RTSP
                "-vf",             f"scale={self.width}:{self.height}",  # Redimensionnement
                "-f",              "image2pipe",      # Sortie sous forme de flux d'images
                "-pix_fmt",        "bgr24",           # Format compatible OpenCV (Blue Green Red)
                "-vcodec",         "rawvideo",        # Pixels bruts, sans compression
                "-",                                  # Sortie sur stdout
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,    # PIPE et non DEVNULL : on veut pouvoir lire les erreurs
            bufsize=self._bufsize,     # CORRECTIF BUG 2 : buffer > taille d'une frame
        )

    def run(self):
        """
        Boucle principale du thread de lecture.
        Tourne indéfiniment, relance FFmpeg automatiquement en cas d'échec.
        """
        while not self._stop_event.is_set():
            print(f"[{self.cam_id}] Connexion au flux RTSP...")
            self._process = self._start_ffmpeg()

            # ── CORRECTIF BUG 1 ──
            # Lance immédiatement le thread de drainage stderr pour ce nouveau processus.
            # Sans ça, le buffer stderr OS se remplit en ~2 min et freeze tout.
            stderr_drain_thread = threading.Thread(
                target=drain_stderr,
                args=(self._process, self.cam_id, self._stop_event),
                daemon=True,
                name=f"{self.cam_id}_stderr_drain",
            )
            stderr_drain_thread.start()

            # ── CORRECTIF BUG 3 ──
            # Vide la queue avant de commencer à lire le nouveau processus.
            # Après une reconnexion, la queue peut contenir une frame de
            # l'ancienne session FFmpeg. Sans ce drain, le worker traiterait
            # une frame "fantôme" corrompue au redémarrage.
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass

            try:
                while not self._stop_event.is_set():
                    # ─────────────────────────────────────────────────────
                    # LECTURE EXACTE DE frame_size OCTETS
                    # ─────────────────────────────────────────────────────
                    # On utilise read_exactly() et non pipe.read() directement.
                    # read_exactly() boucle en interne jusqu'à avoir tous les
                    # octets voulus, quelle que soit la taille des chunks OS.
                    raw_bytes = read_exactly(self._process.stdout, self.frame_size)

                    if raw_bytes is None:
                        # read_exactly() a retourné None → pipe réellement fermé
                        # (FFmpeg a quitté ou le flux RTSP est vraiment coupé)
                        print(f"[{self.cam_id}] ⚠️ Flux interrompu (pipe fermé par FFmpeg)")
                        break

                    # ─────────────────────────────────────────────────────
                    # MISE À JOUR DE LA QUEUE (FRAME LA PLUS RÉCENTE)
                    # ─────────────────────────────────────────────────────
                    # Si la queue est pleine (worker encore occupé), on jette
                    # la vieille frame avant d'ajouter la nouvelle.
                    # Cela garantit que le stream est toujours en temps réel.
                    if self.queue.full():
                        try:
                            self.queue.get_nowait()
                        except queue.Empty:
                            pass
                    self.queue.put(raw_bytes)

            except Exception as e:
                print(f"[{self.cam_id}] 💥 Exception dans FFmpegReader : {e}")

            finally:
                # Nettoyage propre du processus FFmpeg avant toute reconnexion
                try:
                    self._process.kill()
                    self._process.wait(timeout=3)
                except Exception:
                    pass
                # Le thread stderr_drain se termine naturellement quand le pipe stderr se ferme

            if not self._stop_event.is_set():
                # Pause avant reconnexion pour ne pas spammer en cas d'erreur réseau
                print(f"[{self.cam_id}] 🔄 Reconnexion dans 3 secondes...")
                time.sleep(3)

        print(f"[{self.cam_id}] FFmpegReader arrêté.")

    def get_frame(self, timeout=2.0):
        """
        Récupère la dernière frame disponible (octets bruts).
        Bloquant jusqu'à `timeout` secondes. Retourne None si rien n'arrive.
        """
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        """Demande l'arrêt propre du thread de lecture."""
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
    Gère tout le cycle d'analyse d'une caméra de surveillance :
      1. Consomme les frames depuis FFmpegReader (via une queue)
      2. Détection des personnes (Radar YOLO)
      3. Analyse de chaque personne (Spécialiste YOLO)
      4. Logique de détection de vol (3 scénarios)
      5. Déclenchement des alertes et enregistrement vidéo
      6. Publication des frames pour Flask (streaming live)

    Une instance = un thread d'analyse = une caméra.
    Chaque instance a son propre état interne : pas de données partagées
    entre caméras, sauf les modèles YOLO (protégés par gpu_lock).
    """

    def __init__(self, cam_id: str, rtsp_url: str, width: int, height: int, fps: int):
        self.cam_id         = cam_id
        self.rtsp_url       = rtsp_url
        self.width          = width
        self.height         = height
        self.fps            = fps
        self.frames_processed = 0

        # ------------------------------------------------------------------
        # MINI-TRACKER SPATIAL D'ARTICLES
        # ------------------------------------------------------------------
        # YOLO ne peut pas tracker des objets dans des crops différents.
        # Ce mini-tracker donne un ID unique aux articles en fonction de leur
        # position (distance) d'une image à l'autre.
        self.next_article_id       = 0   # Compteur auto-incrémenté pour les nouveaux articles
        self.active_article_tracks = {}  # { article_id → {"center": (cx,cy), "miss": N} }
        # La clé "miss" compte le nombre de frames consécutives où l'article n'a PAS été vu.
        # Cela permet de tolérer quelques ratés YOLO sans perdre l'identité de l'objet
        # (voir TRACKER_MISS_TOLERANCE). Sans ça, un seul raté réinitialisait le compteur
        # "tenu" à zéro et empêchait la détection de vol de fonctionner.

        # ------------------------------------------------------------------
        # MÉMOIRE DE LA LOGIQUE DE VOL
        # ------------------------------------------------------------------
        self.suspect_disappearance   = {}  # { article_id → {start_time, last_score, hold_frames, p_id} }
        self.last_known_articles     = {}  # { article_id → (cx, cy) dernière position connue }
        self.object_hold_counter     = {}  # { "article_X" → nb de frames où l'objet est considéré "tenu" }
        self.last_known_scores       = {}  # { article_id → score de confiance YOLO }
        self.hold_durations          = {}  # { article_id → nb de frames cumulées où l'objet est tenu }
        self.last_known_person_boxes = {}  # { person_id  → [x1, y1, x2, y2] dernière boîte connue }
        self.person_last_seen        = {}  # { person_id  → timestamp de la dernière détection }
        # ↑ Utilisé pour nettoyer last_known_person_boxes et éviter les faux positifs
        # avec des boîtes obsolètes de personnes parties.
        self.person_tracking         = {}  # { person_id  → {first_seen, last_seen} en timestamps }

        # ------------------------------------------------------------------
        # GESTION DES ALERTES
        # ------------------------------------------------------------------
        self.last_alert_time    = 0      # Timestamp de la dernière alerte (anti-spam)
        self.alert_text_to_show = ""     # Texte affiché sur la frame lors d'une alerte
        self.alert_text_timer   = 0      # Timestamp jusqu'auquel afficher le texte d'alerte

        # ------------------------------------------------------------------
        # GESTION DES CLIPS VIDÉO
        # ------------------------------------------------------------------
        self.is_recording_alert     = False  # True si on est en train d'enregistrer un clip
        self.alert_ffmpeg_process   = None   # Processus FFmpeg pour le clip annoté
        self.raw_ffmpeg_process     = None   # Processus FFmpeg pour le clip brut (sans dessins)
        self.frames_to_record_after = 0      # Compteur de frames restantes à enregistrer après l'alerte
        self.zoom_target_id         = None   # ID de la personne sur laquelle zoomer pendant le clip
        self.smooth_center          = None   # Centre lissé pour l'anti-tremblement du zoom

        # Buffer circulaire : garde les N dernières frames EN MÉMOIRE.
        # Quand une alerte se déclenche, on peut remonter dans le passé (BEFORE_ALERT_SECS).
        buf_size              = int(BEFORE_ALERT_SECS * fps)
        self.video_buffer     = deque(maxlen=buf_size)  # Frames annotées (avec dessins)
        self.video_buffer_raw = deque(maxlen=buf_size)  # Frames propres (sans dessins)

        # ------------------------------------------------------------------
        # LISTE DES PROCESSUS FFMPEG D'ENREGISTREMENT EN COURS
        # ------------------------------------------------------------------
        # Stocke les processus d'enregistrement actifs pour pouvoir les
        # fermer proprement en cas d'interruption (Ctrl+C).
        self._active_record_procs = []


    # ======================================================================
    # MÉTHODE PRIVÉE : MINI-TRACKER SPATIAL D'ARTICLES (AVEC TOLÉRANCE)
    # ======================================================================
    def _track_articles_custom(self, current_articles_centers, max_distance=60):
        """
        Attribue un ID stable aux articles détectés d'une frame à l'autre,
        avec une tolérance aux ratés YOLO (TRACKER_MISS_TOLERANCE).

        PROBLÈME QUE ÇA RÉSOUT :
        YOLO ne peut pas tracker des objets dans des "crops" différents car
        chaque crop est traité comme une image indépendante.
        Ce mini-tracker contourne ça en associant chaque détection à la
        détection la plus proche de la frame précédente (matching spatial).

        AMÉLIORATION PAR RAPPORT À L'ORIGINAL :
        L'ancienne version supprimait immédiatement un track si l'objet
        n'était pas détecté pendant UNE SEULE frame. Maintenant, un track
        survit TRACKER_MISS_TOLERANCE frames sans détection.

        Paramètres :
            current_articles_centers : liste de tuples (center, conf) détectés cette frame
            max_distance             : distance max en pixels pour considérer que c'est le même objet

        Retourne :
            Liste de tuples (center, article_id, conf) pour les articles vus cette frame
        """
        new_tracks = {}
        tracked    = []
        remaining  = dict(self.active_article_tracks)

        for (center, conf) in current_articles_centers:
            best_id   = None
            best_dist = max_distance

            for a_id, track_data in remaining.items():
                dist = math.hypot(
                    center[0] - track_data["center"][0],
                    center[1] - track_data["center"][1]
                )
                if dist < best_dist:
                    best_dist = dist
                    best_id   = a_id

            if best_id is not None:
                # MATCH TROUVÉ → réutilise l'ID, remet le compteur de ratés à 0
                new_tracks[best_id] = {"center": center, "miss": 0}
                tracked.append((center, best_id, conf))
                del remaining[best_id]
            else:
                # PAS DE MATCH → nouvel objet, nouveau ID
                new_id = self.next_article_id
                self.next_article_id += 1
                new_tracks[new_id] = {"center": center, "miss": 0}
                tracked.append((center, new_id, conf))

        # Pour les tracks non matchés : incrémente le compteur de ratés.
        # S'ils dépassent la tolérance, ils sont supprimés.
        for a_id, track_data in remaining.items():
            miss_count = track_data["miss"] + 1
            if miss_count <= TRACKER_MISS_TOLERANCE:
                new_tracks[a_id] = {"center": track_data["center"], "miss": miss_count}

        self.active_article_tracks = new_tracks
        return tracked


    # ======================================================================
    # MÉTHODE PRIVÉE : DÉCLENCHEMENT DE L'ENREGISTREMENT VIDÉO
    # ======================================================================
    def _start_alert_video(self, type_vol: str, score: float):
        """
        Initialise l'enregistrement d'un clip d'alerte via le GPU (NVENC).

        FONCTIONNEMENT :
          1. Lance deux processus FFmpeg en écriture (annoté + brut)
          2. Vide immédiatement le buffer circulaire (5s AVANT l'alerte)
          3. Les frames APRÈS l'alerte seront envoyées dans run()
          4. Met à jour alerts.json pour l'interface web
        """
        timestamp = datetime.now().strftime("%H%M%S")

        vid_path = os.path.abspath(
            os.path.join(alert_vid_dir, f"{self.cam_id}_Vole_{type_vol}_{timestamp}.mp4")
        )
        raw_path = os.path.abspath(
            os.path.join(raw_dir, f"{self.cam_id}_RAW_{type_vol}_{timestamp}.mp4")
        )

        def get_cmd(path):
            return [
                "ffmpeg", "-y",
                "-f",       "rawvideo",
                "-vcodec",  "rawvideo",
                "-s",       f"{self.width}x{self.height}",
                "-pix_fmt", "bgr24",
                "-r",       str(self.fps),
                "-i",       "-",
                "-vcodec",  "h264_nvenc",
                "-preset",  "fast",
                "-b:v",     "1M",
                path,
            ]

        # stderr=DEVNULL pour les processus d'écriture : on n'a pas besoin de
        # leurs logs et on ne veut surtout pas qu'ils bloquent sur stderr.
        self.alert_ffmpeg_process = subprocess.Popen(
            get_cmd(vid_path), stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self.raw_ffmpeg_process = subprocess.Popen(
            get_cmd(raw_path), stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

        self._active_record_procs.extend([self.alert_ffmpeg_process, self.raw_ffmpeg_process])

        # Vide le buffer circulaire → 5 secondes AVANT l'alerte
        for f in self.video_buffer:
            self.alert_ffmpeg_process.stdin.write(f.tobytes())
        for f in self.video_buffer_raw:
            self.raw_ffmpeg_process.stdin.write(f.tobytes())

        self.is_recording_alert     = True
        self.frames_to_record_after = int(AFTER_ALERT_SECS * self.fps)

        # Mise à jour du fichier JSON (lu par l'interface web)
        with alerts_file_lock:
            with open(ALERT_FILE, "r") as f:
                data = json.load(f)
            data.append({
                "cam":        self.cam_id,
                "type":       type_vol,
                "score":      score,
                "time":       datetime.now().strftime("%H:%M:%S"),
                "video_clip": vid_path,
                "video_raw":  raw_path,
            })
            with open(ALERT_FILE, "w") as f:
                json.dump(data, f, indent=4)

        return vid_path


    # ======================================================================
    # MÉTHODES PRIVÉES : ZOOM LISSÉ SUR LE SUSPECT
    # ======================================================================
    def _smooth_position(self, new_center, alpha=0.2):
        """
        Lisse la position du centre du zoom par interpolation exponentielle
        (EWMA : Exponential Weighted Moving Average).

        alpha = 0.2 : faible réactivité → mouvement fluide mais légèrement en retard
        alpha = 0.8 : forte réactivité → suit mieux mais tremble davantage
        """
        if self.smooth_center is None:
            self.smooth_center = new_center
        else:
            self.smooth_center = (
                int(self.smooth_center[0] * (1 - alpha) + new_center[0] * alpha),
                int(self.smooth_center[1] * (1 - alpha) + new_center[1] * alpha),
            )
        return self.smooth_center

    def _zoom_tracking(self, frame, box):
        """
        Recadre et agrandit l'image pour zoomer sur la personne suspecte.

        FONCTIONNEMENT :
          1. Calcule le centre lissé de la boîte (anti-tremblement)
          2. Crée une zone de crop avec marge de 80px
          3. Redimensionne ce crop à la taille de la frame complète
        """
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = map(int, box)

        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        cx, cy = self._smooth_position((cx, cy))

        bw     = (x2 - x1)
        bh     = (y2 - y1)
        margin = 80

        new_w = min(bw + margin, w)
        new_h = min(bh + margin, h)

        cx1 = max(0, cx - new_w // 2)
        cy1 = max(0, cy - new_h // 2)
        cx2 = min(w, cx + new_w // 2)
        cy2 = min(h, cy + new_h // 2)

        crop = frame[cy1:cy2, cx1:cx2]
        return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)


    # ======================================================================
    # MÉTHODE DE NETTOYAGE : FERMETURE PROPRE EN CAS D'INTERRUPTION
    # ======================================================================
    def cleanup(self):
        """
        Ferme proprement tous les processus FFmpeg d'enregistrement en cours.
        Appelée par le gestionnaire de signal Ctrl+C (SIGINT).
        Sans ça, les fichiers MP4 en cours d'enregistrement seraient corrompus.
        """
        print(f"[{self.cam_id}] Fermeture propre des enregistrements en cours...")
        for proc in self._active_record_procs:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._active_record_procs.clear()


    # ======================================================================
    # BOUCLE PRINCIPALE : TRAITEMENT FRAME PAR FRAME
    # ======================================================================
    def run(self, reader: "FFmpegReader"):
        """
        Boucle infinie qui tourne dans un thread dédié pour cette caméra.
        Consomme les frames depuis le FFmpegReader et traite chacune :
          1. Conversion octets bruts → tableau numpy (image OpenCV)
          2. Détection radar (personnes + tracking ByteTrack)
          3. Crop + détection spécialiste (mains, sacs, articles)
          4. Tracking des articles avec tolérance aux ratés
          5. Logique d'alerte (3 scénarios de vol)
          6. Enregistrement vidéo si alerte active
          7. Publication de la frame pour Flask
        """
        print(f"[{self.cam_id}] Worker démarré.")

        while True:
            # ==========================================
            # ÉTAPE 0 : RÉCUPÉRATION DE LA FRAME
            # ==========================================
            raw_bytes = reader.get_frame(timeout=2.0)
            if raw_bytes is None:
                continue

            # Conversion : octets bruts → image numpy 3D (height × width × 3 canaux BGR)
            frame           = np.frombuffer(raw_bytes, np.uint8).reshape((self.height, self.width, 3))
            clean_frame     = frame.copy()
            annotated_frame = frame.copy()
            current_time    = time.time()
            self.frames_processed += 1

            # ==========================================
            # ÉTAPE 1 : LE RADAR (DÉTECTION DES PERSONNES)
            # ==========================================
            hands_pos        = []
            bags_pos         = []
            raw_articles_pos = []
            persons_boxes    = []

            with gpu_lock:
                results_radar = model_radar.track(
                    frame,
                    persist=True,
                    tracker="bytetrack.yaml",
                    verbose=False,
                    conf=0.2
                )

            if results_radar and results_radar[0].boxes is not None:
                r_boxes = results_radar[0].boxes.xyxy.cpu().numpy()
                r_clss  = results_radar[0].boxes.cls.cpu().numpy()
                r_confs = results_radar[0].boxes.conf.cpu().numpy()
                r_ids   = (
                    results_radar[0].boxes.id.cpu().numpy().astype(int)
                    if results_radar[0].boxes.id is not None
                    else []
                )

                for i, (box, cls, conf) in enumerate(zip(r_boxes, r_clss, r_confs)):
                    name = model_radar.names[int(cls)]

                    if name == "person" and  conf > 0.5:
                        persons_boxes.append(box)
                        x1, y1, x2, y2 = map(int, box)
                        is_loitering  = False
                        presence_time = 0

                        if i < len(r_ids):
                            p_id = r_ids[i]

                            self.last_known_person_boxes[p_id] = box
                            self.person_last_seen[p_id]        = current_time

                            # ── CHRONOMÈTRE DE PRÉSENCE (FLÂNERIE) ──
                            if p_id not in self.person_tracking:
                                self.person_tracking[p_id] = {
                                    "first_seen": current_time,
                                    "last_seen":  current_time,
                                }
                            else:
                                self.person_tracking[p_id]["last_seen"] = current_time

                            presence_time = current_time - self.person_tracking[p_id]["first_seen"]
                            if presence_time > LOITERING_THRESHOLD:
                                is_loitering = True

                        # Visuel : Orange si suspect (flânerie), Bleu sinon
                        if is_loitering:
                            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 160, 255), 2)
                            cv2.putText(
                                annotated_frame,
                                f"SUSPECT: {int(presence_time)}s",
                                (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 2,
                            )
                        else:
                            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 0, 0), 1)

                        # ==========================================
                        # ÉTAPE 2 : LA DÉCOUPE (CROP)
                        # ==========================================
                        padding = 20
                        x1_pad = max(0,           x1 - padding)
                        y1_pad = max(0,           y1 - padding)
                        x2_pad = min(self.width,  x2 + padding)
                        y2_pad = min(self.height, y2 + padding)

                        crop = frame[y1_pad:y2_pad, x1_pad:x2_pad]
                        if crop.size == 0:
                            continue

                        # ==========================================
                        # ÉTAPE 3 : LE SPÉCIALISTE (ANALYSE DU CROP)
                        # ==========================================
                        with gpu_lock:
                            results_spec = model_specialist.predict(crop, verbose=False, conf=0.15)

                        if results_spec and results_spec[0].boxes is not None:
                            s_boxes = results_spec[0].boxes.xyxy.cpu().numpy()
                            s_clss  = results_spec[0].boxes.cls.cpu().numpy()
                            s_confs = results_spec[0].boxes.conf.cpu().numpy()

                            for s_box, s_cls, s_conf in zip(s_boxes, s_clss, s_confs):
                                s_name = model_specialist.names[int(s_cls)]

                                # ==========================================
                                # ÉTAPE 4 : REMAPPING (RECALCUL DES COORDONNÉES)
                                # ==========================================
                                g_x1     = int(s_box[0] + x1_pad)
                                g_y1     = int(s_box[1] + y1_pad)
                                g_x2     = int(s_box[2] + x1_pad)
                                g_y2     = int(s_box[3] + y1_pad)
                                g_center = get_center([g_x1, g_y1, g_x2, g_y2])

                                # Main : Jaune — seuil haut (0.5)
                                if s_name == "hands" and s_conf > 0.5:
                                    hands_pos.append(g_center)
                                    cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 255, 255), 1)

                                # Sac : Rouge — seuil moyen (0.22)
                                elif s_name == "bags" and s_conf > 0.40:
                                    bags_pos.append(g_center)
                                    cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 0, 255), 2)

                                # Article de magasin : Violet — seuil bas (0.20)
                                elif s_name == "article" and s_conf > 0.22:
                                    raw_articles_pos.append((g_center, s_conf))
                                    cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (255, 0, 255), 2)

            # ==========================================
            # NETTOYAGE DES PERSONNES DISPARUES
            # ==========================================
            stale_person_ids = [
                pid for pid, ts in self.person_last_seen.items()
                if current_time - ts > 30.0
            ]
            for pid in stale_person_ids:
                self.last_known_person_boxes.pop(pid, None)
                self.person_last_seen.pop(pid, None)
                self.person_tracking.pop(pid, None)

            # ==========================================
            # ÉTAPE 5 : ASSIGNATION DES IDs AUX ARTICLES
            # ==========================================
            articles_pos = self._track_articles_custom(raw_articles_pos)

            for (a_center, a_id, a_conf) in articles_pos:
                self.last_known_articles[a_id] = a_center
                self.last_known_scores[a_id]   = a_conf
                cv2.putText(
                    annotated_frame, f"ID:{a_id}",
                    (a_center[0] - 10, a_center[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1,
                )

            trigger_alert  = False
            vol_type       = ""
            alert_score    = 0.0
            current_active = []
            target_p_id    = None

            # ──────────────────────────────────────────
            # SCÉNARIO 1 : OBJETS TENUS
            # ──────────────────────────────────────────
            for p_id, p_box in self.last_known_person_boxes.items():
                for (a_center, a_id, a_conf) in articles_pos:
                    if is_point_in_box(a_center, p_box):
                        key = f"article_{a_id}"
                        self.object_hold_counter[key] = self.object_hold_counter.get(key, 0) + 1

                        if self.object_hold_counter[key] >= FRAME_THRESHOLD:
                            current_active.append((a_id, a_center, a_conf))
                            self.hold_durations[a_id] = self.hold_durations.get(a_id, 0) + 1

                            cv2.circle(annotated_frame, a_center, 10, (0, 255, 0), 2)
                            cv2.putText(
                                annotated_frame, "TENU",
                                (a_center[0] + 10, a_center[1]),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
                            )

            # ──────────────────────────────────────────
            # SCÉNARIO 2 : VOL DANS LE SAC
            # ──────────────────────────────────────────
            for (a_id, a_center, a_conf) in current_active:
                a_center = self.last_known_articles[a_id]
                for b_center in bags_pos:
                    dist_sac = math.hypot(a_center[0] - b_center[0], a_center[1] - b_center[1])
                    if dist_sac < 35:
                        if time.time() - self.last_alert_time > ALERT_COOLDOWN:
                            trigger_alert = True
                            vol_type      = "SAC"
                            alert_score   = float(a_conf)
                            for p_id, p_box in self.last_known_person_boxes.items():
                                if is_point_in_box(b_center, p_box):
                                    target_p_id = p_id
                                    break

            # ──────────────────────────────────────────
            # SCÉNARIO 3 : VOL CORPOREL (LOGIQUE ANATOMIQUE)
            # ──────────────────────────────────────────
            visible_ids = {a_id for (_, a_id, _) in articles_pos}

            # 1. Annule la suspicion si l'objet réapparaît
            for a_id in list(self.suspect_disappearance.keys()):
                if a_id in visible_ids:
                    print(f"[{self.cam_id}] Angle mort terminé pour objet {a_id}, suspicion annulée.")
                    del self.suspect_disappearance[a_id]

            # 2. Analyse des disparitions suspectes
            for key, count in self.object_hold_counter.items():
                a_id = int(key.split("_")[1])

                if count >= FRAME_THRESHOLD and a_id not in visible_ids:
                    last_pos = self.last_known_articles.get(a_id)
                    if not last_pos:
                        continue

                    # ── FILTRE ANTI-ERREUR DE LABEL ──
                    is_label_swap = any(
                        math.hypot(last_pos[0] - bc[0], last_pos[1] - bc[1]) < 30
                        for bc in bags_pos
                    )
                    if is_label_swap:
                        continue

                    # ── FILTRE BORD D'ÉCRAN ──
                    margin = 45
                    if not (margin < last_pos[0] < self.width - margin
                            and margin < last_pos[1] < self.height - margin):
                        continue

                    # ── FILTRE GÉOMÉTRIQUE AVANCÉ (ANTI-RAYON) ──
                    is_suspect_zone = False
                    for p_id, p_box in self.last_known_person_boxes.items():
                        if is_point_in_box(last_pos, p_box):
                            p_w = p_box[2] - p_box[0]
                            p_h = p_box[3] - p_box[1]
                            rel_x = (last_pos[0] - p_box[0]) / p_w if p_w > 0 else 0.5
                            rel_y = (last_pos[1] - p_box[1]) / p_h if p_h > 0 else 0.5

                            hauteur_suspecte    = 0.35 <= rel_y <= 0.85
                            centralite_suspecte = 0.25 <= rel_x <= 0.75

                            if hauteur_suspecte and centralite_suspecte:
                                is_suspect_zone = True
                                target_p_id     = p_id
                                break

                    if is_suspect_zone and a_id not in self.suspect_disappearance:
                        self.suspect_disappearance[a_id] = {
                            "start_time":  current_time,
                            "last_score":  self.last_known_scores.get(a_id, 0.5),
                            "hold_frames": self.hold_durations.get(a_id, 0),
                            "p_id":        target_p_id,
                        }

            # 3. Validation finale : 12s pour distinguer angle mort et vrai vol
            for a_id, data in list(self.suspect_disappearance.items()):
                elapsed     = current_time - data["start_time"]
                target_p_id = data["p_id"]

                # ── SÉCURITÉ FUITE ──
                personne_partie = False
                if target_p_id in self.person_tracking:
                    if current_time - self.person_tracking[target_p_id]["last_seen"] > 2.5:
                        personne_partie = True

                if elapsed >= DISAPPEARANCE_TIMEOUT or personne_partie:
                    if current_time - self.last_alert_time > ALERT_COOLDOWN:
                        if data["hold_frames"] > 30:
                            loitering_bonus = 0.0
                            if target_p_id in self.person_tracking:
                                p_time = current_time - self.person_tracking[target_p_id]["first_seen"]
                                if p_time > LOITERING_THRESHOLD:
                                    loitering_bonus = 0.25

                            base_score  = float(
                                0.4 * data["last_score"]
                                + 0.6 * min(1.0, data["hold_frames"] / 30.0)
                            )
                            alert_score = min(1.0, base_score + loitering_bonus)

                            trigger_alert = True
                            vol_type      = "CORPS"

                            if personne_partie and elapsed < DISAPPEARANCE_TIMEOUT:
                                print(f"[{self.cam_id}] ⚡ ALERTE ANTICIPÉE : Suspect {target_p_id} sorti avec objet {a_id}")

                    del self.suspect_disappearance[a_id]
                    self.hold_durations.pop(a_id, None)

            # ==========================================
            # DÉCLENCHEMENT DE L'ALERTE ET ENREGISTREMENT
            # ==========================================
            if trigger_alert and not self.is_recording_alert:
                self.zoom_target_id = target_p_id

                subprocess.Popen(
                    #["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                print(f"[{self.cam_id}] 🚨 ALERTE : VOL {vol_type} (score={alert_score:.2f})")
                self._start_alert_video(vol_type, alert_score)
                self.last_alert_time    = current_time
                self.alert_text_to_show = f" ALERTE : VOL {vol_type} POTENTIEL "
                self.alert_text_timer   = current_time + DISPLAY_TEXT_DURATION

            # ==========================================
            # AFFICHAGE DU TEXTE D'ALERTE (CLIGNOTANT)
            # ==========================================
            if current_time < self.alert_text_timer:
                blink = int(time.time() * 2) % 2

                if blink == 1:
                    color         = (0, 0, 255)
                    thickness     = 2
                    corner_length = 40

                    cv2.line(annotated_frame, (0, 0), (corner_length, 0), color, thickness)
                    cv2.line(annotated_frame, (0, 0), (0, corner_length), color, thickness)
                    cv2.line(annotated_frame, (self.width, 0), (self.width - corner_length, 0), color, thickness)
                    cv2.line(annotated_frame, (self.width, 0), (self.width, corner_length), color, thickness)
                    cv2.line(annotated_frame, (0, self.height), (corner_length, self.height), color, thickness)
                    cv2.line(annotated_frame, (0, self.height), (0, self.height - corner_length), color, thickness)
                    cv2.line(annotated_frame, (self.width, self.height), (self.width - corner_length, self.height), color, thickness)
                    cv2.line(annotated_frame, (self.width, self.height), (self.width, self.height - corner_length), color, thickness)

                font_scale = 0.5
                thickness  = 1
                text_size  = cv2.getTextSize(
                    self.alert_text_to_show, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
                )[0]
                text_x = 10
                text_y = 30
                cv2.rectangle(
                    annotated_frame,
                    (text_x - 5, text_y - text_size[1] - 5),
                    (text_x + text_size[0] + 5, text_y + 5),
                    (0, 0, 0), -1,
                )
                cv2.putText(
                    annotated_frame, self.alert_text_to_show,
                    (text_x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 255), thickness,
                )

            # ==========================================
            # PUBLICATION DE LA FRAME POUR FLASK
            # ==========================================
            with frame_lock:
                output_frames[self.cam_id] = annotated_frame.copy()
                raw_frames[self.cam_id]    = clean_frame.copy()

            self.video_buffer.append(annotated_frame)
            self.video_buffer_raw.append(clean_frame.copy())

            frame_to_record     = annotated_frame.copy()
            frame_raw_to_record = clean_frame.copy()

            if self.zoom_target_id in self.last_known_person_boxes:
                box                 = self.last_known_person_boxes[self.zoom_target_id]
                frame_to_record     = self._zoom_tracking(frame_to_record, box)
                frame_raw_to_record = self._zoom_tracking(frame_raw_to_record, box)

            # ==========================================
            # ÉCRITURE DES FRAMES DANS LE CLIP D'ALERTE
            # ==========================================
            if self.is_recording_alert:
                try:
                    if self.alert_ffmpeg_process and self.alert_ffmpeg_process.stdin:
                        self.alert_ffmpeg_process.stdin.write(frame_to_record.tobytes())
                    if self.raw_ffmpeg_process and self.raw_ffmpeg_process.stdin:
                        self.raw_ffmpeg_process.stdin.write(frame_raw_to_record.tobytes())

                    self.frames_to_record_after -= 1

                    if self.frames_to_record_after <= 0:
                        self.is_recording_alert = False
                        self.zoom_target_id     = None
                        self.smooth_center      = None

                        for proc in [self.alert_ffmpeg_process, self.raw_ffmpeg_process]:
                            if proc:
                                try:
                                    proc.stdin.close()
                                    proc.wait()
                                except Exception:
                                    pass

                        self.alert_ffmpeg_process = None
                        self.raw_ffmpeg_process   = None
                        print(f"[{self.cam_id}] ✅ Clip enregistré.")

                except Exception as e:
                    print(f"[{self.cam_id}] ❌ Erreur enregistrement : {e}")
                    self.is_recording_alert = False

            # ==========================================
            # DÉCRÉMENTATION DES COMPTEURS "TENU"
            # ==========================================
            self.object_hold_counter = {
                k: v - 1 for k, v in self.object_hold_counter.items() if v > 1
            }


# ==========================================
# POINT D'ENTRÉE : LANCEMENT DE TOUS LES THREADS
# ==========================================
if __name__ == "__main__":

    all_workers  = []
    all_readers  = []

    def shutdown_handler(signum, frame):
        """
        Gestionnaire du signal SIGINT (Ctrl+C).
        Ferme proprement tous les processus FFmpeg avant de quitter.
        Sans ça, les fichiers MP4 en cours seraient corrompus.
        """
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

    for cam_cfg in CAMERAS:
        cam_id  = cam_cfg["cam_id"]
        cam_url = cam_cfg["rtsp_url"]
        cam_w   = cam_cfg["width"]
        cam_h   = cam_cfg["height"]
        cam_fps = cam_cfg["fps"]

        # Thread 1 : lecteur RTSP dédié
        reader = FFmpegReader(cam_id, cam_url, cam_w, cam_h)
        all_readers.append(reader)
        threading.Thread(
            target=reader.run,
            daemon=True,
            name=f"{cam_id}_reader",
        ).start()

        # Thread 2 : worker d'analyse YOLO
        worker = CameraWorker(**cam_cfg)
        all_workers.append(worker)
        threading.Thread(
            target=worker.run,
            args=(reader,),
            daemon=True,
            name=f"{cam_id}_worker",
        ).start()

        print(f"✅ {cam_id} démarré → http://192.168.0.97:5000/video/{cam_id}")

    print("\n🔒 Système actif. Ctrl+C pour arrêter.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt demandé. Fermeture propre...")