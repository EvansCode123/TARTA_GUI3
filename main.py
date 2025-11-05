import eel
import threading
import time
import glob
import os
import pandas as pd
import json
from scipy.signal import find_peaks
import numpy as np
import datetime
import pytz
import shutil
import psutil
import sys
import socket
import subprocess
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
gdrive_upload_lock = threading.Lock()

# --- RPi specific imports ---
try:
    # lgpio is used for GPIO pins (relay and boost)
    import lgpio
    # Adafruit libraries for I2C DAC control
    import board
    import busio
    import adafruit_mcp4725
    # ntplib is for syncing time over the internet
    import ntplib
    RPI_MODE = True
    print("Running in Raspberry Pi mode.")
except ImportError:
    print("WARNING: A required hardware library was not found. Running in simulation mode.")
    RPI_MODE = False

# --- GPIO and I2C Configuration ---
RELAY_PIN = 5
BOOST_PIN = 13
I2C_BUS = 1
DS3231_ADDRESS = 0x68
MCP4725_ADDRESS = 0x60 # Default I2C address for the MCP4725

def load_config():
    """ Loads config.json. """
    config_path = "config.json"
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                print("Warning: config.json is corrupted. Exiting.")
                sys.exit(1)
    else:
        print(f"Error: config.json not found. Please create one.")
        sys.exit(1)

config = load_config()

