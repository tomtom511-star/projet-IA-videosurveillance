# 🔒 Système de Détection de Vol Multi-Caméras - Leclairvoyant

> Système de surveillance intelligent capable de détecter automatiquement des vols en magasin via l'analyse vidéo en temps réel sur plusieurs caméras IP simultanées.

---

## 📋 Table des matières

1. [Vue d'ensemble](#vue-densemble)
2. [Architecture du système](#architecture-du-système)
3. [Pipeline de traitement](#pipeline-de-traitement)
4. [Les 3 scénarios de détection de vol](#les-3-scénarios-de-détection-de-vol)
   - [Scénario 1 — CORPS](#scénario-1--vol-corporel-corps)
   - [Scénario 2 — SAC](#scénario-2--vol-dans-le-sac-sac)
   - [Scénario 3 — FLÂNERIE](#scénario-3--flânerie)
5. [Système anti-faux-positifs](#système-anti-faux-positifs)
6. [Variables et seuils clés](#variables-et-seuils-clés)
7. [API Flask — Endpoints disponibles](#api-flask--endpoints-disponibles)
8. [Gestion des fichiers et clips vidéo](#gestion-des-fichiers-et-clips-vidéo)

---

## Vue d'ensemble

```
┌──────────────────────────────────────────────────────────────────────┐
│                    7 CAMÉRAS IP (RTSP) simultanées                   │
│         CAM_21 · CAM_22 · CAM_23 · CAM_45 · CAM_46 · CAM_47 · CAM_49│
└─────────────────────────────┬────────────────────────────────────────┘
                              │  flux vidéo brut (FFmpeg)
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     THREAD GPU CENTRALISÉ                            │
│  ┌───────────────────┐        ┌─────────────────────────────────┐   │
│  │  Modèle RADAR     │  crop  │  Modèle SPÉCIALISTE             │   │
│  │  (détecte les     │───────▶│  (détecte articles, mains, sacs │   │
│  │   personnes)      │        │   dans chaque silhouette)        │   │
│  └───────────────────┘        └─────────────────────────────────┘   │
└─────────────────────────────┬───────────────────────────────────────┘
                              │  résultats par caméra
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│              WORKER PAR CAMÉRA (CameraWorker)                        │
│  • Tracking articles & personnes                                     │
│  • Analyse comportementale (CORPS / SAC / FLÂNERIE)                  │
│  • Déclenchement alertes + enregistrement clip vidéo                 │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
             /alerts.jsonl        Clip MP4 annoté
             (log persistant)     + Clip MP4 brut
```

**Deux modèles YOLO fonctionnent en cascade :**

| Modèle | Rôle | Seuil de confiance |
|---|---|---|
| `model_radar` | Détecte les **personnes** dans la scène complète | ≥ 0.50 |
| `model_specialist` | Détecte **articles / mains / sacs** dans chaque découpe de personne | ≥ 0.25 (articles), ≥ 0.40 (sacs), ≥ 0.50 (mains) |

---

## Architecture du système

### Threads actifs en permanence

```
Thread principal
    ├── Flask (port 5000) — serve les flux MJPEG + API REST
    ├── GPU Batch Worker — reçoit les frames de toutes les caméras et
    │                      lance les inférences YOLO en batch
    ├── Purge Worker — supprime les vieux clips toutes les heures
    └── Par caméra (×7) :
            ├── FFmpegReader — décode le flux RTSP et expose les frames
            ├── Watchdog — redémarre FFmpeg si aucune frame depuis 5s
            ├── Stderr Drain — log les erreurs FFmpeg (max 1/s)
            └── CameraWorker — toute la logique métier de détection
```

### Flux d'une frame

```
FFmpegReader → gpu_pending_frames (dict partagé)
                        │
                        ▼
              gpu_batch_worker (toutes les 80ms)
              ┌──────────────────────────────┐
              │ 1. model_radar sur toutes     │
              │    les frames du batch        │
              │ 2. Découpe chaque personne    │
              │ 3. model_specialist sur        │
              │    tous les crops en batch    │
              └──────────────┬───────────────┘
                             │
                             ▼
                   result_queues[cam_id]
                             │
                             ▼
                   CameraWorker.run()
                   → tracking + logique vol
```

---

## Pipeline de traitement

Voici ce qui se passe à **chaque frame** dans `CameraWorker.run()` :

```
FRAME REÇUE
    │
    ├─ 1. TRACKING PERSONNES
    │       → _track_persons_custom()
    │       → Met à jour last_known_person_boxes, person_position_history
    │
    ├─ 2. DÉTECTION SPÉCIALISTE
    │       → Extrait mains / sacs / articles depuis les résultats GPU
    │       → Filtre les sacs fixes (STATIC_BAG_FRAME_THRESHOLD = 20f)
    │       → Mémorise les positions de mains (hands_history, 20 frames)
    │
    ├─ 3. TRACKING ARTICLES
    │       → _track_articles_custom()
    │       → Matching par distance adaptative (proportionnelle à la taille de la personne)
    │       → Si pas de match : nouvel ID article
    │       → Si miss ≤ 180 frames : track conservé (tolérance aux occultations)
    │
    ├─ 4. SCÉNARIO 1 — OBJETS TENUS (pré-requis SAC et CORPS)
    │       → Article dans la bbox d'une personne ?
    │       → Il bouge avec elle ? (corrélation mouvement)
    │       → Conf YOLO moyenne suffisante ? (≥ 0.25)
    │       → ⟹ Incrément hold_streak + hold_durations
    │
    ├─ 5. SCÉNARIO 2 — VOL DANS LE SAC
    │       → Article près d'un sac ? → article_near_bag
    │       → Article disparu 4+ frames + patience 5s ?
    │       → Main confirmée à proximité ?
    │       → ⟹ ALERTE SAC
    │
    ├─ 6. SCÉNARIO 3 — VOL CORPOREL
    │       → Article tenu disparu de la zone corporelle ?
    │       → Main confirmée ? Zone suspecte ? Frames absentes ≥ 36 ?
    │       → Timeout 12s atteint OU personne partie ?
    │       → ⟹ ALERTE CORPS
    │
    └─ 7. ENREGISTREMENT + PUBLICATION
            → Flask frame annotée
            → Clip MP4 (avant + après alerte)
```

---

## Les 3 scénarios de détection de vol

---

### Scénario 1 — Vol Corporel (`CORPS`)

> **Définition :** Un article tenu par une personne disparaît dans la zone de son corps (poche, vêtement, ceinture...) et ne réapparaît pas.

#### 🟢 Conditions de déclenchement — étape par étape

```
┌─────────────────────────────────────────────────────────────────────┐
│  ÉTAPE 1 — L'article est "tenu" (pré-requis)                        │
│                                                                      │
│  • Article détecté DANS la bbox d'une personne                      │
│  • Pendant ≥ 20 frames consécutives  (ARTICLE_DETECTED_HOLD)        │
│  • Son mouvement corrèle avec celui de la personne                  │
│  • Tenu en continu ≥ 20 frames avec corrélation (HOLD_STREAK)       │
│  • Confiance YOLO moyenne ≥ 0.25  (HOLD_CONF_MIN)                  │
│                                       ⬇                             │
│                              is_held = True                          │
│                              hold_durations[a_id] s'incrémente       │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ÉTAPE 2 — L'article disparaît                                       │
│                                                                      │
│  • Article absent de la frame pendant ≥ 36 frames  (MIN_DISAPP.)    │
│  • Sa dernière position était dans la bbox d'une personne           │
│  • Il était dans la zone corporelle :                               │
│       rel_y ∈ [0.25, 0.85]  (pas en haut du crâne, pas aux pieds) │
│       rel_x ∈ [0.20, 0.80]  (pas aux extrémités du corps)          │
│  • Pas trop excentré (dist au centre corps ≤ 0.38)                 │
│       → évite les faux positifs "bras tendu vers étagère"           │
│  • La personne est vue depuis ≥ 3s (exclut les passages rapides)   │
│                                       ⬇                             │
│                     Entrée dans suspect_disappearance               │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ÉTAPE 3 — Confirmation de la main (filtre anti-faux-positifs)      │
│                                                                      │
│  • _was_hand_near_article() : une main était à < 35px               │
│    de l'article dans les 20 dernières frames                         │
│  • Sans ça → simple occlusion, pas un vol → ignoré                  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ÉTAPE 4 — Déclenchement de l'alerte                                │
│                                                                      │
│  Après 4s de suspicion, si :                                        │
│  • hold_frames > 12                                                 │
│  • Cooldown global respecté (60s)                                   │
│  • Score > 0.5  (ALERT_SCORE_MIN)                                   │
│       score = 0.4 × conf_YOLO + 0.6 × min(1, hold_frames / 60)    │
│       + bonus 0.25 si flânerie confirmée                            │
│                                                                      │
│  OU si la personne quitte la scène (last_seen > 6s)                 │
│       → alerte anticipée avant le timeout 12s                       │
│                                                                      │
│                         ⟹ 🚨 ALERTE CORPS                           │
└─────────────────────────────────────────────────────────────────────┘
```

#### 🔴 Conditions d'annulation de la suspicion

| Condition | Mécanisme |
|---|---|
| L'article **réapparaît** (même ID) pendant ≥ 3 frames | `reappearance_frames ≥ REAPPEARANCE_FRAMES_MIN` → suspicion supprimée |
| L'article réapparaît avec un **nouvel ID** mais visuellement identique | Comparaison histogramme HSV (corrélation ≥ 0.55) → `FIX H` |
| L'article était visible **hors zone corporelle** | Filtre `rel_x / rel_y` → pas de suspicion |
| **Pas de contact de main** confirmé | `_was_hand_near_article()` → ignoré |
| L'article est **trop excentré** (bras tendu) | `dist_to_center > 0.38` → ignoré |
| La personne est vue depuis **moins de 3s** | Filtre `first_seen < 3s` → ignoré |
| L'article a **pivoté** (même objet, nouvel ID tracker) | `FIX P3` — vérif visuelle temporelle → pas de suspicion |
| Timeout **12s** sans déclenchement | `DISAPPEARANCE_TIMEOUT` → nettoyage |

---

### Scénario 2 — Vol dans le Sac (`SAC`)

> **Définition :** Un article tenu par une personne se retrouve à proximité immédiate d'un sac, puis disparaît — il a été glissé dedans.

#### 🟢 Conditions de déclenchement — étape par étape

```
┌─────────────────────────────────────────────────────────────────────┐
│  ÉTAPE 1 — Proximité article / sac                                   │
│                                                                      │
│  • Article dans la bbox d'une personne                              │
│  • Article tenu depuis ≥ 8 frames  (FRAME_THRESHOLD)                │
│  • Un sac détecté à < 25% de la hauteur de la personne             │
│    (distance dynamique, min 30px)                                   │
│  • Le sac est lui-même dans la bbox de la personne                 │
│  • Le sac N'EST PAS fixe (< 20 frames au même endroit)             │
│    → les sacs de comptoir, posés, ne comptent pas                  │
│                                       ⬇                             │
│                  article_near_bag[a_id] créé / incrémenté           │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ÉTAPE 2 — L'article disparaît près du sac                          │
│                                                                      │
│  • Article absent depuis ≥ 4 frames  (frames_gone ≥ 4)             │
│  • Disparu depuis ≥ 5s  (SAC_DISAPPEARANCE_PATIENCE)                │
│    → évite les occultations momentanées (la main passe devant)     │
│  • Proximité confirmée ≥ 14 frames  (SAC_PROXIMITY_FRAMES_MIN)      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ÉTAPE 3 — Confirmation de la main                                   │
│                                                                      │
│  • _was_hand_near_article() : une main près de l'article            │
│    dans les 20 dernières frames                                      │
│  • Sans confirmation → ignoré                                        │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ÉTAPE 4 — Vérifications finales                                     │
│                                                                      │
│  • hold_durations[a_id] > 0  (article réellement tenu)              │
│  • Cooldown par article respecté  (60s)                             │
│                                                                      │
│                         ⟹ 🚨 ALERTE SAC                             │
└─────────────────────────────────────────────────────────────────────┘
```

#### 🔴 Conditions d'annulation / nettoyage

| Condition | Mécanisme |
|---|---|
| L'article **réapparaît** | `frames_gone` reset à 0, `gone_since` supprimé |
| La **personne quitte** la scène | `last_known_person_boxes` vide → nettoyage `article_near_bag` |
| Timeout de rapprochement dépassé | `start_time + frames_near_bag/fps + 2s` → reset |
| Le sac est **fixe** depuis ≥ 20 frames | Filtré par `static_bag_cache` → exclu de `bags_pos_filtered` |
| Pas assez de frames de proximité | `frames_near_bag < 14` → ignoré |

#### ⚠️ Filtrage des sacs fixes

```
À chaque frame :
  Pour chaque sac détecté :
    key = (cx//15, cy//15)   ← cellule de grille 15px
    static_bag_cache[key].count += 1
    Si count < 20  → sac inclus dans l'analyse
    Si count ≥ 20  → sac EXCLU (considéré posé/fixe)

  Si un sac disparaît du détecteur → sa cellule est purgée
  → Le compteur repart à 0 si le sac bouge
```

---

### Scénario 3 — Flânerie (`FLÂNERIE`)

> **Définition :** Une personne reste dans la zone de surveillance trop longtemps, surtout si elle manipule des articles. Ce scénario est un signal d'alerte préventif, pas une alerte de vol directe.

#### 🟢 Conditions de déclenchement

```
┌─────────────────────────────────────────────────────────────────────┐
│  Présence > 120s (LOITERING_THRESHOLD)                              │
│                                                                      │
│  Score progressif :                                                  │
│    loitering_score = min(0.60, 0.30 + (temps - 120s) / 180s)       │
│    → Plafonné à 0.60 pour rester un signal, pas une alerte de vol  │
│                                                                      │
│  Bonus si la personne manipule un article au même moment :          │
│    score = min(1.0, 0.30 + (temps - 120s) / 120s)                  │
│                                                                      │
│  ⟹ Apparaît dans /suspicions — ne génère PAS de clip vidéo seul   │
│    (mais peut augmenter le score d'une alerte CORPS/SAC de +0.25)  │
└─────────────────────────────────────────────────────────────────────┘
```

#### 🔴 Annulation

- La personne quitte la scène (nettoyage après 30s d'absence)
- Identifiant `-( p_id + 1)` utilisé pour les suspicions de flânerie (distinct des articles)

---

## Système anti-faux-positifs

Le système dispose de **7 couches de filtrage** pour éviter les fausses alertes :

```
┌────┬──────────────────────────────────┬──────────────────────────────────────────┐
│ #  │ Filtre                           │ Ce qu'il bloque                          │
├────┼──────────────────────────────────┼──────────────────────────────────────────┤
│ 1  │ _was_hand_near_article()         │ Occultations sans contact de main         │
│    │ (MAIN — dans CORPS et SAC)       │ Ex: article caché par une veste en passant│
├────┼──────────────────────────────────┼──────────────────────────────────────────┤
│ 2  │ Zone corporelle rel_x/rel_y      │ Bras tendu vers étagère                   │
│    │ + dist_to_center ≤ 0.38         │ Article lâché au sol                      │
├────┼──────────────────────────────────┼──────────────────────────────────────────┤
│ 3  │ Confiance YOLO moyenne ≥ 0.25   │ Détections floues / partielles            │
│    │ (HOLD_CONF_MIN)                  │ Objets ambigus mal classifiés             │
├────┼──────────────────────────────────┼──────────────────────────────────────────┤
│ 4  │ hold_durations > 12             │ Articles vus brièvement (pas tenus)       │
├────┼──────────────────────────────────┼──────────────────────────────────────────┤
│ 5  │ Score minimum ≥ 0.5             │ Suspicions faibles / incertaines          │
│    │ (ALERT_SCORE_MIN)                │                                          │
├────┼──────────────────────────────────┼──────────────────────────────────────────┤
│ 6  │ Cooldown 60s global             │ Alertes répétées pour le même événement   │
│    │ + cooldown par article           │                                          │
├────┼──────────────────────────────────┼──────────────────────────────────────────┤
│ 7  │ Signature visuelle HSV [FIX I]  │ Doublon d'alerte pour le même article     │
│    │ corrélation histogramme ≥ 0.55  │ (même objet vu sous angle différent)      │
└────┴──────────────────────────────────┴──────────────────────────────────────────┘
```

### Signature visuelle (FIX H / FIX I)

Chaque article suivi accumule une **empreinte visuelle** basée sur son histogramme de couleur en espace HSV :

```
_capture_article_signature()
  → Découpe l'article (+4px de marge)
  → Calcule histogramme HSV (16×8 bins)
  → Normalise et stocke (max 10 histogrammes glissants)
  → Stocke aussi le ratio largeur/hauteur

_is_same_article_visual()
  → Compare le ratio (tolérance ±35%)
  → Compare les histogrammes (cv2.HISTCMP_CORREL ≥ 0.55)
  → True = même objet physique

_is_duplicate_alert()
  → Parcourt recent_alert_signatures (fenêtre = ALERT_COOLDOWN)
  → Si même objet à < 200px → doublon → bloqué
```

---

## Variables et seuils clés

### Timing et cooldowns

| Variable | Valeur | Rôle |
|---|---|---|
| `ALERT_COOLDOWN` | 60s | Délai minimum entre deux alertes globales |
| `DISAPPEARANCE_TIMEOUT` | 12s | Délai max avant déclenchement CORPS |
| `SAC_DISAPPEARANCE_TIMEOUT` | 2s | Délai de grâce initial pour le SAC |
| `SAC_DISAPPEARANCE_PATIENCE` | 5s | Attente avant validation disparition SAC |
| `LOITERING_THRESHOLD` | 120s | Durée avant détection flânerie |
| `DISPLAY_TEXT_DURATION` | 4s | Durée d'affichage du texte d'alerte |
| `BEFORE_ALERT_SECS` | 16s | Durée du pré-buffer vidéo avant alerte |
| `AFTER_ALERT_SECS` | 4s | Durée d'enregistrement après alerte |

### Tracking articles

| Variable | Valeur | Rôle |
|---|---|---|
| `TRACKER_MISS_TOLERANCE` | 180 frames | Frames manquées avant de perdre un track article |
| `ARTICLE_DETECTED_HOLD_THRESHOLD` | 20 frames | Consécutives pour considérer un article "tenu" |
| `HOLD_STREAK_THRESHOLD` | 20 frames | Streak de mouvement corrélé pour confirmer la tenue |
| `HOLD_STREAK_MISS_MAX` | 10 frames | Frames sans corrélation avant de reset le streak |
| `CONSECUTIVE_MISS_MAX` | 8 frames | Frames manquées avant de reset le compteur consécutif |
| `MIN_DISAPPEARANCE_FRAMES` | 36 frames | Absence minimum avant entrée en suspicion CORPS |
| `REAPPEARANCE_FRAMES_MIN` | 3 frames | Réapparition confirmée → annulation suspicion |
| `PRESENCE_FRAMES_FOR_ABSENCE_RESET` | 3 frames | Présence confirmée → reset compteur absence |

### Tracking personnes

| Variable | Valeur | Rôle |
|---|---|---|
| `PERSON_MISS_TOLERANCE` | 12 frames | Frames manquées avant de perdre un track personne |

### Détection de main

| Variable | Valeur | Rôle |
|---|---|---|
| `HAND_MEMORY_FRAMES` | 20 frames | Fenêtre mémorielle pour vérifier contact de main |
| `HAND_ARTICLE_DIST` | 35 px | Distance max main/article pour confirmer le contact |
| `HOLD_CONF_MIN` | 0.25 | Confiance YOLO moyenne minimum pour valider la tenue |
| `HOLD_CONF_HISTORY_LEN` | 20 frames | Fenêtre glissante pour la moyenne de confiance |

### Scénario SAC

| Variable | Valeur | Rôle |
|---|---|---|
| `SAC_PROXIMITY_FRAMES_MIN` | 14 frames | Proximité article/sac minimum pour valider |
| `SAC_PROXIMITY_DIST` | 30 px | Distance de référence article/sac |
| `STATIC_BAG_FRAME_THRESHOLD` | 20 frames | Seuil pour filtrer les sacs fixes |

### Mouvement corrélé

| Variable | Valeur | Rôle |
|---|---|---|
| `MOVEMENT_HISTORY_FRAMES` | 6 frames | Fenêtre pour calculer la corrélation de mouvement |
| `MOVEMENT_CORRELATION_MIN` | 0.6 | Produit scalaire normalisé minimum (article suit la personne) |

### GPU et batch

| Variable | Valeur | Rôle |
|---|---|---|
| `BATCH_TIMEOUT_SECS` | 80 ms | Attente max pour constituer un batch GPU complet |
| `BATCH_QUEUE_MAXSIZE` | 14 (2×cam) | Taille max de la queue GPU (évite saturation RAM) |
| `SUSPICION_TTL` | 30s | Durée de vie d'une suspicion dans `/suspicions` |

### Score CORPS

```
score = 0.4 × last_conf_YOLO + 0.6 × min(1.0, hold_snapshot / 60)
score += 0.25  si flânerie confirmée
score  capped à 1.0

hold_snapshot = hold_durations au moment de la disparition
               (ne s'incrémente plus pendant l'invisibilité)
```

---

## API Flask — Endpoints disponibles

```
GET  /video/<cam_id>          → Flux vidéo MJPEG annoté en temps réel
                                 Ex: http://192.168.0.97:5000/video/CAM_21

GET  /alerts?last=50          → Liste des alertes enregistrées (JSON)
                                 ?last=N → N dernières alertes

GET  /suspicions              → Suspicions actives en cours (< 30s)
                                 { "CAM_21": { "type": "CORPS", "score": 0.73 } }

GET  /logs?cam=CAM_21         → Logs de debug en mémoire (2000 entrées max)
     &level=DEBUG             → Filtrable par caméra et niveau
     &last=200

POST /snapshot                → Capture un JPEG propre (sans annotations)
     {"cam_id": "CAM_21"}     → Répond { "file": "snapshots/CLEAN_CAM_21_..." }
```

### Format d'une alerte (alerts.jsonl)

```json
{
  "cam":        "CAM_21",
  "type":       "CORPS",
  "score":      0.74,
  "status":     "alerte",
  "time":       "14:23:07",
  "video_clip": "alert_clips/CAM_21_Vole_CORPS_142307.mp4",
  "video_raw":  "alert_clips/raw/CAM_21_RAW_CORPS_142307.mp4"
}
```

---

## Gestion des fichiers et clips vidéo

### Structure des dossiers

```
./
├── alerts.jsonl              ← Log de toutes les alertes déclenchées
├── logs.jsonl                ← Log de debug complet (max 50 000 lignes)
├── snapshots/                ← Photos JPEG à la demande (/snapshot)
│   └── CLEAN_CAM_21_<ts>.jpg
└── alert_clips/
    ├── CAM_21_Vole_CORPS_142307.mp4   ← Vidéo annotée (bounding boxes)
    └── raw/
        └── CAM_21_RAW_CORPS_142307.mp4 ← Vidéo brute sans annotations
```

### Structure d'un clip vidéo

```
[──────── 16s avant ────────][alerte][── 4s après ──]
         pré-buffer                   post-buffer
    (video_buffer, maxlen)        (frames_to_record_after)

Codec : h264_nvenc (GPU NVIDIA)
Qualité : 1 Mbps, preset p1 (ultra-rapide)
Format : yuv420p (compatible lecture universelle)
```

### Purge automatique

- **Rétention :** 30 jours (`CLIP_RETENTION_DAYS`)
- **Vérification :** toutes les heures (`PURGE_INTERVAL_SECS`)
- **Espace minimum requis :** 5 Go (`DISK_MIN_FREE_GB`)
- **Purge d'urgence :** si espace < 5 Go, supprime les 30% de clips les plus anciens

---

