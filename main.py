"""Run the OpenCV viewer; use app.py for the browser dashboard."""

import argparse
import json

import cv2

from detector import DEFAULT_VIDEO, ParkingDetector


def main():
    parser = argparse.ArgumentParser(description="YOLO parking space detector")
    parser.add_argument("--source", default=str(DEFAULT_VIDEO), help="Video path or camera index")
    parser.add_argument("--headless", action="store_true", help="Process without an OpenCV window")
    parser.add_argument("--max-frames", type=int, default=0, help="Stop after N frames (0 = whole video)")
    args = parser.parse_args()
    source = int(args.source) if args.source.isdecimal() else args.source
    cap = cv2.VideoCapture(source)
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video source: {args.source}")
        detector = ParkingDetector()
        processed = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame, counts = detector.analyze(frame)
            processed += 1
            if not args.headless:
                cv2.putText(frame, f"Occupied: {counts['occupied']}   Available: {counts['available']}",
                            (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
                cv2.imshow("Smart Parking - Q to exit", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if args.max_frames and processed >= args.max_frames:
                break
        if not processed:
            raise RuntimeError("Video source opened but did not provide a readable frame.")
        print(json.dumps({"processed_frames": processed, "device": detector.device, **counts}))
    finally:
        cap.release()
        if not args.headless:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