# --- Start ASEQ Spectrometer as separate process ---
def start_spectrometer_process():
    """Start the ASEQ spectrometer script as a separate process"""
    try:
        # Check if aseq_spectrometer.py exists
        if os.path.exists('aseq_spectrometer.py'):
            # Start the spectrometer script as a subprocess
            spectrometer_process = subprocess.Popen(
                [sys.executable, 'aseq_spectrometer.py'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Create a thread to monitor its output
            def monitor_spectrometer_output():
                for line in spectrometer_process.stdout:
                    print(f"[SPECTROMETER] {line.strip()}")
            
            monitor_thread = threading.Thread(target=monitor_spectrometer_output, daemon=True)
            monitor_thread.start()
            
            print("ASEQ Spectrometer process started successfully")
            return spectrometer_process
        else:
            print("WARNING: aseq_spectrometer.py not found. Spectrometer features disabled.")
            return None
    except Exception as e:
        print(f"Failed to start spectrometer process: {e}")
        return None

# --- RTC Helper Functions (using lgpio) ---
def bcd_to_dec(bcd):
    """Convert Binary Coded Decimal to Decimal"""
    return (bcd // 16 * 10) + (bcd % 16)

def dec_to_bcd(dec):
    """Convert Decimal to Binary Coded Decimal"""
    return (dec // 10 * 16) + (dec % 10)

def get_rtc_datetime():
    """Reads the time from a DS3231 RTC module using lgpio."""
    if not RPI_MODE:
        return datetime.datetime.now()
    h = None
    try:
        h = lgpio.i2c_open(I2C_BUS, DS3231_ADDRESS)
        count, time_data = lgpio.i2c_read_i2c_block_data(h, 0, 7)
        lgpio.i2c_close(h)
        if count == 7:
            return datetime.datetime(
                year=bcd_to_dec(time_data[6]) + 2000,
                month=bcd_to_dec(time_data[5] & 0x1F),
                day=bcd_to_dec(time_data[4]),
                hour=bcd_to_dec(time_data[2] & 0x3F),
                minute=bcd_to_dec(time_data[1]),
                second=bcd_to_dec(time_data[0] & 0x7F)
            )
        raise IOError(f"Expected 7 bytes from RTC, got {count}")
    except Exception as e:
        if h: lgpio.i2c_close(h)
        print(f"Error reading from RTC: {e}")
        return datetime.datetime.now()

def set_rtc_datetime(dt):
    """Writes a datetime object to the DS3231 RTC module."""
    if not RPI_MODE:
        print("Simulation mode: Cannot set RTC time.")
        return
    h = None
    try:
        time_data = [
            dec_to_bcd(dt.second), dec_to_bcd(dt.minute), dec_to_bcd(dt.hour),
            dec_to_bcd(dt.weekday() + 1), dec_to_bcd(dt.day),
            dec_to_bcd(dt.month), dec_to_bcd(dt.year - 2000)
        ]
        h = lgpio.i2c_open(I2C_BUS, DS3231_ADDRESS)
        lgpio.i2c_write_i2c_block_data(h, 0, time_data)
        lgpio.i2c_close(h)
        print(f"RTC time set to: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        if h: lgpio.i2c_close(h)
        print(f"Error setting RTC time: {e}")

def sync_rtc_with_ntp():
    """Checks for internet and syncs RTC time with an NTP server."""
    if not RPI_MODE:
        return
    try:
        socket.create_connection(("pool.ntp.org", 123), timeout=5)
        print("Internet connection detected. Attempting to sync RTC with NTP server...")
        client = ntplib.NTPClient()
        response = client.request('pool.ntp.org', version=3)
        ntp_time = datetime.datetime.fromtimestamp(response.tx_time)
        set_rtc_datetime(ntp_time)
    except (socket.gaierror, socket.timeout):
        print("No internet connection. Skipping RTC sync.")
    except Exception as e:
        print(f"An error occurred during NTP sync: {e}")

# --- USB Detection and Saving ---
def monitor_usb_drives():
    """Monitor for USB drives and prompt user to save data."""
    known_drives = set()
    
    while True:
        try:
            # Get all mounted drives
            partitions = psutil.disk_partitions()
            current_drives = set()
            
            for partition in partitions:
                # Check if it's a removable drive (USB)
                if 'removable' in partition.opts or '/media' in partition.mountpoint:
                    current_drives.add(partition.mountpoint)
            
            # Check for newly inserted drives
            new_drives = current_drives - known_drives
            for drive in new_drives:
                print(f"USB drive detected: {drive}")
                # Notify the UI about the new drive
                eel.show_usb_prompt(drive)()
            
            # Update known drives
            known_drives = current_drives
            
        except Exception as e:
            print(f"Error monitoring USB drives: {e}")
        
        time.sleep(2)  # Check every 2 seconds
        
def trigger_fullscreen():
    """
    Waits for the 'Spectrometer GUI' window to appear, then uses wmctrl to
    set its state to fullscreen. This is more reliable than time.sleep().
    """
    search_string = "Spectrometer GUI"  # Your window title from index.html
    try:
        # 1. Wait for the window to exist.
        #    We are now searching for a substring "Spectrometer GUI"
        #    instead of an exact match.
        print(f"Waiting for browser window containing: '{search_string}'")
        
        # This command searches for a window *containing* the search string.
        # It waits (--sync) and gets the first ID found.
        window_id = subprocess.check_output(
            ["xdotool", "search", "--sync", "--onlyvisible", "--name", f".*{search_string}.*"],
            text=True
        ).split('\n')[0].strip()
        
        if not window_id:
             raise Exception("xdotool did not find a window ID.")

        print(f"Window found (ID: {window_id}). Setting fullscreen.")

        # 2. Use wmctrl to set the window state to fullscreen.
        subprocess.run(
            ["wmctrl", "-i", "-r", window_id, "-b", "add,fullscreen"],
            check=True
        )
        print("Fullscreen set successfully.")

    except Exception as e:
        print(f"WARNING: Could not force fullscreen. Is xdotool/wmctrl installed? Error: {e}")


@eel.expose
def copy_data_to_usb(mount_point):
    """Copy all output data to the specified USB drive."""
    try:
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
        if not os.path.exists(output_path):
            eel.usb_copy_status('error', 'No output data found.')()
            return
        
        # Create destination folder with timestamp
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        dest_folder = os.path.join(mount_point, f'spectrometer_data_{timestamp}')
        
        # Copy the entire output folder
        shutil.copytree(output_path, dest_folder)
        
        file_count = len(os.listdir(dest_folder))
        eel.usb_copy_status('success', f'Successfully copied {file_count} files to USB.')()
        
    except Exception as e:
        print(f"Error copying to USB: {e}")
        eel.usb_copy_status('error', f'Failed to copy: {str(e)}')()
@eel.expose
def copy_data_to_usb(mount_point):
    # ... (your existing function)
    pass

@eel.expose
def trigger_gdrive_upload():
    """
    Manually triggers the Google Drive upload in a new background thread.
    Returns a status message to the UI.
    """
    print("Manual Google Drive upload triggered by user...")
    
    # Try to acquire the lock without blocking.
    # If we can't get it, an upload is already running.
    if gdrive_upload_lock.acquire(blocking=False):
        print("Got lock, starting upload thread.")
        
        # We need a small wrapper function to run in the thread
        # so we can release the lock when it's done.
        def upload_task_with_lock_release():
            try:
                # This is the main upload function you already have
                upload_output_to_gdrive()
            except Exception as e:
                print(f"Error in manual upload thread: {e}")
            finally:
                # IMPORTANT: Release the lock when done
                gdrive_upload_lock.release()
                print("Upload finished, lock released.")

        # Start the upload in a new daemon thread
        upload_thread = threading.Thread(target=upload_task_with_lock_release, daemon=True)
        upload_thread.start()
        
        return "Upload started. This may take a few minutes."
    else:
        # We couldn't get the lock
        print("Upload already in progress. Ignoring new request.")
        return "Upload is already in progress. Please wait."
        
# --- START OF NEW GOOGLE DRIVE FUNCTIONS ---

def upload_output_to_gdrive():
    """Zips the output folder and uploads it to Google Drive."""
    print("Starting daily Google Drive upload...")
    try:
        # 1. Get GDrive Folder ID from config
        parent_folder_id = config.get('google_drive_folder_id')
        if not parent_folder_id:
            print("Error: 'google_drive_folder_id' not in config.json. Skipping upload.")
            return

        # 2. Find local output path
        base_path = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(base_path, 'output')
        if not os.path.exists(output_path) or not os.listdir(output_path):
            print("Output folder not found or is empty. Nothing to upload.")
            return
        
        # 3. Create a timestamped zip file
        timestamp = get_rtc_datetime().strftime('%Y%m%d_%H%M%S')
        zip_name = f'spectrometer_data_{timestamp}'
        # Place zip file in the base path, not in the output folder
        zip_path_base = os.path.join(base_path, zip_name)
        
        print(f"Creating zip file: {zip_path_base}.zip")
        shutil.make_archive(zip_path_base, 'zip', output_path)
        
        zip_file_path = zip_path_base + '.zip'
        
        # 4. Authenticate with Google Drive
        print("Authenticating with Google Drive...")
        gauth = GoogleAuth()
        # Use Service Account
        gauth.ServiceAuth("service_account.json")
        drive = GoogleDrive(gauth)

        # 5. Upload the zip file
        print(f"Uploading {zip_file_path} to Google Drive...")
        f = drive.CreateFile({
            'title': os.path.basename(zip_file_path),
            'parents': [{'id': parent_folder_id}]
        })
        f.SetContentFile(zip_file_path)
        f.Upload()
        print(f"Successfully uploaded {zip_file_path}.")

        # 6. Clean up local zip file
        os.remove(zip_file_path)
        print(f"Cleaned up local file: {zip_file_path}")

    except Exception as e:
        print(f"Google Drive upload failed: {e}")
        # Clean up partial zip file if it exists
        if 'zip_file_path' in locals() and os.path.exists(zip_file_path):
            try:
                os.remove(zip_file_path)
                print(f"Cleaned up partial zip file: {zip_file_path}")
            except Exception as e_clean:
                print(f"Error cleaning up zip file: {e_clean}")

def gdrive_upload_scheduler():
    """
    Runs in a background thread, triggering an upload at a specific time each day.
    """
    # Set this to the time you want the upload to happen (24-hour format)
    UPLOAD_HOUR = 3  # 3:00 AM
    UPLOAD_MINUTE = 0
    print(f"Google Drive scheduler started. Will upload daily at {UPLOAD_HOUR:02d}:{UPLOAD_MINUTE:02d}.")

    while True:
        try:
            # Get current time from the reliable RTC function
            now = get_rtc_datetime()
            
            # Calculate the next upload time
            next_upload = now.replace(hour=UPLOAD_HOUR, minute=UPLOAD_MINUTE, second=0, microsecond=0)
            
            if now >= next_upload:
                # If it's already past 3:00 AM, schedule for 3:00 AM tomorrow
                next_upload += datetime.timedelta(days=1)
                
            # Calculate sleep duration in seconds
            sleep_duration = (next_upload - now).total_seconds()
            
            print(f"Next GDrive upload scheduled for: {next_upload}. Sleeping for {sleep_duration:.0f} seconds.")
            
            # Sleep until it's time
            # We use a loop to sleep so it can be interrupted if the app closes
            # (Note: This is a daemon thread, so it will be killed on app exit anyway,
            # but sleeping in chunks is generally safer if we wanted to add an exit flag.)
            time.sleep(sleep_duration)
            
            # --- It's time to upload! ---
            upload_output_to_gdrive()
            
            # Sleep for 60 seconds to ensure we don't re-run in the same minute
            time.sleep(60) 

        except Exception as e:
            print(f"Error in GDrive scheduler thread: {e}")
            # Don't crash the thread, just wait a while and retry
            print("Scheduler error. Retrying in 1 hour.")
            time.sleep(3600) # Sleep for an hour

# --- END OF NEW GOOGLE DRIVE FUNCTIONS ---

# --- RPi Controller ---
class RPIController:
    # ... (rest of your class)
# --- RPi Controller ---
class RPIController:
    def __init__(self):
        self.operation_thread = None
        self.stop_operation = threading.Event()
        self.gpio_h = None
        self.dac = None
        self.i2c = None
        self.is_hw_ready = False

        if RPI_MODE:
            try:
                # Initialize GPIO for Relay and Boost using lgpio
                self.gpio_h = lgpio.gpiochip_open(0)
                lgpio.gpio_claim_output(self.gpio_h, RELAY_PIN)
                lgpio.gpio_claim_output(self.gpio_h, BOOST_PIN)
                self.set_relay(False)
                self.set_boost(False)
                print("GPIO pins initialized.")

                # Initialize I2C and DAC using Adafruit libraries
                self.i2c = busio.I2C(board.SCL, board.SDA)
                self.dac = adafruit_mcp4725.MCP4725(self.i2c, address=MCP4725_ADDRESS)
                print(f"MCP4725 DAC initialized successfully at address {hex(MCP4725_ADDRESS)}")
                self.set_pump(False)  # Set initial state to OFF
                self.is_hw_ready = True

            except Exception as e:
                print(f"FATAL: Could not initialize hardware. Error: {e}")
                self.cleanup()

    def set_pump(self, state):
        """Sets pump state using the Adafruit MCP4725 library."""
        if RPI_MODE and self.dac:
            value = 4095 if state else 0
            try:
                self.dac.raw_value = value
                print(f"Pump DAC set to {'ON' if state else 'OFF'} (value: {value}).")
            except Exception as e:
                print(f"Error setting pump state: {e}")
        else:
            print(f"SIMULATION: Pump set to {'ON' if state else 'OFF'}.")

    def set_relay(self, state):
        """Controls the relay using lgpio."""
        if RPI_MODE and self.gpio_h:
            lgpio.gpio_write(self.gpio_h, RELAY_PIN, 1 if state else 0)
        print(f"Relay set to {'ON' if state else 'OFF'}")

    def set_boost(self, state):
        """Controls the boost pin using lgpio."""
        if RPI_MODE and self.gpio_h:
            lgpio.gpio_write(self.gpio_h, BOOST_PIN, 1 if state else 0)
        print(f"Boost set to {'ON' if state else 'OFF'}")

    def cleanup(self):
        if RPI_MODE:
            self.set_pump(False)  # Ensure pump is off
            if self.gpio_h:
                try:
                    lgpio.gpio_write(self.gpio_h, RELAY_PIN, 0)
                    lgpio.gpio_write(self.gpio_h, BOOST_PIN, 0)
                    lgpio.gpiochip_close(self.gpio_h)
                except Exception as e: 
                    print(f"Error closing GPIO: {e}")
            # Note: Adafruit libraries handle I2C cleanup automatically
        print("Hardware cleanup complete.")

    def _execute_spark_sequence(self):
        """Executes one 4-second ON spark sequence."""
        self.set_boost(True)
        self.set_relay(True)
        time.sleep(4)
        self.set_relay(False)
        self.set_boost(False)

    def run_scan_sequence(self, duration_min, sparks, cycles):
        """Runs the scan sequence."""
        print(f"Starting scan: {duration_min} min, {sparks} sparks, {cycles} cycles")
        self.stop_operation.clear()
        total_time_ms = duration_min * 60 * 1000
        
        for cycle in range(1, cycles + 1):
            if self.stop_operation.is_set():
                break
                
            eel.update_ui(f'CYCLE,{cycle}')()
            
            # Pump stage
            self.set_pump(True)
            start_time = time.time()
            
            while (time.time() - start_time) * 1000 < total_time_ms:
                if self.stop_operation.is_set():
                    self.set_pump(False)
                    return
                time_left = total_time_ms - (time.time() - start_time) * 1000
                eel.update_ui(f'TIME_LEFT,{int(time_left)}')()
                time.sleep(1)
            
            self.set_pump(False)
            
            # Spark stage
            for spark in range(1, sparks + 1):
                if self.stop_operation.is_set():
                    return
                eel.update_ui(f'SPARK,{spark}')()
                self._execute_spark_sequence()
                if spark < sparks:
                    time.sleep(2)
        
        eel.update_ui('DONE')()

    def run_clean_sequence(self, sparks):
        """Runs the clean sequence."""
        print(f"Starting clean: {sparks} sparks")
        self.stop_operation.clear()
        
        eel.update_ui(f'CYCLE,1')()
        
        for spark in range(1, sparks + 1):
            if self.stop_operation.is_set():
                return
            eel.update_ui(f'SPARK,{spark}')()
            self._execute_spark_sequence()
            if spark < sparks:
                time.sleep(2)
        
        eel.update_ui('DONE')()

    def run_pm_sequence(self, sparks, threshold, pm_type):
        """Runs PM monitoring sequence."""
        print(f"Starting PM monitoring: {sparks} sparks, threshold {threshold}, PM{pm_type}")
        self.stop_operation.clear()
        
        # Simulated PM sensor reading
        base_value = 500
        noise_range = 100
        
        while not self.stop_operation.is_set():
            # Simulate sensor reading
            current_value = base_value + np.random.randint(-noise_range, noise_range)
            eel.update_ui(f'PM_VALUE,{current_value}')()
            
            if current_value >= threshold:
                eel.update_ui('PM THRESHOLD REACHED')()
                
                # Execute sparks
                for spark in range(1, sparks + 1):
                    if self.stop_operation.is_set():
                        return
                    eel.update_ui(f'SPARK,{spark}')()
                    self._execute_spark_sequence()
                    if spark < sparks:
                        time.sleep(2)
                
                eel.update_ui('PM SPARKS COMPLETE')()
                # Reset base value after sparking
                base_value = 300
            
            time.sleep(1)

    def run_hourly_monitoring_sequence(self):
        """
        Starts an hourly cycle immediately, unless there are 5 minutes or less left in the hour.
        If so, it waits for the next hour to start the first full cycle.
        """
        print("Starting new Hourly Monitoring sequence.")
        self.stop_operation.clear()

        while not self.stop_operation.is_set():
            try:
                # --- 1. DEFINE TIME WINDOWS FOR THE CURRENT HOUR ---
                current_time = get_rtc_datetime()
                current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
                spark_start_time = current_hour_start.replace(minute=55)
                next_hour_start = current_hour_start + datetime.timedelta(hours=1)

                # --- 2. HANDLE LATE START ---
                # If starting with 5 minutes or less left, just wait for the next full hour.
                if current_time >= spark_start_time:
                    print(f"HOURLY: Less than 5 mins left. Waiting for next full cycle at {next_hour_start.strftime('%H:%M:%S')}")
                    eel.update_ui(f"HOURLY_MONITOR_STATUS,Waiting for next hour,")()
                    eel.update_ui(f"HOURLY_NEXT_EVENT,{next_hour_start.isoformat()}")()

                    while get_rtc_datetime() < next_hour_start:
                        if self.stop_operation.is_set():
                            print("Hourly Monitoring aborted during waiting stage.")
                            return
                        time.sleep(1)
                    
                    # Skip the rest of this loop iteration and start fresh at the new hour.
                    continue

                # --- 3. PUMPING STAGE (This runs only if there is >5 mins left in the hour) ---
                print(f"HOURLY: In pumping window. Pumping until {spark_start_time.strftime('%H:%M:%S')}")
                self.set_pump(True)
                eel.update_ui(f"HOURLY_MONITOR_STATUS,Pumping until {spark_start_time.strftime('%H:%M')},")()

                while get_rtc_datetime() < spark_start_time:
                    if self.stop_operation.is_set():
                        self.set_pump(False)
                        print("Hourly Monitoring aborted during pumping.")
                        return
                    time.sleep(1)
                
                self.set_pump(False)
                print("HOURLY: Pumping complete for this cycle.")

                # --- 4. SPARKING STAGE ---
                print("HOURLY: Starting spark stage.")
                sparks = config.get('hourly_sparks', 15)
                eel.update_ui(f"HOURLY_MONITOR_STATUS,Sparking {sparks} times,")()

                for s in range(1, sparks + 1):
                    if self.stop_operation.is_set():
                        print("Hourly Monitoring aborted during sparking.")
                        return
                    self._execute_spark_sequence()
                    if s < sparks:
                        time.sleep(2)
                print("HOURLY: Sparking complete.")

                # --- 5. WAITING STAGE (For cycles that have finished pumping/sparking) ---
                print(f"HOURLY: Cycle finished. Waiting for next cycle at {next_hour_start.strftime('%H:%M:%S')}")
                eel.update_ui(f"HOURLY_MONITOR_STATUS,Waiting for next hour,")()
                eel.update_ui(f"HOURLY_NEXT_EVENT,{next_hour_start.isoformat()}")()

                while get_rtc_datetime() < next_hour_start:
                    if self.stop_operation.is_set():
                        print("Hourly Monitoring aborted during final waiting stage.")
                        return
                    time.sleep(1)

            except Exception as e:
                print(f"An error occurred in hourly monitor: {e}. Retrying in 5 mins.")
                time.sleep(300)

    def start_operation(self, target, *args):
        if self.operation_thread and self.operation_thread.is_alive(): return False
        self.stop_operation.clear()
        self.operation_thread = threading.Thread(target=target, args=args)
        self.operation_thread.daemon = True
        self.operation_thread.start()
        return True

    def abort_operation(self):
        if self.operation_thread and self.operation_thread.is_alive():
            self.stop_operation.set()
            eel.update_ui('STOPPED')()

@eel.expose
def get_rtc_time_str():
    return get_rtc_datetime().strftime("%Y-%m-%d %H:%M:%S")

# Cleaned up initialization
eel.init('web')
rpi_controller = RPIController()

# Start the spectrometer process
spectrometer_process = start_spectrometer_process()

@eel.expose
def close_app():
    sys.exit(0)

@eel.expose
def get_config(): return config
@eel.expose
def start_scan(duration, sparks, cycles): return rpi_controller.start_operation(rpi_controller.run_scan_sequence, duration, sparks, cycles)
@eel.expose
def start_clean(sparks): return rpi_controller.start_operation(rpi_controller.run_clean_sequence, sparks)
@eel.expose
def start_pm(sparks, threshold, pm_type): return rpi_controller.start_operation(rpi_controller.run_pm_sequence, int(sparks), int(threshold), pm_type)
@eel.expose
def start_hourly_monitoring(): return rpi_controller.start_operation(rpi_controller.run_hourly_monitoring_sequence)
@eel.expose
def abort_all():
    rpi_controller.abort_operation()
    return True
@eel.expose
def is_rpi_ready(): return RPI_MODE and rpi_controller.is_hw_ready

@eel.expose
def list_scans():
    """
    Recursively finds all .csv files in the output directory, sorts them by
    modification time (newest first), and returns them as paths relative
    to the 'output' directory (e.g., '1025/scan_01.csv').
    """
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
    if not os.path.exists(output_path): os.makedirs(output_path)
    
    # Recursively find all .csv files using glob
    files = sorted(
        glob.glob(os.path.join(output_path, '**', '*.txt'), recursive=True),
        key=os.path.getmtime,
        reverse=True
    )
    
    # Return paths relative to the 'output' directory
    return [os.path.relpath(f, output_path) for f in files]

@eel.expose
def get_scan_data(filename):
    """
    Gets scan data for a given file.
    'filename' is a relative path like '1025/scan_01.csv'.
    """
    # Reconstruct the full path
    full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', filename)
    
    if not os.path.exists(full_path): 
        print(f"Error: File not found at {full_path}")
        return {'x': [], 'y': [], 'peaks': []}
        
    try:
        df = pd.read_csv(full_path)
        x, y = df.iloc[:,0].tolist(), df.iloc[:,1].tolist()
        peaks, _ = find_peaks(y, height=6500, distance=50)
        return {'x': x, 'y': y, 'peaks': peaks.tolist()}
    except Exception as e:
        print(f"Error reading file {full_path}: {e}")
        return {'x': [], 'y': [], 'peaks': []}

# Removed the get_scan_data_avg function as requested

if __name__ == '__main__':
    # Attempt to sync RTC with internet time at startup
    #sync_rtc_with_ntp()
    
    try:
        usb_thread = threading.Thread(target=monitor_usb_drives, daemon=True)
        usb_thread.start()

        gdrive_thread = threading.Thread(target=gdrive_upload_scheduler, daemon=True)
        gdrive_thread.start()
        
        # Use 'custom' mode and specify the exact command INCLUDING the URL
        eel.start(
            'index.html', 
            mode='custom',
            host='localhost',
            port=8000,
            cmdline_args=[
                '/usr/bin/chromium',
                '--kiosk',
                '--ozone-platform=wayland',
                '--disable-pinch',
                '--noerrdialogs',
                '--disable-infobars',
                '--disable-session-crashed-bubble',
                '--disable-component-update',
                'http://localhost:8000/index.html'  # <-- Add the full URL
            ]
        )
        
    except (SystemExit, MemoryError, KeyboardInterrupt):
        print("UI closed, shutting down application.")
    finally:
        # Clean up
        rpi_controller.abort_operation()
        rpi_controller.cleanup()
        
        # Terminate spectrometer process if it exists
        if spectrometer_process:
            try:
                spectrometer_process.terminate()
                spectrometer_process.wait(timeout=5)
            except:
                spectrometer_process.kill()
        
        print("Application has been shut down.")
