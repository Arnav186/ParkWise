"""Edit parking regions: four left clicks add a space; right click removes one."""

from pathlib import Path
import pickle

import cv2
import numpy as np


PROJECT_DIR = Path(__file__).resolve().parent
ROI_PATH = PROJECT_DIR / "Space_ROIs"
REFERENCE_PATH = PROJECT_DIR / "ROI_Reference.png"
WINDOW_NAME = "Parking space selection (Q to quit)"

posList = []
polygon_points = []


def save_positions():
    with ROI_PATH.open("wb") as handle:
        pickle.dump(posList, handle)


def mouseClick(event, x, y, flags, params):
    if event == cv2.EVENT_LBUTTONDOWN:
        polygon_points.append((x, y))
        if len(polygon_points) == 4:
            posList.append(polygon_points.copy())
            save_positions()
            polygon_points.clear()
    elif event == cv2.EVENT_RBUTTONDOWN:
        for index, polygon in enumerate(posList):
            if cv2.pointPolygonTest(np.array(polygon, dtype=np.int32), (x, y), False) >= 0:
                posList.pop(index)
                save_positions()
                break


def main():
    global posList

    reference = cv2.imread(str(REFERENCE_PATH))
    if reference is None:
        raise FileNotFoundError(f"Cannot read parking reference image: {REFERENCE_PATH}")

    try:
        with ROI_PATH.open("rb") as handle:
            posList = pickle.load(handle)
    except FileNotFoundError:
        posList = []
    polygon_points.clear()

    try:
        cv2.namedWindow(WINDOW_NAME)
        cv2.setMouseCallback(WINDOW_NAME, mouseClick)
        while True:
            frame = reference.copy()
            for polygon in posList:
                points = np.array(polygon, dtype=np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [points], True, (0, 0, 255), 2)
            for point in polygon_points:
                cv2.circle(frame, point, 5, (0, 255, 0), -1)

            cv2.imshow(WINDOW_NAME, frame)
            if cv2.waitKey(20) & 0xFF == ord("q"):
                break
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
