import os
import random
import shutil

# Chemins
image_dir = "Dataset_Specialiste_v2/images"
label_dir = "Dataset_Specialiste_v2/labels"

# Liste tous les fichiers images
images = [f for f in os.listdir(image_dir) if f.endswith('.jpg')]
random.shuffle(images)

# Calcul du split (20% pour la validation)
split = int(len(images) * 0.2)
val_images = images[:split]
train_images = images[split:]

def move_files(files, folder_name):
    for f in files:
        # Déplacer l'image
        shutil.move(os.path.join(image_dir, f), os.path.join(image_dir, folder_name, f))
        # Déplacer le label correspondant (.txt)
        label_f = f.rsplit('.', 1)[0] + '.txt'
        if os.path.exists(os.path.join(label_dir, label_f)):
            shutil.move(os.path.join(label_dir, label_f), os.path.join(label_dir, folder_name, label_f))

move_files(val_images, 'val')
move_files(train_images, 'train')
print("Split terminé !")