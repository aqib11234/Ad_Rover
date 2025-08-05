# === Environment Configuration ===
import os
os.environ["DISPLAY"] = ":0"
os.environ["QT_QPA_PLATFORM"] = "xcb"
os.environ["QT_X11_NO_MITSHM"] = "1"

# === Imports ===
import cv2
import time
import numpy as np
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from threading import Thread

# === Flask App Configuration ===
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ADS_FOLDER'] = 'advertisement'
app.config['SECRET_KEY'] = 'jetson-ad-manager-2024'

ALLOWED_EXTENSIONS = {
    'images': {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'},
    'videos': {'mp4', 'avi', 'mov', 'mkv', 'webm', 'flv'}
}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['ADS_FOLDER'], exist_ok=True)

# === Helper Functions ===
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in (ALLOWED_EXTENSIONS['images'] | ALLOWED_EXTENSIONS['videos'])

def get_file_type(filename):
    ext = filename.rsplit('.', 1)[1].lower()
    return 'image' if ext in ALLOWED_EXTENSIONS['images'] else 'video' if ext in ALLOWED_EXTENSIONS['videos'] else 'unknown'

def get_file_info(filepath):
    try:
        stat = os.stat(filepath)
        return {
            'size': stat.st_size,
            'modified': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            'size_mb': round(stat.st_size / (1024 * 1024), 2)
        }
    except:
        return {'size': 0, 'modified': 'Unknown', 'size_mb': 0}

def resize_to_fullscreen(image, screen_width, screen_height):
    h, w = image.shape[:2]
    scale = min(screen_width / w, screen_height / h)
    resized = cv2.resize(image, (int(w * scale), int(h * scale)))
    bg = np.zeros((screen_height, screen_width, 3), dtype=np.uint8)
    x, y = (screen_width - resized.shape[1]) // 2, (screen_height - resized.shape[0]) // 2
    bg[y:y+resized.shape[0], x:x+resized.shape[1]] = resized
    return bg

def wait_for_display(max_attempts=15):
    print("⏳ Checking for display availability...")

    # Check for Xorg process first
    for i in range(10):  # Try every 1s for 10s
        if subprocess.run(['pgrep', '-x', 'Xorg'], capture_output=True).returncode == 0:
            print("✅ Xorg is running")
            break
        print(f"Waiting for Xorg... ({i+1}/10)")
        time.sleep(1)
    else:
        print("❌ Xorg not found. Skipping display wait.")
        return False

    # Now check xdpyinfo (i.e. X11 access is ready)
    for attempt in range(max_attempts):
        for xauth in [
            '/home/jetson/.Xauthority',
            f'/tmp/.X0-{os.getuid()}',
            '/var/run/lightdm/jetson/xauthority'
        ]:
            if os.path.exists(xauth):
                os.environ['XAUTHORITY'] = xauth
                result = subprocess.run(['xdpyinfo'], env=os.environ.copy(), capture_output=True)
                if result.returncode == 0:
                    print(f"✅ Display ready after {attempt+1} attempts")
                    return True
        print(f"🔁 Waiting for X display... ({attempt+1}/{max_attempts})")
        time.sleep(1)

    print("⚠️ Timeout: Display not ready")
    return False

