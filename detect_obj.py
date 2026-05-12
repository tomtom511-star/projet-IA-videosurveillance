"""
╔══════════════════════════════════════════════════════════════════════════╗
║         SYSTÈME DE DÉTECTION DE VOL MULTI-CAMÉRAS — YOLO + Flask        ║
║                          VERSION 9 — FILTRES ANTI-FAUX-POSITIFS          ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  ARCHITECTURE GÉNÉRALE :                                                 ║
║  ┌──────────┐    ┌──────────────────┐    ┌──────────────────────────┐  ║
║  │ CAM RTSP │───▶│  FFmpegReader    │───▶│  frame_queue (maxsize=1) │  ║
║  │ CAM RTSP │───▶│  (thread dédié)  │    │  (drop des vieilles      │  ║
║  │   ...    │    │  1 par caméra    │    │   frames automatique)    │  ║
║  └──────────┘    └──────────────────┘    └────────────┬─────────────┘  ║
║                                                        │                 ║
║                              ┌─────────────────────────▼──────────────┐ ║
║                              │         batch_input_queue               │ ║
║                              │  Chaque caméra y dépose sa frame        │ ║
║                              └─────────────────────────┬──────────────┘ ║
║                                                        │                 ║
║                              ┌─────────────────────────▼──────────────┐ ║
║                              │      GPU_BATCH_THREAD (thread unique)   │ ║
║                              │                                         │ ║
║                              │  POURQUOI UN SEUL THREAD GPU ?          │ ║
║                              │  Avec 7 caméras et un gpu_lock partagé, │ ║
║                              │  chaque worker attendait les 6 autres.  │ ║
║                              │  Résultat : ~1 FPS réel par caméra.     │ ║
║                              │  Ici : 1 seul appel YOLO sur un batch   │ ║
║                              │  de N frames simultanées → x4 à x6      │ ║
║                              │  plus rapide sur Quadro P2200.          │ ║
║                              │                                         │ ║
║                              │  Radar batch → résultats par cam_id     │ ║
║                              │  Spécialiste batch sur les crops         │ ║
║                              └─────────────────────────┬──────────────┘ ║
║                                                        │                 ║
║                              ┌─────────────────────────▼──────────────┐ ║
║                              │  result_queues[cam_id]  (1 par caméra) │ ║
║                              │  Chaque worker récupère SES résultats   │ ║
║                              └─────────────────────────┬──────────────┘ ║
║                                                        │                 ║
║                              ┌─────────────────────────▼──────────────┐ ║
║                              │  CameraWorker (1 par caméra)            │ ║
║                              │  Logique métier : tracking, détection   │ ║
║                              │  vol, alertes, enregistrement clips     │ ║
║                              └─────────────────────────┬──────────────┘ ║
║                                                        │                 ║
║                    Flask /video/<cam_id>               │                 ║
║                    Flask /alerts                       │                 ║
║                    Flask /suspicions  ◀────────────────┘                 ║
║                                                                          ║
║  SUSPICIONS :                                                            ║
║  Les suspicions NE sont PLUS écrites dans alerts.jsonl.                 ║
║  Elles sont stockées EN MÉMOIRE dans un dict partagé                    ║
║  et exposées via GET /suspicions (polling léger par l'interface).       ║
║  Pas de clip, pas de bruit dans le JSON.                                ║
║  L'agent clique et va directement sur la caméra concernée.              ║
║                                                                          ║
║  ALERTES (alerts.jsonl) :                                               ║
║  Format JSON Lines (1 alerte = 1 ligne) → append O(1).                 ║
║  Pas de relecture du fichier entier à chaque alerte.                    ║
║  Résiste à des semaines de logs sans ralentir.                          ║
║                                                                          ║
║  CORRECTIFS v9 vs v8 — FILTRES ANTI-FAUX-POSITIFS :                    ║
║  ────────────────────────────────────────────────────                    ║
║                                                                          ║
║  [FILTRE A] Confiance moyenne minimale pour valider "tenu"              ║
║    Un article doit avoir une confiance MOYENNE ≥ HOLD_CONF_MIN          ║
║    (0.40) sur les dernières frames pour être considéré tenu.            ║
║    Un vrai article en main : conf stable à 0.6–0.8.                    ║
║    Un faux positif (badge, téléphone...) : oscille entre 0.22–0.35.    ║
║    → Ne touche PAS au seuil de détection YOLO (0.22 conservé).         ║
║                                                                          ║
║  [FILTRE B] Durée minimale d'absence avant suspicion corporelle         ║
║    L'article doit être absent au moins MIN_DISAPPEARANCE_FRAMES (36f    ║
║    = 3s) AVANT de démarrer le timer de suspicion.                       ║
║    Avant : une simple occlusion de 5 frames suffisait.                  ║
║    Maintenant : une occlusion courte (client qui passe devant)          ║
║    ne déclenche jamais de suspicion.                                    ║
║                                                                          ║
║  [FILTRE D] Score minimum absolu pour déclencher une alerte             ║
║    alert_score doit dépasser ALERT_SCORE_MIN (0.55) pour créer          ║
║    un clip et écrire dans alerts.jsonl.                                 ║
║    En dessous : l'événement reste une suspicion en mémoire              ║
║    (visible sur l'interface, pas de clip, pas de bruit JSONL).          ║
║    Formule actuelle peut donner 0.39 avec des valeurs minimales         ║
║    → ces cas ne généreront plus d'alerte formelle.                      ║
║                                                                          ║
║  [FILTRE E] Cooldown PAR article_id, pas global par caméra             ║
║    Avant : un seul last_alert_time par caméra → 2 vols simultanés      ║
║    sur la même caméra = le 2e était silencé pendant ALERT_COOLDOWN.    ║
║    Maintenant : chaque article_id a son propre timestamp d'alerte.     ║
║    → 2 vrais vols simultanés sur la même caméra = 2 alertes.           ║
║    Le cooldown global (last_alert_time) est conservé uniquement         ║
║    pour la mise à jour de l'UI (texte clignotant).                      ║
║                                                                          ║
║  CORRECTIFS v8 (conservés) :                                            ║
║  ─────────────────────                                                   ║
║  [PERF] HOLD_STREAK_THRESHOLD abaissé de 60 à 20 frames (~1.7s à 12FPS)║
║  [PERF] HOLD_STREAK_MISS_MAX relevé de 3 à 10 frames                   ║
║  [PERF] hold_durations requis pour alerte CORPS abaissé de 60 à 30     ║
║  [PERF] Chemin de détection primaire inversé : article_consecutive_     ║
║         frames est maintenant LE chemin principal                        ║
║  [BUG]  article_consecutive_frames avec tolérance CONSECUTIVE_MISS_MAX  ║
║  [BUG]  Race condition sur suspect_disappearance corrigée               ║
║  [BUG]  _suspicion_logged est un dict par article, pas global           ║
║  [BUG]  Scénario SAC : distance vérifiée indépendamment du streak       ║
║  [DEBUG] Logs intermédiaires toutes les 30 frames via DEBUG_LOGS        ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from ultralytics import YOLO       # Charge YOLO, le modèle IA de détection d'objets
import cv2                         # OpenCV : gère les images et les flux vidéo
import os                          # Permet de gérer les dossiers et variables d'environnement
import math                        # Permet de calculer les distances (math.hypot)
import json                        # Sérialisation JSON pour le fichier d'alertes et l'API Flask
import signal                      # Permet d'intercepter Ctrl+C pour fermer proprement FFmpeg
import numpy as np                 # Manipulation des tableaux de pixels (images)
import subprocess                  # Lance des processus externes (FFmpeg)
from datetime import datetime      # Pour horodater les alertes et noms de fichiers
import time                        # Pour les délais et timestamps
import torch                       # PyTorch : force l'utilisation du GPU NVIDIA
from collections import deque      # Buffer circulaire : garde les N dernières frames en mémoire
import queue                       # queue.Queue : communication thread-safe entre threads
from flask import Flask, Response, request, jsonify  # Serveur web pour streamer la vidéo
import threading                   # Gestion des threads


# ==========================================
# CONFIGURATION GPU
# ==========================================
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
os.environ["YOLO_VERBOSE"] = "False"

# Mettre à True pour voir les logs de debug (streaks, consecutifs, etc.)
# Utile pour diagnostiquer pourquoi les alertes ne se déclenchent pas.
# Mettre à False en production pour éviter le bruit dans les logs.
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
ALERT_COOLDOWN        = 20    # Temps minimum (en secondes) entre 2 alertes SUR LE MÊME ARTICLE
                              # (v9 : cooldown par article_id, pas global — voir FILTRE E)
DISAPPEARANCE_TIMEOUT = 12.0  # Temps (en secondes) avant de considérer un objet "disparu sous les vêtements"
FRAME_THRESHOLD       = 8     # Nombre de frames consécutives pour valider qu'un objet est bien "tenu"
LOITERING_THRESHOLD   = 90.0  # Temps (en secondes) avant qu'une personne soit considérée suspecte (flânerie)

# Durée d'affichage du texte d'alerte à l'écran
DISPLAY_TEXT_DURATION = 4.0

# Durées du clip vidéo enregistré lors d'une alerte
BEFORE_ALERT_SECS = 5
AFTER_ALERT_SECS  = 8

# Tolérance du mini-tracker : nombre de frames pendant lesquelles un article
# peut disparaître (occlusion, raté YOLO) avant d'être définitivement perdu.
TRACKER_MISS_TOLERANCE = 5

# ── PARAMÈTRES DE DÉTECTION DE CONTACT ───────────────────────────────────────

# Mémoire des positions de mains : à 12 FPS, 24 frames = 2 secondes.
# Si une main a été proche d'un article dans ces 2s avant sa disparition,
# on considère qu'il y a eu contact réel → on démarre le timer de suspicion.
HAND_MEMORY_FRAMES = 24       # 2 secondes à 12 FPS

# Distance max en pixels pour considérer qu'une main "touche" un article.
# 50px ≈ largeur d'une main vue depuis une caméra de rayon standard.
HAND_ARTICLE_DIST  = 50

# Fenêtre de calcul de la corrélation de mouvement article/personne.
MOVEMENT_HISTORY_FRAMES = 6   # 0.5 seconde à 12 FPS

# Corrélation minimale (cosine similarity) entre déplacement article et personne.
# 0.6 = même direction à 60% minimum. En dessous → article fixe sur rayon.
MOVEMENT_CORRELATION_MIN = 0.6

# ── PARAMÈTRES DU STREAK "TENU" (CORRECTIFS v8) ──────────────────────────────

# CORRECTIF v8 : HOLD_STREAK_THRESHOLD abaissé de 60 à 20 frames.
# POURQUOI : à 12 FPS, 60 frames = 5 secondes CONSÉCUTIVES de corrélation parfaite.
# En conditions réelles (marche, occlusions, bbox YOLO qui "respire"),
# ce seuil n'était jamais atteint → zéro alerte.
# 20 frames (~1.7s) reste défensif contre les faux positifs mais est atteignable.
HOLD_STREAK_THRESHOLD = 20    # v8 : 20 frames (~1.7s) au lieu de 60

# CORRECTIF v8 : HOLD_STREAK_MISS_MAX relevé de 3 à 10 frames.
# POURQUOI : 3 frames = 250ms, insuffisant pour absorber le mouvement naturel
# de marche (bras qui oscillent, bbox qui "respire" de quelques pixels).
# 10 frames = ~830ms, absorbe les oscillations sans masquer un vrai lâcher.
HOLD_STREAK_MISS_MAX = 10     # v8 : 10 frames au lieu de 3

# NOUVEAU v8 : nombre de frames consécutives de détection d'un article dans
# la bbox pour le considérer comme tenu VIA LE CHEMIN PRINCIPAL (détection pure).
# Le modèle spécialiste ne détecte les articles QU'en main → 12 frames = ~1s.
# C'est maintenant le chemin PRIMAIRE (plus robuste que la corrélation).
ARTICLE_DETECTED_HOLD_THRESHOLD = 12

# NOUVEAU v8 : tolérance aux "trous" dans la détection consécutive.
# POURQUOI : si YOLO rate 1-2 frames, le compteur consécutif tombait à 0
# immédiatement, rendant le chemin primaire aussi fragile que le streak.
# Avec 5 frames de tolérance, les ratés ponctuels ne réinitialisent plus le compteur.
CONSECUTIVE_MISS_MAX = 5      # v8 : nouveau paramètre, n'existait pas en v7

# ── PARAMÈTRES ANTI-FAUX-POSITIFS (NOUVEAUX v9) ──────────────────────────────

# [FILTRE A] Confiance moyenne minimale sur la fenêtre d'historique pour
# valider qu'un article est "tenu". Ne touche pas au seuil de détection YOLO.
#
# POURQUOI : le spécialiste est entraîné avec noise/blur/mosaic pour fonctionner
# à conf=0.22 en conditions dégradées. Mais un VRAI article tenu en main est
# détecté à 0.6–0.8 en moyenne sur plusieurs frames. Un faux positif (badge,
# téléphone personnel, bord de rayon) oscille entre 0.22 et 0.35 car YOLO
# n'est pas sûr. En exigeant une moyenne ≥ 0.40 sur les dernières frames,
# on filtre le bruit sans toucher à la sensibilité brute du détecteur.
HOLD_CONF_MIN = 0.30          # v9 : confiance moyenne minimale pour valider "tenu"

# Nombre de frames d'historique de confiance à conserver par article.
# 20 frames = ~1.7s à 12 FPS → fenêtre glissante suffisamment longue pour
# lisser les "respirations" YOLO sans mémoriser des détections trop anciennes.
HOLD_CONF_HISTORY_LEN = 20    # v9 : fenêtre glissante de confiance

# [FILTRE B] Durée minimale d'absence de l'article (en frames) avant de
# démarrer le timer de suspicion corporelle.
#
# POURQUOI : avant v9, le timer démarrait dès que consec_gone = True,
# c'est-à-dire après seulement CONSECUTIVE_MISS_MAX (5) frames d'absence.
# Une simple occlusion (un autre client qui passe devant, éblouissement
# ponctuel, angle mort partiel) déclenchait donc systématiquement une
# suspicion. Avec 36 frames (3s à 12 FPS), une occlusion courte est ignorée.
# Un vrai vol corporel implique que l'article reste invisible bien plus longtemps.
MIN_DISAPPEARANCE_FRAMES = 36 # v9 : 3 secondes à 12 FPS avant d'entrer en suspicion

# [FILTRE D] Score minimum absolu pour déclencher une alerte formelle
# (clip vidéo + écriture dans alerts.jsonl).
#
# POURQUOI : la formule de score peut donner ~0.39 avec les valeurs minimales
# (conf=0.22, hold_frames=30, pas de loitering). Ces cas ambigus deviennent
# des suspicions en mémoire (visibles sur l'interface) mais pas des alertes
# avec clip. L'agent voit la notification discrète et peut aller vérifier
# sans être noyé dans des clips de faux positifs.
# 0.55 = niveau "probable" : on veut des alertes à clip uniquement quand
# le système est raisonnablement sûr, pas sur chaque doute.
ALERT_SCORE_MIN = 0.55        # v9 : score minimum pour alerte formelle avec clip

# ── AUTRES PARAMÈTRES GPU / SUSPICION ────────────────────────────────────────

# Délai maximum pour remplir un batch GPU avant de l'envoyer quand même.
# 100ms — cohérent avec 7 appels .track() séquentiels
BATCH_TIMEOUT_SECS = 0.080

# Durée de vie d'une suspicion en mémoire.
# Si aucune alerte formelle ne confirme dans ce délai, la suspicion expire.
SUSPICION_TTL = 30            # secondes

# ─────────────────────────────────────────────────────────────────────────────

# ── PARAMÈTRES SCÉNARIO SAC (v10) ────────────────────────────────────────────

# Nb minimum de frames où l'article doit être proche du sac AVANT de
# surveiller sa disparition. À 12 FPS, 6 frames = 0.5s.
# Évite qu'une seule frame de proximité accidentelle déclenche la séquence.
SAC_PROXIMITY_FRAMES_MIN = 12

# Distance max article/sac pour considérer une proximité (pixels).
# 35px ≈ distance main-ouverture du sac vue depuis une caméra de rayon.
SAC_PROXIMITY_DIST = 40

# Temps max (secondes) pour attendre la disparition de l'article après
# le rapprochement. Au-delà → l'article est resté visible, reset du compteur.
# 3s suffit : un vrai geste d'insertion dans un sac dure <2s.
SAC_DISAPPEARANCE_TIMEOUT = 2.0

# ==========================================
# DOSSIERS ET FICHIER D'ALERTES (JSONL)
# ==========================================
# On utilise alerts.jsonl (JSON Lines : 1 ligne = 1 alerte, append-only).
# Avantage majeur : écriture O(1) quelle que soit la taille du fichier.
# Après 6 mois de logs, l'écriture est aussi rapide qu'au premier jour.
ALERT_FILE    = "alerts.jsonl"
alert_vid_dir = "alert_clips"
raw_dir       = os.path.join(alert_vid_dir, "raw")

os.makedirs(alert_vid_dir, exist_ok=True)
os.makedirs(raw_dir,       exist_ok=True)
os.makedirs("snapshots",   exist_ok=True)

# Crée le fichier vide s'il n'existe pas (0 lignes = fichier JSONL valide)
if not os.path.exists(ALERT_FILE):
    open(ALERT_FILE, "w").close()

# Verrou dédié à l'écriture du fichier alerts.jsonl.
# Sans ce verrou, deux threads pourraient écrire en même temps → ligne corrompue.
alerts_file_lock = threading.Lock()


# ==========================================
# SUSPICIONS EN MÉMOIRE
# ==========================================
# Les suspicions ne sont PLUS écrites dans le fichier JSONL.
# Elles vivent uniquement en RAM dans ce dict :
#   { cam_id → {"time": "HH:MM:SS", "score": float, "type": str, "expires_at": float} }
#
# L'interface web polle GET /suspicions toutes les 2-3 secondes pour afficher
# une notification discrète "Œil sur CAM_XX".
# La suspicion disparaît automatiquement après SUSPICION_TTL secondes,
# ou immédiatement quand une alerte formelle est levée sur la même caméra.
active_suspicions: dict = {}
suspicions_lock = threading.Lock()


# ==========================================
# CHARGEMENT DES MODÈLES YOLO
# ==========================================
# Les deux modèles sont chargés UNE SEULE FOIS et utilisés UNIQUEMENT
# par le thread GPU centralisé. Les workers n'y accèdent jamais directement.

print("CUDA available:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")

# MODÈLE 1 : LE RADAR — cherche les personnes sur l'image globale
model_radar = YOLO("runs/detect/radar_global_v2/weights/best.pt")
model_radar.to("cuda")


# MODÈLE 2 : LE SPÉCIALISTE — cherche mains, sacs et articles dans un crop personne
model_specialist = YOLO("runs/detect/radar_specialiste_v3/weights/best.pt")
model_specialist.to("cuda")




# ==========================================
# QUEUES DE COMMUNICATION GPU
# ==========================================
# Architecture batch :
#   1. Chaque CameraWorker dépose (cam_id, frame) dans batch_input_queue.
#   2. Le thread GPU collecte jusqu'à len(CAMERAS) frames, fait UNE inférence
#      YOLO en batch, redistribue les résultats dans result_queues[cam_id].
#   3. Chaque worker récupère ses résultats depuis result_queues[cam_id].

# Queue globale sans limite de taille (les workers ne produisent qu'une frame/cycle)
batch_input_queue = queue.Queue()

# Une queue de résultats par caméra, maxsize=1 pour ne pas accumuler de retard
result_queues: dict = {
    cam["cam_id"]: queue.Queue(maxsize=1)
    for cam in CAMERAS
}


# ==========================================
# ÉTAT PARTAGÉ ENTRE LES THREADS ET FLASK
# ==========================================
output_frames: dict = {}   # cam_id → frame annotée (pour le stream Flask)
raw_frames:    dict = {}   # cam_id → frame propre (pour les snapshots)
frame_lock = threading.RLock()


# ==========================================
# SERVEUR FLASK — STREAMING LIVE MULTI-CAMÉRAS
# ==========================================
app = Flask(__name__)


def generate_stream(cam_id: str):
    # On mémorise la dernière frame envoyée au navigateur.
    # Permet de ne yielder que quand une nouvelle frame est disponible,
    # au lieu d'envoyer la même frame en boucle toutes les 40ms.
    last_sent = None
    while True:
        got_lock = frame_lock.acquire(blocking=False)
        frame = None
        try:
            if got_lock:
                frame = output_frames.get(cam_id)
        finally:
            if got_lock:
                frame_lock.release()

        if frame is not None and frame is not last_sent:
            # qualité JPEG réduite de 50 à 40.
            # Sur un stream de surveillance local, la différence visuelle
            # est imperceptible, mais le gain sur la bande passante réseau
            # et le temps d'encodage CPU est de ~20% par frame.
            _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 40])
            last_sent = frame
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        else:
            # sleep réduit de 40ms à 20ms quand il n'y a
            # pas de nouvelle frame, au lieu d'attendre un cycle complet.
            # L'ancien sleep fixe de 40ms introduisait une latence artificielle
            # même quand le GPU avait déjà produit une nouvelle frame depuis 5ms.
            time.sleep(0.02)


@app.route("/video/<cam_id>")
def video(cam_id):
    """Route Flask : diffuse le flux live de la caméra cam_id en MJPEG."""
    return Response(
        generate_stream(cam_id),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/snapshot", methods=["POST"])
def take_snapshot():
    """Route Flask : capture et sauvegarde l'image brute de la caméra demandée."""
    data   = request.get_json()
    cam_id = data.get("cam_id", "unknown")

    with frame_lock:
        frame = raw_frames.get(cam_id)
        if frame is None:
            return jsonify({"status": "error", "message": "Pas d'image disponible"}), 500
        frame_to_save = frame.copy()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"CLEAN_{cam_id}_{timestamp}.jpg"
    file_path = os.path.join("snapshots", file_name)
    cv2.imwrite(file_path, frame_to_save)
    print(f"📸 Snapshot enregistré : {file_path}")
    return jsonify({"status": "success", "file": file_path}), 200


