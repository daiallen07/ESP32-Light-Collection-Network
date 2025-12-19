import socket
import struct
import threading
import time
import csv
import os
from datetime import datetime
from collections import defaultdict, deque
from gpiozero import LED, Button
from luma.led_matrix.device import max7219
from luma.core.interface.serial import spi, noop
from luma.core.render import canvas

# Multicast Config
MULTICAST_GROUP = '239.1.1.1'
MULTICAST_PORT = 5000
BUTTON_PIN = 17
YELLOW_LED_PIN = 27
LOG_DIRECTORY = ''
DATA_THROTTLE = 0.1 

# LED Matrix Configuration
MATRIX_UPDATE_INTERVAL = 4.0
MATRIX_HISTORY_SIZE = 8
MAX_LIGHT_VALUE = 4095
LIGHT_STEP = MAX_LIGHT_VALUE / 8

# Hardware setup
button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05)
yellow_led = LED(YELLOW_LED_PIN)

# Initialize LED Matrix
try:
    serial = spi(port=0, device=0, gpio=noop())
    matrix_device = max7219(serial, width=8, height=8, block_orientation=0, rotate=0)
    matrix_device.contrast(8)
    MATRIX_ENABLED = True
except Exception as e:
    print(f"LED Matrix initialization failed: {e}")
    MATRIX_ENABLED = False

# Global state variables
listening_thread = None
stop_listening = False
current_log_file = None
log_writer = None
log_file_handle = None
reset_in_progress = False

# Data queue
packet_queue = deque()
queue_lock = threading.Lock()

# Matrix data
matrix_columns = deque(maxlen=MATRIX_HISTORY_SIZE)
matrix_lock = threading.Lock()
last_matrix_update = 0

light_value_accumulator = []
accumulator_lock = threading.Lock()

# Data tracking
packet_count = 0
last_process_time = 0

os.makedirs(LOG_DIRECTORY, exist_ok=True)


def map_value_to_height(light_value):
    """Map light sensor value (0-4095) to LED matrix height (0-7)"""
    light_value = max(0, min(MAX_LIGHT_VALUE, light_value))
    height = int(light_value / LIGHT_STEP)
    if height > 7:
        height = 7
    return height


def update_led_matrix():
    """Update the 8x8 LED matrix display"""
    if not MATRIX_ENABLED:
        return
    
    try:
        with matrix_lock:
            columns_copy = list(matrix_columns)
        
        with canvas(matrix_device) as draw:
            for col_idx, height in enumerate(columns_copy):
                for row in range(height + 1):
                    draw.point((col_idx, 7 - row), fill="white")
        
    except Exception as e:
        print(f"Error updating LED matrix: {e}")


def clear_led_matrix():
    """Clear the LED matrix display"""
    if not MATRIX_ENABLED:
        return
    
    try:
        matrix_device.clear()
        with matrix_lock:
            matrix_columns.clear()
        with accumulator_lock:
            light_value_accumulator.clear()
    except Exception as e:
        print(f"Error clearing LED matrix: {e}")


def create_new_log_file():
    """Create a new CSV log file"""
    global current_log_file, log_writer, log_file_handle
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    current_log_file = os.path.join(LOG_DIRECTORY, f"esp32_log_{timestamp}.csv")
    
    log_file_handle = open(current_log_file, 'w', newline='')
    log_writer = csv.writer(log_file_handle)
    log_writer.writerow(['Timestamp', 'Master_IP', 'Light_Value'])
    log_file_handle.flush()
    
    print(f"Log file: {current_log_file}")
    return current_log_file


def close_log_file():
    """Close the current log file"""
    global log_file_handle, log_writer
    
    if log_file_handle:
        try:
            log_file_handle.close()
        except:
            pass
        log_file_handle = None
        log_writer = None


def log_master_data(master_ip, light_value):
    """Log master device data"""
    global log_writer, log_file_handle, packet_count, last_process_time
    
    current_time = time.time()
    
    if current_time - last_process_time < DATA_THROTTLE:
        return
    last_process_time = current_time
    
    packet_count += 1
    
    with accumulator_lock:
        light_value_accumulator.append(light_value)
    
    if log_writer:
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            log_writer.writerow([timestamp, master_ip, light_value])
            log_file_handle.flush()
        except Exception as e:
            print(f"Error logging data: {e}")


def process_packets():
    """Process packets from queue"""
    try:
        for _ in range(100):
            with queue_lock:
                if packet_queue:
                    message, address = packet_queue.popleft()
                else:
                    break
            
            parts = message.split(',')
            if len(parts) >= 3:
                is_master = int(parts[0]) == 1
                light_value = int(parts[1])
                device_ip = address[0]
                
                if is_master:
                    log_master_data(device_ip, light_value)
                    
    except Exception as e:
        print(f"Error processing packets: {e}")


