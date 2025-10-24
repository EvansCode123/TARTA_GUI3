"""
ASEQ Spectrometer using pyusb instead of hidapi
Modified to work with libusb-win32 driver

pre-req:
    pip install numpy
    pip install pyusb
"""
from __future__ import annotations

import logging
import math
import struct
import time

from types import TracebackType
from typing import Optional, Type

import usb.core
import usb.util
import numpy as np

from aseq_datastructures import *

LOGGER = logging.getLogger(__name__)

# USB endpoint addresses
EP_OUT = 0x02  # Endpoint for sending data to device
EP_IN = 0x81   # Endpoint for receiving data from device


class LR1:
    @classmethod
    def discover(cls, target_serial_no: str = None) -> LR1:
        """Find and return the first ASEQ LR1 spectrometer"""
        device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        
        if device is None:
            raise OSError("No LR1 spectrometers found. Check connection and device power.")
        
        return LR1(device)

    def __init__(self, device: usb.core.Device) -> None:
        self.device = device
        self.connected = False
        self.status = None
        self.frames_in_mem = None
        self.parameters = None
        self.frame_format = None
        self.calibration: Calibration = None
        self.external_trigger = False
        
    def __str__(self) -> str:
        serial = "unknown"
        try:
            serial = usb.util.get_string(self.device, self.device.iSerialNumber)
        except:
            pass
        return f"Spectrometer [{serial}]: {'' if self.connected else 'dis'}connected"

    def __enter__(self) -> LR1:
        self._open()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        self._close()
        return False

    def _open(self) -> None:
        try:
            # Detach kernel driver if necessary (Linux only)
            try:
                if self.device.is_kernel_driver_active(0):
                    self.device.detach_kernel_driver(0)
            except (NotImplementedError, AttributeError):
                # Not supported on Windows
                pass
            
            # Set configuration
            self.device.set_configuration()
            
            # Give device time to initialize
            time.sleep(0.2)
            
            # Try reset, but don't fail if it times out
            try:
                self.reset()
            except (usb.core.USBError, OSError) as e:
                LOGGER.warning(f"Reset command failed (this may be normal): {e}")
            
        except usb.core.USBError as e:
            raise OSError(f"Unable to open spectrometer: {e}")

        LOGGER.debug(f"Connected to spectrometer")
        self.connected = True
        self.get_parameters()
        self.get_frame_format()
        self.get_status()
        self.get_calibration()

    def _close(self) -> None:
        try:
            usb.util.dispose_resources(self.device)
        except:
            pass
        self.connected = False
        self.status = None
        self.frames_in_mem = None
        self.parameters = None
        self.frame_format = None
        self.calibration = None
        LOGGER.debug(f"Device Closed")

    def _receive(self, correct_reply: ReplyCode, timeout_ms: int) -> list:
        """Read response from device"""
        try:
            reply = self.device.read(EP_IN, PACKET_SIZE_BYTES, timeout_ms)
            reply = list(reply)
            if reply[0] == correct_reply.value:
                return reply
            else:
                raise OSError(f"Incorrect reply: expected {correct_reply.value}, got {reply[0]}")
        except usb.core.USBError as e:
            raise OSError(f"USB read error: {e}")

    def send(self, report: bytes) -> None:
        """Send command to device"""
        try:
            # Convert to bytes if needed
            if isinstance(report, list):
                report = bytes(report)
            
            # HID reports typically don't include the report ID in USB interrupt transfers
            # Remove the first byte (report ID) if it's 0
            if len(report) > 0 and report[0] == ZERO_REPORT_ID:
                report = report[1:]
            
            self.device.write(EP_OUT, report, STANDARD_TIMEOUT_MS)
        except usb.core.USBError as e:
            raise OSError(f"Unable to write to device: {e}")

    def _send_and_receive(
        self,
        report: bytes,
        correct_reply: int,
        timeout_ms: int,
    ) -> list:
        self.send(report)
        results = self._receive(correct_reply, timeout_ms)
        return results

    def get_status(self) -> Status:
        report = [ZERO_REPORT_ID, RequestCode.status.value, 0x00]
        reply = self._send_and_receive(report, ReplyCode.status, STANDARD_TIMEOUT_MS)
        self.status = Status(reply[1])
        self.frames_in_mem = int.from_bytes(reply[2:4], byteorder="little")
        return self.status

    def reset(self) -> None:
        report = [ZERO_REPORT_ID, RequestCode.reset.value]
        self.send(report)
        time.sleep(0.1)  # Give device time to reset
        LOGGER.debug(f"Device Reset")

    def detach(self) -> None:
        report = [ZERO_REPORT_ID, RequestCode.detach.value]
        self.send(report)
        LOGGER.debug(f"Device Detached")

    def get_parameters(self) -> Parameters:
        LOGGER.debug("Loading Parameters")
        report = [
            ZERO_REPORT_ID,
            RequestCode.get_acquisition_parameters.value,
            0x00,
        ]
        reply = self._send_and_receive(
            report,
            ReplyCode.get_acquisition_parameters,
            STANDARD_TIMEOUT_MS,
        )
        self.parameters = Parameters().from_bytes(reply)
        return self.parameters

    def set_parameters(self) -> None:
        LOGGER.debug("Setting Parameters")
        report = [ZERO_REPORT_ID, RequestCode.set_acquisition_parameters.value]
        report += self.parameters.to_bytes()
        _ = self._send_and_receive(
            report,
            ReplyCode.set_acquisition_parameters,
            STANDARD_TIMEOUT_MS,
        )
        time.sleep(PARAMETER_SET_DELAY_S)

    def set_exposure_ms(self, exposure_ms: int) -> None:
        LOGGER.debug(f"Setting exposure to {exposure_ms} ms")
        self.parameters.exposure_time_ms = exposure_ms
        report = [ZERO_REPORT_ID, RequestCode.set_exposure.value]
        report += self.parameters.to_bytes()[-4:]
        _ = self._send_and_receive(
            report,
            ReplyCode.set_exposure,
            STANDARD_TIMEOUT_MS,
        )

    def get_frame_format(self) -> FrameFormat:
        LOGGER.debug("Getting Frame Format")
        report = [ZERO_REPORT_ID, RequestCode.get_frame_format.value]
        reply = self._send_and_receive(
            report,
            ReplyCode.get_frame_format,
            STANDARD_TIMEOUT_MS,
        )
        self.frame_format = FrameFormat().from_bytes(reply)
        return self.frame_format

    def set_frame_format(self) -> None:
        LOGGER.debug("Setting Frame Format")
        report = [ZERO_REPORT_ID, RequestCode.set_frame_format.value]
        report += self.frame_format.to_bytes()
        _ = self._send_and_receive(
            report,
            ReplyCode.set_frame_format,
            STANDARD_TIMEOUT_MS,
        )

    def set_external_trigger(self, mode: TriggerMode, slope: TriggerSlope) -> None:
        report = [
            ZERO_REPORT_ID,
            RequestCode.set_external_trigger.value,
            mode.value,
            slope.value,
        ]
        reply = self._send_and_receive(
            report,
            ReplyCode.set_external_trigger,
            STANDARD_TIMEOUT_MS,
        )
        self.external_trigger = not (mode.value == TriggerMode.disabled.value)

    def set_optical_trigger(
        self,
        mode: TriggerMode,
        pixel_index: int,
        threshold: int,
    ) -> None:
        report = struct.pack(
            "<BBHH",
            ZERO_REPORT_ID,
            RequestCode.set_optical_trigger.value,
            pixel_index,
            threshold,
        )
        _ = self._send_and_receive(
            report,
            ReplyCode.set_optical_trigger,
            STANDARD_TIMEOUT_MS,
        )

    def software_trigger(self) -> None:
        LOGGER.debug("Software Trigger")
        report = [ZERO_REPORT_ID, RequestCode.set_software_trigger.value]
        self.send(report)

    def clear_memory(self) -> None:
        LOGGER.debug(f"Clearing Memory")
        report = [ZERO_REPORT_ID, RequestCode.clear_memory.value]
        _ = self._send_and_receive(
            report,
            ReplyCode.clear_memory,
            STANDARD_TIMEOUT_MS,
        )

    def get_raw_frame(self, buffer_index: int = 0, offset: int = 0) -> list:
        LOGGER.debug(f"Reading Frame from index {buffer_index}")
        pixels_in_frame = self.frame_format.pixels_in_frame
        packets_to_get = int(math.ceil(pixels_in_frame / NUM_OF_PIXELS_IN_PACKET))

        if packets_to_get > MAX_PACKETS_IN_FRAME:
            raise ValueError("Too many packets to get")

        report = struct.pack(
            "<BBHHB",
            ZERO_REPORT_ID,
            RequestCode.get_frame.value,
            offset,
            buffer_index,
            packets_to_get,
        )

        self.send(report)

        frame_buffer = [0] * pixels_in_frame
        packets_remaining = MAX_PACKETS_IN_FRAME
        packets_received = 0
        while packets_remaining > 0:
            reply = self._receive(ReplyCode.get_frame, STANDARD_TIMEOUT_MS)
            packets_received += 1

            data = struct.unpack(
                "<BHB" + "H" * NUM_OF_PIXELS_IN_PACKET, bytearray(reply)
            )
            pixel_offset = data[1]
            packets_remaining = data[2]
            pixels = data[3:]

            if packets_remaining >= REMAINING_PACKETS_ERROR:
                raise ValueError("Device error when sending packets.")

            if not packets_remaining == (packets_to_get - packets_received):
                raise OSError("Remaining packets error.  Packet dropped?")

            end_offset = pixel_offset + len(pixels)
            frame_buffer[pixel_offset:end_offset] = pixels

        data = frame_buffer[32 : pixels_in_frame - 14]
        LOGGER.debug(f"Read {len(data)} pixels")
        return np.array(data)

    def grab_one(self, exposure_ms=None) -> np.array:
        if exposure_ms:
            self.parameters.exposure_time_ms = exposure_ms
        self.set_parameters()
        self.clear_memory()
        self.software_trigger()
        while self.get_status() == Status.in_progress:
            LOGGER.debug("Waiting for capture to finish")
            time.sleep(self.parameters.exposure_time_ms / 1000)
        raw_read = self.get_raw_frame()
        return raw_read

    def _check_flash_parameters(self, data: int | bytearray, offset: int = 0) -> None:
        if isinstance(data, bytearray) or isinstance(data, bytes):
            length = len(data)
        elif isinstance(data, int):
            length = data
        else:
            raise ValueError(f"Unknown flash data type of {type(data)}")

        if length < 0 or offset < 0:
            raise ValueError("Length and offset must be positive")
        if offset > FLASH_MAX_OFFSET:
            raise ValueError(
                f"offset of {offset} greater than maximum of {FLASH_MAX_OFFSET}"
            )
        if offset + length > FLASH_MAX_BYTES:
            raise ValueError(
                f"length + offset of {offset + length} greater than maximum of {FLASH_MAX_BYTES}"
            )

    def read_flash(self, bytes_to_read: int, abs_offset: int = 0) -> bytearray:
        self._check_flash_parameters(bytes_to_read, abs_offset)

        payload_size = PACKET_SIZE_BYTES - 4
        packets_to_get = int(math.ceil(bytes_to_read / payload_size))

        buffer = [0] * packets_to_get * payload_size
        offset_increment = 0
        LOGGER.debug(f"Reading {bytes_to_read} bytes from flash")
        while packets_to_get:
            packet_batch = int(min(packets_to_get, FLASH_MAX_READ_PACKETS))

            report = struct.pack(
                "<BBIB",
                ZERO_REPORT_ID,
                RequestCode.read_flash.value,
                abs_offset + offset_increment,
                packet_batch,
            )
            self.send(report)
            time.sleep(0.01)

            packets_remaining = packet_batch
            packets_received = 0
            while packets_remaining > 0:
                reply = self._receive(ReplyCode.read_flash, STANDARD_TIMEOUT_MS)
                packets_received += 1

                data = struct.unpack("<BHB" + "B" * payload_size, bytearray(reply))
                local_offset = data[1]
                packets_remaining = data[2]
                data_frame = data[3:]

                if packets_remaining >= REMAINING_PACKETS_ERROR:
                    raise ValueError("Device error when sending packets.")

                if not packets_remaining == (packet_batch - packets_received):
                    raise OSError("Remaining packets error.  Packet dropped?")

                start_offset = offset_increment + local_offset
                end_offset = start_offset + len(data_frame)
                buffer[start_offset:end_offset] = data_frame

            packets_to_get = max(0, packets_to_get - packet_batch)
            offset_increment += packet_batch * payload_size

        return bytearray(buffer[:bytes_to_read])

    def erase_flash(self) -> None:
        LOGGER.debug("Erasing Flash")
        report = [ZERO_REPORT_ID, RequestCode.erase_flash.value]
        _ = self._send_and_receive(
            report,
            ReplyCode.erase_flash,
            FLASH_ERASE_TIMEOUT_MS,
        )

    def write_flash(self, data_bytes: bytearray, abs_offset: int = 0) -> None:
        self._check_flash_parameters(data_bytes, abs_offset)

        bytes_remaining = len(data_bytes)
        read_offset = 0
        write_offset = abs_offset

        LOGGER.debug(f"Writing {bytes_remaining} bytes to flash")
        while bytes_remaining:
            payload_size = min(bytes_remaining, FLASH_MAX_WRITE_BYTES)
            payload = data_bytes[read_offset : read_offset + payload_size]
            report = struct.pack(
                "<BBIB" + "B" * payload_size,
                ZERO_REPORT_ID,
                RequestCode.write_flash.value,
                write_offset,
                payload_size,
                *payload,
            )
            _ = self._send_and_receive(
                report,
                ReplyCode.write_flash,
                STANDARD_TIMEOUT_MS,
            )

            read_offset += payload_size
            write_offset += payload_size
            bytes_remaining = max(0, bytes_remaining - payload_size)

    def get_calibration(self) -> Calibration:
        LOGGER.debug("Loading calibration")
        BYTES_TO_READ = 97089
        try:
            raw_read = self.read_flash(BYTES_TO_READ, abs_offset=0)
            self.calibration = Calibration().from_bytes(raw_read)
            LOGGER.debug("Calibration loaded")
            return self.calibration
        except Exception as e:
            LOGGER.error(f"Unable to load calibration. {e}")

    def apply_irradiance_calibration(self, raw_spectra: np.array) -> np.array:
        return np.multiply(raw_spectra, self.calibration.irr_norm) / (
            self.calibration.prnu_norm
            * self.calibration.irr_scaler
            * (self.parameters.exposure_time_ms * 100)
        )


