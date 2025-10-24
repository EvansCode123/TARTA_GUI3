import time
import lgpio
import sys

# --- GPIO Configuration ---
# These pins should match your main.py and hardware wiring.
RELAY_PIN = 5
BOOST_PIN = 13

def setup_gpio():
    """Initializes the GPIO chip and claims the necessary pins for output."""
    try:
        print("Attempting to open GPIO chip...")
        h = lgpio.gpiochip_open(0)
        print(f"Claiming GPIO {BOOST_PIN} (Boost) as output.")
        lgpio.gpio_claim_output(h, BOOST_PIN)
        print(f"Claiming GPIO {RELAY_PIN} (Relay) as output.")
        lgpio.gpio_claim_output(h, RELAY_PIN)
        
        # Set initial state to OFF
        lgpio.gpio_write(h, BOOST_PIN, 0)
        lgpio.gpio_write(h, RELAY_PIN, 0)
        
        print("SUCCESS: GPIO pins initialized and set to OFF.")
        return h
    except Exception as e:
        print("-" * 50)
        print(f"FATAL ERROR: Could not initialize GPIO pins. Error: {e}")
        print("Please check the following:")
        print("1. You are running this script on a Raspberry Pi.")
        print("2. The 'lgpio' library is installed correctly.")
        print("3. You have the necessary permissions (try running with 'sudo').")
        print("-" * 50)
        return None

def execute_spark_sequence(h):
    """Executes one 4-second ON spark sequence."""
    if not h:
        print("Cannot execute spark sequence, GPIO handle is not valid.")
        return
        
    print("\n--- STARTING SPARK SEQUENCE ---")
    try:
        print(f"Step 1: Turning ON Boost Pin ({BOOST_PIN})...")
        lgpio.gpio_write(h, BOOST_PIN, 1)
        
        print(f"Step 2: Turning ON Relay Pin ({RELAY_PIN})... (You should hear a click)")
        lgpio.gpio_write(h, RELAY_PIN, 1)
        
        print("Step 3: Waiting for 4 seconds...")
        time.sleep(4)
        
        print(f"Step 4: Turning OFF Relay Pin ({RELAY_PIN})... (You should hear another click)")
        lgpio.gpio_write(h, RELAY_PIN, 0)
        
        print(f"Step 5: Turning OFF Boost Pin ({BOOST_PIN})...")
        lgpio.gpio_write(h, BOOST_PIN, 0)
        print("--- SPARK SEQUENCE COMPLETE ---\n")
        
    except Exception as e:
        print(f"An error occurred during the spark sequence: {e}")

def cleanup_gpio(h):
    """Turns off pins and releases the GPIO chip."""
    if h:
        print("Cleaning up GPIO resources...")
        try:
            lgpio.gpio_write(h, BOOST_PIN, 0)
            lgpio.gpio_write(h, RELAY_PIN, 0)
            lgpio.gpiochip_close(h)
            print("GPIO cleanup complete.")
        except Exception as e:
            print(f"An error occurred during cleanup: {e}")

def main():
    """Main function to run the interactive test."""
    gpio_handle = setup_gpio()

    if gpio_handle is None:
        sys.exit(1)

    try:
        print("\n--- Spark Hardware Test Utility ---")
        while True:
            command = input("Press ENTER to run a spark sequence, or type 'quit' to exit: ").lower()
            if command == "quit":
                break
            execute_spark_sequence(gpio_handle)
            
    except KeyboardInterrupt:
        print("\nCtrl+C detected. Exiting.")
    finally:
        cleanup_gpio(gpio_handle)
        print("Script finished.")

if __name__ == "__main__":
    main()