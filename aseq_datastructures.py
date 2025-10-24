""" This module is for the data structures used by the ASEQ spectrometer"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
import numpy as np
import struct

VENDOR_ID = 0xE220  # 57888
PRODUCT_ID = 0x100  # 256
ENCODING = "utf-8"

ZERO_REPORT_ID = 0
PACKET_SIZE_BYTES = 64
STANDARD_TIMEOUT_MS = 100
PARAMETER_SET_DELAY_S = 0.1
MAX_PACKETS_IN_FRAME = 124
REMAINING_PACKETS_ERROR = 250
NUM_OF_PIXELS_IN_PACKET = 30
FLASH_ERASE_TIMEOUT_MS = 5000
FLASH_MAX_READ_PACKETS = 100
FLASH_MAX_WRITE_BYTES = 58
FLASH_MAX_OFFSET = 0x1FFFF
FLASH_MAX_BYTES = 0x20000
CALIBRATION_LINES = 10975  # for python 3.11+ we had to change the way the numpy arrays are initialized in the dataclass. this changed the number of lines read in the flash by 1 -Ilia


class RequestCode(IntEnum):
    status = 1
    set_exposure = 2
    set_acquisition_parameters = 3
    set_frame_format = 4
    set_external_trigger = 5
    set_software_trigger = 6
    clear_memory = 7
    get_frame_format = 8
    get_acquisition_parameters = 9
    set_all_parameters = 0x0C
    get_frame = 0x0A
    set_optical_trigger = 0x0B
    read_flash = 0x1A
    write_flash = 0x1B
    erase_flash = 0x1C
    reset = 0xF1
    detach = 0xF2


class ReplyCode(IntEnum):
    status = 0x81
    set_exposure = 0x82
    set_acquisition_parameters = 0x83
    set_frame_format = 0x84
    set_external_trigger = 0x85
    set_software_trigger = 0x86
    clear_memory = 0x87
    get_frame_format = 0x88
    get_acquisition_parameters = 0x89
    set_all_parameters = 0x8C
    get_frame = 0x8A
    set_optical_trigger = 0x8B
    read_flash = 0x9A
    write_flash = 0x9B
    erase_flash = 0x9C


class TriggerMode(IntEnum):
    disabled = 0
    enabled = 1
    oneshot = 2


class TriggerSlope(IntEnum):
    disabled = 0
    rising = 1
    falling = 2
    rise_fall = 3


class ScanMode(IntEnum):
    continuous = 0
    idle = 1
    every_frame_idle = 2
    frame_averaging = 3


class AverageMode(IntEnum):
    disabled = 0
    average_2 = 1
    average_4 = 2
    average_8 = 3


class Status(IntFlag):
    idle = 0
    in_progress = 1
    memory_full = 2


@dataclass
class Parameters:
    scan_count: int = 1
    blank_scan_count: int = 0
    scan_mode: ScanMode = ScanMode.continuous
    exposure_time_ms: int = 10

    def from_bytes(self, report: list) -> Parameters:
        # Assumes the incoming report still has the first ID byte.
        (
            self.scan_count,
            self.blank_scan_count,
            scan_mode,
            exp_10s_of_us,
        ) = struct.unpack("<HHBL", bytearray(report[1:10]))

        self.scan_mode = ScanMode(scan_mode)
        self.exposure_time_ms = exp_10s_of_us / 100
        return self

    def to_bytes(self) -> bytearray:
        exp_10s_of_us = int(self.exposure_time_ms * 100)
        report = struct.pack(
            "<HHBL",
            self.scan_count,
            self.blank_scan_count,
            self.scan_mode.value,
            exp_10s_of_us,
        )
        return report


@dataclass
class FrameFormat:
    start_element: int = 1
    end_element: int = 10
    reduction_mode: AverageMode = AverageMode.disabled
    pixels_in_frame: int = 10

    def from_bytes(self, report: list) -> FrameFormat:
        # Assumes the incoming report still has the first ID byte.
        (
            self.start_element,
            self.end_element,
            reduction_mode,
            self.pixels_in_frame,
        ) = struct.unpack("<HHBH", bytearray(report[1:8]))
        self.reduction_mode = AverageMode(reduction_mode)
        return self

    def to_bytes(self) -> bytearray:
        report = struct.pack(
            "<HHBH",
            self.start_element,
            self.end_element,
            self.reduction_mode.value,
            self.pixels_in_frame,
        )
        return report
    


@dataclass
class Calibration:
    """
    - Calibration is an ASCII file
    - Only the c.Y type with irradiance calibration is supported.
    - Detector has 3648, thus offsets are needed
        - Wave array has 3653 elements
        - prnu and irr array has 3654 elements
    - Blank memory locations are stored with 0xFF so those are filtered from the
      end of the input array.
    """

    model: str = None
    type: str = None
    serial: int = None
    irr_scaler: float = None
    irr_wave: float = None
    _wavelengths: np.ndarray = field(default_factory=lambda: np.ones(3653))
    _prnu_norm: np.ndarray = field(default_factory=lambda: np.ones(3654))
    _irr_norm: np.ndarray = field(default_factory=lambda: np.ones(3654))

    @property
    def wavelengths(self):
        return self._wavelengths[:-5]

    @property
    def prnu_norm(self):
        return self._prnu_norm[:-6]

    @property
    def irr_norm(self):
        return self._irr_norm[:-6]

    def from_bytes(self, raw: bytearray) -> Calibration:
        while raw and raw[-1] == 0xFF:
            raw.pop()
        lines = raw.decode(ENCODING).replace("\t", "").replace("\r", "").split("\n")
       
        if len(lines) != CALIBRATION_LINES:
            raise ValueError(
                f"Invalid calibration length.  Expected {CALIBRATION_LINES} lines, got {len(lines)}"
            )
        header = lines[0].split()
        self.model = header[0]
        self.type = header[1]
        self.serial = int(header[2])
        self.irr_scaler = float(lines[1])
        self.irr_wave = float(lines[2])
        self._wavelengths = np.asarray(lines[12:3665]).astype(float)
        self._prnu_norm = np.asarray(lines[3666:7318]).astype(float)
        self._prnu_norm = np.append(self._prnu_norm, [1.0, 1.0])  # for some reason lenghts dont match
        self._irr_norm = np.asarray(lines[7320:10974]).astype(float)
        return self

    def to_bytes(self) -> bytearray:
        report = [""] * CALIBRATION_LINES
        report[0] = f"{self.model} {self.type} {self.serial}"
        report[1] = f"{self.irr_scaler:.6e}"
        report[2] = f"{self.irr_wave:.6f}"
        report[12:3665] = self._wavelengths.astype(str)
        report[3666:7320] = self._prnu_norm.astype(str)
        report[7321:10975] = self._irr_norm.astype(str)
        report = "\n".join(report)
        return bytearray(report, encoding=ENCODING)

    def from_file(self, file_path: str) -> Calibration:
        with open(file_path, "rb") as f:
            self.from_bytes(f.read())

    def to_file(self, file_path: str) -> None:
        with open(file_path, "wb") as f:
            f.write(self.to_bytes())