@app.route("/alerts")
def get_alerts():
    """
    Route Flask : retourne les alertes enregistrées depuis alerts.jsonl.
    Lit le fichier ligne par ligne → pas de rechargement complet en mémoire.

    Paramètre optionnel ?last=N pour ne récupérer que les N dernières alertes.
    Exemple : GET /alerts?last=50
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
            pass   # Ignore les lignes corrompues (coupure disque, redémarrage brutal)

    return jsonify(alerts)


@app.route("/suspicions")
def get_suspicions():
    """
    Route Flask : retourne les suspicions actives EN MÉMOIRE (pas dans le JSONL).

    L'interface web polle cette route toutes les 2-3 secondes pour afficher
    une notification discrète "Œil sur CAM_XX" sans interrompre l'agent.
    Pas de clip, pas d'écriture disque.

    Les suspicions expirées (SUSPICION_TTL dépassé) sont nettoyées à chaque appel.
    Retourne : { cam_id → {time, score, type} }
    """
    now = time.time()
    with suspicions_lock:
        # Nettoyage automatique des suspicions expirées
        expired = [c for c, s in active_suspicions.items() if now > s["expires_at"]]
        for cam_id in expired:
            del active_suspicions[cam_id]

        # On copie sans le champ interne expires_at (inutile pour le client)
        result = {
            cam_id: {
                "time":  s["time"],
                "score": s["score"],
                "type":  s["type"],
            }
            for cam_id, s in active_suspicions.items()
        }

    return jsonify(result)


def start_server():
    """Lance le serveur Flask dans un thread dédié."""
    import logging
    # Désactive les logs des requêtes HTTP de Werkzeug
    # (évite le spam GET /suspicions toutes les 5s dans le terminal)
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)  # Ne montre que les vraies erreurs, plus les 200 OK
    
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
    """Lit exactement n_bytes octets depuis un pipe, même si plusieurs read() sont nécessaires."""
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
    Thread de drainage stderr FFmpeg.
    CORRECTIF BUG : sans drainage, le buffer OS (~64Ko) se remplit,
    FFmpeg se bloque en écriture stderr, ce qui bloque aussi stdout → freeze.
    """
    try:
        for line in process.stderr:
            if stop_event.is_set():
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded and "error" in decoded.lower():
                print(f"[{cam_id}] FFmpeg stderr: {decoded}")
    except Exception:
        pass


def append_alert_jsonl(alert_dict: dict):
    """
    Écrit UNE alerte dans alerts.jsonl en mode append (O(1)).
    Thread-safe grâce à alerts_file_lock.

    Format JSON Lines : chaque ligne est un objet JSON autonome et valide.
    L'interface lit avec : [json.loads(line) for line in open(ALERT_FILE)]
    """
    with alerts_file_lock:
        with open(ALERT_FILE, "a") as f:
            f.write(json.dumps(alert_dict, ensure_ascii=False) + "\n")


# ==========================================
# GESTION DE L'ESPACE DISQUE ET PURGE DES CLIPS
# ==========================================

# Espace disque minimum requis (en Go) pour autoriser un enregistrement.
# En dessous de ce seuil, l'enregistrement est refusé et un warning est loggé.
# Ajuster selon la taille de votre partition de stockage.
DISK_MIN_FREE_GB = 5.0

# Durée de rétention des clips vidéo (en jours).
# Les clips plus anciens que cette valeur sont supprimés automatiquement.
# La CNIL recommande 30 jours max pour la vidéosurveillance.
CLIP_RETENTION_DAYS = 30

# Intervalle entre deux passes de purge (en secondes).
# 3600 = toutes les heures. Pas besoin de purger plus souvent.
PURGE_INTERVAL_SECS = 3600


