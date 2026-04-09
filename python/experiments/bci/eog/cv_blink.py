"""
cv_blink.py — dlib-only webcam blink detector

python cv_blink.py --model shape_predictor_68_face_landmarks.dat 
"""

import os
import time
import collections
import threading

import cv2
import dlib
import numpy as np


# dlib eye landmark indices (68-point model)
_LEFT_EYE_IDX = list(range(36, 42))   # 36–41
_RIGHT_EYE_IDX = list(range(42, 48))  # 42–47

_DEFAULT_MODEL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "shape_predictor_68_face_landmarks.dat"
)


def _shape_to_np(shape, indices):
    return np.array(
        [(shape.part(i).x, shape.part(i).y) for i in indices],
        dtype=np.float32
    )


def _ear_from_pts(pts):
    """
    Eye Aspect Ratio from 6 eye landmark points.
    EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
    """
    A = np.linalg.norm(pts[1] - pts[5])
    B = np.linalg.norm(pts[2] - pts[4])
    C = np.linalg.norm(pts[0] - pts[3])
    return float((A + B) / (2.0 * C + 1e-6))


class CVBlinkDetector:
    """
    dlib-based webcam blink detector.

    Parameters
    ----------
    ear_threshold : float
        EAR below this means eye is closed
    debounce_s : float
        Minimum time between counted blinks
    camera_idx : int
        OpenCV camera index
    model_path : str
        Path to shape_predictor_68_face_landmarks.dat
    monitor_secs : float
        Log history length
    """

    def __init__(
        self,
        ear_threshold: float = 0.19,
        debounce_s: float = 0.25,
        camera_idx: int = 0,
        model_path: str = _DEFAULT_MODEL,
        monitor_secs: float = 60.0,
    ):
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Could not find dlib model:\n{model_path}"
            )

        self.ear_threshold = ear_threshold
        self._debounce_s = debounce_s
        self._camera_idx = camera_idx

        self._detector = dlib.get_frontal_face_detector()
        self._predictor = dlib.shape_predictor(model_path)

        self._ear = 1.0
        self._blinked = False
        self._last_blink_t = 0.0
        self._was_closed = False

        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._latest_frame = None

        self._log = collections.deque(maxlen=int(30 * monitor_secs))

        print(f"[CV] Using dlib backend  (model: {os.path.basename(model_path)})")

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="cv-blink")
        self._thread.start()

    def stop(self):
        self._running = False

    def pop_blink(self) -> bool:
        with self._lock:
            if self._blinked:
                self._blinked = False
                return True
        return False

    @property
    def ear(self) -> float:
        return self._ear

    @property
    def latest_frame(self):
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    @property
    def log(self):
        with self._lock:
            return list(self._log)

    @property
    def backend(self) -> str:
        return "dlib"

    def _run(self):
        # Avoid CAP_DSHOW in background thread: on Windows, DirectShow requires
        # COM init per-thread, which may not happen when pygame is in main thread.
        cap = cv2.VideoCapture(self._camera_idx)
        if not cap.isOpened():
            cap.release()
            print("[CV] Default backend failed, retrying with CAP_DSHOW ...")
            cap = cv2.VideoCapture(self._camera_idx, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        if not cap.isOpened():
            print("[CV] Could not open camera")
            return

        print("[CV] Camera opened")
        print("[CV] Width:", cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        print("[CV] Height:", cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        while self._running:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            t = time.time()

            rects = self._detector(gray, 0)
            ear_val = float("nan")
            blink_f = 0

            if len(rects) > 0:
                rect = max(rects, key=lambda r: r.width() * r.height())
                shape = self._predictor(gray, rect)

                l_pts = _shape_to_np(shape, _LEFT_EYE_IDX)
                r_pts = _shape_to_np(shape, _RIGHT_EYE_IDX)
                ear_val = (_ear_from_pts(l_pts) + _ear_from_pts(r_pts)) / 2.0

                if ear_val < self.ear_threshold:
                    self._was_closed = True
                else:
                    if self._was_closed and (t - self._last_blink_t) > self._debounce_s:
                        with self._lock:
                            self._blinked = True
                        self._last_blink_t = t
                        blink_f = 1
                    self._was_closed = False

            thumb = cv2.resize(frame, (160, 120))
            with self._lock:
                self._ear = ear_val
                self._latest_frame = thumb
                self._log.append((t, ear_val, blink_f))

        cap.release()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="dlib blink detector test window")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--model", type=str, default=_DEFAULT_MODEL)
    parser.add_argument("--threshold", type=float, default=0.19)
    parser.add_argument("--debounce", type=float, default=0.25)
    args = parser.parse_args()

    if not os.path.exists(args.model):
        raise FileNotFoundError(f"Model not found:\n{args.model}")

    detector = dlib.get_frontal_face_detector()
    predictor = dlib.shape_predictor(args.model)

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    if not cap.isOpened():
        raise RuntimeError("Could not open camera")

    print(f"Backend: dlib  |  model: {os.path.basename(args.model)}")
    print("Camera opened")
    print("Width:", cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    print("Height:", cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    ear_threshold = args.threshold
    debounce_s = args.debounce

    blink_count = 0
    last_blink_t = 0.0
    was_closed = False
    blink_flash = 0.0

    ear_history = collections.deque([0.3] * 120, maxlen=120)

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            continue

        display = frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        t = time.time()
        h, w = display.shape[:2]

        ear_val = float("nan")
        faces = detector(gray, 0)

        if len(faces) > 0:
            rect = max(faces, key=lambda r: r.width() * r.height())
            x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
            shape = predictor(gray, rect)

            l_pts = _shape_to_np(shape, _LEFT_EYE_IDX)
            r_pts = _shape_to_np(shape, _RIGHT_EYE_IDX)
            ear_val = (_ear_from_pts(l_pts) + _ear_from_pts(r_pts)) / 2.0

            face_col = (0, 200, 80) if ear_val >= ear_threshold else (0, 100, 220)
            cv2.rectangle(display, (x1, y1), (x2, y2), face_col, 2)

            for pts in (l_pts, r_pts):
                hull = cv2.convexHull(pts.astype(np.int32))
                cv2.drawContours(display, [hull], -1, (255, 160, 0), 2)
                for px, py in pts.astype(int):
                    cv2.circle(display, (px, py), 2, (0, 200, 255), -1)

            if ear_val < ear_threshold:
                was_closed = True
            else:
                if was_closed and (t - last_blink_t) > debounce_s:
                    blink_count += 1
                    last_blink_t = t
                    blink_flash = 0.35
                was_closed = False
        else:
            cv2.putText(
                display, "NO FACE", (w // 2 - 60, h // 2),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 200), 2
            )

        ear_history.append(ear_val if not np.isnan(ear_val) else 0.3)

        graph_h = 70
        graph_y0 = h - graph_h
        cv2.rectangle(display, (0, graph_y0), (w, h), (20, 20, 20), -1)

        thr_y = int(graph_y0 + graph_h * (1.0 - ear_threshold / 0.5))
        cv2.line(display, (0, thr_y), (w, thr_y), (0, 80, 180), 1)
        cv2.putText(
            display, f"thr={ear_threshold:.2f}",
            (6, max(12, thr_y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 130, 220), 1
        )

        pts_graph = []
        for i, e in enumerate(ear_history):
            gx = int(i * (w - 1) / (len(ear_history) - 1))
            e = np.clip(e, 0.0, 0.5)
            gy = int(graph_y0 + graph_h * (1.0 - e / 0.5))
            pts_graph.append((gx, gy))

        if len(pts_graph) > 1:
            cv2.polylines(display, [np.array(pts_graph)], False, (80, 220, 80), 1)

        blink_flash = max(0.0, blink_flash - 1 / 30.0)
        if blink_flash > 0:
            alpha = blink_flash / 0.35
            overlay = display.copy()
            overlay[:graph_y0] = (0, 0, 200)
            cv2.addWeighted(overlay, alpha * 0.35, display, 1 - alpha * 0.35, 0, display)
            cv2.putText(
                display, "BLINK", (w // 2 - 70, h // 2 - 20),
                cv2.FONT_HERSHEY_DUPLEX, 1.6, (255, 255, 255), 3
            )

        cv2.rectangle(display, (0, 0), (w, 60), (0, 0, 0), -1)

        ear_str = f"EAR {ear_val:.3f}" if not np.isnan(ear_val) else "EAR ---"
        ear_col = (0, 220, 80) if (not np.isnan(ear_val) and ear_val >= ear_threshold) else (0, 80, 220)

        cv2.putText(display, ear_str, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.75, ear_col, 2)
        cv2.putText(display, f"Blinks: {blink_count}", (8, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)
        cv2.putText(display, "[dlib]", (w - 80, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 1)
        cv2.putText(display, "t/T threshold  q quit", (w - 170, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1)

        cv2.imshow("CV Blink Test", display)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        elif key == ord("t"):
            ear_threshold = round(max(0.10, ear_threshold - 0.01), 2)
            print(f"EAR threshold -> {ear_threshold}")
        elif key == ord("T"):
            ear_threshold = round(min(0.40, ear_threshold + 0.01), 2)
            print(f"EAR threshold -> {ear_threshold}")

    cap.release()
    cv2.destroyAllWindows()
    print(f"\nTotal blinks: {blink_count}")
    print(f"dlib settings: ear_threshold={ear_threshold}")