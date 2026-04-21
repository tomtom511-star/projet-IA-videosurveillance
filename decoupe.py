import cv2
import os
from pathlib import Path

# --- CONFIGURATION ---
root_folder = 'Data_global' 
output_dir = Path('Dataset_Specialiste')
padding = 25
ID_PERSONNE = 3 

(output_dir / 'images').mkdir(parents=True, exist_ok=True)
(output_dir / 'labels').mkdir(parents=True, exist_ok=True)

def get_bbox_from_segmentation(parts):
    """Transforme une liste de points de segmentation en [x_center, y_center, width, height]"""
    # On ignore l'ID de classe (parts[0])
    points = list(map(float, parts[1:]))
    # On sépare les X (indices pairs) et les Y (indices impairs)
    xs = points[0::2]
    ys = points[1::2]
    
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    
    w = x_max - x_min
    h = y_max - y_min
    x_c = x_min + w/2
    y_c = y_min + h/2
    
    return x_c, y_c, w, h

def remap_annotations(labels_path, x1_crop, y1_crop, w_crop, h_crop, w_orig, h_orig):
    new_labels = []
    if not labels_path.exists(): return new_labels
    
    with open(labels_path, 'r') as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3: continue # Ligne vide ou malformée
            
            cls = int(float(parts[0]))
            if cls == ID_PERSONNE: continue
            
            # On récupère la BBox (même si c'est de la segmentation à l'origine)
            x_c, y_c, w_n, h_n = get_bbox_from_segmentation(parts)
            
            px_c, py_c = x_c * w_orig, y_c * h_orig
            pw, ph = w_n * w_orig, h_n * h_orig
            
            # L'objet est-il dans le crop ?
            if (x1_crop < px_c < x1_crop + w_crop) and (y1_crop < py_c < y1_crop + h_crop):
                new_x = (px_c - x1_crop) / w_crop
                new_y = (py_c - y1_crop) / h_crop
                new_w = pw / w_crop
                new_h = ph / h_crop
                new_labels.append(f"{cls} {new_x} {new_y} {new_w} {new_h}\n")
    return new_labels

# --- BOUCLE ---
image_paths = list(Path(root_folder).rglob('*.jpg')) + list(Path(root_folder).rglob('*.png'))

print(f"Conversion Segmentation -> BBox pour {len(image_paths)} images...")

for img_path in image_paths:
    label_path = img_path.parent.parent / 'labels' / f"{img_path.stem}.txt"
    if not label_path.exists(): continue

    img = cv2.imread(str(img_path))
    if img is None: continue
    h0, w0, _ = img.shape
    
    with open(label_path, 'r') as f:
        for line in f:
            parts = line.split()
            if not parts: continue
            cls = int(float(parts[0]))
            
            if cls == ID_PERSONNE:
                # On calcule la zone de découpe à partir de la segmentation de la personne
                x_c, y_c, w_n, h_n = get_bbox_from_segmentation(parts)
                
                cx, cy, cw, ch = x_c*w0, y_c*h0, w_n*w0, h_n*h0
                x1 = int(max(0, cx - cw/2 - padding))
                y1 = int(max(0, cy - ch/2 - padding))
                x2 = int(min(w0, cx + cw/2 + padding))
                y2 = int(min(h0, cy + ch/2 + padding))
                
                crop_img = img[y1:y2, x1:x2]
                if crop_img.size == 0: continue
                
                annos = remap_annotations(label_path, x1, y1, x2-x1, y2-y1, w0, h0)
                
                if annos:
                    name = f"{img_path.stem}_crop_{x1}_{y1}"
                    cv2.imwrite(str(output_dir / 'images' / f"{name}.jpg"), crop_img)
                    with open(output_dir / 'labels' / f"{name}.txt", 'w') as f_out:
                        f_out.writelines(annos)

print("C'est terminé ! Tu as transformé tes polygones en rectangles découpés.")