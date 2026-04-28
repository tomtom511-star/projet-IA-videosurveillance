
# 🔄 GUIDE : LA BOUCLE D'AMÉLIORATION CONTINUE (SOP)
Ce guide contient toutes les informations nécéssaire pour l'amélioration continu du projet, vous trouverez ci-dessous les étapes à suivre pour améliorer les modèles d'IA.

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

Upload : Envoi ces images dans ton projet Roboflow Global.
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
ATTENTION: Faire gaffe aux lignes 6 et 7 avec le chemin
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
