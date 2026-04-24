import cv2
import os
from pathlib import Path

# 1. Choisir une image et son label au hasard (ou une spécifique)
# On prend la première image du dossier pour tester
image_folder = Path('Dataset_Specialiste_v2/images')
label_folder = Path('Dataset_Specialiste_v2/labels')

# On cherche une image qui a un label non vide pour que ce soit intéressant
img_list = list(image_folder.glob('*.jpg'))
test_img = None
test_label = None

for img_p in img_list:
    lbl_p = label_folder / f"{img_p.stem}.txt"
    if lbl_p.exists() and lbl_p.stat().st_size > 0:
        test_img = str(img_p)
        test_label = str(lbl_p)
        break

if not test_img:
    print("Aucun crop avec des objets trouvé !")
    exit()

# 2. Lecture et dessin
img = cv2.imread(test_img)
h, w, _ = img.shape
names = {0: 'article', 1: 'bags', 2: 'hands'}

with open(test_label, 'r') as f:
    for line in f:
        cls, x, y, nw, nh = map(float, line.split())
        # Conversion YOLO -> Pixels
        x1 = int((x - nw/2) * w)
        y1 = int((y - nh/2) * h)
        x2 = int((x + nw/2) * w)
        y2 = int((y + nh/2) * h)
        
        # Dessin du rectangle
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
        # Label texte
        label_text = names.get(int(cls), str(int(cls)))
        cv2.putText(img, label_text, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

# 3. Sauvegarde au lieu de l'affichage
output_path = 'verification_crop.jpg'
cv2.imwrite(output_path, img)

print(f"Vérification terminée ! Ouvre le fichier '{output_path}' pour voir le résultat.")