"""Overhead camera: simulated frames, USB, Pi camera, MJPEG, and fixed-exposure MP4."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

# 5x7 glyphs for a readable stamp on simulated frames.
_FONT = {
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "01010", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
}


def ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def draw_text(buf: bytearray, width: int, height: int, x: int, y: int, text: str, color: tuple[int, int, int], scale: int = 2) -> None:
    cursor = x
    for char in text.upper():
        glyph = _FONT.get(char, _FONT[" "])
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        px = cursor + col * scale + sx
                        py = y + row * scale + sy
                        if 0 <= px < width and 0 <= py < height:
                            index = (py * width + px) * 3
                            buf[index : index + 3] = bytes(color)
        cursor += 6 * scale


def fill_rect(
    buf: bytearray,
    width: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
) -> None:
    x0 = max(0, x0)
    y0 = max(0, y0)
    row = bytes(color) * max(0, x1 - x0)
    height = len(buf) // (width * 3)
    for y in range(y0, min(height, y1)):
        start = (y * width + x0) * 3
        buf[start : start + len(row)] = row


class SimulatedCamera:
    driver_name = "simulated"

    def __init__(self, config: dict[str, Any], label: str = "NURSERY"):
        self.width = int(config.get("width", 320))
        self.height = int(config.get("height", 240))
        if self.width % 2 or self.height % 2:
            raise ValueError("frame width and height must be even")
        self.fps = float(config.get("fps", 10))
        self.mm_per_pixel = float(config.get("mm_per_pixel", 0.4))
        self.exposure = "fixed"
        self.exposure_us = int(config.get("exposure_us", 8000))
        self.label = label
        self._tick = 0

    def describe(self) -> dict[str, Any]:
        return {
            "driver": self.driver_name,
            "exposure": self.exposure,
            "auto_exposure": False,
            "exposure_us": self.exposure_us,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "mm_per_pixel": self.mm_per_pixel,
            "role": "overhead",
        }

    def frame_rgb(self) -> bytes:
        self._tick += 1
        w, h = self.width, self.height
        buf = bytearray(bytes((186, 214, 206)) * (w * h))
        fill_rect(buf, w, 8, 8, w - 8, h - 8, (32, 96, 112))
        fill_rect(buf, w, 16, 16, w - 16, h - 16, (64, 150, 158))
        # A few blocks drift so a recorded clip is not a single frozen picture.
        for index, base in enumerate((40, 120, 200)):
            x = 24 + (base + self._tick * (2 + index)) % (w - 70)
            y = 40 + index * 50
            fill_rect(buf, w, x, y, x + 36, y + 10, (230, 214, 186))
        stamp = f"{self.label} FIXED"
        draw_text(buf, w, h, 20, h - 28, stamp, (255, 248, 236), scale=2)
        return bytes(buf)

    def close(self) -> None:
        return


class UsbCamera:
    driver_name = "usb"

    def __init__(self, config: dict[str, Any]):
        import cv2

        self.cv2 = cv2
        self.width = int(config.get("width", 1280))
        self.height = int(config.get("height", 720))
        self.fps = float(config.get("fps", 15))
        self.mm_per_pixel = float(config.get("mm_per_pixel", 0.4))
        self.exposure_us = int(config.get("exposure_us", 8000))
        index = int(config.get("device", 0))
        self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(f"USB camera {index} did not open")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        # V4L2: 1 is manual exposure on many kernels; 3 is auto on others.
        manual = float(config.get("auto_exposure_manual_value", 1))
        self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, manual)
        # OpenCV exposure is often in log steps, not microseconds. Store both.
        if "exposure" in config:
            self.cap.set(cv2.CAP_PROP_EXPOSURE, float(config["exposure"]))

    def describe(self) -> dict[str, Any]:
        return {
            "driver": self.driver_name,
            "exposure": "fixed",
            "auto_exposure": False,
            "exposure_us": self.exposure_us,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "mm_per_pixel": self.mm_per_pixel,
            "role": "overhead",
            "reported_exposure": self.cap.get(self.cv2.CAP_PROP_EXPOSURE),
        }

    def frame_rgb(self) -> bytes:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise RuntimeError("USB camera returned no frame")
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        self.width, self.height = w, h
        return rgb.tobytes()

    def close(self) -> None:
        self.cap.release()


class PiCamera:
    driver_name = "picamera2"

    def __init__(self, config: dict[str, Any]):
        from picamera2 import Picamera2

        self.width = int(config.get("width", 1280))
        self.height = int(config.get("height", 720))
        self.fps = float(config.get("fps", 30))
        self.mm_per_pixel = float(config.get("mm_per_pixel", 0.4))
        self.exposure_us = int(config.get("exposure_us", 8000))
        gain = float(config.get("analogue_gain", 1.0))
        self.picam = Picamera2()
        video = self.picam.create_video_configuration(
            main={"size": (self.width, self.height), "format": "RGB888"}
        )
        self.picam.configure(video)
        self.picam.set_controls(
            {"AeEnable": False, "ExposureTime": self.exposure_us, "AnalogueGain": gain}
        )
        self.picam.start()

    def describe(self) -> dict[str, Any]:
        return {
            "driver": self.driver_name,
            "exposure": "fixed",
            "auto_exposure": False,
            "exposure_us": self.exposure_us,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "mm_per_pixel": self.mm_per_pixel,
            "role": "overhead",
        }

    def frame_rgb(self) -> bytes:
        array = self.picam.capture_array()
        return array.tobytes()

    def close(self) -> None:
        self.picam.stop()


def rgb_to_jpeg(rgb: bytes, width: int, height: int) -> bytes:
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to encode JPEG snapshots")
    proc = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-i",
            "pipe:0",
            "-frames:v",
            "1",
            "-f",
            "mjpeg",
            "pipe:1",
        ],
        input=rgb,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.startswith(b"\xff\xd8"):
        detail = proc.stderr.decode("utf-8", "replace")[-400:]
        raise RuntimeError(f"ffmpeg jpeg encode failed: {detail}")
    return proc.stdout


def write_mp4(path: Path, frames: list[bytes], width: int, height: int, fps: float) -> None:
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to record MP4 clips")
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for frame in frames:
            proc.stdin.write(frame)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    code = proc.wait(timeout=60)
    if code != 0 or not path.exists():
        raise RuntimeError(f"ffmpeg mp4 encode failed: {stderr[-400:]}")


def jpeg_part(jpeg: bytes, boundary: bytes = b"prawnframe") -> bytes:
    header = (
        b"--" + boundary + b"\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
    )
    return header + jpeg + b"\r\n"


def stream_mjpeg(wfile: Any, frame_source: Callable[[], bytes], width: int, height: int, fps: float) -> None:
    """Encode live RGB frames to a multipart MJPEG response until the client leaves."""
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for the MJPEG stream")
    proc = subprocess.Popen(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-an",
            "-f",
            "mjpeg",
            "-q:v",
            "6",
            "pipe:1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    stop = threading.Event()

    def produce() -> None:
        interval = 1.0 / fps if fps else 0.1
        assert proc.stdin is not None
        try:
            while not stop.is_set():
                proc.stdin.write(frame_source())
                proc.stdin.flush()
                time.sleep(interval)
        except (BrokenPipeError, OSError):
            return
        finally:
            try:
                proc.stdin.close()
            except OSError:
                return

    writer = threading.Thread(target=produce, daemon=True)
    writer.start()
    assert proc.stdout is not None
    buffer = b""
    try:
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9", start + 2 if start >= 0 else 0)
                if start < 0 or end < 0:
                    if start > 0:
                        buffer = buffer[start:]
                    break
                jpeg = buffer[start : end + 2]
                buffer = buffer[end + 2 :]
                wfile.write(jpeg_part(jpeg))
                wfile.flush()
    finally:
        stop.set()
        proc.kill()
        proc.wait(timeout=5)


def build_camera(config: dict[str, Any], label: str) -> Any:
    camera_cfg = dict(config.get("camera") or {})
    driver = camera_cfg.get("driver", "simulated")
    if driver == "simulated":
        return SimulatedCamera(camera_cfg, label=label)
    if driver == "usb":
        return UsbCamera(camera_cfg)
    if driver == "picamera2":
        return PiCamera(camera_cfg)
    raise ValueError(f"unknown camera driver {driver}")
