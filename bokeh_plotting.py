""" Launch with bokeh serve --show bokeh_plotting.py"""

from bokeh.plotting import figure, curdoc
from bokeh.models import Slider, Toggle, Button, ColumnDataSource
from bokeh.layouts import column
from bokeh.io import show
from bokeh.driving import linear

from aseq_spectrometer import LR1, TriggerMode, TriggerSlope

import numpy as np
import time

# # Simulate the get_one() function
# def get_one(exposure_time):
#     """
#     Simulate the spectrum data: a simple sine wave with noise based on the exposure time
#     """
#     wavelength = np.linspace(400, 700, 300)  # Wavelength in nm (e.g., visible spectrum)
#     signal = np.sin(wavelength / 100) + np.random.normal(0, 0.1, len(wavelength))  # Signal with noise
    
#     # Simulating some effect based on exposure time
#     signal *= (exposure_time / 50)  # Amplify or dampen the signal based on exposure time
#     return wavelength, signal

#load spectromerter
spectro =  LR1.discover()
spectro._open()

wavelength = spectro.calibration.wavelengths

# Create initial data
exposure_time = 50  # Default exposure time in ms
signal = spectro.grab_one(exposure_time)

#baseline
baseline_signal = np.zeros_like(signal)

# Create a ColumnDataSource to bind the data to the plot
source = ColumnDataSource(data={'wavelength': wavelength, 'signal': signal})

# Create the plot
p = figure(title="Spectrum", x_axis_label="Wavelength (nm)", y_axis_label="Signal", width=800, height=400)
p.line('wavelength', 'signal', source=source, line_width=2, color="blue", legend_label="Signal")

# Define sliders and other widgets
exposure_slider = Slider(start=2, end=1000, value=50, step=1, title="Exposure Time (ms)")
external_trigger_toggle = Toggle(label="Use External Trigger", button_type="success", active=False)
calibrate_toggle = Toggle(label="Show Calibrated Result", button_type="success", active=False)
baseline_toggle = Toggle(label="Subtract Baseline", button_type="success", active=False)
baseline_button = Button(label="Capture Baseline", button_type="success")

# Function to capture a baseline (for example, reset signal to zero)
def capture_baseline():
    global baseline_signal
    baseline_signal = spectro.grab_one(exposure_time)

def external_trigger_toggle_callback(attr):
    if external_trigger_toggle.active:
        spectro.set_external_trigger(TriggerMode.enabled, TriggerSlope.rising )
    else:
        spectro.set_external_trigger(TriggerMode.disabled, TriggerSlope.rising )

# Function to simulate periodic updates (every second)
@linear()
def periodic_update(step):
    exposure_time = exposure_slider.value
    if external_trigger_toggle.active:
        spectro.set_exposure_ms(exposure_time)
        spectro.clear_memory() #this is a little hacky because it gets called every second ev, but since 
        signal = spectro.get_raw_frame()
    else:
        signal = spectro.grab_one(exposure_time)
    if baseline_toggle.active:
        signal = signal - baseline_signal
    if calibrate_toggle.active:
        signal = spectro.apply_irradiance_calibration(signal)
    source.data = {'wavelength': wavelength, 'signal': signal}

# Add callbacks to the widgets
baseline_button.on_click(capture_baseline)
external_trigger_toggle.on_click(external_trigger_toggle_callback)

# Layout the components
layout = column(exposure_slider, external_trigger_toggle, calibrate_toggle,baseline_toggle, baseline_button, p)

# Add periodic updates every second
curdoc().add_periodic_callback(periodic_update, 1000)

# Add the layout to the current document
curdoc().add_root(layout)

