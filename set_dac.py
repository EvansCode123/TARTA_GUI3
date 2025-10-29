import time
import sys

try:
    # --- RPi specific imports ---
    import board
    import busio
    import adafruit_mcp4725
    RPI_MODE = True
    print("Hardware libraries loaded successfully.")
except ImportError:
    print("\n--- ERROR ---")
    print("This script requires the 'adafruit-circuitpython-mcp4725' library.")
    print("Please run this script on your Raspberry Pi after installing the library:")
    print("pip install adafruit-circuitpython-mcp4725")
    RPI_MODE = False
    sys.exit(1)
except RuntimeError:
    print("\n--- ERROR ---")
    print("Could not initialize hardware. Are you running this on a Raspberry Pi?")
    RPI_MODE = False
    sys.exit(1)


# --- Configuration (from your Main.py) ---
# The default I2C address for the MCP4725
MCP4725_ADDRESS = 0x60 

def set_dac_default_to_zero():
    """
    Connects to the MCP4725 DAC and permanently sets its 
    power-on default value (EEPROM) to 0.
    """
    if not RPI_MODE:
        return # Should have already exited, but for safety.

    print("\n--- DAC EEPROM Update Utility ---")
    print(f"Attempting to connect to MCP4725 DAC at address {hex(MCP4725_ADDRESS)}...")

    try:
        # Initialize I2C and DAC
        i2c = busio.I2C(board.SCL, board.SDA)
        dac = adafruit_mcp4725.MCP4725(i2c, address=MCP4725_ADDRESS)
        
        print("Successfully connected to DAC.")
        
        # Check current value
        current_volatile_value = dac.raw_value
        print(f"Current VOLATILE (temporary) value: {current_volatile_value} (out of 4095)")

        print("\n!!! WARNING !!!")
        print("This script will permanently set the DAC's POWER-ON DEFAULT value to 0.")
        print("This will overwrite the value stored in its internal EEPROM.")
        
        # User confirmation
        try:
            choice = input("\nAre you sure you want to proceed? (y/n): ").strip().lower()
            if choice != 'y':
                print("Operation cancelled. No changes were made.")
                return
        except KeyboardInterrupt:
            print("\nOperation cancelled. No changes were made.")
            return

        print("\nSetting DAC's current value to 0...")
        # 1. Set the volatile (current) value to 0
        dac.raw_value = 0
        
        print("Saving value '0' to DAC's internal EEPROM (permanent memory)...")
        # 2. Save this '0' value to the non-volatile EEPROM memory
        dac.save_to_eeprom()
        
        # The MCP4725 takes a moment (up to 50ms) to write to EEPROM
        time.sleep(0.1) # Wait 100ms to be safe
        
        print("\n--- SUCCESS ---")
        print("The DAC's power-on default value has been permanently set to 0.")
        print("You can now power-cycle your device, and it will start at 0V.")

    except (ValueError, OSError) as e:
        print("\n--- FAILED ---")
        print(f"Error: Could not communicate with DAC at address {hex(MCP4725_ADDRESS)}.")
        print("Please check your I2C connections, permissions, and the address.")
        print(f"Details: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")

if __name__ == "__main__":
    set_dac_default_to_zero()
