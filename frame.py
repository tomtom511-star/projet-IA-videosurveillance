from ultralytics import YOLO
import cv2
import os
import torch
from datetime import datetime

# --- CONFIGURATION GPU ---
device = 'cuda' if torch.cuda.is_available() else 'cpu'
model = YOLO("runs/detect/train/weights/best.pt").to(device)

# --- CONFIGURATION DU DOSSIER DE CAPTURE ---
# On crée le dossier frame_video s'il n'existe pas
output_frames_dir = "frame_video"
os.makedirs(output_frames_dir, exist_ok=True)

cap = cv2.VideoCapture("vidéos/test4.mp4")

# Compteur pour nommer les images
frame_count = 0
timestamp_session = datetime.now().strftime("%Y%m%d_%H%M%S")

while True:
    ret, frame = cap.read() # Image originale
    if not ret: break

    frame_count += 1

    # --- SAUVEGARDE DANS "frame_video" ---
    # On enregistre une image toutes les 30 frames (environ 1 seconde)
    if frame_count % 60 == 0:
        # Nom de l'image : frame_SESSION_NUMERO.jpg
        img_name = f"frame_{timestamp_session}_{frame_count}.jpg"
        save_path = os.path.join(output_frames_dir, img_name)
        
        # Sauvegarde de l'image BRUTE (sans dessins YOLO)
        cv2.imwrite(save_path, frame)
        print(f"Image sauvegardée : {img_name}")

    # --- ANALYSE IA (Juste pour l'affichage) ---
    results = model.track(frame, persist=True, tracker="bytetrack.yaml", imgsz=640)
    
    # On récupère l'image avec les boîtes pour la montrer à l'écran
    annotated_frame = results[0].plot()

    # Affichage
    cv2.imshow("Capture et Detection", annotated_frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()