def initialize_fullscreen_window(name, width, height):
    for attempt in range(5):
        try:
            print(f"Initializing fullscreen window (attempt {attempt+1})")
            if attempt > 0:
                try: cv2.destroyWindow(name)
                except: pass
                time.sleep(0.5)

            cv2.namedWindow(name, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_EXPANDED)
            time.sleep(1)
            test_img = np.zeros((100, 100, 3), dtype=np.uint8)
            cv2.imshow(name, test_img)
            cv2.waitKey(1)

            cv2.setWindowProperty(name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            cv2.moveWindow(name, 0, 0)
            time.sleep(0.5)

            fullscreen_test = np.zeros((height, width, 3), dtype=np.uint8)
            cv2.imshow(name, fullscreen_test)
            cv2.waitKey(100)
            return True
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return False

# === Flask Routes ===
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/ads', methods=['GET'])
def get_ads():
    try:
        ads = []
        for f in os.listdir(app.config['ADS_FOLDER']):
            if allowed_file(f):
                path = os.path.join(app.config['ADS_FOLDER'], f)
                ads.append({
                    'filename': f,
                    'type': get_file_type(f),
                    'size': get_file_info(path)['size'],
                    'size_mb': get_file_info(path)['size_mb'],
                    'modified': get_file_info(path)['modified'],
                    'url': f'/api/ads/file/{f}'
                })
        ads.sort(key=lambda x: x['modified'], reverse=True)
        return jsonify({'success': True, 'ads': ads, 'total': len(ads)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ads/file/<filename>')
def get_ad_file(filename):
    try:
        return send_from_directory(app.config['ADS_FOLDER'], filename)
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404

@app.route('/api/upload', methods=['POST'])
def upload_ad():
    try:
        file = request.files.get('file')
        if not file or file.filename == '' or not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Invalid file'}), 400
        fname = secure_filename(file.filename)
        fpath = os.path.join(app.config['ADS_FOLDER'], fname)
        if os.path.exists(fpath):
            return jsonify({'success': False, 'error': f'File "{fname}" already exists'}), 409
        file.save(fpath)
        return jsonify({'success': True, 'message': f'File "{fname}" uploaded', 'file': get_file_info(fpath)})
    except RequestEntityTooLarge:
        return jsonify({'success': False, 'error': 'File too large'}), 413
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ads/<filename>', methods=['DELETE'])
def delete_ad(filename):
    try:
        path = os.path.join(app.config['ADS_FOLDER'], secure_filename(filename))
        if not os.path.exists(path):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        os.remove(path)
        return jsonify({'success': True, 'message': f'File "{filename}" deleted'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# === Fullscreen Display Thread ===
def play_ads_fullscreen():
    wait_for_display()
    time.sleep(1)
    width, height = 1080, 1920
    window_name = "AdPlayer"

    if not initialize_fullscreen_window(window_name, width, height):
        print("Fullscreen initialization failed")
        return

    idle_img = cv2.imread('adrover.jpg')
    print("Ad display loop started")

    while True:
        try:
            ads = sorted([f for f in os.listdir(app.config['ADS_FOLDER']) if allowed_file(f)])
            if not ads:
                if idle_img is not None:
                    cv2.imshow(window_name, resize_to_fullscreen(idle_img, width, height))
                    if cv2.waitKey(5000) & 0xFF == ord('q'): break
                continue

            for f in ads:
                path = os.path.join(app.config['ADS_FOLDER'], f)
                typ = get_file_type(f)
                print(f"Playing: {f} ({typ})")

                if typ == 'image':
                    img = cv2.imread(path)
                    if img is not None:
                        cv2.imshow(window_name, resize_to_fullscreen(img, width, height))
                        if cv2.waitKey(15000) & 0xFF == ord('q'): return
                    else:
                        print(f"Could not load image: {f}")
                elif typ == 'video':
                    try:
                        # Show idle image between transitions to avoid previous frame flash
                        if idle_img is not None:
                            cv2.imshow(window_name, resize_to_fullscreen(idle_img, width, height))
                            cv2.waitKey(1)

                        subprocess.run([
                            "gst-launch-1.0", "filesrc", f"location={path}", "!", "qtdemux", "name=demux",
                            "demux.video_0", "!", "queue", "!", "h264parse", "!", "nvv4l2decoder",
                            "!", "nvvidconv", "flip-method=3", "!", "nvoverlaysink", "sync=false"
                        ], check=True)
                    except subprocess.CalledProcessError as e:
                        print("GStreamer error:", e)
        except Exception as e:
            print("Display loop error:", e)
            try:
                cv2.destroyAllWindows()
                time.sleep(2)
                initialize_fullscreen_window(window_name, width, height)
            except: pass

# === Startup ===
Thread(target=play_ads_fullscreen, daemon=True).start()
if __name__ == '__main__':
    print("Starting Flask server...")
    app.run(host='0.0.0.0', port=5002, debug=True, use_reloader=False)
