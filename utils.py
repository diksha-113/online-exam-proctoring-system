import cv2
import face_recognition
import os
import time
import json
import itertools
import threading
import keyboard
import pyaudio
import audioop
from datetime import datetime

# -------------------------------
# Global Variables
# -------------------------------
violations = []          # All violations
violations_log = []      # Current session
cap = None               # Webcam capture
frame = None             # Current frame
cap_lock = threading.Lock()
frame_lock = threading.Lock()
violations_lock = threading.Lock()

current_student = None
current_exam_id = None

# Video writers
writer1 = [None]  # Face Missing
writer3 = [None]  # Multiple Faces
writer5 = [None]  # Audio

shortcuts = []  # Keys pressed

# Last violation timestamps (to throttle repeated logs)
last_audio_violation_time = 0
audio_violation_cooldown = 1  # seconds

# -------------------------------
# MySQL Utils
# -------------------------------
app = None
mysql = None

def init_mysql_utils(flask_app, mysql_conn):
    global app, mysql
    app = flask_app
    mysql = mysql_conn

# -------------------------------
# Setters and Getters
# -------------------------------
def set_current_student(student_id):
    global current_student
    current_student = student_id

def get_current_student():
    return current_student

def set_current_exam(exam_id):
    global current_exam_id
    current_exam_id = exam_id

def get_current_exam():
    return current_exam_id