def _get_free_gb(path: str = ".") -> float:
    """
    Retourne l'espace libre en Go sur la partition contenant 'path'.
    Retourne -1.0 en cas d'erreur de lecture (chemin invalide, etc.).
    Fonction interne utilisée par check_disk_space et emergency_free_space.
    """
    try:
        stat = os.statvfs(path)
        return (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
    except Exception as e:
        print(f"⚠️  Impossible de lire l'espace disque sur '{path}' : {e}")
        return -1.0


def emergency_free_space():
    """
    Libère de l'espace disque en urgence en supprimant 30% des clips les plus anciens.

    Appelée automatiquement par check_disk_space() quand l'espace libre passe
    sous DISK_MIN_FREE_GB Go. Elle tente de libérer assez de place pour que
    l'enregistrement en cours puisse se faire, sans bloquer le système.

    COMPORTEMENT :
    - Collecte TOUS les fichiers .mp4 de alert_clips/ et alert_clips/raw/
    - Les trie du plus ancien au plus récent (par date de modification)
    - Supprime les 30% les plus anciens (arrondi au supérieur)
    - NE TOUCHE PAS aux fichiers .jsonl (trace d'audit conservée)
    - NE TOUCHE PAS aux fichiers récents (les clips du jour sont protégés
      par le tri : on supprime toujours les plus vieux en premier)

    Retourne True si après suppression l'espace est suffisant, False sinon.
    """
    # Collecter tous les .mp4 des deux dossiers (annotés + raw)
    all_clips = []
    for folder in [alert_vid_dir, raw_dir]:
        if not os.path.isdir(folder):
            continue
        for filename in os.listdir(folder):
            if not filename.endswith(".mp4"):
                continue
            filepath = os.path.join(folder, filename)
            try:
                mtime     = os.path.getmtime(filepath)
                size      = os.path.getsize(filepath)
                all_clips.append((mtime, filepath, size))
            except Exception:
                pass  # Fichier inaccessible → on l'ignore

    if not all_clips:
        print("[PURGE URGENCE] ⚠️  Aucun clip à supprimer — impossible de libérer de l'espace.")
        return False

    # Trier du plus ancien (index 0) au plus récent (index -1)
    all_clips.sort(key=lambda x: x[0])

    # Calculer combien de clips supprimer : 30% arrondis au supérieur
    n_to_delete = math.ceil(len(all_clips) * 0.30)
    clips_to_delete = all_clips[:n_to_delete]

    total_freed = 0
    total_deleted = 0
    for mtime, filepath, size in clips_to_delete:
        try:
            os.remove(filepath)
            total_freed   += size
            total_deleted += 1
            if DEBUG_LOGS:
                age_days = (time.time() - mtime) / 86400
                print(
                    f"[PURGE URGENCE] 🗑️  {filepath} "
                    f"({size / 1024 / 1024:.1f} Mo, âge={age_days:.1f}j)"
                )
        except Exception as e:
            print(f"[PURGE URGENCE] ⚠️  Impossible de supprimer {filepath} : {e}")

    print(
        f"[PURGE URGENCE] ✅ {total_deleted}/{len(all_clips)} clip(s) supprimé(s), "
        f"{total_freed / 1024 / 1024:.0f} Mo libérés."
    )

    # Vérifier si l'espace est maintenant suffisant
    free_after = _get_free_gb(alert_vid_dir)
    if free_after >= DISK_MIN_FREE_GB:
        print(f"[PURGE URGENCE] ✅ Espace suffisant après purge : {free_after:.2f} Go libres.")
        return True
    else:
        print(
            f"[PURGE URGENCE] ❌ Espace toujours insuffisant après purge : "
            f"{free_after:.2f} Go libres (minimum : {DISK_MIN_FREE_GB} Go). "
            f"Enregistrement annulé."
        )
        return False


def check_disk_space(path: str = ".") -> bool:
    """
    Vérifie qu'il reste au moins DISK_MIN_FREE_GB Go libres avant un enregistrement.

    COMPORTEMENT EN CAS D'ESPACE INSUFFISANT :
    Au lieu de refuser immédiatement l'enregistrement, on tente d'abord de
    libérer de l'espace via emergency_free_space() qui supprime 30% des clips
    les plus anciens. Si après cette purge d'urgence l'espace est suffisant,
    l'enregistrement peut se faire normalement. Sinon seulement, on refuse.

    POURQUOI CETTE APPROCHE :
    Bloquer un enregistrement parce que le disque est plein peut faire rater
    une vraie alerte de vol. Il vaut mieux supprimer de vieux clips (déjà
    traités par l'agent) que de perdre une nouvelle preuve vidéo.
    Les métadonnées dans alerts.jsonl sont conservées même si le clip est supprimé.

    Retourne True si l'enregistrement peut se faire, False sinon.
    """
    free_gb = _get_free_gb(path)

    if free_gb < 0:
        # Erreur de lecture → on autorise par défaut pour ne pas bloquer
        return True

    if free_gb >= DISK_MIN_FREE_GB:
        # Espace suffisant → enregistrement autorisé directement
        return True

    # Espace insuffisant → tentative de libération automatique
    print(
        f"⚠️  ESPACE DISQUE BAS : {free_gb:.2f} Go libres "
        f"(minimum : {DISK_MIN_FREE_GB} Go). "
        f"Tentative de libération automatique (30% des clips les plus anciens)..."
    )
    return emergency_free_space()


def purge_old_clips():
    """
    Supprime les clips vidéo (annotés ET raw) plus vieux que CLIP_RETENTION_DAYS jours.
    Appelée dans un thread dédié toutes les PURGE_INTERVAL_SECS secondes.

    DOSSIERS PURGÉS :
      - alert_clips/          (clips annotés avec les boîtes de détection)
      - alert_clips/raw/      (clips bruts sans annotation)

    Les fichiers .jsonl ne sont PAS supprimés : ils sont légers (quelques Ko
    par alerte) et servent de trace d'audit. Les purger séparément si besoin.

    THREAD-SAFETY : la suppression de fichiers est atomique au niveau OS.
    Pas de verrou nécessaire car FFmpeg a fermé le fichier avant que la purge
    ne puisse le toucher (les fichiers récents sont protégés par le filtre d'âge).
    """
    cutoff = time.time() - (CLIP_RETENTION_DAYS * 86400)  # timestamp limite

    dirs_to_purge = [alert_vid_dir, raw_dir]
    total_deleted = 0
    total_freed   = 0  # en octets

    for folder in dirs_to_purge:
        if not os.path.isdir(folder):
            continue
        for filename in os.listdir(folder):
            if not filename.endswith(".mp4"):
                continue
            filepath = os.path.join(folder, filename)
            try:
                file_mtime = os.path.getmtime(filepath)
                if file_mtime < cutoff:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    total_deleted += 1
                    total_freed   += file_size
                    if DEBUG_LOGS:
                        print(f"[PURGE] 🗑️  Supprimé : {filepath} ({file_size / 1024 / 1024:.1f} Mo)")
            except Exception as e:
                print(f"[PURGE] ⚠️  Impossible de supprimer {filepath} : {e}")

    if total_deleted > 0:
        print(
            f"[PURGE] ✅ {total_deleted} clip(s) supprimé(s), "
            f"{total_freed / 1024 / 1024:.1f} Mo libérés "
            f"(rétention : {CLIP_RETENTION_DAYS} jours)."
        )


def purge_worker():
    """
    Thread de purge automatique des clips.
    Tourne indéfiniment, lance une passe de purge toutes les PURGE_INTERVAL_SECS secondes.
    Démarre immédiatement par une première passe au lancement (utile après un redémarrage).
    """
    print(f"[PURGE] Thread de purge démarré (rétention : {CLIP_RETENTION_DAYS} jours, "
          f"intervalle : {PURGE_INTERVAL_SECS // 3600}h).")
    while True:
        purge_old_clips()
        time.sleep(PURGE_INTERVAL_SECS)


# ==========================================
# THREAD GPU CENTRALISÉ — INFÉRENCE EN BATCH
# ==========================================
def gpu_batch_worker():
    """
    Thread unique qui gère TOUTES les inférences YOLO pour toutes les caméras.

    FONCTIONNEMENT EN 5 ÉTAPES :
    1. Collecte les frames de batch_input_queue jusqu'à avoir un batch plein
       OU jusqu'à l'expiration du timeout BATCH_TIMEOUT_SECS.
    2. Fait UNE inférence Radar en batch sur toutes les frames collectées.
    3. Pour chaque personne détectée, extrait le crop et accumule un batch.
    4. Fait UNE inférence Spécialiste en batch sur tous les crops.
    5. Redistribue les résultats dans result_queues[cam_id].

    GAINS SUR QUADRO P2200 :
    L'overhead CUDA (initialisation du kernel) est fixe par appel : ~5-15ms.
    Avant : 7 appels × (overhead + inférence) ≈ 7 × 80ms = 560ms/cycle → 1.8 FPS
    Après : 1 appel batch de 7 frames ≈ 110ms/cycle → 7 FPS par caméra
    """
    n_cameras = len(CAMERAS)

    while True:
        # ── Étape 1 : collecte du batch ──────────────────────────────────
        # On attend d'avoir soit un batch complet, soit le timeout écoulé.
        # dict plutôt que liste : si une caméra est rapide et envoie 2 frames
        # avant que le batch parte, on garde seulement la plus récente.
        batch    = {}
        deadline = time.time() + BATCH_TIMEOUT_SECS

        while time.time() < deadline:   # on attend toujours la deadline complète
            try:
                timeout_left = max(0.001, deadline - time.time())
                cam_id, frame = batch_input_queue.get(timeout=timeout_left)
                batch[cam_id] = frame
                # Sans cette condition, la boucle attendait TOUJOURS jusqu'à la deadline
                # complète (100ms), même si les 7 caméras avaient déjà toutes déposé
                # leur frame après 30ms par exemple.
                # Résultat sans ce correctif : 70ms d'attente inutile à chaque cycle,
                # soit ~40% du temps CPU du thread GPU passé à ne rien faire.
                if len(batch) >= len(CAMERAS):  # batch complet → on n'attend plus
                    break
            except queue.Empty:
                break   # Timeout → on envoie le batch partiel

        if not batch:
            time.sleep(0.005)
            continue

        cam_ids = list(batch.keys())
        frames  = [batch[c] for c in cam_ids]

        # ── Étape 2+3 : Radar 1 appel/caméra + extraction des crops ─────────
        # CORRECTIF : on ne passe PAS un batch multi-caméras à .track().
        # ByteTracker avec persist=True traite un batch comme un flux mono-caméra
        # continu : les IDs se mélangent entre caméras, et les nouvelles personnes
        # qui entrent dans le champ sont ignorées (tracker les voit comme du bruit
        # face aux IDs qu'il connaît déjà).
        # Solution : 1 appel .track() par caméra → chaque caméra a son propre
        # état ByteTracker isolé. Le spécialiste (étape 4) reste en batch complet.

        all_crops  = []   # Tous les crops (toutes caméras confondues)
        radar_data = {}   # { cam_id → liste de dicts personne }

        for i, cam_id in enumerate(cam_ids):
            frame = frames[i]
            h, w  = frame.shape[:2]
            persons_this_cam = []

            try:
                result = model_radar.predict(
                    frame,
                    verbose=False,
                    conf=0.15,
                    imgsz=640,
                    # Sans half=True ici, YOLO reconvertit les tenseurs en FP32 à chaque appel
                    # avant l'inférence, ce qui annule complètement le gain du model.half()
                    # et ajoute même une conversion inutile à chaque frame.
                    half=True,
                )[0]
            except Exception as e:
                print(f"[GPU] ❌ Erreur inférence Radar {cam_id} : {e}")
                radar_data[cam_id] = persons_this_cam
                continue

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

                padding = 20

                x1p = max(0, x1 - padding)
                y1p = max(0, y1 - padding)
                x2p = min(w,  x2 + padding)
                y2p = min(h,  y2 + padding)

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

        # ── Étape 4 : inférence Spécialiste en batch ─────────────────────
        # Un seul appel YOLO sur TOUS les crops de TOUTES les caméras.
        spec_by_idx = {}
        if all_crops:
            try:
                spec_results = model_specialist.predict(
                    all_crops, 
                    verbose=False, 
                    conf=0.15,
                    # Même raisonnement que pour le radar : half=True doit accompagner
                    # model.half() sinon la conversion FP16→FP32→FP16 se fait à chaque
                    # batch de crops, ce qui est plus lent que de rester en FP32 pur.
                    half=True,
                )
                for idx, res in enumerate(spec_results):
                    spec_by_idx[idx] = res
            except Exception as e:
                print(f"[GPU] ❌ Erreur inférence Spécialiste : {e}")

        # ── Étape 5 : distribution des résultats ─────────────────────────
        for cam_id in cam_ids:
            persons = radar_data.get(cam_id, [])
            for p in persons:
                p["spec_result"] = spec_by_idx.get(p["crop_idx"])
            _put_result(cam_id, persons)


def _put_result(cam_id: str, persons: list):
    """
    Pousse les résultats d'inférence dans la queue de la caméra.
    Si la queue est pleine (worker lent), on écrase le vieux résultat.
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
        rq.put_nowait(persons)
    except queue.Full:
        pass


# ==========================================
# CLASSE FFmpegReader — THREAD DE LECTURE RTSP DÉDIÉ
# ==========================================
class FFmpegReader:
    """
    Thread indépendant qui lit le flux RTSP via FFmpeg EN CONTINU
    et ne conserve que la dernière frame dans une queue de taille 1.

    POURQUOI CETTE CLASSE EXISTE :
    Avant, la lecture FFmpeg et l'analyse YOLO se faisaient dans la même boucle.
    YOLO prend ~80-200ms par frame. FFmpeg produit une frame toutes les ~83ms.
    Résultat : FFmpeg remplissait son buffer, se bloquait, freeze total.

    SOLUTION : ce thread lit FFmpeg aussi vite que possible et met chaque frame
    dans une queue de taille 1. Si le worker est lent, les vieilles frames sont
    écrasées. Le flux reste toujours en temps réel.

    RECONNEXION AUTOMATIQUE : si FFmpeg plante, le thread attend 3s et relance.
    """

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
        # bufsize doit être > taille d'une frame (704×576×3 = 1.16 Mo)
        # sinon read_exactly() fait des dizaines de read() par frame → latence croissante
        self._bufsize    = self.frame_size * 10

    def _start_ffmpeg(self):
        return subprocess.Popen(
            [
                "ffmpeg",
                "-loglevel",       "warning",
                "-rtsp_flags",     "prefer_tcp",
                "-rtsp_transport", "tcp",
                "-timeout",        "10000000",
                "-max_delay",      "500000",
                "-i",              self.rtsp_url,
                "-vf",             f"scale={self.width}:{self.height}",
                "-f",              "image2pipe",
                "-pix_fmt",        "bgr24",
                "-vcodec",         "rawvideo",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=self._bufsize,
        )

    def run(self):
        """Boucle principale du thread de lecture. Relance FFmpeg automatiquement."""
        import select

        while not self._stop_event.is_set():
            print(f"[{self.cam_id}] Connexion au flux RTSP...")
            self._process = self._start_ffmpeg()

            # ── WATCHDOG ──────────────────────────────────────────────────
            # Surveille l'arrivée des frames. Si aucune frame n'arrive depuis
            # 5s, tue FFmpeg proactivement avant qu'il ne freeze.
            # Les arguments sont passés explicitement pour éviter les problèmes
            # de capture par référence (chaque watchdog est lié à SA session).
            last_frame_time = [time.time()]
            stop_watchdog   = threading.Event()

            def watchdog(last_frame_time=last_frame_time, stop_watchdog=stop_watchdog):
                while not stop_watchdog.is_set():
                    time.sleep(1)
                    if stop_watchdog.is_set():
                        break
                    if time.time() - last_frame_time[0] > 5:
                        print(f"[{self.cam_id}] Watchdog : aucune frame depuis 5s, relance FFmpeg")
                        self.is_reconnecting = True
                        try:
                            self._process.kill()
                        except Exception:
                            pass
                        return

            threading.Thread(target=watchdog, daemon=True, name=f"{self.cam_id}_watchdog").start()

            # Drainage stderr (évite le freeze du pipe stdout)
            stderr_drain_thread = threading.Thread(
                target=drain_stderr,
                args=(self._process, self.cam_id, self._stop_event),
                daemon=True,
                name=f"{self.cam_id}_stderr_drain",
            )
            stderr_drain_thread.start()

            # Vide la queue avant de lire le nouveau processus
            # pour ne pas propager une frame de l'ancienne session FFmpeg
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass

            try:
                while not self._stop_event.is_set():
                    # select() avec timeout 2s : évite que read() bloque indéfiniment
                    # → permet au watchdog d'agir dès que la caméra freeze
                    ready = select.select([self._process.stdout], [], [], 2.0)[0]
                    if not ready:
                        continue

                    raw_bytes = read_exactly(self._process.stdout, self.frame_size)

                    if raw_bytes is None:
                        print(f"[{self.cam_id}] ⚠️ Flux interrompu (pipe fermé par FFmpeg)")
                        break

                    last_frame_time[0]   = time.time()
                    self.is_reconnecting = False

                    # Queue de taille 1 : écrase l'ancienne frame si le worker est lent
                    if self.queue.full():
                        try:
                            self.queue.get_nowait()
                        except queue.Empty:
                            pass
                    self.queue.put(raw_bytes)

            except Exception as e:
                print(f"[{self.cam_id}] 💥 Exception dans FFmpegReader : {e}")

            finally:
                # CRUCIAL : arrêter le watchdog AVANT de tuer FFmpeg
                stop_watchdog.set()
                try:
                    self._process.kill()
                    self._process.wait(timeout=3)
                except Exception:
                    pass

            if not self._stop_event.is_set():
                print(f"[{self.cam_id}] 🔄 Reconnexion dans 3 secondes...")
                time.sleep(3)

        print(f"[{self.cam_id}] FFmpegReader arrêté.")

    def get_frame(self, timeout=2.0):
        """Récupère la dernière frame disponible. Bloquant jusqu'à timeout secondes."""
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
      1. Dépose chaque frame dans batch_input_queue pour le thread GPU
      2. Récupère les résultats depuis result_queues[cam_id]
      3. Tracking des articles avec tolérance aux ratés
      4. Logique de détection de vol (3 scénarios)
      5. Déclenchement des alertes et enregistrement vidéo
      6. Publication des frames annotées pour Flask

    Le worker ne touche PAS au GPU directement.
    Toute l'inférence est déléguée à gpu_batch_worker via les queues.
    """

    def __init__(self, cam_id: str, rtsp_url: str, width: int, height: int, fps: int):
        self.cam_id           = cam_id
        self.rtsp_url         = rtsp_url
        self.width            = width
        self.height           = height
        self.fps              = fps
        self.frames_processed = 0

        # Verrou d'écriture stdin FFmpeg pour l'enregistrement
        self._record_stdin_lock = threading.Lock()
        self._pre_alert_done    = threading.Event()

        # Verrou dédié pour _active_record_procs
        # Évite la race condition entre le worker (append) et cleanup() (iteration)
        self._record_procs_lock   = threading.Lock()
        self._active_record_procs = []

        # ------------------------------------------------------------------
        # MINI-TRACKER SPATIAL D'ARTICLES ET DE PERSONNES
        # ------------------------------------------------------------------
        self.next_article_id       = 0
        self.active_article_tracks = {}

        self.next_person_id = 0
        self.active_person_tracks = {}

        # Historique des positions de chaque article (pour corrélation mouvement)
        self.article_position_history: dict = {}

        # Historique des positions des personnes (pour corrélation mouvement)
        self.person_position_history: dict = {}

        # Buffer glissant des positions de mains (vérification contact avant disparition)
        self.hands_history: deque = deque(maxlen=HAND_MEMORY_FRAMES)

        # Streak CONTINU "tenu" : nb de frames consécutives où l'article se déplace avec la personne
        self.hold_streak: dict = {}

        # Compteur de frames où l'article n'est PAS détecté "tenu" (tolérance streak)
        # Permet d'absorber les oscillations naturelles de marche sans casser le streak
        self.hold_streak_miss: dict = {}   # { article_id → nb frames manquantes consécutives }

        # CORRECTIF v8 : compteur PRIMAIRE de frames consécutives détectées dans la bbox.
        # Remplace le streak de corrélation comme chemin principal.
        self.article_consecutive_frames: dict = {}  # { article_id → nb frames consécutives }

        # CORRECTIF v8 : tolérance aux trous dans la détection consécutive.
        # Évite que 1-2 frames YOLO ratées réinitialisent le compteur à 0.
        self.article_consecutive_miss: dict = {}    # { article_id → nb frames manquantes }

        # ── [FILTRE A] v9 : historique de confiance par article ───────────────
        # Fenêtre glissante (HOLD_CONF_HISTORY_LEN frames) de la confiance YOLO
        # pour chaque article. La moyenne doit dépasser HOLD_CONF_MIN pour que
        # l'article soit considéré "tenu". Filtre les faux positifs à conf basse
        # sans toucher au seuil de détection brut (0.22 conservé).
        self.article_conf_history: dict = {}  # { article_id → deque de float }


        # SCÉNARIO SAC : suivi de la séquence proximité → disparition
        # { article_id → {"frames_near_bag": int, "bag_center": tuple, "p_id": int} }
        self.article_near_bag: dict = {}
        self.static_bag_cache: dict = {}   # { (gx, gy) → {"count": int, "center": tuple} }


        # ------------------------------------------------------------------
        # MÉMOIRE DE LA LOGIQUE DE VOL
        # ------------------------------------------------------------------
        self.suspect_disappearance   = {}   # { article_id → {start_time, last_score, hold_frames, p_id} }
        self.last_known_articles     = {}   # { article_id → (cx, cy) dernière position connue }
        self.object_hold_counter     = {}   # { "article_X" → nb de frames où l'objet est "tenu" }
        self.last_known_scores       = {}   # { article_id → score de confiance YOLO }
        self.hold_durations          = {}   # { article_id → nb de frames cumulées "tenu" }
        self.last_known_person_boxes = {}   # { person_id  → [x1, y1, x2, y2] dernière boîte }
        self.person_last_seen        = {}   # { person_id  → timestamp de la dernière détection }
        self.person_tracking         = {}   # { person_id  → {first_seen, last_seen} }

        # CORRECTIF v8 : _suspicion_logged est maintenant un dict par article,
        # pas un bool global. Évite qu'un 2e article ne perde sa notification.
        self._suspicion_logged: dict = {}  # { article_id → bool }

        # ------------------------------------------------------------------
        # GESTION DES ALERTES
        # ------------------------------------------------------------------
        self.last_alert_time    = 0    # timestamp global, conservé pour l'UI (texte clignotant)

        # ── [FILTRE E] v9 : cooldown PAR article_id ───────────────────────────
        # Avant v9 : last_alert_time était GLOBAL par caméra → si une alerte
        # se déclenchait sur l'article A, l'article B était silencé pendant
        # ALERT_COOLDOWN secondes, même s'il s'agissait d'un vol indépendant.
        # Maintenant : chaque article a son propre timestamp de dernière alerte.
        # → 2 vols simultanés sur la même caméra = 2 alertes correctement levées.
        self.article_alert_time: dict = {}  # { article_id → timestamp dernière alerte }

        self.alert_text_to_show = ""
        self.alert_text_timer   = 0

        # ------------------------------------------------------------------
        # GESTION DES CLIPS VIDÉO
        # ------------------------------------------------------------------
        self.is_recording_alert     = False
        self.alert_ffmpeg_process   = None
        self.raw_ffmpeg_process     = None
        self.frames_to_record_after = 0
        self.zoom_target_id         = None
        self.smooth_center          = None

        buf_size              = int(BEFORE_ALERT_SECS * fps)
        self.video_buffer     = deque(maxlen=buf_size)
        self.video_buffer_raw = deque(maxlen=buf_size)


    # ======================================================================
    # DISTANCE DE MATCHING DYNAMIQUE DANS LE MINI-TRACKER
    # ======================================================================
    def _get_adaptive_max_distance(self, article_center):
        """
        Calcule la distance maximale de matching du mini-tracker en fonction
        de la taille apparente de la personne la plus proche de l'article.

        POURQUOI :
        Avec 60px fixe, un article porté par quelqu'un proche de la caméra
        (bbox large → grand déplacement en pixels par frame) perdait son ID.
        Résultat : hold_durations réinitialisé → détection de vol ratée.

        LOGIQUE : hauteur bbox personne ≈ proxy de sa distance à la caméra.
        On prend 15% de cette hauteur comme seuil de matching.
        """
        best_dist    = float("inf")
        adaptive_dist = 80   # Valeur par défaut si aucune personne visible

        for p_id, p_box in self.last_known_person_boxes.items():
            if time.time() - self.person_last_seen.get(p_id, 0) > 5.0:
                continue  # Ignore les boîtes obsolètes (personne partie depuis +5s)

            p_cx = (p_box[0] + p_box[2]) / 2
            p_cy = (p_box[1] + p_box[3]) / 2
            dist = math.hypot(article_center[0] - p_cx, article_center[1] - p_cy)

            if dist < best_dist:
                best_dist     = dist
                person_height = p_box[3] - p_box[1]
                adaptive_dist = max(40, int(person_height * 0.15))

        return adaptive_dist


    # ======================================================================
    # CORRÉLATION MOUVEMENT ARTICLE/PERSONNE
    # ======================================================================
    def _is_article_moving_with_person(self, article_id, person_id):
        """
        Vérifie si l'article se déplace dans la même direction que la personne
        sur les MOVEMENT_HISTORY_FRAMES dernières frames (cosine similarity).

        Retourne False si l'article est fixe et la personne en mouvement
        → article sur étagère, pas dans la main.
        Retourne True si même direction (correlation ≥ 0.6) ou si pas assez
        d'historique (bénéfice du doute pour éviter les faux négatifs).

        NOTE v8 : ce chemin est maintenant SECONDAIRE.
        Le chemin primaire est la détection consécutive par le spécialiste.
        """
        a_hist = self.article_position_history.get(article_id)
        p_hist = self.person_position_history.get(person_id)

        if not a_hist or not p_hist or len(a_hist) < 3 or len(p_hist) < 3:
            return True  # Pas assez d'historique → bénéfice du doute

        a_dx  = a_hist[-1][0] - a_hist[0][0]
        a_dy  = a_hist[-1][1] - a_hist[0][1]
        a_mag = math.hypot(a_dx, a_dy)

        p_dx  = p_hist[-1][0] - p_hist[0][0]
        p_dy  = p_hist[-1][1] - p_hist[0][1]
        p_mag = math.hypot(p_dx, p_dy)

        if p_mag < 3:
            return True   # Personne immobile → peut cacher l'article sur place → on accepte

        if a_mag < 3 and p_mag > 8:
            return False  # Article fixe, personne qui marche → sur étagère

        dot = (a_dx * p_dx + a_dy * p_dy)
        correlation = dot / (a_mag * p_mag) if (a_mag * p_mag) > 0 else 0
        return correlation >= MOVEMENT_CORRELATION_MIN


    # ======================================================================
    # VÉRIFICATION CONTACT MAIN / ARTICLE
    # ======================================================================
    def _was_hand_near_article(self, article_center):
        """
        Vérifie si une main a été détectée à moins de HAND_ARTICLE_DIST pixels
        de l'article dans les 2 dernières secondes (HAND_MEMORY_FRAMES frames).

        C'est la protection principale contre les faux positifs de vol corporel :
        sans contact de main confirmé → l'occlusion est ignorée, pas de suspicion.
        """
        for frame_hands in self.hands_history:
            for hand_center in frame_hands:
                if math.hypot(
                    article_center[0] - hand_center[0],
                    article_center[1] - hand_center[1]
                ) < HAND_ARTICLE_DIST:
                    return True
        return False


    # ======================================================================
    # MINI-TRACKER SPATIAL D'ARTICLES (AVEC TOLÉRANCE AUX RATÉS)
    # ======================================================================
    def _track_articles_custom(self, current_articles_centers):
        """
        Attribue un ID stable aux articles détectés d'une frame à l'autre,
        avec une tolérance aux ratés YOLO (TRACKER_MISS_TOLERANCE frames).
        Distance de matching adaptée à la taille apparente de la personne.

        [FILTRE A — v9] : initialise aussi article_conf_history pour les
        nouveaux articles et met à jour la confiance pour les articles existants.
        La mise à jour de conf_history se fait ici car c'est le point central
        où chaque article est identifié avec sa confiance YOLO courante.
        """
        new_tracks = {}
        tracked    = []
        remaining  = dict(self.active_article_tracks)

        for (center, conf) in current_articles_centers:
            adaptive_dist = self._get_adaptive_max_distance(center)
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

            if best_id is not None:
                new_tracks[best_id] = {"center": center, "miss": 0}
                tracked.append((center, best_id, conf))
                del remaining[best_id]

                if best_id not in self.article_position_history:
                    self.article_position_history[best_id] = deque(maxlen=MOVEMENT_HISTORY_FRAMES)
                self.article_position_history[best_id].append(center)

                # [FILTRE A — v9] Mise à jour de l'historique de confiance
                # pour un article EXISTANT. La deque est bornée à HOLD_CONF_HISTORY_LEN
                # → fenêtre glissante : les vieilles confs sortent automatiquement.
                if best_id not in self.article_conf_history:
                    self.article_conf_history[best_id] = deque(maxlen=HOLD_CONF_HISTORY_LEN)
                self.article_conf_history[best_id].append(conf)

            else:
                # Nouvel article → nouvel ID
                new_id = self.next_article_id
                self.next_article_id += 1
                new_tracks[new_id] = {"center": center, "miss": 0}
                tracked.append((center, new_id, conf))
                self.article_position_history[new_id] = deque(maxlen=MOVEMENT_HISTORY_FRAMES)
                self.article_position_history[new_id].append(center)

                # [FILTRE A — v9] Initialisation de l'historique de confiance
                # pour un NOUVEL article. Première conf enregistrée.
                self.article_conf_history[new_id] = deque(maxlen=HOLD_CONF_HISTORY_LEN)
                self.article_conf_history[new_id].append(conf)

        # Tracks non matchés : incrémente le compteur de ratés ou supprime
        for a_id, track_data in remaining.items():
            miss_count = track_data["miss"] + 1
            if miss_count <= TRACKER_MISS_TOLERANCE:
                new_tracks[a_id] = {"center": track_data["center"], "miss": miss_count}

        self.active_article_tracks = new_tracks
        return tracked


    # ==================================
    # MINI-TRACKER SPATIAL DE PERSONNES 
    # ==================================
    def _track_persons_custom(self, detections):
        new_tracks = {}
        tracked = []

        remaining = dict(self.active_person_tracks)

        for box in detections:
            x1, y1, x2, y2 = map(int, box)

            center = (
                (x1 + x2) // 2,
                (y1 + y2) // 2
            )

            best_id = None
            best_dist = 120

            for p_id, old_center in remaining.items():
                dist = math.hypot(
                    center[0] - old_center[0],
                    center[1] - old_center[1]
                )

                if dist < best_dist:
                    best_dist = dist
                    best_id = p_id

            if best_id is not None:
                new_tracks[best_id] = center
                tracked.append((best_id, box))
                del remaining[best_id]

            else:
                new_id = self.next_person_id
                self.next_person_id += 1

                new_tracks[new_id] = center
                tracked.append((new_id, box))

        self.active_person_tracks = new_tracks

        return tracked


    # ======================================================================
    # DÉCLENCHEMENT DE L'ENREGISTREMENT VIDÉO
    # ======================================================================
    def _start_alert_video(self, type_vol: str, score: float):
        """
        Initialise l'enregistrement d'un clip d'alerte via NVENC (GPU).
        Écrit l'alerte dans alerts.jsonl (append-only, O(1)).

        Vérifie l'espace disque disponible avant de lancer FFmpeg.
        Si l'espace est insuffisant, l'alerte est quand même loggée dans
        alerts.jsonl (avec video_clip=None) mais aucun clip n'est enregistré.
        """
        # ── Vérification espace disque AVANT de lancer FFmpeg ────────────
        # Sans cette vérification, FFmpeg démarre sans erreur visible mais
        # échoue silencieusement → fichier MP4 corrompu ou vide.
        if not check_disk_space(alert_vid_dir):
            # Pas assez de place : on logue l'alerte sans clip vidéo
            append_alert_jsonl({
                "cam":        self.cam_id,
                "type":       type_vol,
                "score":      round(score, 3),
                "status":     "alerte",
                "time":       datetime.now().strftime("%H:%M:%S"),
                "video_clip": None,
                "video_raw":  None,
                "note":       f"Enregistrement annulé : espace disque < {DISK_MIN_FREE_GB} Go",
            })
            return None

        timestamp = datetime.now().strftime("%H%M%S")
        vid_path  = os.path.abspath(
            os.path.join(alert_vid_dir, f"{self.cam_id}_Vole_{type_vol}_{timestamp}.mp4")
        )
        raw_path  = os.path.abspath(
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
                "-vf",      "format=yuv420p",
                "-vcodec",  "h264_nvenc",
                "-preset",  "p1",
                "-b:v",     "1M",
                path,
            ]

        self.alert_ffmpeg_process = subprocess.Popen(
            get_cmd(vid_path), stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.raw_ffmpeg_process = subprocess.Popen(
            get_cmd(raw_path), stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )

        # Snapshot immédiat du buffer pré-alerte pour éviter la race condition
        buffer_snapshot     = list(self.video_buffer)
        buffer_raw_snapshot = list(self.video_buffer_raw)

        # Accès à _active_record_procs sous verrou dédié
        with self._record_procs_lock:
            self._active_record_procs = [
                p for p in self._active_record_procs
                if p is not None and p.poll() is None
            ]
            for p in [self.alert_ffmpeg_process, self.raw_ffmpeg_process]:
                if p is not None:
                    self._active_record_procs.append(p)

        def write_pre_alert_buffer():
            # Attend 200ms que NVENC s'initialise (évite le BrokenPipeError)
            time.sleep(0.2)
            with self._record_stdin_lock:
                try:
                    for f in buffer_snapshot:
                        if self.alert_ffmpeg_process and self.alert_ffmpeg_process.stdin:
                            self.alert_ffmpeg_process.stdin.write(f.tobytes())
                except Exception as e:
                    print(f"[{self.cam_id}] ⚠️ Erreur écriture buffer annoté (pré-alerte) : {e}")
                    try:
                        self.alert_ffmpeg_process.kill()
                    except Exception:
                        pass
                    self.alert_ffmpeg_process = None

                try:
                    for f in buffer_raw_snapshot:
                        if self.raw_ffmpeg_process and self.raw_ffmpeg_process.stdin:
                            self.raw_ffmpeg_process.stdin.write(f.tobytes())
                except Exception as e:
                    print(f"[{self.cam_id}] ⚠️ Erreur écriture buffer brut (pré-alerte) : {e}")
                    try:
                        self.raw_ffmpeg_process.kill()
                    except Exception:
                        pass
                    self.raw_ffmpeg_process = None

            self._pre_alert_done.set()

        self._pre_alert_done.clear()
        threading.Thread(
            target=write_pre_alert_buffer,
            daemon=True,
            name=f"{self.cam_id}_pre_alert_writer",
        ).start()

        self.is_recording_alert     = True
        self.frames_to_record_after = int(AFTER_ALERT_SECS * self.fps)

        # Écriture JSONL : append-only, O(1), pas de relecture du fichier entier
        append_alert_jsonl({
            "cam":        self.cam_id,
            "type":       type_vol,
            "score":      round(score, 3),
            "status":     "alerte",
            "time":       datetime.now().strftime("%H:%M:%S"),
            "video_clip": vid_path,
            "video_raw":  raw_path,
        })

        return vid_path


    # ======================================================================
    # NOTIFICATION DE SUSPICION EN MÉMOIRE
    # ======================================================================
    def _notify_suspicion(self, article_id: int, type_vol: str, score: float):
        """
        Enregistre une suspicion EN MÉMOIRE uniquement (zéro écriture disque).
        L'interface web récupère ces suspicions via GET /suspicions.

        Une suspicion expire automatiquement après SUSPICION_TTL secondes,
        ou est effacée immédiatement si une alerte formelle est levée.

        CORRECTIF v8 : prend article_id en paramètre pour gérer
        plusieurs articles en suspicion simultanément par caméra.
        """
        with suspicions_lock:
            active_suspicions[self.cam_id] = {
                "time":       datetime.now().strftime("%H:%M:%S"),
                "score":      round(score, 2),
                "type":       type_vol,
                "expires_at": time.time() + SUSPICION_TTL,
            }
        print(f"[{self.cam_id}] 👁 SUSPICION article {article_id} : VOL {type_vol} possible (score={score:.2f})")
        self._suspicion_logged[article_id] = True

    def _clear_suspicion(self, article_id: int = None):
        """
        Efface la suspicion active pour cette caméra.
        Appelée lors d'une alerte formelle (la suspicion devient inutile)
        ou quand l'article réapparaît (fausse alarme confirmée).

        CORRECTIF v8 : prend article_id pour ne réinitialiser que le flag
        de l'article concerné, pas tous les articles.
        """
        with suspicions_lock:
            active_suspicions.pop(self.cam_id, None)
        if article_id is not None:
            self._suspicion_logged.pop(article_id, None)
        else:
            self._suspicion_logged.clear()


    # ======================================================================
    # ZOOM LISSÉ SUR LE SUSPECT
    # ======================================================================
    def _smooth_position(self, new_center, alpha=0.35):
        """Lisse la position du centre du zoom par interpolation exponentielle (EWMA)."""
        if self.smooth_center is None:
            self.smooth_center = new_center
        else:
            self.smooth_center = (
                int(self.smooth_center[0] * (1 - alpha) + new_center[0] * alpha),
                int(self.smooth_center[1] * (1 - alpha) + new_center[1] * alpha),
            )
        return self.smooth_center

    def _zoom_tracking(self, frame, box):
        """Recadre et agrandit l'image pour zoomer sur la personne suspecte."""
        h, w   = frame.shape[:2]
        x1, y1, x2, y2 = map(int, box)
        cx, cy = self._smooth_position(((x1 + x2) // 2, (y1 + y2) // 2))
        
        # Zoom proportionnel à la taille de la personne
        person_w = x2 - x1
        person_h = y2 - y1
        new_w  = min(int(person_w + 130), w)
        new_h  = min(int(person_h + 130), h)
        
        # CORRECTION : forcer les indices en int pour éviter le TypeError
        cx1 = int(max(0, cx - new_w // 2))
        cy1 = int(max(0, cy - new_h // 2))
        cx2 = int(min(w, cx + new_w // 2))
        cy2 = int(min(h, cy + new_h // 2))
        
        # Sécurité : si le crop est vide (cas extrême), retourner la frame originale
        if cx2 <= cx1 or cy2 <= cy1:
            return frame
        
        return cv2.resize(frame[cy1:cy2, cx1:cx2], (w, h), interpolation=cv2.INTER_LINEAR)


    # ======================================================================
    # NETTOYAGE
    # ======================================================================
    def cleanup(self):
        """Ferme proprement tous les processus FFmpeg. Appelée par SIGINT (Ctrl+C)."""
        print(f"[{self.cam_id}] Fermeture propre des enregistrements en cours...")
        with self._record_procs_lock:
            procs = list(self._active_record_procs)

        for proc in procs:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        with self._record_procs_lock:
            self._active_record_procs.clear()


    # ======================================================================
    # BOUCLE PRINCIPALE : TRAITEMENT FRAME PAR FRAME
    # ======================================================================
    def run(self, reader: "FFmpegReader"):
        """
        Boucle infinie dans un thread dédié pour cette caméra.

        Étapes :
        1. Récupère la frame depuis FFmpegReader
        2. La dépose dans batch_input_queue pour le thread GPU centralisé
        3. Récupère les résultats depuis result_queues[cam_id]
        4. Applique la logique métier (tracking, détection, alertes)

        Le worker n'appelle PAS les modèles YOLO directement.
        """
        print(f"[{self.cam_id}] Worker démarré.")
    
        while True:
            # ==========================================
            # ÉTAPE 0 : RÉCUPÉRATION DE LA FRAME
            # ==========================================
            raw_bytes = reader.get_frame(timeout=2.0)
            if raw_bytes is None:
                # Overlay de reconnexion si le flux est coupé
                if reader.is_reconnecting:
                    with frame_lock:
                        last_frame = output_frames.get(self.cam_id)
                    if last_frame is not None:
                        overlay    = cv2.GaussianBlur(last_frame, (31, 31), 0)
                        band_y1    = self.height // 2 - 45
                        band_y2    = self.height // 2 + 45
                        roi        = overlay[band_y1:band_y2, 0:self.width]
                        black_band = np.zeros_like(roi)
                        overlay[band_y1:band_y2, 0:self.width] = cv2.addWeighted(roi, 0.35, black_band, 0.65, 0)
                        msg1 = "  Perte de la connexion RTSP"
                        font = cv2.FONT_HERSHEY_SIMPLEX
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
                print(f"[{self.cam_id}] ⚠️ Frame corrompue ignorée ({len(raw_bytes)} octets) : {e}")
                continue

            clean_frame     = frame.copy()
            annotated_frame = frame.copy()
            current_time    = time.time()
            self.frames_processed += 1

            # ==========================================
            # ÉTAPE 1 : DÉPÔT VERS LE THREAD GPU
            # ==========================================
            # On dépose (cam_id, frame) dans la queue globale du batch GPU.
            # La queue est sans limite de taille car les workers produisent
            # au max 1 frame par cycle, et le GPU consomme plus vite.
            batch_input_queue.put_nowait((self.cam_id, frame.copy()))

            # ==========================================
            # ÉTAPE 2 : RÉCUPÉRATION DES RÉSULTATS GPU
            # ==========================================
            # On attend les résultats du thread GPU (max 1s).
            # Si le GPU est en retard, on publie la frame brute pour ne pas
            # freezer le stream Flask et on passe au cycle suivant.
            try:
                persons_data = result_queues[self.cam_id].get(timeout=1.0)
            except queue.Empty:
                with frame_lock:
                    output_frames[self.cam_id] = annotated_frame.copy()
                    raw_frames[self.cam_id]    = clean_frame.copy()
                self.video_buffer.append(annotated_frame)
                self.video_buffer_raw.append(clean_frame)
                continue


            # ==========================================
            # TRACKING CUSTOM DES PERSONNES
            # ==========================================

            person_boxes = [
                person_data["box"]
                for person_data in persons_data
            ]

            tracked_persons = self._track_persons_custom(person_boxes)

            for (p_id, _), person_data in zip(tracked_persons, persons_data):

                person_data["p_id"] = p_id

            # ==========================================
            # ÉTAPE 3 : TRAITEMENT DES RÉSULTATS
            # ==========================================
            # persons_data est une liste de dicts, un par personne détectée :
            # { p_id, box, conf, spec_result, x1_pad, y1_pad }

            hands_pos        = []
            bags_pos         = []
            raw_articles_pos = []

            for person_data in persons_data:
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

                    # SUSPICION FLÂNERIE : toute personne présente depuis plus de
                    # LOITERING_THRESHOLD secondes → suspicion discrète en mémoire.
                    # Pas besoin de manipuler un article : rester longtemps dans un rayon
                    # est suffisamment suspect pour alerter l'agent discrètement.
                    # Le flag utilise un ID négatif pour ne jamais entrer en conflit
                    # avec les vrais article_id du scénario CORPS (toujours positifs).
                    if presence_time > LOITERING_THRESHOLD:
                        loitering_key = -(p_id + 1)  # négatif → jamais en conflit avec article_id
                        # On recrée la suspicion à chaque fois que le score augmente
                        # (toutes les ~2.5s via le cycle de frames) pour la maintenir
                        # active tant que la personne est là et renouveler son TTL de 30s.
                        # Sans ça, la suspicion expirerait après 30s même si la personne reste.
                        loitering_score = min(0.60, 0.30 + (presence_time - LOITERING_THRESHOLD) / 180.0)
                        self._notify_suspicion(loitering_key, "FLÂNERIE", loitering_score)
                        
                    # Flânerie = suspect UNIQUEMENT si flânerie ET manipulation active d'un article.
                    # Un client qui compare les prix sans rien toucher → client normal.
                    person_is_handling = any(
                        self.hold_streak.get(a_id, 0) > 0
                        for a_id in self.last_known_articles
                        if is_point_in_box(self.last_known_articles[a_id], box)
                    )
                    if presence_time > LOITERING_THRESHOLD and person_is_handling:
                        is_loitering = True
                        
                        # SUSPICION FLÂNERIE : une personne qui flâne ET manipule un article
                        # → suspicion discrète en mémoire, visible sur l'interface.
                        # On utilise p_id comme article_id fictif pour éviter les conflits
                        # avec les vrais article_id du scénario CORPS.
                        # On ne crée la suspicion qu'une seule fois (pas à chaque frame)
                        # grâce au flag _suspicion_logged keyed sur p_id négatif.
                        loitering_key = -(p_id + 1)  # négatif → jamais en conflit avec un vrai article_id
                        if not self._suspicion_logged.get(loitering_key, False):
                            loitering_score = min(1.0, 0.3 + (presence_time - LOITERING_THRESHOLD) / 120.0)
                            # Score entre 0.30 (dès le seuil) et 0.60 (après 2 min de flânerie)
                            # Augmente graduellement avec le temps de présence suspect
                            self._notify_suspicion(loitering_key, "FLÂNERIE", loitering_score)

                # Visuel : Orange si suspect (flâne ET tient), Bleu sinon
                if is_loitering:
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 160, 255), 2)
                    cv2.putText(annotated_frame, f"SUSPECT: {int(presence_time)}s",
                                (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 140, 255), 2)
                else:
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 0, 0), 1)

                # ==========================================
                # ÉTAPE 4 : RÉSULTATS DU SPÉCIALISTE
                # ==========================================
                # Le spécialiste a déjà été appliqué sur le crop par le thread GPU.
                # On remappe ici les coordonnées du crop vers l'image globale.
                if spec_res is not None and spec_res.boxes is not None:
                    s_boxes = spec_res.boxes.xyxy.cpu().numpy()
                    s_clss  = spec_res.boxes.cls.cpu().numpy()
                    s_confs = spec_res.boxes.conf.cpu().numpy()

                    for s_box, s_cls, s_conf in zip(s_boxes, s_clss, s_confs):
                        s_name = model_specialist.names[int(s_cls)]

                        # Remapping : coordonnées crop → coordonnées image globale
                        g_x1     = int(s_box[0] + x1_pad)
                        g_y1     = int(s_box[1] + y1_pad)
                        g_x2     = int(s_box[2] + x1_pad)
                        g_y2     = int(s_box[3] + y1_pad)
                        g_center = get_center([g_x1, g_y1, g_x2, g_y2])

                        # Main : Jaune — seuil haut (0.5)
                        if s_name == "hands" and s_conf > 0.5:
                            hands_pos.append(g_center)
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 255, 255), 1)

                        # Sac : Rouge — seuil moyen (0.40)
                        elif s_name == "bags" and s_conf > 0.40:
                            bags_pos.append(g_center)
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (0, 0, 255), 2)

                        # Article de magasin : Violet — seuil bas (0.22)
                        # Le seuil bas est justifié : le modèle a été entraîné avec
                        # noise/blur/mosaic pour fonctionner en conditions dégradées.
                        # Les faux positifs de détection sont filtrés en aval par :
                        #   [FILTRE A] confiance moyenne ≥ HOLD_CONF_MIN sur la fenêtre glissante
                        #   [FILTRE D] score minimum d'alerte ≥ ALERT_SCORE_MIN
                        #   _was_hand_near_article() et la zone anatomique suspecte
                        elif s_name == "article" and s_conf > 0.22:
                            raw_articles_pos.append((g_center, s_conf))
                            cv2.rectangle(annotated_frame, (g_x1, g_y1), (g_x2, g_y2), (255, 0, 255), 2)


            # ── FILTRE SACS FIXES (transpalettes, caddies posés, étagères) ───────────
            # Un sac détecté au même endroit (grille 15px) depuis > 10 frames consécutives
            # est considéré fixe → exclu du scénario SAC pour éviter les faux positifs.
            bags_pos_filtered = []
            for b_center in bags_pos:
                key = (b_center[0] // 15, b_center[1] // 15)
                if key not in self.static_bag_cache:
                    self.static_bag_cache[key] = {"count": 0, "center": b_center}
                self.static_bag_cache[key]["count"] += 1
                if self.static_bag_cache[key]["count"] < 20:
                    bags_pos_filtered.append(b_center)
            # Purge des entrées trop vieilles (sac qui a bougé → réinitialise son compteur)
            # On réinitialise les cases non vues cette frame
            seen_keys = {(b[0] // 15, b[1] // 15) for b in bags_pos}
            for key in list(self.static_bag_cache.keys()):
                if key not in seen_keys:
                    del self.static_bag_cache[key]
            bags_pos = bags_pos_filtered

            # Enregistre les positions de mains de cette frame dans le buffer glissant
            # (utilisé par _was_hand_near_article avant la disparition d'un article)
            self.hands_history.append(list(hands_pos))


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
                self.person_position_history.pop(pid, None)
                # Nettoie aussi le flag de suspicion flânerie de cette personne
                self._suspicion_logged.pop(-(pid + 1), None)

            # ==========================================
            # NETTOYAGE DES ARTICLES DISPARUS
            # ==========================================
            active_ids  = set(self.active_article_tracks.keys())
            suspect_ids = set(self.suspect_disappearance.keys())
            for a_id in list(self.last_known_articles.keys()):
                if a_id not in active_ids and a_id not in suspect_ids:
                    self.last_known_articles.pop(a_id, None)
                    self.last_known_scores.pop(a_id, None)
                    self.hold_durations.pop(a_id, None)
                    self.object_hold_counter.pop(f"article_{a_id}", None)
                    self.hold_streak.pop(a_id, None)
                    self.hold_streak_miss.pop(a_id, None)
                    self.article_consecutive_frames.pop(a_id, None)
                    self.article_consecutive_miss.pop(a_id, None)
                    self.article_position_history.pop(a_id, None)
                    # [FILTRE A — v9] Nettoyage de l'historique de confiance
                    self.article_conf_history.pop(a_id, None)
                    # [FILTRE E — v9] Nettoyage du cooldown par article
                    self.article_alert_time.pop(a_id, None)
                    self.article_near_bag.pop(a_id, None)  

            # ==========================================
            # ÉTAPE 5 : ASSIGNATION DES IDs AUX ARTICLES
            # ==========================================
            articles_pos = self._track_articles_custom(raw_articles_pos)

            for (a_center, a_id, a_conf) in articles_pos:
                self.last_known_articles[a_id] = a_center
                self.last_known_scores[a_id]   = a_conf
                cv2.putText(annotated_frame, f"ID:{a_id}",
                            (a_center[0] - 10, a_center[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1)

            trigger_alert     = False
            vol_type          = ""
            alert_score       = 0.0
            current_active    = []
            target_p_id       = None
            alert_article_id_sac = None

            # ──────────────────────────────────────────────────────────────
            # SCÉNARIO 1 : OBJETS TENUS
            #
            # ARCHITECTURE v8 (inversée par rapport à v7) :
            # ┌────────────────────────────────────────────────────────────┐
            # │ CHEMIN PRIMAIRE : détection consécutive par le spécialiste │
            # │  → Si l'article est détecté X frames de suite dans la bbox │
            # │    d'une personne, il est considéré "tenu" directement.    │
            # │  → Le modèle spécialiste détecte les articles QU'en main,  │
            # │    donc la détection seule est suffisante.                 │
            # │  → Tolérance CONSECUTIVE_MISS_MAX : 1-2 frames ratées YOLO │
            # │    ne réinitialisent plus le compteur.                     │
            # │                                                            │
            # │ CHEMIN SECONDAIRE : corrélation de mouvement (streak)      │
            # │  → Si la corrélation article/personne est forte sur 20f    │
            # │    (HOLD_STREAK_THRESHOLD), l'article est aussi "tenu".    │
            # │  → Tolérance HOLD_STREAK_MISS_MAX : 10 frames de marge     │
            # │    pour les oscillations naturelles de marche.             │
            # │                                                            │
            # │ [FILTRE A — v9] : un article n'entre dans current_active   │
            # │  que si sa confiance MOYENNE sur la fenêtre glissante      │
            # │  dépasse HOLD_CONF_MIN (0.40). Filtre les faux positifs    │
            # │  à conf basse sans modifier le seuil de détection YOLO.   │
            # └────────────────────────────────────────────────────────────┘
            # ──────────────────────────────────────────────────────────────

            # Ensemble des articles qui se trouvent actuellement dans une bbox personne
            articles_in_person_bbox = set()

            for p_id, p_box in self.last_known_person_boxes.items():
                for (a_center, a_id, a_conf) in articles_pos:
                    if is_point_in_box(a_center, p_box):
                        articles_in_person_bbox.add(a_id)
                        key = f"article_{a_id}"
                        self.object_hold_counter[key] = self.object_hold_counter.get(key, 0) + 1

                        # ── CHEMIN PRIMAIRE : détection consécutive ─────────────
                        # CORRECTIF v8 : on compte les frames consécutives avec
                        # une tolérance aux trous (CONSECUTIVE_MISS_MAX).
                        # Si le trou est dans la tolérance, on ne remet PAS à 0.
                        # Si le trou dépasse la tolérance, le compteur repart de 0.
                        current_consec = self.article_consecutive_frames.get(a_id, 0)
                        # L'article est détecté cette frame → reset du compteur de trous
                        self.article_consecutive_miss[a_id] = 0
                        self.article_consecutive_frames[a_id] = current_consec + 1
                        consecutive = self.article_consecutive_frames[a_id]

                        # ── CHEMIN SECONDAIRE : corrélation de mouvement ────────
                        article_moves_with_person = self._is_article_moving_with_person(a_id, p_id)

                        if article_moves_with_person:
                            # Corrélation OK → streak progresse, reset du compteur de miss
                            self.hold_streak_miss[a_id] = 0
                            self.hold_streak[a_id] = self.hold_streak.get(a_id, 0) + 1
                        else:
                            # Corrélation KO → on incrémente le miss counter
                            miss = self.hold_streak_miss.get(a_id, 0) + 1
                            self.hold_streak_miss[a_id] = miss
                            if miss >= HOLD_STREAK_MISS_MAX:
                                # Trop de miss consécutifs → on remet le streak à 0
                                self.hold_streak[a_id]      = 0
                                self.hold_streak_miss[a_id] = 0

                        # ── DÉCISION FINALE ─────────────────────────────────────
                        # L'article est "tenu" si l'UN OU L'AUTRE chemin valide.
                        # Chemin primaire : X frames consécutives de détection (modèle spécialiste)
                        # Chemin secondaire : streak de corrélation atteint le seuil
                        article_held_by_detection = (consecutive >= ARTICLE_DETECTED_HOLD_THRESHOLD)
                        article_held_by_streak    = (
                            self.object_hold_counter.get(key, 0) >= FRAME_THRESHOLD
                            and self.hold_streak.get(a_id, 0) >= HOLD_STREAK_THRESHOLD
                        )
                        is_held = article_held_by_detection or article_held_by_streak

                        if is_held:
                            # ── [FILTRE A — v9] Vérification confiance moyenne ──────
                            # Avant d'ajouter l'article à current_active, on s'assure
                            # que sa confiance MOYENNE sur la fenêtre glissante dépasse
                            # HOLD_CONF_MIN. Un vrai article en main sera détecté de
                            # façon stable à conf élevée. Un faux positif (badge,
                            # téléphone, reflet) oscillera autour du seuil de détection
                            # brut (0.22) et sera donc éliminé ici.
                            conf_history = self.article_conf_history.get(a_id, deque())
                            mean_conf = (
                                sum(conf_history) / len(conf_history)
                                if conf_history else 0.0
                            )
                            if mean_conf < HOLD_CONF_MIN:
                                if DEBUG_LOGS and self.frames_processed % 30 == 0:
                                    print(
                                        f"[{self.cam_id}] [FILTRE A] Article {a_id} ignoré : "
                                        f"conf moy={mean_conf:.2f} < {HOLD_CONF_MIN}"
                                    )
                                # Conf trop faible → on n'ajoute pas à current_active
                                # mais on ne réinitialise pas les compteurs non plus :
                                # si le modèle devient plus sûr sur les prochaines frames,
                                # l'article pourra encore passer le filtre.
                                continue

                            current_active.append((a_id, a_center, a_conf))
                            self.hold_durations[a_id] = self.hold_durations.get(a_id, 0) + 1

                            cv2.circle(annotated_frame, a_center, 10, (0, 255, 0), 2)
                            cv2.putText(annotated_frame, "TENU",
                                        (a_center[0] + 10, a_center[1]),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # ── Gestion des articles hors bbox : compteur de miss consécutifs ──
            # CORRECTIF v8 : on ne remet PAS à 0 immédiatement quand l'article
            # sort de la bbox. On tolère CONSECUTIVE_MISS_MAX frames avant le reset.
            # Cela évite les resets dus aux "respirations" de bbox YOLO
            # (la bbox personne qui s'agrandit/rétrécit de quelques pixels par frame).
            for (a_center, a_id, a_conf) in articles_pos:
                if a_id not in articles_in_person_bbox:
                    # L'article n'est dans aucune bbox personne cette frame
                    consec_miss = self.article_consecutive_miss.get(a_id, 0) + 1
                    self.article_consecutive_miss[a_id] = consec_miss

                    if consec_miss >= CONSECUTIVE_MISS_MAX:
                        # Assez de frames hors bbox → on remet le compteur consécutif à 0
                        self.article_consecutive_frames[a_id] = 0
                        self.article_consecutive_miss[a_id]   = 0

                    # Le streak de mouvement, lui, on le pénalise aussi
                    miss = self.hold_streak_miss.get(a_id, 0) + 1
                    self.hold_streak_miss[a_id] = miss
                    if miss >= HOLD_STREAK_MISS_MAX:
                        self.hold_streak[a_id]      = 0
                        self.hold_streak_miss[a_id] = 0

            # ── Log de debug (toutes les ~2.5s si DEBUG_LOGS=True) ─────────────
            if DEBUG_LOGS and self.frames_processed % 30 == 0 and articles_pos:
                for (_, a_id, _) in articles_pos:
                    streak    = self.hold_streak.get(a_id, 0)
                    consec    = self.article_consecutive_frames.get(a_id, 0)
                    hold_dur  = self.hold_durations.get(a_id, 0)
                    s_miss    = self.hold_streak_miss.get(a_id, 0)
                    c_miss    = self.article_consecutive_miss.get(a_id, 0)
                    conf_hist = self.article_conf_history.get(a_id, deque())
                    mean_conf = sum(conf_hist) / len(conf_hist) if conf_hist else 0.0
                    print(
                        f"[{self.cam_id}] Article {a_id} | "
                        f"Streak={streak}/{HOLD_STREAK_THRESHOLD} (miss={s_miss}) | "
                        f"Consec={consec}/{ARTICLE_DETECTED_HOLD_THRESHOLD} (miss={c_miss}) | "
                        f"HoldDur={hold_dur} | ConfMoy={mean_conf:.2f}/{HOLD_CONF_MIN}"
                    )

            # ──────────────────────────────────────────────────────────────
            # SCÉNARIO 2 : VOL DANS LE SAC (v10 — logique séquentielle)
            #
            # SÉQUENCE REQUISE :
            #   Phase 1 : article visible + proche sac ≥ SAC_PROXIMITY_FRAMES_MIN frames
            #   Phase 2 : article DISPARAÎT (n'est plus visible par YOLO)
            #   → ALERTE
            #
            # Si l'article reste visible après le rapprochement : pas d'alerte.
            # Si le rapprochement dure < SAC_PROXIMITY_FRAMES_MIN frames : bruit ignoré.
            # Si la disparition tarde > SAC_DISAPPEARANCE_TIMEOUT après le rapprochement : reset.
            # ──────────────────────────────────────────────────────────────

            visible_article_ids = {a_id for (_, a_id, _) in articles_pos}

            # ── Phase 1 : détection de la proximité article/sac ──────────
            for p_id, p_box in self.last_known_person_boxes.items():
                for (a_center, a_id, a_conf) in articles_pos:
                    if not is_point_in_box(a_center, p_box):
                        continue
                    key = f"article_{a_id}"
                    if self.object_hold_counter.get(key, 0) < FRAME_THRESHOLD:
                        continue  # Pas assez vu dans la bbox → bruit

                    for b_center in bags_pos:
                        dist = math.hypot(a_center[0] - b_center[0], a_center[1] - b_center[1])
                        if dist < SAC_PROXIMITY_DIST:
                            # [SAC FILTRE 1] Le sac doit être DANS la bbox de la personne.
                            # Un sac posé sur un transpalette ou au sol est hors bbox → ignoré.
                            if not is_point_in_box(b_center, p_box):
                                continue

                            if a_id not in self.article_near_bag:
                                # Première frame de proximité → on démarre le compteur
                                self.article_near_bag[a_id] = {
                                    "frames_near_bag":    1,
                                    "bag_center":         b_center,
                                    "bag_initial_center": b_center,   # [SAC FILTRE 2]
                                    "bag_moved":          False,       # [SAC FILTRE 2]
                                    "p_id":               p_id,
                                    "conf":               a_conf,
                                    "start_time":         current_time,
                                }
                            else:
                                # Proximité confirmée → on incrémente
                                self.article_near_bag[a_id]["frames_near_bag"] += 1
                                self.article_near_bag[a_id]["bag_center"]       = b_center
                                self.article_near_bag[a_id]["conf"]             = a_conf
                                # [SAC FILTRE 2] Le sac a-t-il bougé depuis le début ?
                                # Un vrai sac porté se déplace avec la personne (> 8px).
                                init = self.article_near_bag[a_id]["bag_initial_center"]
                                if math.hypot(b_center[0] - init[0], b_center[1] - init[1]) > 20:
                                    self.article_near_bag[a_id]["bag_moved"] = True
                            break  # Un seul sac suffit
        

            # ── Nettoyage : article qui s'éloigne du sac → reset ─────────
            for a_id in list(self.article_near_bag.keys()):
                data = self.article_near_bag[a_id]

                # L'article est toujours visible mais plus proche d'aucun sac → abandon
                if a_id in visible_article_ids:
                    still_near = False
                    a_center   = self.last_known_articles.get(a_id)
                    if a_center:
                        for b_center in bags_pos:
                            if math.hypot(a_center[0] - b_center[0], a_center[1] - b_center[1]) < SAC_PROXIMITY_DIST:
                                still_near = True
                                break
                    if not still_near:
                        del self.article_near_bag[a_id]
                        continue

                # Timeout : la disparition tarde trop → probablement un faux rapprochement
                if current_time - data["start_time"] > data["frames_near_bag"] / self.fps + SAC_DISAPPEARANCE_TIMEOUT:
                    if DEBUG_LOGS:
                        print(f"[{self.cam_id}] [SAC] Timeout rapprochement article {a_id} → reset")
                    del self.article_near_bag[a_id]

            # ── Phase 2 : l'article a disparu après la phase de proximité → ALERTE ──
            for a_id, data in list(self.article_near_bag.items()):
                if a_id in visible_article_ids:
                    continue  # Toujours visible → pas un vol

                if data["frames_near_bag"] < SAC_PROXIMITY_FRAMES_MIN:
                    # Proximité trop courte → bruit, on nettoie
                    del self.article_near_bag[a_id]
                    continue

                # [SAC FILTRE 2] Le sac n'a jamais bougé → sac fixe (transpalette,
                # caddie posé, sac de marchandise). Pas un vol dans un sac porté.
                if not data.get("bag_moved", False):
                    if DEBUG_LOGS:
                        print(f"[{self.cam_id}] [SAC] Article {a_id} : sac immobile → faux positif ignoré")
                    del self.article_near_bag[a_id]
                    continue

                # Contact de main confirmé ? (même logique que CORPS)
                last_pos = self.last_known_articles.get(a_id)
                if last_pos and not self._was_hand_near_article(last_pos):
                    if DEBUG_LOGS:
                        print(f"[{self.cam_id}] [SAC] Article {a_id} disparu mais aucune main → ignoré")
                    del self.article_near_bag[a_id]
                    continue

                # Cooldown par article
                last_alert_this_article = self.article_alert_time.get(a_id, 0)
                if current_time - last_alert_this_article <= ALERT_COOLDOWN:
                    del self.article_near_bag[a_id]
                    continue

                # ✅ Toutes les conditions remplies → alerte SAC
                trigger_alert = True
                vol_type      = "SAC"
                alert_score   = float(data["conf"])
                target_p_id   = data["p_id"]
                alert_article_id_sac = a_id   # ← mémorise l'id avant le del
                del self.article_near_bag[a_id]
                break

            # ──────────────────────────────────────────────────────────────
            # SCÉNARIO 3 : VOL CORPOREL (LOGIQUE ANATOMIQUE)
            # ──────────────────────────────────────────────────────────────
            visible_ids = {a_id for (_, a_id, _) in articles_pos}

            # 1. Annule la suspicion si l'objet réapparaît (angle mort confirmé)
            for a_id in list(self.suspect_disappearance.keys()):
                if a_id in visible_ids:
                    print(f"[{self.cam_id}] Angle mort terminé pour objet {a_id}, suspicion annulée.")
                    # CORRECTIF v8 : vérification d'existence avant del (évite KeyError)
                    if a_id in self.suspect_disappearance:
                        del self.suspect_disappearance[a_id]
                    self._clear_suspicion(a_id)

            # 2. Analyse des disparitions suspectes
            for key, count in self.object_hold_counter.items():
                a_id = int(key.split("_")[1])

                # Toutes ces conditions doivent être vraies pour démarrer le timer :
                # - article bien vu dans la bbox (count ≥ FRAME_THRESHOLD)
                # - streak OU compteur consécutif retombé à 0 (l'article vient de disparaître)
                # - article non visible par YOLO (pas un angle mort partiel)
                # - article tenu suffisamment longtemps (filtre bruit court)
                streak_gone     = self.hold_streak.get(a_id, 0) == 0
                consec_gone     = self.article_consecutive_frames.get(a_id, 0) == 0
                article_was_active = a_id not in visible_ids and self.hold_durations.get(a_id, 0) >= 30

                if not (count >= FRAME_THRESHOLD
                        and (streak_gone or consec_gone)
                        and article_was_active):
                    continue

                last_pos = self.last_known_articles.get(a_id)
                if not last_pos:
                    continue

                # Filtre anti-erreur de label (article confondu avec un sac)
                if any(math.hypot(last_pos[0] - bc[0], last_pos[1] - bc[1]) < 30 for bc in bags_pos):
                    continue

                # Filtre bord d'écran (article sorti du champ de vue → pas une dissimulation)
                margin = 45
                if not (margin < last_pos[0] < self.width - margin
                        and margin < last_pos[1] < self.height - margin):
                    continue

                # PROTECTION PRINCIPALE contre les faux positifs :
                # Sans contact de main confirmé dans les 2s → on ignore la disparition.
                if not self._was_hand_near_article(last_pos):
                    if DEBUG_LOGS:
                        print(f"[{self.cam_id}] Disparition objet {a_id} ignorée : aucune main à proximité.")
                    continue

                # Filtre géométrique : la disparition doit être dans une zone anatomique suspecte
                # (buste/ventre de la personne, pas les épaules ou le bas des jambes)
                is_suspect_zone = False
                for p_id, p_box in self.last_known_person_boxes.items():
                    if is_point_in_box(last_pos, p_box):
                        p_w   = p_box[2] - p_box[0]
                        p_h   = p_box[3] - p_box[1]
                        rel_x = (last_pos[0] - p_box[0]) / p_w if p_w > 0 else 0.5
                        rel_y = (last_pos[1] - p_box[1]) / p_h if p_h > 0 else 0.5

                        if 0.35 <= rel_y <= 0.85 and 0.25 <= rel_x <= 0.75:
                            is_suspect_zone = True
                            target_p_id     = p_id
                            break

                # [FILTRE B — v9] Durée minimale d'absence avant suspicion
                # On vérifie que l'article est absent depuis au moins
                # MIN_DISAPPEARANCE_FRAMES frames. Sans ce filtre, une simple
                # occlusion courte (5 frames, soit ~0.4s) suffisait à démarrer
                # le timer de suspicion et potentiellement déclencher une alerte.
                # Avec 36 frames (3s), seules les vraies disparitions prolongées
                # entrent en suspicion.
                frames_absent = self.article_consecutive_miss.get(a_id, 0)

                # CORRECTIF v8 : on vérifie que l'article n'est pas déjà
                # en cours de suspicion avant d'en créer une nouvelle.
                if (is_suspect_zone
                        and a_id not in self.suspect_disappearance
                        and frames_absent >= MIN_DISAPPEARANCE_FRAMES):  # [FILTRE B — v9]
                    self.suspect_disappearance[a_id] = {
                        "start_time":  current_time,
                        "last_score":  self.last_known_scores.get(a_id, 0.5),
                        "hold_frames": self.hold_durations.get(a_id, 0),
                        "p_id":        target_p_id,
                    }

            # 3. Validation finale : DISAPPEARANCE_TIMEOUT pour distinguer angle mort et vrai vol
            for a_id, data in list(self.suspect_disappearance.items()):
                elapsed     = current_time - data["start_time"]
                target_p_id = data["p_id"]

                # Sécurité fuite : la personne est partie avec l'article
                personne_partie = (
                    target_p_id is not None
                    and target_p_id in self.person_tracking
                    and current_time - self.person_tracking[target_p_id]["last_seen"] > 2.5
                )

                # Entre 4s et DISAPPEARANCE_TIMEOUT : suspicion discrète EN MÉMOIRE uniquement.
                # Pas de JSON, pas de clip. L'agent voit "Œil sur CAM_XX" sur son écran.
                # CORRECTIF v8 : on vérifie _suspicion_logged par article_id, pas en global.
                if (4.0 <= elapsed < DISAPPEARANCE_TIMEOUT
                        and not self._suspicion_logged.get(a_id, False)
                        and data["hold_frames"] > 30
                        and time.time() - self.last_alert_time > ALERT_COOLDOWN):

                    loitering_bonus = 0.25 if (
                        target_p_id is not None
                        and target_p_id in self.person_tracking
                        and current_time - self.person_tracking[target_p_id]["first_seen"] > LOITERING_THRESHOLD
                    ) else 0.0
                    base_score = float(
                        0.4 * data["last_score"]
                        + 0.6 * min(1.0, data["hold_frames"] / 60.0)
                    )
                    suspicion_score = min(1.0, base_score + loitering_bonus)
                    # CORRECTIF v8 : on passe article_id pour tracker la suspicion par article
                    self._notify_suspicion(a_id, "CORPS", suspicion_score)

                if elapsed >= DISAPPEARANCE_TIMEOUT or personne_partie:
                    # [FILTRE E — v9] : cooldown par article_id pour le scénario CORPS
                    last_alert_this_article = self.article_alert_time.get(a_id, 0)
                    if time.time() - last_alert_this_article > ALERT_COOLDOWN:
                        # CORRECTIF v8 : seuil abaissé de 60 à 30 frames (2.5s)
                        # L'ancien seuil de 60 était trop restrictif en conditions réelles.
                        if data["hold_frames"] > 30:
                            loitering_bonus = 0.25 if (
                                target_p_id is not None
                                and target_p_id in self.person_tracking
                                and current_time - self.person_tracking[target_p_id]["first_seen"] > LOITERING_THRESHOLD
                            ) else 0.0
                            base_score  = float(
                                0.4 * data["last_score"]
                                + 0.6 * min(1.0, data["hold_frames"] / 60.0)
                            )
                            alert_score = min(1.0, base_score + loitering_bonus)

                            # ── [FILTRE D — v9] Score minimum pour alerte formelle ──────
                            # Si le score calculé est inférieur à ALERT_SCORE_MIN,
                            # l'événement reste une suspicion en mémoire (visible sur
                            # l'interface) mais ne génère pas de clip ni d'entrée JSONL.
                            # Objectif : réserver les alertes "avec clip" aux cas où le
                            # système est raisonnablement sûr, pas sur chaque doute.
                            #
                            # POURQUOI 0.55 et pas plus haut :
                            # On veut rater le moins de vrais positifs possible.
                            # 0.55 correspond à : conf moy ≥ 0.40 (filtre A déjà passé)
                            # + hold_frames suffisant (≥ 30) → situation plausible.
                            # Les cas vraiment ambigus (score 0.39–0.54) restent visibles
                            # comme suspicions : l'agent peut aller vérifier sans clip.
                            if alert_score < ALERT_SCORE_MIN:
                                if DEBUG_LOGS:
                                    print(
                                        f"[{self.cam_id}] [FILTRE D] Alerte CORPS article {a_id} "
                                        f"bloquée : score={alert_score:.2f} < {ALERT_SCORE_MIN}. "
                                        f"Reste en suspicion mémoire."
                                    )
                                # On logue en suspicion si ce n'est pas déjà fait
                                if not self._suspicion_logged.get(a_id, False):
                                    self._notify_suspicion(a_id, "CORPS", alert_score)
                            else:
                                trigger_alert = True
                                vol_type      = "CORPS"

                                if personne_partie and elapsed < DISAPPEARANCE_TIMEOUT:
                                    print(f"[{self.cam_id}] ⚡ ALERTE ANTICIPÉE : Suspect {target_p_id} sorti avec objet {a_id}")

                    # CORRECTIF v8 : vérification d'existence avant del (évite KeyError)
                    if a_id in self.suspect_disappearance:
                        del self.suspect_disappearance[a_id]
                    self.hold_durations.pop(a_id, None)
                    self._clear_suspicion(a_id)

            # ==========================================
            # DÉCLENCHEMENT DE L'ALERTE ET ENREGISTREMENT
            # ==========================================
            # La logique de décision tourne TOUJOURS, indépendamment de l'état
            # d'enregistrement (découplage décision/enregistrement).
            if trigger_alert:
                self.zoom_target_id = target_p_id
                print(f"[{self.cam_id}] 🚨 ALERTE : VOL {vol_type} (score={alert_score:.2f})")

                # La suspicion est remplacée par l'alerte formelle
                self._clear_suspicion()

                # [FILTRE E — v9] : mise à jour du cooldown par article
                # On identifie l'article_id concerné pour mettre à jour son timestamp.
                # Pour VOL SAC : on récupère a_id depuis la boucle SAC (target défini).
                # Pour VOL CORPS : a_id est dans suspect_disappearance (déjà dépilé).
                # On parcourt current_active pour trouver l'article le plus proche
                # du suspect, ou on utilise l'article qui a déclenché via SAC.
                alert_article_id = None
                if vol_type == "SAC":
                    alert_article_id = alert_article_id_sac
                elif vol_type == "CORPS":
                    # Pour CORPS, l'article est dans current_active ou dans hold_durations
                    if current_active:
                        alert_article_id = current_active[0][0]

                if alert_article_id is not None:
                    self.article_alert_time[alert_article_id] = current_time

                if not self.is_recording_alert:
                    self._start_alert_video(vol_type, alert_score)
                    self.last_alert_time    = current_time
                    self.alert_text_to_show = f" ALERTE : VOL {vol_type} POTENTIEL "
                    self.alert_text_timer   = current_time + DISPLAY_TEXT_DURATION
                else:
                    # Enregistrement déjà actif → on prolonge juste la durée du clip en cours
                    # au lieu de créer une entrée JSONL sans clip (qui pollue l'interface)
                    self.frames_to_record_after = int(AFTER_ALERT_SECS * self.fps)
                    self.last_alert_time = current_time
                    print(f"[{self.cam_id}] ⚡ Alerte supplémentaire pendant enregistrement → clip prolongé")
            # ==========================================
            # AFFICHAGE DU TEXTE D'ALERTE (CLIGNOTANT)
            # ==========================================
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

                font_scale = 0.5
                thickness  = 1
                text_size  = cv2.getTextSize(self.alert_text_to_show, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)[0]
                cv2.rectangle(annotated_frame, (5, 10 - text_size[1] - 5), (15 + text_size[0], 35), (0, 0, 0), -1)
                cv2.putText(annotated_frame, self.alert_text_to_show,
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 255), thickness)

            # ==========================================
            # OVERLAY "RECONNEXION EN COURS" (WATCHDOG)
            # ==========================================
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

            # ==========================================
            # PUBLICATION DE LA FRAME POUR FLASK
            # ==========================================
            with frame_lock:
                output_frames[self.cam_id] = annotated_frame.copy()
                raw_frames[self.cam_id]    = clean_frame.copy()

            self.video_buffer.append(annotated_frame)
            self.video_buffer_raw.append(clean_frame)

            # Copies lazy : uniquement si un enregistrement est actif (évite les copies inutiles)
            frame_to_record     = None
            frame_raw_to_record = None

            if self.is_recording_alert or self.zoom_target_id in self.last_known_person_boxes:
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
                        self.smooth_center      = None

                        for proc in [self.alert_ffmpeg_process, self.raw_ffmpeg_process]:
                            if proc:
                                try:
                                    proc.stdin.close()
                                    proc.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    print(f"[{self.cam_id}] ⚠️ FFmpeg enregistrement trop lent, kill forcé.")
                                    proc.kill()
                                except Exception:
                                    pass

                        self.alert_ffmpeg_process = None
                        self.raw_ffmpeg_process   = None
                        print(f"[{self.cam_id}] ✅ Clip enregistré.")

                except Exception as e:
                    # Erreur d'écriture → on stoppe proprement sans crasher le worker.
                    # La caméra continue de fonctionner.
                    print(f"[{self.cam_id}] ❌ Erreur enregistrement : {e}")
                    self.is_recording_alert   = False
                    self.zoom_target_id       = None
                    self.smooth_center        = None
                    self.alert_ffmpeg_process = None
                    self.raw_ffmpeg_process   = None

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

    all_workers = []
    all_readers = []

    def shutdown_handler(signum, frame):
        """
        Gestionnaire du signal SIGINT (Ctrl+C).
        Ferme proprement tous les processus FFmpeg avant de quitter.
        Sans ça, les fichiers MP4 en cours d'enregistrement seraient corrompus.
        """
        print("\n⏹ Arrêt demandé. Fermeture propre des enregistrements...")
        for w in all_workers:
            w.cleanup()
        for r in all_readers:
            r.stop()
        print("✅ Fermeture terminée.")
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)

    # Démarrage du serveur Flask dans son thread dédié
    threading.Thread(target=start_server, daemon=True).start()
    print("🌐 Serveur Flask démarré sur http://192.168.0.97:5000")

    # ── Thread GPU centralisé (batch) ────────────────────────────────────────
    # Doit être démarré AVANT les workers pour que batch_input_queue soit prête.
    threading.Thread(
        target=gpu_batch_worker,
        daemon=True,
        name="gpu_batch_worker",
    ).start()
    print("🖥️  Thread GPU centralisé démarré (mode batch — 1 inférence pour toutes les caméras)")

    # ── Thread de purge automatique des clips ────────────────────────────────
    # Lance une première passe immédiatement au démarrage (utile après un
    # redémarrage brutal qui aurait laissé des vieux clips non supprimés),
    # puis tourne toutes les PURGE_INTERVAL_SECS secondes en arrière-plan.
    threading.Thread(
        target=purge_worker,
        daemon=True,
        name="purge_worker",
    ).start()

    # Démarrage des threads par caméra (2 threads : lecteur RTSP + worker logique)
    for cam_cfg in CAMERAS:
        cam_id = cam_cfg["cam_id"]

        # Thread 1 : lecteur RTSP dédié — lit FFmpeg en continu, drop des vieilles frames
        reader = FFmpegReader(cam_cfg["cam_id"], cam_cfg["rtsp_url"], cam_cfg["width"], cam_cfg["height"])
        all_readers.append(reader)
        threading.Thread(
            target=reader.run,
            daemon=True,
            name=f"{cam_id}_reader",
        ).start()

        # Thread 2 : worker d'analyse — logique métier uniquement, plus d'accès GPU direct
        worker = CameraWorker(**cam_cfg)
        all_workers.append(worker)
        threading.Thread(
            target=worker.run,
            args=(reader,),
            daemon=True,
            name=f"{cam_id}_worker",
        ).start()

        print(f"✅ {cam_id} démarré → http://192.168.0.97:5000/video/{cam_id}")

    print("\n🔒 Système actif — v9")
    print("   Alertes    → GET /alerts?last=50")
    print("   Suspicions → GET /suspicions  (polling recommandé toutes les 2-3s)")
    print("   Snapshot   → POST /snapshot {\"cam_id\": \"CAM_21\"}")
    print("   Debug logs → DEBUG_LOGS = True en haut du fichier")
    print(f"   Purge auto → clips > {CLIP_RETENTION_DAYS}j supprimés, espace min {DISK_MIN_FREE_GB}Go")
    print(f"   Filtres v9 → ConfMin={HOLD_CONF_MIN} | ScoreMin={ALERT_SCORE_MIN} | "
          f"DisparitionMin={MIN_DISAPPEARANCE_FRAMES}f ({MIN_DISAPPEARANCE_FRAMES/12:.1f}s) | "
          f"Cooldown par article")
    print("   Ctrl+C pour arrêter proprement.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt demandé. Fermeture propre...")