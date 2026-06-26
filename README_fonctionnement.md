# 🔒 Système de Détection de Vol — Principe de fonctionnement


## Table des matières

- [Architecture](#architecture)
- [Accès à l'interface](#accès-à-linterface)
- [Accès au Dataset](#accès-au-dataset)
- [Commandes de diagnostic](#commandes-de-diagnostic-via-ssh)
- [Commandes de contrôle des services](#commandes-de-contrôle-des-services)
- [Endpoints Flask utiles](#endpoints-flask-utiles-moteur-de-détection)
- [Fichiers importants à modifier](#fichiers-importants-à-modifier)
- [Caméras surveillées](#caméras-surveillées)
- [Contrôle du son](#contrôle-du-son)
- [Dossiers de données](#dossiers-de-données)
- [Changer le mot de passe](#changer-le-mot-de-passe)
- [Problèmes fréquents](#problèmes-fréquents)



## Architecture

Le système repose sur deux services qui tournent **automatiquement au démarrage** de la machine `surveillance-ia` (HP Z2 Tower G4), gérés par systemd :

| Service | Rôle | Port |
|---------|------|------|
| `surveillance-streamlit` | Interface web (app.py) | 8501 |
| `surveillance-detection` | Moteur IA YOLO (detect_obj.py) | 5000 |

> **Ne plus jamais lancer ces programmes manuellement.** Systemd s'en charge au boot.

---

## Accès à l'interface

| Contexte | URL |
|----------|-----|
| Machine physique locale | http://127.0.0.1:8501 ou http://leclairvoyant.fr |
| Accès distant (réseau interne) | http://192.168.0.97/ |

---

## Accès au DATASET

Se rendre sur le site https://app.roboflow.com et se connecter avec l'adresse email : `info.olivetdis@yahoo.com`

---

## Commandes de diagnostic (via SSH)

Se connecter en SSH sur la machine :
```bash
ssh surveillance-ia@192.168.0.97
```

### Vérifier que les services tournent
```bash
sudo systemctl status surveillance-streamlit
sudo systemctl status surveillance-detection
```
Les deux doivent afficher `Active: active (running)` en vert.

### Voir les logs en temps réel
```bash
# Logs du moteur de détection (erreurs, alertes, GPU...)
sudo journalctl -u surveillance-detection -f

# Logs de l'interface Streamlit
sudo journalctl -u surveillance-streamlit -f

# Quitter avec Ctrl+C
```

### Voir les dernières lignes de logs (sans suivi temps réel)
```bash
sudo journalctl -u surveillance-detection -n 50 --no-pager
sudo journalctl -u surveillance-streamlit -n 50 --no-pager
```

---

## Commandes de contrôle des services

### Redémarrer après une modification du code
```bash
sudo systemctl restart surveillance-detection
sudo systemctl restart surveillance-streamlit
```

### Arrêter manuellement
```bash
sudo systemctl stop surveillance-detection
sudo systemctl stop surveillance-streamlit
```

### Redémarrer les deux d'un coup
```bash
sudo systemctl restart surveillance-detection surveillance-streamlit
```

### Désactiver le démarrage automatique (si besoin temporairement)
```bash
sudo systemctl disable surveillance-detection
sudo systemctl disable surveillance-streamlit
```

### Réactiver le démarrage automatique
```bash
sudo systemctl enable surveillance-detection
sudo systemctl enable surveillance-streamlit
```

---

## Endpoints Flask utiles (moteur de détection)

Base : `http://192.168.0.97:5000`

| URL | Méthode | Description |
|-----|---------|-------------|
| `/alerts?last=50` | GET | 50 dernières alertes |
| `/suspicions` | GET | Suspicions actives en temps réel |
| `/logs?cam=CAM_21&level=DEBUG` | GET | Logs debug d'une caméra |
| `/snapshot` | POST `{"cam_id": "CAM_21"}` | Capture brute sans annotations |
| `/sound/status` | GET | État du son (activé/désactivé) |
| `/sound/toggle` | POST | Basculer le son ON/OFF |

Exemples depuis un terminal :
```bash
# Vérifier l'état du son
curl http://192.168.0.97:5000/sound/status

# Basculer le son
curl -X POST http://192.168.0.97:5000/sound/toggle

# Voir les 10 dernières alertes
curl http://192.168.0.97:5000/alerts?last=10
```

---

## Fichiers importants à modifier

### `detect_obj.py` — Moteur de détection
Fichier principal du cerveau IA. Paramètres clés en haut du fichier :

| Constante | Valeur actuelle | Rôle |
|-----------|----------------|------|
| `BATCH_TIMEOUT_SECS` | 0.060 | Fenêtre de collecte GPU (ne pas dépasser 0.075) |
| `ALERT_COOLDOWN` | 60s | Délai minimum entre deux alertes |
| `DISAPPEARANCE_TIMEOUT` | 9.0s | Durée avant déclenchement alerte CORPS |
| `BEFORE_ALERT_SECS` | 13s | Secondes de pré-buffer dans le clip |
| `AFTER_ALERT_SECS` | 7s | Secondes enregistrées après l'alerte |
| `TRACKER_MISS_TOLERANCE` | 134 frames | Tolérance de perte d'un article (~11s à 12 FPS) |
| `DEBUG_LOGS` | True | Passer à False en production pour réduire le CPU |

Pour mettre à jour les modèles YOLO (après réentraînement), modifier ces deux lignes :
```python
model_radar = YOLO("runs/detect/radar_global_v2/weights/best.pt")
model_specialist = YOLO("runs/detect/radar_specialiste_v5/weights/best.pt")
```
Puis redémarrer le service :
```bash
sudo systemctl restart surveillance-detection
```



### Fichiers systemd — Configuration des services
```
/etc/systemd/system/surveillance-streamlit.service
/etc/systemd/system/surveillance-detection.service
```
Après toute modification de ces fichiers :
```bash
sudo systemctl daemon-reload
sudo systemctl restart surveillance-streamlit   # ou surveillance-detection
```

---

## Caméras surveillées

| ID | IP | Zone |
|----|----|------|
| CAM_21 | 10.21.9.21 | Rayon alcool fort |
| CAM_22 | 10.21.9.22 | Vins |
| CAM_23 | 10.21.9.23 | Champagnes |
| CAM_45 | 10.21.9.45 | Espace culturel — Vue globale |
| CAM_46 | 10.21.9.46 | Électronique / divertissement |
| CAM_47 | 10.21.9.47 | Audio |
| CAM_49 | 10.21.9.49 | Jeux vidéo |

Flux vidéo direct : `http://192.168.0.97:5000/video/CAM_21` (remplacer CAM_21 par l'ID voulu)


### CAS : changement de caméras
Interface web. Si les IPs des caméras changent, mettre à jour le dictionnaire `cameras` dans la section `PAGE LIVE` :
```python
cameras = {
    "🍾 Alcool": [
        {"id": "CAM_21", "name": "...", "url": "/video/CAM_21"},
        ...
    ],
    ...
}
```

Mais aussi dans le dictionnaire CAMERAS tout en haut de `detect_obj.py` 
```python
    {
        "cam_id":   "CAM_21",
        "rtsp_url": "...",
        "width":    704,
        "height":   576,
        "fps":      12,
    },
```

---

## Contrôle du son

Le son ne peut plus être contrôlé via Ctrl+B (pas de terminal interactif avec systemd).
Deux alternatives :

**Depuis l'interface web** — bouton "Basculer le son" dans la sidebar.

**Depuis un terminal SSH :**
```bash
curl -X POST http://192.168.0.97:5000/sound/toggle
```

---

## Dossiers de données

| Dossier | Contenu |
|---------|---------|
| `alert_clips/` | Clips annotés des alertes |
| `alert_clips/raw/` | Clips bruts sans annotations |
| `alert_clips/VP/` | Clips classés Vrai Positif |
| `alert_clips/FP/` | Clips classés Faux Positif |
| `snapshots/` | Captures manuelles |
| `alerts.jsonl` | Historique de toutes les alertes |
| `logs.jsonl` | Historique des logs système |

---

## Changer le mot de passe

Les identifiants sont définis en clair dans `app.py`, vers la ligne 20 :

```python
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin"
```

Modifie ces deux valeurs, puis redémarre le service :

```bash
sudo systemctl restart surveillance-streamlit
```

> ⚠️ Ces identifiants sont stockés en clair dans le code source. Ne pas utiliser un mot de passe sensible réutilisé ailleurs.


## Problèmes fréquents

**Un service est en erreur (`failed`) :**
```bash
sudo journalctl -u surveillance-detection -n 50 --no-pager
# Lire le message d'erreur et corriger le code ou la config
sudo systemctl restart surveillance-detection
```

**Le port 8501 est déjà occupé :**
```bash
pkill -f "streamlit run app.py"
sudo systemctl restart surveillance-streamlit
```

**Le GPU n'est pas détecté :**
```bash
nvidia-smi   # vérifier que le GPU est visible
# Si le service est démarré trop tôt au boot, ajouter un délai :
# Modifier ExecStart dans le .service avec : ExecStartPre=/bin/sleep 10
```

**Voir l'utilisation CPU/GPU en temps réel :**
```bash
htop #CPU
nvidia-smi dmon -s u     # utilisation GPU en continu
```