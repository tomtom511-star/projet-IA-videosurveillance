# 📡 NOTICE TECHNIQUE : RADAR INTELLIGENT

Ce document récapitule la méthodologie exacte utilisée pour le déploiement des deux modèles YOLO sur notre infrastructure (Quadro P2200 + NVENC).

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

# 🔁 PHASE 4 : CYCLE DE VIE (ALERTE & AMÉLIORATION)

## 🎯 Objectif
Détection en temps réel et correction des erreurs.

## 📹 Script detect_obj.py

Combine le Radar (global) et le Spécialiste (zoom).

Logique :
- Hold Time > 8 frames  
- Centralité Corps  
- Patience 12s  

## 🎥 Double Enregistrement NVENC

- Vole_XXX.mp4 : Vidéo avec carrés YOLO (pour l'analyse)
- RAW_XXX.mp4 : Vidéo source vierge (pour le dataset futur)

---

# 🔄 GUIDE : LA BOUCLE D'AMÉLIORATION CONTINUE (SOP)

## 🧪 ÉTAPE 1 : Identification et Extraction

Repérage : On analyse les vidéos dans 'alert_clips/'.  
Récupération : 'alert_clips/raw/'  

Extraction :
- 1 image / seconde
- uniquement les erreurs visibles
Pour cela on se sert du code frame.py : 
ATTENTION : bien changer la ligne 14 par le bon chemin de la vidéo

On lance dans le terminal : `python3 frame.py`

---

## 🧠 ÉTAPE 2 : Mise à jour du Dataset Global (Radar)

Upload Roboflow Global.

Upload : Tu envoies ces images dans ton projet Roboflow Global.
Correction : Corrige ou ajoute les labels (ID 3 pour la personne, et les autres pour les mains/sacs/articles).
Génération : Créer une Nouvelle Version sur Roboflow. Garde tes paramètres d'augmentation (Blur, Noise, Light) pour que le modèle reste robuste.
Export : Télécharge le nouveau data.yaml et les images et renomme le Data_global_vX avec X la version du dataset 
Transport : Déplace le dans le dossier du projet
---

## 🔧 ÉTAPE 3 : améliorer le radar global

pas besoin ici car les personnes sont suffisamment bien détectée

---

## ✂️ ÉTAPE 4 : Préparation du Spécialiste

Relancer le script de découpe sur tes nouvelles images en adaptant le script decoupe.py :
    Il faut changer les ligne 6 et 7 en ajoutant les version (ex: Data_global_vX ou bien Dataset_specialiste_vX)
    On lance 
    `python3 decoupe.py`

Sur vs code, sur le dossier créé par le script de découpe on fait clic droit new file : data.yaml => ici c'est le meme que la version du radar spécialiste antérieur (sauf si ajout ou suppression de classes) donc on copie colle. ATTENTION: Bien penser à vérifier le chemin de la ligne 1 pour que se soit le bon dossier !!

Pour tester que cela fonctionne on lance le script verif.py (ATTENTION: changer les lignes 7 et 8 par le bon dossier)
`python3 verif.py`
Cela va nous donner une image annotée dans le fichier global du projet, vérifier que c'est bien annoter (classes valides)

Ensuite va dans le dossier du modèle spécialiste:
`cd Dataset_Specialiste_vX`

Puis on crée les dossiers pour séparer les données (valid et train):
`mkdir -p images/train images/val labels/train labels/val`

Split : Lancement du script de séparation pour isoler 80% des images pour le train et 20% pour le valid:
ATTENTION: Faire gaffe au ligne 6 et 7 avec le chemin
`python3 split.py`


---

## 🚀 ÉTAPE 5 : Ré-entraînement du SPÉCIALISTE

 Même logique, on repart du dernier meilleur spécialiste.
`yolo task=detect mode=train model=runs/detect/radar_specialiste_v(X-1)/weights/best.pt data=Dataset_Specialiste_vX/data.yaml epochs=200 patience=50 imgsz=640 batch=-1 mosaic=1.0 mixup=0.2 cos_lr=True close_mosaic=10 name=specialiste_final_vX`

### 🧠 best.pt vs last.pt

#### best.pt
    C'est la version qui a eu les meilleurs scores de précision lors des tests de validation.
    Pour le Fine-Tuning. C'est le cerveau le plus "brillant" que l'on a produit. C'est la base pour devenir encore meilleur.

#### last.pt
    C'est l'image exacte du modèle à la toute dernière époque de l'entraînement. 
    Pour la reprise après crash. Si l'entraînement a duré 20h et que le PC a planté, on reprend le last.pt pour finir les époques restantes.

---

## 🔄 ÉTAPE 6 : Mise à jour detect_obj.py (SWAP)

On ne déplace rien.

On met juste à jour dans le script detect_obj.py:
- model_radar  
- model_specialiste  

→ vers les nouveaux 'best.pt'
