from pathlib import Path
import cv2
import numpy as np

root = Path(__file__).resolve().parent / 'reference'
root.mkdir(exist_ok=True)
for name, color in [('youtube_mobile.png', (240, 245, 250)), ('youtube_pc.png', (245, 245, 245))]:
    image = np.full((1080, 1920, 3), color, dtype=np.uint8)
    cv2.rectangle(image, (0, 0), (1920, 140), (255, 255, 255), -1)
    cv2.rectangle(image, (80, 170), (430, 260), (245, 245, 245), -1)
    cv2.rectangle(image, (80, 300), (1800, 380), (230, 230, 230), -1)
    cv2.rectangle(image, (80, 430), (1700, 520), (225, 225, 225), -1)
    cv2.rectangle(image, (80, 560), (1600, 650), (220, 220, 220), -1)
    cv2.putText(image, 'YouTube', (90, 95), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 2)
    cv2.putText(image, 'Channel Name', (90, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
    cv2.putText(image, 'Subscribe', (90, 360), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)
    cv2.putText(image, 'Recommended', (90, 490), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 40), 2)
    cv2.putText(image, 'Shorts', (90, 620), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 40), 2)
    cv2.imwrite(str(root / name), image)
    print(f'created {root / name}')
