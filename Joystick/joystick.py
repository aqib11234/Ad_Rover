from flask import Flask, render_template, request, jsonify
import serial
import threading
import time

app = Flask(__name__)

# === Serial Configuration ===
PORT = '/dev/serial/by-path/platform-70090000.xusb-usb-0:3.2:1.0'
BAUDRATE = 115200

# === Global Variables ===
ser = None
sending = False
current_command = None
lock = threading.Lock()

# === Serial Connection Handler ===
def connect_serial():
    global ser
    while True:
        try:
            ser = serial.Serial(PORT, BAUDRATE, timeout=1)
            print(f"✅ Connected to OpenCR on {PORT}")
            return
        except serial.SerialException as e:
            print(f"❌ Serial error: {e}, retrying in 2s...")
            time.sleep(2)

# Call once at startup
connect_serial()

# === Background Thread to Monitor Serial Connection ===
def monitor_serial_connection():
    global ser
    while True:
        if ser and not ser.is_open:
            print("⚠️ Serial port closed. Reconnecting...")
            connect_serial()
        time.sleep(1)

# Start monitor thread
threading.Thread(target=monitor_serial_connection, daemon=True).start()

# === Background Thread to Continuously Send Command ===
def send_continuous():
    global sending, current_command, ser
    while True:
        with lock:
            if sending and current_command:
                try:
                    ser.write(f"{current_command}\r\n".encode('utf-8'))
                    print(f"Sent: {current_command}")
                except (serial.SerialException, OSError) as e:
                    print(f"⚠️ Write failed: {e}")
                    try:
                        ser.close()
                    except:
                        pass
                    ser = None
                    connect_serial()
        time.sleep(0.1)  # 10Hz send rate

# Start the background thread
threading.Thread(target=send_continuous, daemon=True).start()

# === Routes ===
@app.route("/")
def index():
    return render_template("joystick.html")

@app.route("/move", methods=["POST"])
def move():
    global sending, current_command, ser
    data = request.json
    direction = data.get("direction")
    action = data.get("action")
    print(f"Action: {action}, Direction: {direction}")

    with lock:
        if action == "start":
            sending = True
            current_command = direction
        elif action == "stop":
            sending = False
            current_command = None
            try:
                if ser:
                    ser.write("s\r\n".encode('utf-8'))
                    print("Sent: s (Stop)")
            except (serial.SerialException, OSError) as e:
                print(f"⚠️ Stop command failed: {e}")
                try:
                    ser.close()
                except:
                    pass
                ser = None
                connect_serial()
    return jsonify(status="ok")

# === Run App ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)

