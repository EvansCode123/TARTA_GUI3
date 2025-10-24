"""Export spectrometer calibration to a text file"""
import logging
import datetime
import os
from aseq_spectrometer import LR1

logging.basicConfig(level=logging.INFO)

# Create output directory if it doesn't exist
os.makedirs('output', exist_ok=True)

print("Connecting to spectrometer...")
with LR1.discover() as spectro:
    print(f"Connected to: {spectro}")
    
    if spectro.calibration is None:
        print("ERROR: No calibration data available!")
        exit(1)
    
    # Generate filename with timestamp
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'output/calibration_{timestamp}.txt'
    
    # Write calibration to file
    with open(filename, 'w') as f:
        cal = spectro.calibration
        
        # Write header information
        f.write(f"Model: {cal.model}\n")
        f.write(f"Type: {cal.type}\n")
        f.write(f"Serial: {cal.serial}\n")
        f.write(f"Irradiance Scaler: {cal.irr_scaler}\n")
        f.write(f"Irradiance Wavelength: {cal.irr_wave}\n")
        f.write("\n")
        
        # Write wavelength data
        f.write("Wavelengths (nm):\n")
        for wl in cal.wavelengths:
            f.write(f"{wl:.6f}\n")
        
        f.write("\n")
        
        # Write PRNU normalization data
        f.write("PRNU Normalization:\n")
        for prnu in cal.prnu_norm:
            f.write(f"{prnu:.6f}\n")
        
        f.write("\n")
        
        # Write irradiance normalization data
        f.write("Irradiance Normalization:\n")
        for irr in cal.irr_norm:
            f.write(f"{irr:.6f}\n")
    
    print(f"\nCalibration data saved to: {filename}")
    print(f"Model: {cal.model}")
    print(f"Serial: {cal.serial}")
    print(f"Wavelength range: {cal.wavelengths[0]:.1f} - {cal.wavelengths[-1]:.1f} nm")
    print(f"Total pixels: {len(cal.wavelengths)}")