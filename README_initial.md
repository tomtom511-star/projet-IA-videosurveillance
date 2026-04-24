# 📡 NOTICE TECHNIQUE : RADAR INTELLIGENT

Ce document récapitule la méthodologie exacte utilisée pour le déploiement des deux modèles YOLO sur l'infrastructure (Quadro P2200 + NVENC).

---

# 🧠 PHASE 1 : MODÈLE 1 - LE RADAR (radar_global_v1)

## 🎯 Objectif
Détecter uniquement les personnes sur le flux grand angle pour définir les zones de crop.

## 🗂️ Roboflow :

Upload des images "grand angle" de ta caméra.

Annotation : tout annoter sur le dataset général (article, mains, sacs, et personnes).

Génération du dataset :  
Augmentation : Pour simuler les conditions réelles, ajoute les filtres :

- Noise (Bruit) : Simule le grain des caméras de nuit (+3%)
- Blur (Flou) : Simule les mouvements rapides (1.5px)
- Brightness (Lumière) : Simule les variations d'éclairage du magasin (-25% à +25%)

=> 'Exporter en zip sur le pc puis le dézipper'  
=> 'Renommer le dossier en Data_global et le coller dans le dossier du projet'

Vérification dans data.yaml que la classe Personne correspond bien à l’ID 3.

## 🚀 Entraînement ciblé :

Lancement de l'entraînement en forçant l’IA à ignorer tout le reste :

### 💻 Terminal

`cd projet-IA-videosurveillance`  
`source venv/bin/activate`
`yolo task=detect mode=train model=yolo11n.pt data=Data_global/data.yaml epochs=600 patience=100 imgsz=640 classes=3 mosaic=0.5 degrees=10.0 cos_lr=True name=radar_global_v1`

## 📁 Organisation :

Une fois l'entraînement fini, le dossier est généré dans runs/detect/

Le fichier 'best.pt' est utilisé comme "déclencheur" dans le script principal.

---

# ✂️ PHASE 2 : PRÉPARATION DU DATASET "SPÉCIALISTE"

## 🎯 Objectif
Créer la matière première pour le second modèle à partir des détections du premier.

## 🧩 Script de découpe (decoupe.py)

Le modèle radar_global_v1 analyse tes vidéos/images de test.
Pour chaque détection de classe ID 3 (Personne), le script extrait un rectangle de l'image.
Padding : Ajout d'une marge de sécurité (ex: 20px) pour capturer les mains et les objets portés.

### 💻 Terminal

`python3 decoupe.py`

## 📁 Organisation des données (split.py)

Sur vs code, sur le dossier créé par le script de découpe on fait clic droit new file : data.yaml => ici c'est le meme que la version du radar global sauf qu'on ignore la classes personne (on la supprime) . ATTENTION: Bien penser à vérifier le chemin de la ligne 1 par le bon dossier !!

Pour tester que cela fonctionne on lance le script verif.py (ATTENTION: changer les lignes 7 et 8 par le bon dossier)
`python3 verif.py`
Cela va nous donner une image annotée dans le fichier global du projet, vérifier que c'est bien annoter (classes valides)

Ensuite va dans le dossier du modèle spécialiste:
`cd Dataset_Specialiste_v1`

Puis on crée les dossiers pour séparer les données (valid et train):
`mkdir -p images/train images/val labels/train labels/val`

Split : Lancement du script de séparation pour isoler 80% des images pour le train et 20% pour le valid:
`python3 split.py`

---

# 🎯 PHASE 3 : MODÈLE 2 - LE SPÉCIALISTE (radar_specialiste_v1)

## 🎯 Objectif
Devenir expert en détection de détails sur images zoomées.

## 🧠 Entraînement focalisé

On entraîne ce modèle sur les crops de la Phase 2.

Comme il "hérite" des labels du modèle global, il sait déjà ce qu'est une main ou un article,  
mais il apprend ici à les reconnaître avec une bien meilleure résolution.

### 💻 Terminal

`yolo task=detect mode=train model=yolo11n.pt data=Dataset_Specialiste/data.yaml epochs=300 imgsz=640 batch=-1 mosaic=1.0 mixup=0.2 cos_lr=True close_mosaic=10 name=radar_specialisete_v1`

---

# 🔁 PHASE 4 : CYCLE DE VIE (code principal)

## 📹 Script detect_obj.py

Combine le Radar (global) et le Spécialiste (zoom).

Logique :
- Hold Time > 8 frames  
- Centralité Corps  
- Patience 12s  

 Double enregistrement: 
- Vole_XXX.mp4 : Vidéo avec carrés YOLO (pour l'analyse)
- RAW_XXX.mp4 : Vidéo source vierge (pour le dataset futur)

---