# -------------------------------
# Violation Functions
# -------------------------------
def add_violation(student_id, exam_id, violation_type, details=""):
    """Add violation safely to memory, JSON, and DB"""
    global violations
    record = {
        "student_id": student_id,
        "exam_id": exam_id,
        "type": violation_type,
        "details": details,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    }
    # Add to in-memory list
    with violations_lock:
        violations.append(record)
    
    # Save to JSON for testing
    write_json(record)

    # Save to DB if MySQL is initialized
    if mysql is not None and app is not None:
        try:
            with app.app_context():
                cur = mysql.connection.cursor()
                try:
                    cur.execute("""
                        INSERT INTO violations (student_id, exam_id, type, details, timestamp)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (student_id, exam_id, violation_type, details, record["timestamp"]))
                    mysql.connection.commit()

                    cur.execute("""
                        INSERT INTO results (student_id, exam_id, score, status, trust_score, violations)
                        VALUES (%s, %s, 0, 'Failed', 100, 1)
                        ON DUPLICATE KEY UPDATE violations = violations + 1
                    """, (student_id, exam_id))
                    mysql.connection.commit()
                finally:
                    cur.close()
                print(f"[DB] Violation added for student {student_id}, exam {exam_id}")
        except Exception as e:
            print(f"[DB] Failed to add violation: {e}")

def get_violations_by_student_exam(student_id, exam_id):
    try:
        with app.app_context():
            cur = mysql.connection.cursor(dictionary=True)
            cur.execute("SELECT * FROM violations WHERE student_id=%s AND exam_id=%s", (student_id, exam_id))
            vios = cur.fetchall()
            cur.close()
            return vios
    except Exception as e:
        print(f"[DB] Failed to fetch violations: {e}")
        return []

def reset_violations():
    global violations_log
    with violations_lock:
        violations_log = []

# -------------------------------
# Result ID Generator
# -------------------------------
_result_id_counter = itertools.count(1)
def get_resultId():
    return next(_result_id_counter)

# -------------------------------
# Video Utilities
# -------------------------------
def start_recording(filename, frame):
    if frame is None or frame.size == 0:
        return None
    height, width, _ = frame.shape
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    return cv2.VideoWriter(filename, fourcc, 20.0, (width, height))

def log_violation(v_type, details, frame, writer_ref, filename, student_id=None, exam_id=None):
    with violations_lock:
        violations_log.append({
            "student_id": student_id,
            "exam_id": exam_id,
            "type": v_type,
            "time": time.strftime("%H:%M:%S"),
            "details": details
        })
    if writer_ref and writer_ref[0] is None and frame is not None and frame.size > 0:
        writer_ref[0] = start_recording(filename, frame)
    if writer_ref and writer_ref[0] is not None and frame is not None and frame.size > 0:
        writer_ref[0].write(frame)

def release_resources():
    global cap, writer1, writer3, writer5
    with cap_lock:
        if cap and cap.isOpened():
            cap.release()
    for w in [writer1, writer3, writer5]:
        if w[0]:
            w[0].release()
            w[0] = None
    keyboard.unhook_all()
    cv2.destroyAllWindows()

# -------------------------------
# Frame Capture Thread
# -------------------------------
def capture_frames():
    global cap, frame
    with cap_lock:
        if not cap or not cap.isOpened():   # ✅ open camera here
                    # Use CAP_DSHOW on Windows to avoid OpenCV C++ exceptions
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            print("[ERROR] Webcam not found!")
            return
    while True:
        ret, f = cap.read()
        if ret:
            with frame_lock:
                frame = f.copy()
        else:
            time.sleep(0.05)

def get_frame():
    with frame_lock:
        return frame.copy() if frame is not None else None

# -------------------------------
# Cheat Detection Functions
# -------------------------------
def cheat_Detection1():
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    while True:
        f = get_frame()
        if f is None:
            time.sleep(0.1)
            continue
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, 1.2, 6)
        if len(faces) == 0:
            add_violation(get_current_student(), get_current_exam(), "Face Missing", "No face detected")
            log_violation("Face Missing", "No face detected", f, writer1, "face_missing.mp4",
                          get_current_student(), get_current_exam())
        time.sleep(0.1)

def cheat_Detection2():
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    while True:
        f = get_frame()
        if f is None:
            time.sleep(0.1)
            continue
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, 1.2, 6)
        if len(faces) > 1:
            add_violation(get_current_student(), get_current_exam(), "Multiple Faces", f"{len(faces)} faces detected")
            log_violation("Multiple Faces", f"{len(faces)} faces detected", f, writer3, "multiple_faces.mp4",
                          get_current_student(), get_current_exam())
        time.sleep(0.1)

def cheat_Detection3():
    global last_audio_violation_time
    try:
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
        while True:
            data = stream.read(1024, exception_on_overflow=False)
            rms = audioop.rms(data, 2)
            now = time.time()
            if rms > 3000 and now - last_audio_violation_time >= audio_violation_cooldown:
                last_audio_violation_time = now
                add_violation(get_current_student(), get_current_exam(), "Audio", "Loud sound detected")
                log_violation("Audio", "Loud sound detected", None, writer5, "audio_violation.mp4",
                              get_current_student(), get_current_exam())
            time.sleep(0.1)
        stream.stop_stream()
        stream.close()
        p.terminate()
    except Exception as e:
        print(f"[Cheat3] Error: {e}")

# -------------------------------
# Face Recognition
# -------------------------------
class FaceRecognition:
    def __init__(self):
        self.known_encodings = []
        self.known_names = []
        self.encoded = False

    def encode_faces(self, profiles_folder="Profiles"):
        os.makedirs(profiles_folder, exist_ok=True)
        self.known_encodings.clear()
        self.known_names.clear()
        for file in os.listdir(profiles_folder):
            path = os.path.join(profiles_folder, file)
            img = face_recognition.load_image_file(path)
            enc = face_recognition.face_encodings(img)
            if enc:
                self.known_encodings.append(enc[0])
                self.known_names.append(os.path.splitext(file)[0])
        self.encoded = True
        print(f"[FR] Encoded {len(self.known_encodings)} faces.")

    def run_recognition(self, show_window=True):
        try:
            while True:
                f = get_frame()
                if f is None:
                    time.sleep(0.05)
                    continue
                rgb_frame = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
                face_locations = face_recognition.face_locations(rgb_frame)
                face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)

                for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
                    matches = face_recognition.compare_faces(self.known_encodings, face_encoding)
                    name = "Unknown"
                    if True in matches:
                        first_match_index = matches.index(True)
                        name = self.known_names[first_match_index]

                    cv2.rectangle(f, (left, top), (right, bottom), (0, 255, 0), 2)
                    cv2.putText(f, name, (left, top - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

                if show_window:
                    cv2.imshow("Proctoring - Face Recognition", f)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
        except Exception as e:
            print(f"[FR] Error: {e}")
        finally:
            release_resources()

fr = FaceRecognition()

# -------------------------------
# Keyboard Shortcut Detection
# -------------------------------
def shortcut_handler(event):
    try:
        if event.event_type != "down":
            return
        modifiers = []
        if keyboard.is_pressed("ctrl"): modifiers.append("Ctrl")
        if keyboard.is_pressed("alt"): modifiers.append("Alt")
        if keyboard.is_pressed("shift"): modifiers.append("Shift")
        key_name = '+'.join(modifiers + [event.name]) if modifiers else event.name
        shortcuts.append(key_name)
        add_violation(get_current_student(), get_current_exam(), "Shortcut", f"Key {key_name} pressed")
    except Exception as e:
        print(f"[Keyboard] Error: {e}")

# New keyboard monitoring thread
def keyboard_violation_thread():
    """Monitor forbidden keys continuously"""
    forbidden_keys = ["ctrl", "alt", "tab", "win", "esc", "f4"]
    while True:
        for key in forbidden_keys:
            if keyboard.is_pressed(key):
                add_violation(get_current_student(), get_current_exam(), "Shortcut", f"Key {key} pressed")
                time.sleep(1)  # throttle repeated logs
        time.sleep(0.1)

# -------------------------------
# Trust Score
# -------------------------------
def get_TrustScore(student_id=None):
    score = 100
    with violations_lock:
        counted_types = set()  # track unique violation types
        for v in violations_log:
            if student_id and v.get("student_id") != student_id:
                continue
            v_type = v["type"]
            if v_type not in counted_types:
                counted_types.add(v_type)
                if v_type == "Face Missing": score -= 10
                elif v_type == "Multiple Faces": score -= 15
                elif v_type == "Shortcut": score -= 5
                elif v_type == "Audio": score -= 10
                else: score -= 10
    return max(score, 0)


# -------------------------------
# JSON Utilities
# -------------------------------
def write_json(data, filename="violations.json"):
    try:
        with open(filename, "a") as f:
            f.write(json.dumps(data) + "\n")
    except Exception as e:
        print(f"[JSON] Error writing to {filename}: {e}")

def move_file_to_output_folder(filename, folder="Profiles"):
    os.makedirs(folder, exist_ok=True)
    src_path = os.path.join(os.getcwd(), filename)
    dest_path = os.path.join(os.getcwd(), folder, filename)
    try:
        if os.path.exists(src_path):
            os.replace(src_path, dest_path)
    except Exception as e:
        print(f"[FILE] Error moving {filename}: {e}")

def start_proctoring():
    """Starts webcam capture and cheat detection threads"""
    # Start frame capture
    t_frame = threading.Thread(target=capture_frames, daemon=True)
    t_frame.start()

    # Start cheat detection threads
    t1 = threading.Thread(target=cheat_Detection1, daemon=True)
    t2 = threading.Thread(target=cheat_Detection2, daemon=True)
    t3 = threading.Thread(target=cheat_Detection3, daemon=True)
    t1.start()
    t2.start()
    t3.start()

    # Start keyboard monitoring thread
    t4 = threading.Thread(target=keyboard_violation_thread, daemon=True)
    t4.start()

    print("[PROCTOR] Proctoring threads started")
    return [t_frame, t1, t2, t3, t4]

# -------------------------------
# Dummy Recorder
# -------------------------------
class Recorder:
    def record(self):
        print("[Recorder] Exam recording started...")

recorder = Recorder()
