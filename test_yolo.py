import cv2
import os
from ultralytics import YOLO

model_dir = "models"
yolo_model_path = os.path.join(model_dir, "best.pt")
if not os.path.exists(yolo_model_path):
    print("No best.pt")

if os.path.exists(yolo_model_path):
    model = YOLO(yolo_model_path)
    print(model.names)
