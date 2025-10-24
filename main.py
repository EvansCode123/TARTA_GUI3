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

# The data handling functions below are unchanged.
@eel.expose
def list_scans():
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
    if not os.path.exists(output_path): os.makedirs(output_path)
    files = sorted(glob.glob(os.path.join(output_path, '*.csv')), key=os.path.getmtime, reverse=True)
    return [os.path.basename(f) for f in files]

@eel.expose
def get_scan_data(filename):
    full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', filename)
    if not os.path.exists(full_path): return {'x': [], 'y': [], 'peaks': []}
    df = pd.read_csv(full_path)
    x, y = df.iloc[:,0].tolist(), df.iloc[:,1].tolist()
    peaks, _ = find_peaks(y, height=6500, distance=50)
    return {'x': x, 'y': y, 'peaks': peaks.tolist()}

@eel.expose
def get_scan_data_avg():
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
    files = sorted(glob.glob(os.path.join(output_path, '*.csv')), key=os.path.getmtime, reverse=True)[:10]
    if not files: return {'x': [], 'y': [], 'peaks': []}
    dfs = [pd.read_csv(f) for f in files]
    min_len = min(len(df) for df in dfs)
    dfs_trimmed = [df.iloc[:min_len, :] for df in dfs]
    combined_df = pd.concat(dfs_trimmed)
    avg_df = combined_df.groupby(combined_df.columns[0])[combined_df.columns[1]].mean().reset_index()
    x, y = avg_df.iloc[:,0].tolist(), avg_df.iloc[:,1].tolist()
    peaks, _ = find_peaks(y, height=6500, distance=50)
    return {'x': x, 'y': y, 'peaks': peaks.tolist()}

if __name__ == '__main__':
    # Attempt to sync RTC with internet time at startup
    sync_rtc_with_ntp()
    
    try:
        usb_thread = threading.Thread(target=monitor_usb_drives, daemon=True)
        usb_thread.start()
        eel.start('index.html', size=(1280, 800))
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
