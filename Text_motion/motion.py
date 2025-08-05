
from flask import Flask, render_template, request, redirect, url_for, flash
import os
import serial
import time
import threading
import ctypes

# === Flask App Config ===
app = Flask(__name__)
app.secret_key = 'supersecretkey'
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# === Serial Configuration ===
PORT = '/dev/serial/by-path/platform-70090000.xusb-usb-0:3.2:1.0'
BAUDRATE = 115200

# === Global Variables ===
stop_execution = False
execution_thread = None  # Background thread for motion


# === Create uploads folder if it doesn't exist ===
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


# === Kill Thread Utility ===
def kill_thread(thread):
    """Forcefully kill a thread by raising SystemExit"""
    if not thread.is_alive():
        return
    thread_id = thread.ident
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(thread_id),
        ctypes.py_object(SystemExit)
    )
    if res == 0:
        print("⚠ Thread not found.")
    elif res > 1:
        # Undo if it messed up
        ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, None)
        print("⚠ PyThreadState_SetAsyncExc failed.")


# === Wait for "Done" response ===
def wait_for_done_response(ser, timeout=30):
    """Wait for 'Done' response from OpenCR with timeout"""
    start_time = time.time()
    buffer = ""

    while (time.time() - start_time) < timeout:
        if stop_execution:
            print("🛑 Stop requested during wait_for_done_response()")
            ser.write('s'.encode())  # send stop to OpenCR
            raise SystemExit  # Kill this thread

        if ser.in_waiting > 0:
            try:
                data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                buffer += data
                print(f"Received: {data.strip()}")

                if "Done" in buffer:
                    print("✓ Movement completed - 'Done' received")
                    return True

            except Exception as e:
                print(f"Error reading serial data: {e}")

        time.sleep(0.01)

    print("⚠ Timeout waiting for 'Done' response")
    return False


# === Send motion commands ===
def send_commands_to_opencr(filepath):
    global stop_execution
    loop_count = 0

    try:
        ser = serial.Serial(PORT, BAUDRATE, timeout=5)
        print(f"Connected to OpenCR on {PORT} at {BAUDRATE} baud.")

        ser.reset_input_buffer()
        ser.reset_output_buffer()

        while not stop_execution:
            loop_count += 1
            print(f"Starting Loop {loop_count}")

            with open(filepath, 'r') as file:
                lines = file.readlines()

            commands = []
            for line in lines:
                line = line.strip()
                if not line or ':' not in line:
                    continue
                cmd, value = line.split(':', 1)
                cmd = cmd.strip().lower()
                value = value.strip()

                if cmd == 'turn':
                    serial_cmd = f"a {value}\r\n"
                    commands.append(('move', serial_cmd))
                elif cmd == 'move':
                    serial_cmd = f"d {value}\r\n"
                    commands.append(('move', serial_cmd))
                elif cmd == 'wait':
                    try:
                        wait_time = float(value)
                        commands.append(('wait', wait_time))
                    except ValueError:
                        print(f"Invalid wait time: {value}")
                else:
                    print(f"Unknown command: {cmd}")

            # Execute commands
            for action, param in commands:
                if stop_execution:
                    print("🛑 Stop requested: aborting commands")
                    ser.write('s'.encode())  # Send stop to OpenCR
                    raise SystemExit  # Kill this thread

                if action == 'move':
                    print(f"Sending movement command: {param.strip()}")
                    ser.write(param.encode('utf-8'))

                    if not wait_for_done_response(ser):
                        print("❌ Movement command failed or stopped.")
                        raise SystemExit

                elif action == 'wait':
                    print(f"Waiting for {param} seconds...")
                    start_wait = time.time()
                    while (time.time() - start_wait) <= param:
                        if stop_execution:
                            print("🛑 Stop requested during wait")
                            ser.write('s'.encode())  # Send stop
                            raise SystemExit
                        time.sleep(0.1)

            print("All commands in loop completed")

        print("Execution stopped.")
        flash("Motion execution stopped.", "warning")

    except serial.SerialException as e:
        print(f"Serial error: {e}")
        flash(f"Serial error: {e}", "danger")
    except FileNotFoundError:
        print(f"File '{filepath}' not found.")
        flash(f"File '{filepath}' not found.", "danger")
    except SystemExit:
        print("🛑 Thread terminated by Stop request.")
    except Exception as e:
        print(f"Unexpected error: {e}")
        flash(f"Unexpected error: {e}", "danger")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("Serial connection closed.")


# === Routes ===
@app.route("/", methods=['GET', 'POST'])
def index():
    files = os.listdir(UPLOAD_FOLDER)
    return render_template('index.html', files=files)


@app.route("/upload", methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('index'))
    file = request.files['file']
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('index'))
    if file:
        filename = file.filename
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        flash(f"File '{filename}' uploaded successfully!", 'success')
        return redirect(url_for('index'))


@app.route("/delete/<filename>")
def delete_file(filename):
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            flash(f"File '{filename}' deleted successfully!", "success")
        else:
            flash(f"File '{filename}' not found.", "danger")
    except Exception as e:
        flash(f"Error deleting file '{filename}': {e}", "danger")
    return redirect(url_for('index'))


@app.route("/send/<filename>")
def send_file(filename):
    global stop_execution, execution_thread
    stop_execution = False  # Reset stop flag
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    # Start background thread
    if execution_thread is None or not execution_thread.is_alive():
        execution_thread = threading.Thread(target=send_commands_to_opencr, args=(filepath,))
        execution_thread.start()
        flash(f"Started executing '{filename}'", "info")
    else:
        flash("Another execution is already running.", "warning")

    return redirect(url_for('index'))


@app.route("/stop")
def stop_motion():
    global stop_execution, execution_thread
    stop_execution = True
    print("🛑 Stop signal received from user.")

    # Emergency stop: send 's' to OpenCR immediately
    try:
        ser = serial.Serial(PORT, BAUDRATE, timeout=2)
        ser.write('s'.encode())  # Send emergency stop
        ser.close()
        print("✔ Emergency stop signal sent to OpenCR.")
    except serial.SerialException as e:
        print(f"Error sending stop signal to OpenCR: {e}")

    # Kill the background thread
    if execution_thread and execution_thread.is_alive():
        kill_thread(execution_thread)
        print("💥 Background thread forcefully terminated.")

    flash("Emergency stop: motion halted immediately.", "warning")
    return redirect(url_for('index'))


# === Run App ===
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)