# Demo Example Usage:
if __name__ == "__main__":
    import datetime
    import os
    
    logging.basicConfig(level=logging.INFO)
    
    # Create output directory if it doesn't exist
    os.makedirs('output', exist_ok=True)
    
    with LR1.discover() as spectro:
        print(f"Connected to: {spectro}")
        
        # Configure external trigger for rising edge
        print("Configuring external trigger (rising edge)...")
        spectro.set_external_trigger(TriggerMode.enabled, TriggerSlope.rising)
        
        # Set exposure time
        exposure_ms = 50
        spectro.set_exposure_ms(exposure_ms)
        print(f"Exposure time set to {exposure_ms} ms")
        
        # Get wavelengths for saving
        wavelengths = spectro.calibration.wavelengths if spectro.calibration else None
        
        print("\nWaiting for external trigger signal...")
        print("Press Ctrl+C to stop\n")
        
        scan_count = 0
        try:
            while True:
                # Clear memory before waiting for trigger
                spectro.clear_memory()
                
                # Wait for trigger and capture
                # Note: With external trigger, device waits for trigger signal automatically
                # We just need to poll status until capture is complete
                status = spectro.get_status()
                while status == Status.idle:
                    time.sleep(0.01)  # Poll every 10ms
                    status = spectro.get_status()
                
                # Wait for capture to complete
                while status == Status.in_progress:
                    time.sleep(exposure_ms / 1000)
                    status = spectro.get_status()
                
                # Read the frame
                frame = spectro.get_raw_frame()
                scan_count += 1
                
                # Generate filename with timestamp
                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                filename = f'output/scan_{scan_count:04d}_{timestamp}.csv'
                
                # Save to CSV file without headers
                if wavelengths is not None:
                    # Save with wavelength and intensity columns
                    data = np.column_stack((wavelengths, frame))
                    np.savetxt(filename, data, delimiter=',', fmt='%.6f,%d')
                else:
                    # Save intensity only
                    np.savetxt(filename, frame, delimiter=',', fmt='%d')
                
                print(f"Scan {scan_count}: Saved to {filename} (Min: {frame.min()}, Max: {frame.max()}, Mean: {frame.mean():.1f})")
                
        except KeyboardInterrupt:
            print(f"\n\nStopped. Total scans captured: {scan_count}")
            print(f"Files saved in 'output/' directory")
