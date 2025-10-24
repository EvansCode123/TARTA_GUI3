# Spectrometer GUI on Raspberry Pi

A simple guide to set up and run the Spectrometer GUI on a Raspberry Pi.

---

## Quick start (clone and run)

```bash
git clone https://github.com/EvansCode123/TARTA_GUI.git
cd TARTA_GUI
pip install -r requirements.txt --break-system-packages
python3 main.py
```

---

## You Will Need
- A Raspberry Pi connected to the internet
- The project files (`main.py`, `requirements.txt`) on your Pi

---

## Step 1: Prepare Your Raspberry Pi

Update your system and enable the correct hardware settings.

1. Open a terminal on your Raspberry Pi.
2. Update the system:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```
3. Enable the I2C hardware interface:
   ```bash
   sudo raspi-config
   ```
   - Navigate to **3 Interface Options**
   - Select **I5 I2C**
   - Choose **Yes** to enable it
   - Select **Finish** and reboot if prompted

---

## Step 2: Install the Software

1. Navigate to the project folder. For example:
   ```bash
   cd /home/pi/Desktop/your-project-folder-name
   ```
2. Install the required libraries:
   ```bash
   pip install -r requirements.txt --break-system-packages
   ```

---

## Step 3: Run the Application

1. Make sure you are in the project folder.
2. Start the program:
   ```bash
   python3 main.py
   ```

---

## Stopping the Program

- Close the application window, **or**
- Press `CTRL + C` in the terminal

---

## Notes
- Test I2C with i2cdetect -y 1
- Make sure your Pi has I2C enabled before running.