def matrix_update_thread():
    """Update LED matrix periodically"""
    global last_matrix_update
    
    while not stop_listening:
        try:
            current_time = time.time()
            
            if current_time - last_matrix_update >= MATRIX_UPDATE_INTERVAL:
                with accumulator_lock:
                    if light_value_accumulator:
                        avg_light = sum(light_value_accumulator) / len(light_value_accumulator)
                        light_value_accumulator.clear()
                    else:
                        avg_light = 0
                
                height = map_value_to_height(avg_light)
                
                with matrix_lock:
                    matrix_columns.append(height)
                
                update_led_matrix()
                last_matrix_update = current_time
            
            time.sleep(0.1)
            
        except Exception as e:
            print(f"Matrix update thread error: {e}")
            time.sleep(1)


def listen_to_multicast():
    """Listen for UDP multicast packets"""
    global stop_listening
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8388608)
        
        sock.bind(('', MULTICAST_PORT))
        
        mreq = struct.pack("4sl", socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        
        sock.setblocking(False)
        
        while not stop_listening:
            try:
                data, address = sock.recvfrom(1024)
                message = data.decode('utf-8').strip()
                
                with queue_lock:
                    packet_queue.append((message, address))
                    
            except BlockingIOError:
                time.sleep(0.0001)
                continue
            except Exception:
                if not stop_listening:
                    pass
        
        sock.close()
        
    except Exception as e:
        print(f"Listener error: {e}")


def send_reset_command():
    """Send reset command to ESP32 devices"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    
    reset_message = "0,0,255,1"
    
    for i in range(10):
        sock.sendto(reset_message.encode('utf-8'), (MULTICAST_GROUP, MULTICAST_PORT))
        time.sleep(0.1)
    
    sock.close()


def stop_multicast_listener():
    """Stop the multicast listening thread"""
    global stop_listening, listening_thread
    
    stop_listening = True
    
    if listening_thread:
        listening_thread.join(timeout=3.0)
        listening_thread = None
    
    close_log_file()


def start_multicast_listener():
    """Start multicast listening thread"""
    global stop_listening, listening_thread, packet_count, last_process_time, last_matrix_update
    
    stop_listening = False
    packet_count = 0
    last_process_time = 0
    last_matrix_update = time.time()
    
    with queue_lock:
        packet_queue.clear()
    
    clear_led_matrix()
    create_new_log_file()
    
    listening_thread = threading.Thread(target=listen_to_multicast, daemon=True)
    listening_thread.start()
    
    if MATRIX_ENABLED:
        matrix_thread = threading.Thread(target=matrix_update_thread, daemon=True)
        matrix_thread.start()


def handle_reset_sequence():
    """Execute reset sequence"""
    global reset_in_progress
    
    try:
        print("\nRESET INITIATED")
        
        yellow_led.on()
        stop_multicast_listener()
        clear_led_matrix()
        send_reset_command()
        time.sleep(3)
        yellow_led.off()
        time.sleep(5)
        start_multicast_listener()
        
        print("RESET COMPLETE\n")
        
    except Exception as e:
        print(f"Error during reset: {e}")
    finally:
        reset_in_progress = False


def button_pressed_handler():
    """Called when button is pressed"""
    global reset_in_progress
    
    yellow_led.on()
    time.sleep(0.1)
    yellow_led.off()
    
    if reset_in_progress:
        return
    
    reset_in_progress = True
    
    reset_thread = threading.Thread(target=handle_reset_sequence, daemon=False)
    reset_thread.start()


def main_loop():
    """Main processing loop"""
    while True:
        try:
            process_packets()
            time.sleep(0.01)
            
        except KeyboardInterrupt:
            print("\nShutting down...")
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(0.1)


def main():
    """Main program"""
    
    print("="*60)
    print("ESP32 Logger with 8x8 LED Matrix")
    print("="*60)
    print(f"Logs: {LOG_DIRECTORY}")
    print(f"Button: GPIO {BUTTON_PIN}")
    print(f"LED: GPIO {YELLOW_LED_PIN}")
    print(f"Matrix: {'Enabled' if MATRIX_ENABLED else 'Disabled'}")
    print("="*60 + "\n")
    
    button.when_pressed = button_pressed_handler
    
    start_multicast_listener()
    
    try:
        main_loop()
    except KeyboardInterrupt:
        pass
    finally:
        stop_multicast_listener()
        yellow_led.off()
        if MATRIX_ENABLED:
            clear_led_matrix()
        print("\nShutdown complete")


if __name__ == "__main__":
    main()
