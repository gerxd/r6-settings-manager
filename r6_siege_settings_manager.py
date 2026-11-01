# /// script
# requires-python = ">=3.11"
# dependencies = ["PySide6-Essentials>=6.7", "numpy>=1.26"]
# ///
import argparse
import codecs
import ctypes
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from string import Template
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPalette, QPen, QPixmap,
                           QPolygonF, QShortcut)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget, QStyle,
                               QStyledItemDelegate, QStyleOptionSlider, QVBoxLayout, QWidget)

APP_NAME = "R6 Siege Settings Manager"
__version__ = "0.4"
SETTINGS_FILE = "GameSettings.ini"
MAX_BACKUPS = 50
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
BENCH_RE = re.compile(r"^Benchmark-(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})\.(ini|html)$", re.I)

COL = {
    "bg": "#0f1115", "panel": "#15181d", "card": "#1b1f26", "card_hi": "#232831", "line": "#2d333d",
    "text": "#e8eaed", "muted": "#9099a6", "faint": "#636c78",
    "accent": "#ef9a3c", "accent_hi": "#f7ae5c", "accent_deep": "#a85a14", "accent_dim": "#3a2a17", "ink": "#1c1206",
    "ok": "#4fb37a", "ok_dim": "#173826", "bad": "#e5675f", "bad_dim": "#3d1c1b",
    "info": "#62a8e8", "violet": "#b48cf2",
}
PROFILE_COLORS = ["#ef9a3c", "#62a8e8", "#4fb37a", "#e5675f", "#b48cf2", "#e57fb8", "#4cc3c3", "#c8ccd2"]
SITES = {
    "R6 Tracker": "https://r6.tracker.network/r6siege/profile/ubi/{uuid}/overview",
    "stats.cc": "https://stats.cc/siege/{uuid}",
}
REGIONS = {
    "ap-southeast-2": "Sydney", "ap-northeast-1": "Tokyo", "ap-northeast-3": "Osaka", "ap-northeast-2": "Seoul",
    "ap-east-1": "Hong Kong", "ap-southeast-1": "Singapore", "ap-south-1": "Mumbai", "eu-north-1": "Stockholm",
    "eu-west-1": "Ireland", "eu-west-2": "London", "eu-central-1": "Frankfurt", "eu-west-3": "Paris",
    "eu-south-1": "Milan", "af-south-1": "Cape Town", "sa-east-1": "Sao Paulo", "us-east-1": "N. Virginia",
    "us-east-2": "Ohio", "ca-central-1": "Montreal", "us-west-1": "N. California", "us-west-2": "Oregon",
    "uaenorth": "UAE North",
}
DATACENTERS = ["default"] + [f"gamelift/{r}" for r in REGIONS if "-" in r] + ["playfab/uaenorth"]


def documents_dir():
    if sys.platform == "win32":
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
            return Path(buf.value)
    return Path.home() / "Documents"


DEFAULT_ROOT = documents_dir() / "My Games" / "Rainbow Six - Siege"
APP_DIR = Path(os.environ.get("R6SM_DATA") or Path(os.environ.get("APPDATA") or Path.home()) / "R6SiegeSettingsManager")


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32), ("dmSpecVersion", ctypes.c_ushort), ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort), ("dmDriverExtra", ctypes.c_ushort), ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long), ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong), ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32), ("dmLogPixels", ctypes.c_ushort), ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong), ("dmPelsHeight", ctypes.c_ulong), ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong), ("dmICMMethod", ctypes.c_ulong), ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong), ("dmDitherType", ctypes.c_ulong), ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong), ("dmPanningWidth", ctypes.c_ulong), ("dmPanningHeight", ctypes.c_ulong),
    ]


def display_modes():
    modes = set()
    if sys.platform != "win32":
        return modes
    try:
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        i = 0
        while ctypes.windll.user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
            if dm.dmBitsPerPel >= 24 and dm.dmPelsWidth >= 640:
                modes.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
            i += 1
    except (OSError, AttributeError):
        pass
    return modes


def siege_running():
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.lower().startswith('"rainbowsix') for line in out.splitlines())


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nice_date(d):
    return f"{d.day} {d:%b %Y}, {d.hour}:{d:%M}"


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def aspect_name(w, h):
    if not w or not h:
        return ""
    r = w / h
    for a, b in ((16, 9), (16, 10), (4, 3), (5, 4), (21, 9), (32, 9), (3, 2)):
        if abs(r - a / b) / (a / b) < 0.012:
            return f"{a}:{b}"
    return f"{r:.2f}:1"


class IniFile:
    def __init__(self, text="", bom=False):
        self.bom = bom
        self.newline = "\n" if "\n" in text and "\r\n" not in text else "\r\n"
        self.trailing = text.endswith("\n") or not text
        body = text[:-len(self.newline)] if text.endswith(self.newline) else text
        self.lines = body.split(self.newline) if body else []
        self._index()

    @classmethod
    def from_bytes(cls, data):
        bom = data.startswith(codecs.BOM_UTF8)
        return cls(data[3 if bom else 0:].decode("utf-8", "surrogateescape"), bom)

    @classmethod
    def load(cls, path):
        return cls.from_bytes(Path(path).read_bytes())

    def copy(self):
        return IniFile.from_bytes(self.to_bytes())

    def to_bytes(self):
        text = self.newline.join(self.lines) + (self.newline if self.trailing and self.lines else "")
        return (codecs.BOM_UTF8 if self.bom else b"") + text.encode("utf-8", "surrogateescape")

    def _index(self):
        self.sections = {}
        sec = None
        for i, line in enumerate(self.lines):
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                sec = s[1:-1].strip()
                self.sections.setdefault(sec, {"header": i, "keys": {}})
            elif sec is not None and s and s[0] not in ";#" and "=" in s:
                self.sections[sec]["keys"][line.split("=", 1)[0].strip()] = i

    def get(self, sec, key):
        i = self.sections.get(sec, {}).get("keys", {}).get(key)
        return None if i is None else self.lines[i].split("=", 1)[1].strip()

    def set(self, sec, key, value):
        value = str(value)
        info = self.sections.get(sec)
        if info and key in info["keys"]:
            i = info["keys"][key]
            self.lines[i] = f"{self.lines[i].split('=', 1)[0]}={value}"
            return
        if info is None:
            if self.lines and self.lines[-1].strip():
                self.lines.append("")
            self.lines += [f"[{sec}]", f"{key}={value}"]
        else:
            last = max([info["header"], *info["keys"].values()])
            self.lines.insert(last + 1, f"{key}={value}")
        self._index()

    def items(self):
        for sec, info in self.sections.items():
            for key, i in sorted(info["keys"].items(), key=lambda kv: kv[1]):
                yield sec, key, self.lines[i].split("=", 1)[1].strip()

    def values(self):
        return {(s, k): v for s, k, v in self.items()}

    def comments(self, sec):
        info = self.sections.get(sec)
        out = []
        if not info:
            return out
        for line in self.lines[info["header"] + 1:]:
            s = line.strip()
            if s.startswith("["):
                break
            if s.startswith(";"):
                out.append(s[1:])
        return out

    def hints(self):
        out = {}
        for sec in self.sections:
            for c in self.comments(sec):
                m = re.match(r"\s*([A-Za-z][\w/]*)\s*(?:=>|\.|:)\s*(.+)", c)
                if m:
                    out[(sec, m.group(1))] = m.group(2).strip()
        return out

    def datacenters(self):
        out = []
        for c in self.comments("ONLINE"):
            m = re.match(r"\s+(default|[a-z]+/[a-z0-9-]+)\b", c)
            if m and m.group(1) not in out:
                out.append(m.group(1))
        return out


@dataclass
class Spec:
    sec: str
    key: str
    label: str
    kind: str
    cat: str = "Other"
    group: str = "General"
    help: str = ""
    choices: dict | None = None
    lo: float = 0
    hi: float = 100
    step: float = 1
    digits: int = 0
    unit: str = ""
    hw: bool = False
    ini_only: bool = False
    scale: float = 1
    offset: float = 0

    def __post_init__(self):
        if self.kind == "toggle" and not self.choices:
            self.choices = {"0": "Off", "1": "On"}

    @property
    def path(self):
        return f"{self.sec}.{self.key}"

    def show(self, value):
        if value is None:
            return "not set"
        if self.kind == "datacenter":
            return region_label(value)
        if self.choices and value in self.choices:
            return self.choices[value]
        if self.kind in ("range", "number", "limit", "refresh"):
            v = num(value)
            if v is None:
                return value
            if self.kind == "limit" and v < 30:
                return "Off"
            if self.kind == "refresh" and v == 0:
                return "Auto"
            return f"{self.offset + self.scale * v:.{self.digits}f}{self.unit}"
        if self.kind == "resolution":
            return "Auto" if value == "0" else f"{value} px"
        return value


def region_label(value):
    if value == "default":
        return "Automatic (lowest ping)"
    code = value.split("/", 1)[-1]
    return f"{REGIONS[code]}  ({code})" if code in REGIONS else value


SCOPES = ("1x", "1xHalf", "2x", "2xHalf", "3x", "4x", "5x", "8x", "12x")
SCOPE_LABELS = {s: {"1xHalf": "1.5x", "2xHalf": "2.5x"}.get(s, s) for s in SCOPES}
GROUP_NOTES = {
    ("Graphics", "Quality"): "Labels follow the in-game menu order. Which number stands for which level comes from "
                             "community files, not from Ubisoft.",
    ("Mouse", "Aim down sights"): "At 50, every sight matches hipfire by monitor distance.",
}
DEG_PER_COUNT = 0.00572957795
SENS_KEYS = (("INPUT", "MouseYawSensitivity"), ("INPUT", "MousePitchSensitivity"),
             ("INPUT", "MouseSensitivityMultiplierUnit"))
SYNONYMS = {"gamma": ["brightness"], "cap": ["fps limit"], "scale": ["scaling"], "stretched": ["resolution", "aspect ratio"],
            "ptt": ["push to talk"], "fullscreen": ["display mode"], "borderless": ["display mode"],
            "windowed": ["display mode"], "sens": ["sensitivity"]}
CALCULATOR_TEXT = ["Sensitivity calculator", "DPI eDPI cm/360", "Mouse", "Hipfire"]

CATEGORIES = ["Display", "Graphics", "Mouse", "Controller", "Controls", "Audio", "Voice chat",
              "Accessibility", "Network", "System", "Other"]


def _schema():
    out = []

    def add(cat, group, path, label, kind, help="", **kw):
        sec, key = path.split(".")
        out.append(Spec(sec, key, label, kind, cat, group, help, **kw))

    D, G, M, C, K, A, V, X, N, S = CATEGORIES[:10]
    hold = {"0": "Hold", "1": "Toggle"}

    add(D, "Screen", "DISPLAY_SETTINGS.WindowMode", "Display mode", "choice",
        choices={"0": "Fullscreen", "1": "Windowed", "2": "Borderless"})
    add(D, "Screen", "DISPLAY_SETTINGS.ResolutionWidth", "Resolution", "resolution", "Auto uses the desktop resolution.")
    add(D, "Screen", "DISPLAY_SETTINGS.ResolutionHeight", "Resolution height", "hidden")
    add(D, "Screen", "DISPLAY_SETTINGS.RefreshRate", "Refresh rate", "refresh", "Auto lets DirectX pick the rate.",
        unit=" Hz", digits=3)
    add(D, "Screen", "DISPLAY_SETTINGS.AspectRatio", "Aspect ratio", "choice",
        "Display (value 0) comes from a comment in the file. The game menu does not offer it.",
        choices={str(i): r for i, r in enumerate(["Display", "Resolution", "5:4", "4:3", "3:2", "16:10", "5:3", "16:9",
                                                  "19:10", "21:9"])})
    add(D, "Screen", "DISPLAY_SETTINGS.UseLetterbox", "Widescreen letterbox", "toggle")
    add(D, "Screen", "DISPLAY_SETTINGS.Monitor", "Monitor", "number", "Index of the display the game opens on.", hw=True)
    add(D, "Picture", "DISPLAY_SETTINGS.DefaultFOV", "Field of view", "range", "Vertical field of view.",
        lo=60, hi=90, unit="°")
    add(D, "Picture", "DISPLAY.Brightness", "Brightness", "range", lo=0, hi=100)
    add(D, "Frame rate and latency", "DISPLAY.FPSLimit", "FPS limit", "limit",
        "Off removes the cap. The lowest cap is 30 fps.", lo=30, hi=500, unit=" fps")
    add(D, "Frame rate and latency", "DISPLAY_SETTINGS.VSync", "VSync", "choice",
        choices={"0": "Off", "1": "Every frame", "2": "Every 2nd frame"})
    add(D, "Frame rate and latency", "DISPLAY.NVReflex", "NVIDIA Reflex", "choice",
        choices={"0": "Off", "1": "On", "2": "On + Boost"})
    add(D, "Frame rate and latency", "DISPLAY.NVReflexIndicator", "Reflex latency indicator", "toggle")
    add(D, "Window", "DISPLAY.InitialWindowPositionX", "Window position X", "number", "-1 centers the window.", hw=True)
    add(D, "Window", "DISPLAY.InitialWindowPositionY", "Window position Y", "number", "-1 centers the window.", hw=True)

    add(G, "Preset", "QUALITY.OverallQualityLevelName", "Overall quality", "combo",
        "Custom keeps the values below. Only Custom and High are confirmed as names the game writes.",
        choices={v: v for v in ("Custom", "Low", "Medium", "High", "Ultra")})
    tiers = ["Low", "Medium", "High", "Very High", "Ultra", "Ultra+"]
    for key, label, labels in [("Texture", "Texture quality", tiers[:5]),
                               ("TextureFiltering", "Texture filtering", ["Linear", "2x", "4x", "8x", "16x"]),
                               ("Geometry", "LOD quality", tiers), ("Lighting", "Shading quality", ["Low", "Medium", "High", "Ultra"]),
                               ("Shadow", "Shadow quality", tiers[:5]), ("Reflection", "Reflection quality", tiers[:3]),
                               ("VFX", "VFX quality", tiers), ("AO", "Ambient occlusion", ["Off", "SSBC", "SSBC+"]),
                               ("LensEffects", "Lens effects", ["Off", "Bloom", "Bloom + flare"])]:
        add(G, "Quality", f"CUSTOM_QUALITY.{key}", label, "choice", choices={str(i): l for i, l in enumerate(labels)})
    add(G, "Quality", "CUSTOM_QUALITY.DOF", "Zoom-in depth of field", "toggle")
    add(G, "Quality", "CUSTOM_QUALITY.Atmospheric", "Atmospheric", "number",
        "Custom files hold -1. What it does is not documented.")
    up = "Upscaling"
    add(G, up, "CUSTOM_QUALITY.Upscaler", "Upscaling technique", "choice",
        "Only Temporal is checked against the game menu. After you save another choice, check it in the game.",
        choices={"0": "Off", "1": "Temporal", "2": "NVIDIA DLSS", "3": "AMD FSR 1.0", "4": "AMD FSR 2.0"})
    add(G, up, "CUSTOM_QUALITY.TemporalUpscalerMode", "Upscaling mode", "choice",
        choices={"0": "Fixed render scaling", "1": "Adaptive render scaling"})
    add(G, up, "CUSTOM_QUALITY.RenderScalingFactor", "Render scaling", "range", "Used in fixed mode.",
        lo=25, hi=100, step=5, scale=5, offset=25, unit="%")
    add(G, up, "CUSTOM_QUALITY.AdaptiveRenderScalingTargetFPS", "Adaptive render scaling target", "range",
        "Used in adaptive mode. The range is read from the menu slider and may be slightly off.",
        lo=30, hi=330, step=5, scale=5, offset=5, unit=" fps")
    add(G, up, "CUSTOM_QUALITY.AntiAliasing", "Anti-aliasing mode", "choice",
        "Listed in menu order. Off and FXAA are not available with Temporal upscaling.",
        choices={str(i): n for i, n in enumerate(["T-AA", "Off", "FXAA", "T-AA-2x", "T-AA-4x"])})
    add(G, up, "CUSTOM_QUALITY.Sharpness", "Anti-aliasing sharpness", "range", lo=0, hi=100, step=5, scale=5)
    modes = ["Auto", "Quality", "Balanced", "Performance", "Ultra Performance"]
    for key, label, note in [("DLSSPerfQual", "DLSS mode", "Listed in menu order."),
                             ("FSR2PerfQual", "FSR 2.0 mode", "The list is assumed to match DLSS mode."),
                             ("FSRPerfQual", "FSR 1.0 mode", "The list is assumed to match DLSS mode.")]:
        add(G, up, f"CUSTOM_QUALITY.{key}", label, "choice", f"Used when this upscaler is selected. {note}",
            choices={str(i): n for i, n in enumerate(modes)})
    add(G, "Texture streaming", "CUSTOM_QUALITY.TextureStreaming", "Texture streaming", "number")
    add(G, "Texture streaming", "CUSTOM_QUALITY.TextureVRAMLimit", "Texture VRAM limit", "number")

    add(M, "Hipfire", "INPUT.MouseYawSensitivity", "Horizontal sensitivity", "range", "In-game slider. Default 50.",
        lo=1, hi=100)
    add(M, "Hipfire", "INPUT.MousePitchSensitivity", "Vertical sensitivity", "range", "In-game slider. Default 50.",
        lo=1, hi=100)
    add(M, "Hipfire", "INPUT.MouseSensitivityMultiplierUnit", "Sensitivity multiplier", "number",
        "Scales both sliders. Default 0.02. A lower value gives finer slider steps.", step=0.0001, digits=6,
        ini_only=True)
    add(M, "Hipfire", "INPUT.MouseSensitivity", "Mouse sensitivity", "range",
        "What it does is not documented. The two sliders above are the in-game controls.", lo=1, hi=100)
    add(M, "Aim down sights", "INPUT.ADSMouseUseSpecific", "Per-scope ADS", "toggle")
    add(M, "Aim down sights", "INPUT.ADSMouseSensitivityGlobal", "Global ADS", "range", lo=1, hi=100)
    for scope in SCOPES:
        add(M, "Aim down sights", f"INPUT.ADSMouseSensitivity{scope}", f"ADS {SCOPE_LABELS[scope]}", "range", lo=1, hi=100)
    add(M, "Aim down sights", "INPUT.ADSMouseMultiplierUnit", "ADS multiplier", "number",
        "Scales the ADS sliders. Default 0.02.", step=0.0001, digits=6, ini_only=True)
    add(M, "Aim down sights", "INPUT.AimDownSightsMouse", "Legacy ADS", "range",
        "The single ADS slider from before the 2020 ADS rework.", lo=1, hi=100)
    add(M, "Aim down sights", "INPUT.XFactorAiming", "X factor aiming", "number",
        "Legacy ADS multiplier. Default 0.02.", step=0.0001, digits=6, ini_only=True)
    add(M, "Mouse", "INPUT.RawInputMouseKeyboard", "Raw input", "toggle")
    add(M, "Mouse", "INPUT.InvertMouseAxisY", "Invert mouse Y axis", "toggle")
    add(M, "Mouse", "INPUT.MouseScroll", "Mouse scroll", "toggle")

    add(C, "Look", "INPUT.YawSensitivity", "Horizontal sensitivity", "range", lo=1, hi=100)
    add(C, "Look", "INPUT.PitchSensitivity", "Vertical sensitivity", "range", lo=1, hi=100)
    add(C, "Look", "INPUT.InvertAxisY", "Invert Y axis", "toggle")
    add(C, "Look", "INPUT.Rumble", "Vibration", "toggle")
    add(C, "Deadzones", "INPUT.DeadzoneLeftStick", "Left stick", "range", lo=0, hi=100, unit="%")
    add(C, "Deadzones", "INPUT.DeadzoneRightStick", "Right stick", "range", lo=0, hi=100, unit="%")
    add(C, "Deadzones", "INPUT.OuterDeadzoneRightStick", "Right stick outer", "range", lo=0, hi=100, unit="%")
    add(C, "Deadzones", "INPUT.DeadzoneTriggerLeft", "Left trigger", "range", lo=0, hi=100, unit="%")
    add(C, "Deadzones", "INPUT.DeadzoneTriggerRight", "Right trigger", "range", lo=0, hi=100, unit="%")
    add(C, "Deadzones", "INPUT.DeadzoneSwitchInputStick", "Switch input stick", "range", lo=0, hi=100, unit="%")
    add(C, "Aim down sights", "INPUT.ADSGamepadUseSpecific", "Per-scope ADS", "toggle")
    add(C, "Aim down sights", "INPUT.ADSGamepadSensitivityGlobal", "Global ADS", "range", lo=1, hi=100)
    for scope in SCOPES:
        add(C, "Aim down sights", f"INPUT.ADSGamepadSensitivity{scope}", f"ADS {SCOPE_LABELS[scope]}", "range", lo=1, hi=100)
    add(C, "Aim down sights", "INPUT.ADSGamepadMultiplierUnit", "ADS multiplier", "number",
        "Scales the ADS sliders. Default 0.02.", step=0.0001, digits=6, ini_only=True)
    add(C, "Aim down sights", "INPUT.AimDownSights", "Legacy ADS", "range",
        "The single ADS slider from before the 2020 ADS rework.", lo=1, hi=100)
    add(C, "Advanced", "INPUT.AdvancedGamepadOptions", "Advanced options", "toggle")
    add(C, "Advanced", "INPUT.ControllerStickRotationCurve", "Stick response curve", "number")
    for key, label in [("GamepadHorizontalFullTiltBoostModifier", "Full tilt boost horizontal"),
                       ("GamepadVerticalFullTiltBoostModifier", "Full tilt boost vertical"),
                       ("FullTiltBoostRampupDelay", "Full tilt ramp-up delay"),
                       ("GamepadFullTiltBoostRampupTime", "Full tilt ramp-up time"),
                       ("ADSGamepadHorizontalFullTiltBoostModifier", "ADS full tilt boost horizontal"),
                       ("ADSGamepadVerticalFullTiltBoostModifier", "ADS full tilt boost vertical"),
                       ("ADSFullTiltBoostRampupDelay", "ADS full tilt ramp-up delay"),
                       ("ADSGamepadFullTiltBoostRampupTime", "ADS full tilt ramp-up time"),
                       ("GamepadLookDampeningTime", "Look dampening time")]:
        float_key = "Delay" in key or "Time" in key
        add(C, "Advanced", f"INPUT.{key}", label, "number", step=0.05 if float_key else 1, digits=2 if float_key else 0)
    add(C, "Advanced", "INPUT.ControllerInputDevice", "Controller input device", "number")

    for key, label in [("ToggleAim", "Aim"), ("ToggleAimGamepad", "Aim (controller)"), ("ToggleLean", "Lean"),
                       ("ToggleSprint", "Sprint"), ("ToggleCrouch", "Crouch"), ("ToggleProne", "Prone"),
                       ("ToggleWalk", "Walk"), ("ToggleDroneBoost", "Drone boost"),
                       ("ToggleGadgetDeploymentKeyboard", "Gadget deployment"),
                       ("ToggleGadgetDeploymentGamepad", "Gadget deployment (controller)")]:
        add(K, "Hold or toggle", f"INPUT.{key}", label, "choice", choices=hold)
    add(K, "Scheme", "INPUT.ControlSchemeIndex", "Control scheme", "number")

    for key, label in [("MasterVolume", "Master"), ("InGameSFXVolume", "In-game effects"),
                       ("InGameMusicVolume", "In-game music"), ("MenuSFXVolume", "Menu effects"),
                       ("MenuMusicVolume", "Menu music"), ("VoiceVolume", "Voice lines")]:
        add(A, "Volume", f"AUDIO.{key}", label, "range", lo=0, hi=100, unit="%")
    add(A, "Output", "AUDIO.DynamicRangeMode", "Dynamic range", "choice",
        choices={"0": "Hi-Fi", "1": "TV", "2": "Night mode"})
    add(A, "Output", "AUDIO.MonoOutput", "Mono audio", "toggle")
    add(A, "Output", "AUDIO.Mute", "Mute all", "toggle")
    add(A, "Subtitles", "AUDIO.Subtitle", "Subtitles", "toggle")
    add(A, "Subtitles", "AUDIO.SubtitleType", "Subtitle type", "number")

    add(V, "Voice chat", "AUDIO.VoiceChatEnabled", "Voice chat", "toggle")
    add(V, "Voice chat", "AUDIO.VoiceChatTeamOnly", "Team only", "toggle")
    add(V, "Voice chat", "AUDIO.VoiceChatMuteAll", "Mute everyone", "toggle")
    add(V, "Voice chat", "AUDIO.VoiceChatPlaybackLevel", "Playback volume", "range", lo=0, hi=100, unit="%")
    add(V, "Microphone", "AUDIO.VoiceChatCaptureMode", "Capture mode", "choice",
        choices={"0": "Auto", "1": "Manual", "2": "Push to talk"})
    add(V, "Microphone", "AUDIO.VoiceChatCaptureLevel", "Microphone volume", "range", lo=0, hi=100, unit="%")
    add(V, "Microphone", "AUDIO.VoiceChatCaptureThresholdV2", "Voice activation threshold", "range", lo=0, hi=100)

    add(X, "Colors", "ACCESSIBILITY.AccessibilityColorMode", "Color mode", "number")
    for key, label in [("PositiveColorIndex", "Positive color"), ("NegativeColorIndex", "Negative color"),
                       ("ObjectiveColorIndex", "Objective color"), ("PingColorIndex", "Ping color"),
                       ("TeamColorAllyIndex", "Ally team color"), ("TeamColorEnemyIndex", "Enemy team color")]:
        add(X, "Colors", f"ACCESSIBILITY.{key}", label, "number")
    add(X, "Effects", "ACCESSIBILITY.StunVFXMode", "Stun visual effect", "number")
    add(X, "Effects", "ACCESSIBILITY.TinnitusSFXMode", "Tinnitus sound effect", "number")
    add(X, "Effects", "ACCESSIBILITY.HearingFatigueAid", "Hearing fatigue aid", "toggle")

    add(N, "Matchmaking", "ONLINE.DataCenterHint", "Server region", "datacenter",
        "Default picks the region with the lowest ping.", ini_only=True)
    add(N, "Matchmaking", "GAMEPLAY.GameplayPingEnable", "Gameplay ping", "toggle")
    add(N, "Proxy", "ONLINE.UseProxyAutoDiscovery", "Proxy auto discovery", "toggle",
        "When on, the game looks up http_proxy, https_proxy, all_proxy and no_proxy and uses them for all its web "
        "requests.")

    add(S, "GPU", "DISPLAY.GPUAdapter", "GPU adapter index", "number", hw=True)
    add(S, "GPU", "DISPLAY.GPUAdapterSelectMode", "GPU selection", "choice",
        choices={"0": "By index", "1": "By adapter ID"}, hw=True)
    add(S, "GPU", "DISPLAY.GPUAdapterInfo", "GPU adapter ID", "info", hw=True)
    add(S, "GPU", "DISPLAY.GPUInfo", "GPU info", "info", hw=True)
    add(S, "GPU", "DISPLAY_SETTINGS.EnableAMDMultiDraw", "AMD multi draw", "toggle")
    add(S, "GPU", "DISPLAY_SETTINGS.EnableIntelMultiDraw", "Intel multi draw", "toggle")
    add(S, "Audio devices", "AUDIO.AudioOutputDevice", "Output device ID", "info", hw=True)
    add(S, "Audio devices", "AUDIO.AudioOutputVoiceChatDevice", "Voice chat output ID", "info", hw=True)
    add(S, "Audio devices", "AUDIO.AudioInputVoiceChatDevice", "Voice chat input ID", "info", hw=True)
    add(S, "Advanced", "DISPLAY.Console", "In-game console", "toggle",
        "Opens with the ` key when that key has no other binding.", ini_only=True)
    add(S, "Advanced", "HARDWARE_INFO.HardwareNotificationEnable", "Hardware notifications", "toggle", hw=True)
    add(S, "Advanced", "GENERAL.Version", "File version", "info", hw=True)
    return out


SCHEMA = _schema()
SPECS = {(s.sec, s.key): s for s in SCHEMA}
RANK = {(s.sec, s.key): i for i, s in enumerate(SCHEMA)}
GROUP_RANK = {}
for _s in SCHEMA:
    GROUP_RANK.setdefault((_s.cat, _s.group), len(GROUP_RANK))


def auto_spec(sec, key, value):
    label = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", key)
    if sec in ("HARDWARE_INFO", "READ_ONLY"):
        return Spec(sec, key, label, "info", "System", "Detected hardware" if sec == "HARDWARE_INFO" else "Engine",
                    hw=True)
    v = num(value)
    if v is None:
        return Spec(sec, key, label, "text", "Other", sec.replace("_", " ").title())
    decimals = len(value.split(".", 1)[1].rstrip("0")) if "." in value else 0
    return Spec(sec, key, label, "number", "Other", sec.replace("_", " ").title(),
                step=1 if not decimals else 10 ** -min(decimals, 3), digits=min(decimals, 4))


def search_words(text):
    # Splits at spaces, hyphens and camelCase, so "VSync" gives "v" and "sync".
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return re.findall(r"[a-z0-9]+", text.lower())


def search_index(fields):
    # The words of a field are joined, so a query word can run over several: "vsync" and "antialiasing" match.
    compact, starts = "", []
    for f in fields:
        for w in search_words(f):
            starts.append(len(compact))
            compact += w
        compact += " "
    return compact, starts


def search_query(text):
    # Each typed word gives itself and its synonyms. A synonym like "display mode" must match as one run of words.
    clauses = []
    for raw in text.split():
        w = "".join(search_words(raw))
        if w:
            clauses.append([w] + ["".join(search_words(p)) for key, phrases in SYNONYMS.items()
                                  if key == w or len(w) >= 3 and key.startswith(w) for p in phrases])
    return clauses


def search_hit(index, clauses):
    compact, starts = index
    return all(any(compact.startswith(w, s) for w in alts for s in starts) for alts in clauses)


@dataclass
class Profile:
    uuid: str
    folder: Path

    @property
    def path(self):
        return self.folder / SETTINGS_FILE

    @property
    def locked(self):
        try:
            return not os.stat(self.path).st_mode & stat.S_IWRITE
        except OSError:
            return False

    @property
    def mtime(self):
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None


@dataclass
class Backup:
    path: Path
    when: datetime
    reason: str
    keep: bool = False


def scan_profiles(root):
    try:
        dirs = sorted(p for p in Path(root).iterdir()
                      if p.is_dir() and UUID_RE.match(p.name) and (p / SETTINGS_FILE).is_file())
    except OSError:
        return []
    return [Profile(p.name, p) for p in dirs]


def set_locked(path, locked):
    os.chmod(path, stat.S_IREAD if locked else stat.S_IREAD | stat.S_IWRITE)


class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.file = self.folder / "store.json"
        self.data = {"profiles": {}, "presets": {}, "settings": {}}
        try:
            self.data.update(json.loads(self.file.read_text("utf-8")))
        except (OSError, ValueError):
            pass

    def save(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
        os.replace(tmp, self.file)

    def meta(self, uuid):
        m = self.data["profiles"].setdefault(uuid, {})
        m.setdefault("label", "")
        m.setdefault("tags", [])
        m.setdefault("notes", "")
        if "color" not in m:
            used = {p.get("color") for p in self.data["profiles"].values()}
            free = [c for c in PROFILE_COLORS if c not in used]
            m["color"] = free[0] if free else PROFILE_COLORS[int(uuid[:8], 16) % len(PROFILE_COLORS)]
        return m

    def name(self, uuid):
        return self.meta(uuid)["label"] or f"Profile {uuid[:8]}"

    def all_tags(self):
        return sorted({t for m in self.data["profiles"].values() for t in m.get("tags", [])}, key=str.lower)

    def backup_dir(self, uuid):
        return self.folder / "backups" / uuid

    @staticmethod
    def backup_name(when, reason, keep):
        # Windows does not allow some characters in file names, so a time such as 20:09 is written 20.09.
        text = re.sub(r'[\x00-\x1f<>:"/\\|?*_]', "", re.sub(r"(?<=\d):(?=\d)", ".", reason))
        text = " ".join(text.split())[:60].strip(" .") or "backup"
        return f"{when:%Y%m%d-%H%M%S}-{when.microsecond // 1000:03d}_{'keep_' if keep else ''}{text}.ini"

    def backup(self, uuid, data, reason, keep=False):
        # Returns the new file, or None when the newest backup holds the same bytes. Kept backups skip the rotation.
        have = self.backups(uuid)
        if have and not keep:
            try:
                if have[0].path.read_bytes() == data:
                    return None
            except OSError:
                pass
        d = self.backup_dir(uuid)
        d.mkdir(parents=True, exist_ok=True)
        when = datetime.now()
        when = when.replace(microsecond=when.microsecond // 1000 * 1000)
        if have and when <= have[0].when:
            when = have[0].when + timedelta(milliseconds=1)
        while (d / self.backup_name(when, reason, keep)).exists():
            when += timedelta(milliseconds=1)
        dest = d / self.backup_name(when, reason, keep)
        dest.write_bytes(data)
        for old in [b for b in self.backups(uuid) if not b.keep][MAX_BACKUPS:]:
            try:
                old.path.unlink(missing_ok=True)
            except OSError:
                pass
        return dest

    def backup_before(self, uuid, data, action):
        # The first backup of a profile is its original, and it is kept.
        first = not self.backups(uuid)
        return self.backup(uuid, data, "original" if first else f"before {action}", keep=first)

    def backups(self, uuid):
        # Newer stamps have milliseconds, and kept backups have keep_ before the reason.
        out = []
        for p in self.backup_dir(uuid).glob("*.ini"):
            m = re.match(r"(\d{8}-\d{6})(?:-(\d{3}))?_(keep_)?(.*)\.ini$", p.name)
            try:
                when = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(microsecond=int(m.group(2) or 0) * 1000) \
                    if m else datetime.fromtimestamp(p.stat().st_mtime)
            except (ValueError, OSError):
                continue
            # Older names have hyphens for spaces. A date from nice_date gets its colon back.
            reason = (re.sub(r"(\d{4}, \d{1,2})\.(\d\d)\b", r"\1:\2", m.group(4)) if m.group(2)
                      else m.group(4).replace("-", " ")) if m else p.stem
            out.append(Backup(p, when, reason, bool(m and m.group(3))))
        return sorted(out, key=lambda b: (b.when, b.path.name), reverse=True)

    def set_keep(self, b, keep):
        dest = b.path.with_name(self.backup_name(b.when, b.reason, keep))
        b.path.rename(dest)
        b.path, b.keep = dest, keep


def write_profile(store, profile, ini, action):
    # Backups hold every saved version: the file before the write, unless the newest backup has it, and the result.
    data = ini.to_bytes()
    store.backup_before(profile.uuid, profile.path.read_bytes(), action)
    locked = profile.locked
    if locked:
        set_locked(profile.path, False)
    try:
        tmp = profile.path.with_name(profile.path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, profile.path)
    finally:
        if locked:
            set_locked(profile.path, True)
    try:
        store.backup(profile.uuid, data, f"after {action}")
    except OSError:
        pass  # The next write backs up this version first.


@dataclass
class Bench:
    when: datetime
    ini: Path
    html: Path | None
    stats: dict
    detail: dict | None = None


# Benchmark tiles: decimals, unit, caption, and 1 when higher is better, -1 when lower is better, 0 when neither.
BENCH_TILES = ((0, "", "average fps", 1), (0, "", "1st percentile of sampled fps", 1), (0, "", "lowest fps", 1),
               (0, "", "highest fps", 1), (1, " s", "duration", 0), (1, " ms", "average CPU / GPU frame time", -1),
               (0, "%", "GPU load", 0), (0, " °C", "GPU temperature", 0))


def bench_names(folder):
    # Report names in the benchmark folder with their date and kind. A missing folder has none.
    out = {}
    try:
        names = [p.name for p in Path(folder).iterdir()]
    except OSError:
        return out
    for name in names:
        m = BENCH_RE.match(name)
        try:
            if m:
                out[name] = (datetime(*map(int, m.groups()[:6])), m.group(7).lower())
        except ValueError:
            pass
    return out


def bench_html(when, htmls):
    # The game writes the report a few seconds after the stats.
    return next((h for t, h in htmls if 0 <= (t - when).total_seconds() <= 15), None)


def scan_benchmarks(root):
    folder = Path(root) / "Benchmark"
    names = bench_names(folder)
    htmls = [(t, folder / n) for n, (t, kind) in names.items() if kind == "html"]
    out = []
    for when, p in sorted((t, folder / n) for n, (t, kind) in names.items() if kind == "ini"):
        html = bench_html(when, htmls)
        try:
            stats = {k: num(v) for _, k, v in IniFile.load(p).items()}
        except OSError:
            continue
        out.append(Bench(when, p, html, stats))
    return out


def parse_bench_html(path):
    text = Path(path).read_text("utf-8", errors="replace")

    def series(name):
        i = text.rfind(f"{name} = [")
        if i < 0:
            return np.array([])
        body = text[text.index("[", i) + 1:text.index("]", i)]
        try:
            return np.array([float(x) for x in body.split(",") if x.strip()], dtype=float)
        except ValueError:
            return np.array([])

    def scalar(name):
        found = re.findall(rf"\b{name}\s*=\s*(-?\d+(?:\.\d+)?)\s*[;\n]", text)
        return float(found[-1]) if found else None

    strings = dict(re.findall(r'(?:var|let|const) (\w+) = "([^"]*)";', text))
    settings = []
    arr = re.search(r"changlistArray\s*=\s*\[(.*?)\];", text, re.S)
    if arr:
        for label, var in re.findall(r'"([^"]+)",\s*(\w+),\s*\w+', arr.group(1)):
            if var in strings:
                settings.append((label.capitalize() if label.isupper() else label, strings[var]))
    system = {k[12:]: v.strip() for k, v in re.findall(r'id="(system_info_\w+)">([^<]*)<', text)}
    extra = {k: scalar(f"_{k}") for k in ("frames", "gpu_load", "gpu_temperature", "vram_used", "sys_vram_total",
                                          "ram_used", "sys_ram_total", "total_cpu_load")}
    return {"fps": series("dataPointsFPS"), "cpu": series("dataPointsCPU_time"), "gpu": series("dataPointsGPU_time"),
            "settings": settings, "system": system, "extra": extra}


F = SimpleNamespace()

QSS = Template("""
QMainWindow, QDialog { background: $bg; }
QToolTip { background: $card_hi; color: $text; border: 1px solid $line; padding: 4px 8px; }
QLabel { background: transparent; }
QLabel[tone="muted"] { color: $muted; }
QLabel[tone="faint"] { color: $faint; }
QLabel[tone="accent"] { color: $accent; }
QLabel[tone="ok"] { color: $ok; }
QLabel[tone="bad"] { color: $bad; }
*[role="h1"] { font-size: 22px; font-weight: bold; }
*[role="h2"] { font-size: 15px; font-weight: bold; }
*[role="bold"] { font-weight: bold; }
*[role="small"] { font-size: 12px; }
*[role="caps"] { font-size: 11px; font-weight: bold; }
*[role="tile"] { font-size: 24px; font-weight: bold; }
*[role="mono"] { font-family: Consolas; font-size: 13px; }
*[role="tiny_mono"] { font-family: Consolas; font-size: 11px; }

QPushButton { background: $card_hi; color: $text; border: none; border-radius: 8px; padding: 0 14px; }
QPushButton:hover { background: $line; }
QPushButton:pressed { background: $card; padding-top: 2px; }
QPushButton:disabled { color: $faint; }
QDialog QPushButton { min-height: 30px; min-width: 72px; }
QPushButton[kind="primary"] { background: $accent; color: $ink; font-weight: bold; }
QPushButton[kind="primary"]:hover { background: $accent_hi; }
QPushButton[kind="primary"]:pressed { background: $accent_deep; }
QPushButton[kind="primary"]:disabled { background: $card_hi; color: $faint; }
QPushButton[kind="flat"] { background: transparent; color: $muted; }
QPushButton[kind="flat"]:hover { background: $card_hi; color: $text; }
QPushButton[kind="flat"]:disabled { color: $faint; }
QPushButton[kind="uuid"] { background: transparent; color: $faint; padding: 0 8px; }
QPushButton[kind="uuid"]:hover { background: $card_hi; color: $muted; }
QPushButton[kind="step"] { padding: 0; font-size: 15px; }
QPushButton[kind="step"]:pressed { padding-top: 2px; }
QPushButton[kind="reset"] { background: transparent; color: $accent; padding: 0; font-size: 16px; }
QPushButton[kind="reset"]:hover { background: $line; }
QPushButton[kind="danger"] { background: transparent; color: $bad; border: 1px solid $bad_dim; }
QPushButton[kind="ghost"] { background: transparent; color: $bad; }
QPushButton[kind="danger"]:hover, QPushButton[kind="ghost"]:hover { background: $bad_dim; }
QPushButton[kind="chip"] { background: $line; border-radius: 14px; padding: 0 12px; font-size: 12px; }
QPushButton[kind="chip"]:hover { background: $bad_dim; }
QPushButton[kind="quick"] { background: transparent; color: $muted; border: 1px solid $line; border-radius: 12px;
                            padding: 0 10px; font-size: 12px; }
QPushButton[kind="quick"]:hover { background: $card_hi; color: $text; }
QPushButton[kind="nav"] { background: transparent; color: $muted; text-align: left; padding: 0 12px; }
QPushButton[kind="nav"]:hover { background: $card_hi; }
QPushButton[kind="nav"][active="true"] { background: $card_hi; color: $text; }

QFrame[seg="choice"] { background: $card_hi; border-radius: 8px; }
QFrame[seg="choice"] QPushButton, QFrame[seg="tabs"] QPushButton { background: transparent; border-radius: 6px; padding: 0 12px; }
QFrame[seg="choice"] QPushButton:hover { background: $line; }
QFrame[seg="tabs"] QPushButton { font-weight: bold; padding: 0 10px; }
QFrame[seg="tabs"] QPushButton[badge="true"] { padding-right: 40px; }
QFrame[seg="tabs"] QPushButton:hover { background: $card_hi; }
QFrame[seg="choice"] QPushButton:pressed, QFrame[seg="tabs"] QPushButton:pressed { padding-top: 2px; }
QFrame[seg="choice"] QPushButton:checked, QFrame[seg="tabs"] QPushButton:checked { background: $accent_deep; color: #ffffff; }
QFrame[seg="choice"] QPushButton:checked:hover, QFrame[seg="tabs"] QPushButton:checked:hover { background: #bd6a1f; }

QLineEdit, QPlainTextEdit { background: $bg; color: $text; border: 1px solid $line; border-radius: 8px; padding: 0 6px;
                            selection-background-color: $accent_deep; selection-color: #ffffff; }
QPlainTextEdit { padding: 4px 2px; }
QDialog QLineEdit { min-height: 30px; }
QLineEdit:hover, QPlainTextEdit:hover { border-color: $faint; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: $accent; }
QLineEdit:disabled { color: $faint; border-color: $card_hi; }

QComboBox { background: $card_hi; color: $text; border: none; border-radius: 8px; padding: 0 28px 0 12px; }
QComboBox:hover { background: $line; }
QComboBox:disabled { color: $faint; }
QComboBox:editable { background: $bg; border: 1px solid $line; padding-left: 4px; }
QComboBox:editable:hover { border-color: $faint; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QLineEdit { background: transparent; border: none; padding: 0; }
QComboBox QAbstractItemView { background: $card_hi; color: $text; border: 1px solid $line; padding: 4px; outline: 0; }
QComboBox QAbstractItemView::item { min-height: 28px; padding: 0 8px; border-radius: 6px; }
QComboBox QAbstractItemView::item:hover { background: $line; }
QComboBox QAbstractItemView::item:selected { background: $accent_deep; color: #ffffff; }

QSlider { background: transparent; min-height: 22px; }
QSlider::groove:horizontal { height: 4px; background: $line; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::sub-page:horizontal:disabled { background: $line; }
QSlider::handle:horizontal { background: $text; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover { background: $accent_hi; }
QSlider::handle:horizontal:pressed { background: $accent; }
QSlider::handle:horizontal:disabled { background: $faint; }

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $line; border-radius: 3px; min-height: 32px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $line; border-radius: 3px; min-width: 32px; }
QScrollBar::handle:hover, QScrollBar::handle:pressed { background: $faint; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QMenu { background: $card_hi; color: $text; border: 1px solid $line; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 6px; }
QMenu::item:selected { background: $accent_deep; color: #ffffff; }
QMenu::separator { height: 1px; background: $line; margin: 4px 6px; }
""")


def font(px, bold=False, family="Segoe UI"):
    f = QFont(family)
    f.setPixelSize(px)
    f.setBold(bold)
    if family == "Consolas":
        f.setStyleHint(QFont.Monospace)
    return f


def qt_app():
    app = QApplication.instance()
    if app is not None:
        return app
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "bg"),
                      (QPalette.AlternateBase, "card"), (QPalette.Text, "text"), (QPalette.Button, "card_hi"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent_deep"), (QPalette.ToolTipBase, "card_hi"),
                      (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "faint"), (QPalette.Link, "accent"),
                      (QPalette.Light, "line"), (QPalette.Midlight, "card_hi"), (QPalette.Mid, "line"),
                      (QPalette.Dark, "bg"), (QPalette.Shadow, "bg")):
        pal.setColor(role, QColor(COL[key]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(COL["faint"]))
    app.setPalette(pal)
    app.setFont(font(13))
    F.__dict__.update(body=font(13), small=font(12), tiny=font(10, True), caps=font(11, True), chart=font(12),
                      chart_small=font(11))
    app.setStyleSheet(QSS.substitute(COL))
    return app


def show_msg(parent, kind, text):
    {"info": QMessageBox.information, "warning": QMessageBox.warning, "error": QMessageBox.critical}[kind](
        parent, APP_NAME, text)


def ask_yes_no(parent, text):
    return QMessageBox.question(parent, APP_NAME, text) == QMessageBox.Yes


def ask_save(parent, text):
    answer = QMessageBox.question(parent, APP_NAME, text, QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                                  QMessageBox.Save)
    return {QMessageBox.Save: True, QMessageBox.Discard: False}.get(answer)


def ask_text(parent, title, prompt):
    text, ok = QInputDialog.getText(parent, title, prompt)
    return text if ok else None


def mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(*(round(x + (y - x) * t) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3])))


def restyle(w, **props):
    if all(w.property(k) == v for k, v in props.items()):
        return
    for k, v in props.items():
        w.setProperty(k, v)
    w.style().unpolish(w)
    w.style().polish(w)


def hbox(spacing=6, margins=(0, 0, 0, 0)):
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    return lay


def vbox(spacing=6, margins=(0, 0, 0, 0)):
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    return lay


def discard(w):
    # Hiding a focused widget sends focus to the next one in the chain, often the sidebar filter.
    fw = QApplication.focusWidget()
    if fw is not None and (fw is w or w.isAncestorOf(fw)):
        fw.clearFocus()
    w.hide()
    w.deleteLater()


def clear_layout(lay):
    while lay.count():
        item = lay.takeAt(0)
        if item.widget():
            discard(item.widget())
        elif item.layout():
            clear_layout(item.layout())
            item.layout().deleteLater()


def label(text="", role=None, tone=None, wrap=False, width=None, margins=None):
    w = QLabel(text)
    w.setTextFormat(Qt.PlainText)
    if role:
        w.setProperty("role", role)
    if tone:
        w.setProperty("tone", tone)
    if wrap:
        w.setWordWrap(True)
    if width:
        w.setFixedWidth(width)
    if margins:
        w.setContentsMargins(*margins)
    return w


def button(text, command=None, kind=None, width=None, height=30, role=None):
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if role:
        b.setProperty("role", role)
    if width:
        b.setMinimumWidth(width)
    b.setFixedHeight(height)
    b.setCursor(Qt.PointingHandCursor)
    if command:
        b.clicked.connect(lambda *_: command())
    return b


def entry(placeholder="", width=None, height=34, role=None, center=False, text=""):
    e = QLineEdit(text)
    e.setPlaceholderText(placeholder)
    if role:
        e.setProperty("role", role)
    if width:
        e.setFixedWidth(width)
    e.setFixedHeight(height)
    if center:
        e.setAlignment(Qt.AlignCenter)
    return e


def scroll_area(margins=(2, 0, 10, 0), spacing=0):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    area.verticalScrollBar().setSingleStep(24)
    body = QWidget()
    lay = vbox(spacing, margins)
    body.setLayout(lay)
    area.setWidget(body)
    body.setAutoFillBackground(False)
    return area, lay


def wheel_steps(acc, fn):
    while acc >= 120:
        fn(1)
        acc -= 120
    while acc <= -120:
        fn(-1)
        acc += 120
    return acc


def text_at(p, x, y, text, color, anchor="c"):
    fm = p.fontMetrics()
    w, h = fm.horizontalAdvance(text) + 2, fm.height()
    left = {"e": x - w, "w": x, "c": x - w / 2}[anchor]
    p.setPen(QColor(color))
    p.drawText(QRectF(left, y - h / 2, w, h), Qt.AlignCenter, text)


def paint_count(widget, n, right=10):
    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    p.setFont(F.tiny)
    text = str(n)
    w = max(24, p.fontMetrics().horizontalAdvance(text) + 12)
    r = QRectF(widget.width() - right - w, (widget.height() - 20) / 2, w, 20)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(COL["accent"]))
    p.drawRoundedRect(r, 10, 10)
    p.setPen(QColor(COL["ink"]))
    p.drawText(r, Qt.AlignCenter, text)


def soft(path):
    # Zero-width spaces after separators let a long path wrap inside a narrow label.
    return str(path).replace("\\", "\\​").replace("/", "/​")


class Card(QFrame):
    def __init__(self, bg=None, radius=12, hot=None, border=None, width=0):
        super().__init__()
        self.bg, self.radius, self.hot_bg = QColor(bg or COL["card"]), radius, hot
        self.border, self.border_w = border, width
        self.hot, self.on_click, self.on_menu = False, None, None

    def set_border(self, color, width):
        self.border, self.border_w = color, width
        self.update()

    def enterEvent(self, e):
        if self.hot_bg:
            self.hot = True
            self.update()

    def leaveEvent(self, e):
        if self.hot:
            self.hot = False
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.on_click:
            self.on_click()
        else:
            super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        if self.on_menu:
            self.on_menu(e.globalPos())
        else:
            e.ignore()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.border_w
        p.setPen(QPen(QColor(self.border), w) if w else Qt.NoPen)
        p.setBrush(QColor(self.hot_bg) if self.hot else self.bg)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(w / 2, w / 2, -w / 2, -w / 2), self.radius, self.radius)
        self.paint_extra(p)

    def paint_extra(self, p):
        pass


class Pill(QWidget):
    def __init__(self, text="", bg=None, fg=None, font=None, height=20, radius=6, pad=6):
        super().__init__()
        self.font_, self.radius, self.pad = font or F.tiny, radius, pad
        self.setFixedHeight(height)
        self.set(text, bg, fg)

    def set(self, text, bg, fg):
        self.txt, self.bg, self.fg = text, QColor(bg or COL["line"]), QColor(fg or COL["text"])
        self.setFixedWidth(QFontMetrics(self.font_).horizontalAdvance(text) + 2 * self.pad)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.bg)
        p.drawRoundedRect(QRectF(self.rect()), self.radius, self.radius)
        p.setPen(self.fg)
        p.setFont(self.font_)
        p.drawText(self.rect(), Qt.AlignCenter, self.txt)


class Dot(QWidget):
    def __init__(self, size=14):
        super().__init__()
        self.setFixedSize(size, size)
        self.color = QColor(COL["faint"])

    def set_color(self, color):
        self.color = QColor(color)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(QRectF(self.rect()))


class HoverButton(QAbstractButton):
    def __init__(self):
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


class Switch(HoverButton):
    def __init__(self, text="", w=46, h=24, left=False, tone="text"):
        super().__init__()
        self.setCheckable(True)
        self.setText(text)
        self.tw, self.th, self.left, self.color = w, h, left, QColor(COL[tone])
        self.pos_ = 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(120)
        self.anim.valueChanged.connect(self._step)
        self.toggled.connect(self._toggled)

    def _toggled(self, on):
        end = 1.0 if on else 0.0
        if self.isVisible():
            self.anim.stop()
            self.anim.setStartValue(self.pos_)
            self.anim.setEndValue(end)
            self.anim.start()
        else:
            self.pos_ = end
            self.update()

    def _step(self, v):
        self.pos_ = float(v)
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(F.small)
        extra = 50 if self.left else (fm.horizontalAdvance(self.text()) + 10 if self.text() else 0)
        return QSize(self.tw + extra, max(self.th, fm.height()) + 2)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x = self.width() - self.tw if self.left else 0
        y = (self.height() - self.th) / 2
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.setPen(Qt.NoPen)
        p.setBrush(mix(COL["line"], COL["accent"], self.pos_))
        p.drawRoundedRect(QRectF(x, y, self.tw, self.th), self.th / 2, self.th / 2)
        d = self.th - 6
        p.setBrush(QColor("#ffffff" if self.underMouse() and self.isEnabled() else COL["text"]))
        p.drawEllipse(QRectF(x + 3 + self.pos_ * (self.tw - self.th), y + 3, d, d))
        if self.text():
            p.setPen(self.color)
            p.setFont(F.small)
            if self.left:
                p.drawText(QRectF(0, 0, self.width() - self.tw - 10, self.height()), Qt.AlignRight | Qt.AlignVCenter, self.text())
            else:
                p.drawText(QRectF(self.tw + 8, 0, self.width(), self.height()), Qt.AlignLeft | Qt.AlignVCenter, self.text())


class Check(HoverButton):
    def __init__(self, text="", box=20, font=None, color=None):
        super().__init__()
        self.setCheckable(True)
        self.setText(text)
        self.box, self.font_, self.color = box, font or F.small, QColor(color or COL["text"])

    def sizeHint(self):
        fm = QFontMetrics(self.font_)
        return QSize(self.box + (fm.horizontalAdvance(self.text()) + 10 if self.text() else 2), max(self.box, fm.height()) + 4)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        b, hot = self.box, self.underMouse()
        r = QRectF(1, (self.height() - b) / 2 + 1, b - 2, b - 2)
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COL["accent_hi"] if hot else COL["accent"]))
            p.drawRoundedRect(r, 5, 5)
            pen = QPen(QColor(COL["ink"]), 2)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawPolyline(QPolygonF([QPointF(r.left() + r.width() * 0.25, r.center().y()),
                                      QPointF(r.left() + r.width() * 0.43, r.bottom() - r.height() * 0.28),
                                      QPointF(r.right() - r.width() * 0.22, r.top() + r.height() * 0.28)]))
        else:
            p.setPen(QPen(QColor(COL["accent_hi"] if hot else COL["faint"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        if self.text():
            p.setPen(self.color)
            p.setFont(self.font_)
            p.drawText(QRectF(b + 8, 0, self.width(), self.height()), Qt.AlignLeft | Qt.AlignVCenter, self.text())


class Swatch(HoverButton):
    def __init__(self, color, selected):
        super().__init__()
        self.color, self.selected = QColor(color), selected
        self.setFixedSize(28, 28)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = QRectF(0.5, 0.5, 27, 27)
        if self.selected or self.underMouse():
            p.setBrush(QColor(COL["text"] if self.selected else COL["faint"]))
            p.drawEllipse(r)
            r = r.adjusted(3, 3, -3, -3)
        p.setBrush(self.color)
        p.drawEllipse(r)


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect, apply):
        x, y, line, sp = rect.x(), rect.y(), 0, self.spacing()
        for item in self.items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line:
                x, y, line = rect.x(), y + line + sp, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line = max(line, hint.height())
        return y + line - rect.y()


class Segmented(QFrame):
    picked = Signal(str)

    def __init__(self, values=(), kind="choice", height=32, button=QPushButton):
        super().__init__()
        self.setProperty("seg", kind)
        self.pad = 3 if kind == "choice" else 0
        self.inner = height - 2 * self.pad
        self.setFixedHeight(height)
        self.lay = hbox(2 if kind == "choice" else 4, (self.pad,) * 4)
        self.setLayout(self.lay)
        self.buttons, self.value, self.button = {}, None, button
        self.set_values(values)

    def set_values(self, values):
        for b in self.buttons.values():
            self.lay.removeWidget(b)
            discard(b)
        self.buttons, self.value = {}, None
        for v in values:
            b = self.button(v)
            b.setCheckable(True)
            b.setFixedHeight(self.inner)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda *_, v=v: self._click(v))
            self.lay.addWidget(b)
            self.buttons[v] = b

    def _click(self, v):
        self.set(v)
        self.picked.emit(v)

    def set(self, v):
        self.value = v if v in self.buttons else None
        for k, b in self.buttons.items():
            b.setChecked(k == v)

    def get(self):
        return self.value or ""


class Combo(QComboBox):
    def __init__(self, values=(), width=None, height=32, editable=False, role=None):
        super().__init__()
        self.setItemDelegate(QStyledItemDelegate(self))
        self.setMaxVisibleItems(18)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        if editable:
            self.setEditable(True)
            self.setInsertPolicy(QComboBox.NoInsert)
        if role:
            self.setProperty("role", role)
        if width:
            self.setFixedWidth(width)
        self.setFixedHeight(height)
        self.set_values(values)

    def wheelEvent(self, e):
        e.ignore()

    def set_values(self, values):
        old = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems(list(values))
        self.setCurrentIndex(self.findText(old))
        self.blockSignals(False)

    def get(self):
        return self.currentText()

    def set(self, text):
        self.blockSignals(True)
        if self.isEditable():
            self.setEditText(text)
        else:
            self.setCurrentIndex(self.findText(text))
        self.blockSignals(False)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(COL["muted"] if self.isEnabled() else COL["faint"]), 1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        x, y = self.width() - 15, self.height() / 2
        p.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))


class RepeatButton(QPushButton):
    def __init__(self, text, step):
        super().__init__(text)
        self.step = step
        self.setProperty("kind", "step")
        self.setFixedSize(30, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._repeat)
        self.pressed.connect(self._press)
        self.released.connect(self.timer.stop)

    def _press(self):
        self.step()
        self.timer.start(380)

    def _repeat(self):
        if not self.isDown() or not self.isEnabled():
            self.timer.stop()
            return
        self.timer.setInterval(55)
        self.step()


class Slider(QSlider):
    def __init__(self, nudge, steps, width):
        super().__init__(Qt.Horizontal)
        self.nudge, self.acc, self.w = nudge, 0, width
        self.setRange(0, steps)
        self.setMaximumWidth(width)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return QSize(self.w, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(110, super().minimumSizeHint().height())

    def wheelEvent(self, e):
        # A plain wheel scrolls the page; only Ctrl+wheel changes the value.
        if not e.modifiers() & Qt.ControlModifier:
            e.ignore()
            return
        self.acc = wheel_steps(self.acc + e.angleDelta().y(), self.nudge)
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
            if not handle.contains(e.position().toPoint()):
                span = self.width() - handle.width()
                pos = int(e.position().x() - handle.width() / 2)
                self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), pos, span))
        super().mousePressEvent(e)


class ValueEdit(QLineEdit):
    def __init__(self, width, on_commit, nudge=None, center=True):
        super().__init__()
        self.on_commit, self.nudge, self.acc = on_commit, nudge, 0
        self.setProperty("role", "mono")
        self.setFixedSize(width, 30)
        if center:
            self.setAlignment(Qt.AlignCenter)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.editingFinished.connect(lambda: self.on_commit())

    def event(self, e):
        # Ctrl+Z goes to the app's undo history, not to the text of this field.
        if e.type() == QEvent.ShortcutOverride and e.matches(QKeySequence.Undo):
            e.ignore()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if self.nudge and e.key() in (Qt.Key_Up, Qt.Key_Down):
            self.nudge(1 if e.key() == Qt.Key_Up else -1)
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        if not self.nudge:
            e.ignore()
            return
        self.acc = wheel_steps(self.acc + e.angleDelta().y(), self.nudge)
        e.accept()


class SearchEdit(QLineEdit):
    def __init__(self, placeholder, width, on_escape):
        super().__init__()
        self.on_escape = on_escape
        self.setPlaceholderText(placeholder)
        self.setFixedSize(width, 34)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.on_escape()
            return
        super().keyPressEvent(e)


class Control(QWidget):
    def __init__(self, row, app, spec):
        super().__init__(row)
        self.row, self.app, self.spec = row, app, spec
        self.lay = hbox(0)
        self.setLayout(self.lay)

    @property
    def value(self):
        return self.app.value(self.spec.sec, self.spec.key)

    def emit(self, value, key=None):
        self.app.set_value(self.spec.sec, key or self.spec.key, value, self.row)

    def load(self):
        pass


class ToggleControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.switch = Switch("", 46, 24, left=True, tone="muted")
        self.switch.clicked.connect(self._flip)
        self.lay.addWidget(self.switch)
        self.load()

    def _flip(self, *_):
        v = "1" if self.switch.isChecked() else "0"
        self.emit(v)
        self.switch.setText(self.spec.choices.get(v, ""))

    def load(self):
        v = self.value
        self.switch.setChecked(v == "1")
        self.switch.setText(self.spec.choices.get(v, v or ""))


class ChoiceControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.options = dict(self.choices())
        self._ensure(self.value, rebuild=False)
        labels = list(self.options.values())
        if len(labels) <= 6 and sum(map(len, labels)) <= 42:
            self.widget = Segmented(labels, "choice", 32)
            self.widget.picked.connect(self._pick)
        else:
            self.widget = Combo(labels, 250, 32)
            self.widget.activated.connect(lambda i: self._pick(self.widget.itemText(i)))
        self.lay.addWidget(self.widget)
        self.load()

    def choices(self):
        return self.spec.choices or {}

    def _ensure(self, v, rebuild=True):
        if v is not None and v not in self.options:
            self.options[v] = f"{v} (custom)"
            if rebuild:
                self.widget.set_values(list(self.options.values()))

    def _pick(self, text):
        self.emit(next(v for v, l in self.options.items() if l == text))

    def load(self):
        v = self.value
        self._ensure(v)
        self.widget.set(self.options.get(v, ""))


class DataCenterControl(ChoiceControl):
    def choices(self):
        return {v: region_label(v) for v in self.app.datacenters}

    def load(self):
        self.options = dict(self.choices())
        self._ensure(self.value, rebuild=False)
        self.widget.set_values(list(self.options.values()))
        self.widget.set(self.options.get(self.value, ""))


class ComboControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.box = Combo(list(spec.choices or {}), 220, 32, editable=True)
        self.box.textActivated.connect(lambda _: self.commit())
        self.box.lineEdit().editingFinished.connect(self.commit)
        self.lay.addWidget(self.box)
        self.load()

    def commit(self):
        v = self.box.currentText().strip()
        if v and v != self.value:
            self.emit(v)

    def load(self):
        self.box.set(self.value or "")


class RangeControl(Control):
    slider_width = 220

    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        v = num(self.value)
        self.lo, self.hi = self.bounds(None if v is None else spec.offset + spec.scale * v)
        orig = app.orig.get((spec.sec, spec.key)) or self.value or ""
        self.float_style = "." in orig or (spec.digits > 0 and not orig)
        self.shown = ""
        self.build()
        self.load()

    def bounds(self, v):
        lo, hi = self.spec.lo, self.spec.hi
        if v is not None:
            lo, hi = min(lo, v), max(hi, v)
        return lo, hi

    def build(self):
        self.minus = RepeatButton("−", lambda: self.nudge(-1))
        self.lay.addWidget(self.minus)
        self.steps = max(1, round((self.hi - self.lo) / self.spec.step))
        self.slider = Slider(self.nudge, self.steps, self.slider_width)
        self.slider.valueChanged.connect(lambda i: self._slide(self.lo + (self.hi - self.lo) * i / self.steps))
        self.lay.addSpacing(6)
        self.lay.addWidget(self.slider)
        self.lay.addSpacing(6)
        self.plus = RepeatButton("+", lambda: self.nudge(1))
        self.lay.addWidget(self.plus)
        self.build_entry()
        self.build_unit()

    def build_entry(self):
        self.entry = ValueEdit(86 if self.spec.digits > 2 else 70, self.commit, self.nudge)
        self.lay.addSpacing(8)
        self.lay.addWidget(self.entry)

    def build_unit(self):
        unit = self.spec.unit.strip()
        self.unit = label(unit, "small", "muted", width=26 if unit else None)
        self.lay.addSpacing(4)
        self.lay.addWidget(self.unit)

    def clamp(self, v):
        return min(self.hi, max(self.lo, v))

    def snap(self, v):
        step = self.spec.step
        return round(self.lo + round((v - self.lo) / step) * step, 6)

    def to_file(self, v):
        v = (v - self.spec.offset) / self.spec.scale
        return f"{v:.6f}" if self.float_style else str(int(round(v)))

    def show_value(self, v):
        self.shown = f"{v:.{self.spec.digits}f}"
        self.entry.setText(self.shown)

    def current(self):
        v = num(self.value)
        return self.lo if v is None else self.spec.offset + self.spec.scale * v

    def _slide(self, v):
        v = self.clamp(self.snap(v))
        self.show_value(v)
        self.emit(self.to_file(v))

    def nudge(self, direction):
        v = self.clamp(self.snap(self.current() + direction * self.spec.step))
        self.set_shown(v)
        self.emit(self.to_file(v))

    def commit(self):
        text = self.entry.text().strip()
        if text == self.shown:
            return
        v = num(re.sub(r"[^\d.\-]", "", text))
        if v is None:
            self.load()
            return
        v = self.clamp(self.snap(v) if self.spec.scale != 1 else v)
        self.set_shown(v)
        self.emit(self.to_file(v))

    def set_slider(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(round((v - self.lo) / (self.hi - self.lo) * self.steps) if self.hi > self.lo else 0)
        self.slider.blockSignals(False)

    def set_shown(self, v):
        if hasattr(self, "slider"):
            self.set_slider(v)
        self.show_value(v)

    def load(self):
        self.set_shown(self.current())


class NumberControl(RangeControl):
    def bounds(self, v):
        return -1e12, 1e12

    def build(self):
        self.minus = RepeatButton("−", lambda: self.nudge(-1))
        self.lay.addWidget(self.minus)
        self.build_entry()
        self.plus = RepeatButton("+", lambda: self.nudge(1))
        self.lay.addSpacing(6)
        self.lay.addWidget(self.plus)
        self.build_unit()

    def snap(self, v):
        return round(v, 6)


class LimitControl(RangeControl):
    def bounds(self, v):
        lo, hi = self.spec.lo, self.spec.hi
        if v is not None and v >= lo:
            hi = max(hi, v)
        return lo, hi

    def build(self):
        self.switch = Switch("", 46, 24)
        self.switch.clicked.connect(self._flip)
        self.lay.addWidget(self.switch)
        self.lay.addSpacing(10)
        self.last = None
        super().build()

    def fallback(self):
        # Turning the cap on starts at the refresh rate when the file has one.
        hz = num(self.app.value("DISPLAY_SETTINGS", "RefreshRate"))
        return float(self.clamp(round(hz))) if hz and hz >= self.lo else 144.0

    def emit(self, value, key=None):
        v = num(value)
        if v is not None and v >= self.lo:
            self.last = v
        super().emit(value, key)

    def _flip(self, *_):
        self.emit(self.to_file(self.last or self.fallback()) if self.switch.isChecked() else "0")
        self.load()

    def current(self):
        v = num(self.value) or 0
        return v if v >= self.lo else self.last or self.fallback()

    def load(self):
        v = num(self.value) or 0
        active = v >= self.lo
        if active:
            self.last = v
        self.switch.setChecked(active)
        for w in (self.slider, self.minus, self.plus, self.entry):
            w.setEnabled(active)
        self.set_slider(self.current())
        if active:
            self.show_value(v)
        else:
            self.entry.setText("Off")
            self.shown = "Off"


class TextControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.entry = ValueEdit(260, self.commit, center=False)
        self.lay.addWidget(self.entry)
        self.load()

    def commit(self):
        if self.entry.text() != self.value:
            self.emit(self.entry.text())

    def load(self):
        self.entry.setText(self.value or "")


class InfoControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.info = label("", "mono", "muted")
        self.lay.addWidget(self.info)
        self.load()

    def load(self):
        self.info.setText(self.value or "")


class ResolutionControl(Control):
    CUSTOM = "Custom..."

    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.menu = Combo([], 190, 32)
        self.menu.activated.connect(lambda i: self._pick(self.menu.itemText(i)))
        self.aspect = label("", "small", "muted", width=56)
        self.aspect.setAlignment(Qt.AlignCenter)
        self.lay.addWidget(self.menu)
        self.lay.addSpacing(10)
        self.lay.addWidget(self.aspect)
        self.load()

    def dims(self):
        return self.app.value(self.spec.sec, "ResolutionWidth") or "0", self.app.value(self.spec.sec, "ResolutionHeight") or "0"

    @staticmethod
    def res_label(w, h):
        return "Auto" if (w, h) == ("0", "0") else f"{w} × {h}"

    def load(self):
        w, h = self.dims()
        modes = sorted({(mw, mh) for mw, mh, _ in self.app.modes}, key=lambda m: (m[0] * m[1], m[0]), reverse=True)
        opts = ["Auto"] + [f"{mw} × {mh}" for mw, mh in modes]
        cur = self.res_label(w, h)
        if cur not in opts:
            opts.insert(1, cur)
        self.menu.set_values(opts + [self.CUSTOM])
        self.menu.set(cur)
        self.aspect.setText(aspect_name(int(num(w) or 0), int(num(h) or 0)))

    def _pick(self, text):
        if text == "Auto":
            w, h = "0", "0"
        else:
            if text == self.CUSTOM:
                text = ask_text(self, "Custom resolution", "Width x height, for example 1728x1080") or ""
            m = re.match(r"\s*(\d{3,5})\s*[x×*, ]\s*(\d{3,5})\s*$", text)
            if not m:
                self.load()
                return
            w, h = m.groups()
        with self.app.group(self.spec.label):
            self.emit(w, "ResolutionWidth")
            self.emit(h, "ResolutionHeight")
        self.load()


class RefreshControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.menu = Combo([], 190, 32)
        self.menu.activated.connect(lambda i: self._pick(self.menu.itemText(i)))
        self.lay.addWidget(self.menu)
        self.load()

    def load(self):
        self.map = {"Auto": 0.0}
        for hz in sorted({f for _, _, f in self.app.modes if f > 1}, reverse=True):
            self.map[f"{hz} Hz"] = float(hz)
        v = num(self.value)
        cur = "Auto" if not v else next((k for k, x in self.map.items() if abs(x - v) < 1e-6), f"{v:g} Hz")
        self.map.setdefault(cur, v or 0.0)
        self.menu.set_values(list(self.map))
        self.menu.set(cur)

    def _pick(self, text):
        v = self.map[text]
        float_style = "." in (self.app.orig.get((self.spec.sec, self.spec.key)) or ".")
        self.emit(f"{v:.6f}" if float_style else str(int(v)))


CONTROLS = {"toggle": ToggleControl, "choice": ChoiceControl, "range": RangeControl, "limit": LimitControl,
            "number": NumberControl, "text": TextControl, "info": InfoControl, "resolution": ResolutionControl,
            "refresh": RefreshControl, "datacenter": DataCenterControl, "combo": ComboControl}


def make_control(row, app, spec):
    kind = spec.kind
    v = app.value(spec.sec, spec.key)
    if kind == "toggle" and v not in ("0", "1"):
        kind = "number" if num(v) is not None else "text"
    if kind in ("range", "limit", "number") and num(v) is None:
        kind = "text"
    return CONTROLS[kind](row, app, spec)


class SettingRow(Card):
    def __init__(self, app, spec, token):
        super().__init__(COL["card"], 8, COL["card_hi"])
        self.app, self.spec, self.token, self.dirty = app, spec, token, False
        self.keys = [(spec.sec, spec.key)] + ([(spec.sec, "ResolutionHeight")] if spec.kind == "resolution" else [])
        lay = hbox(12, (18, 7, 8, 7))
        self.setLayout(lay)
        text = vbox(2)
        title = hbox(8)
        title.addWidget(label(spec.label))
        key = label(spec.key, "tiny_mono", "faint")
        inline = len(spec.label) + len(spec.key) <= 44
        if inline:
            title.addWidget(key)
        if spec.ini_only:
            title.addWidget(Pill("NOT IN GAME MENU", COL["accent_dim"], COL["accent"]))
        title.addStretch()
        text.addLayout(title)
        if not inline:
            text.addWidget(key)
        help_text = spec.help or ("" if spec.choices else app.hint(spec))
        if help_text:
            # The help wraps at 330 px. A row layout around it makes the height follow the capped width.
            h = label(help_text, "small", "muted", wrap=True)
            h.setMaximumWidth(330)
            line = hbox(0)
            line.addWidget(h, 1)
            line.addStretch()
            text.addLayout(line)
        lay.addLayout(text, 1)
        self.control = make_control(self, app, spec)
        lay.addWidget(self.control, 0, Qt.AlignVCenter)
        self.reset = button("↺", lambda: app.reset_keys(self.keys), "reset", height=30)
        self.reset.setFixedWidth(30)
        policy = self.reset.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.reset.setSizePolicy(policy)
        lay.addWidget(self.reset)
        self.on_menu = lambda pos: app.row_menu(self, pos)
        for k in self.keys:
            app.rows[k].append(self)
        self.mark()

    def paint_extra(self, p):
        if self.dirty:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COL["accent"]))
            p.drawRoundedRect(QRectF(4, 8, 4, self.height() - 16), 2, 2)

    def load(self):
        self.control.load()

    def mark(self):
        dirty = any(k in self.app.pending for k in self.keys)
        if dirty != self.dirty or self.reset.isHidden() == dirty:
            self.dirty = dirty
            self.reset.setVisible(dirty)
            self.update()
        if dirty:
            saved = [self.app.orig.get(k) for k in self.keys]
            text = ResolutionControl.res_label(*(v or "0" for v in saved)) if len(saved) == 2 else self.spec.show(saved[0])
            self.reset.setToolTip(f"Reset to saved value: {text}")


class NavItem(QPushButton):
    def __init__(self, name, command):
        super().__init__(name)
        self.setProperty("kind", "nav")
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self.count = 0
        self.clicked.connect(lambda *_: command())

    def set_active(self, on):
        restyle(self, active="true" if on else "false")

    def set_count(self, n):
        if n != self.count:
            self.count = n
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count:
            paint_count(self, self.count)


class TabButton(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.count = 0

    def set_count(self, n):
        if n != self.count:
            self.count = n
            restyle(self, badge="true" if n else "false")
            self.updateGeometry()
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count:
            paint_count(self, self.count, 8)


class ProfileCard(Card):
    def __init__(self, app, profile, selected):
        meta = app.store.meta(profile.uuid)
        super().__init__(COL["card_hi"] if selected else COL["card"], 10, COL["card_hi"],
                         meta["color"] if selected else None, 2 if selected else 0)
        self.color = meta["color"]
        lay = vbox(3, (24, 8, 10, 9))
        self.setLayout(lay)
        top = hbox(6)
        top.addWidget(label(app.store.name(profile.uuid), "bold"))
        if selected and app.pending:
            dot = Dot(8)
            dot.set_color(COL["accent"])
            dot.setToolTip("Unsaved changes")
            top.addWidget(dot, 0, Qt.AlignVCenter)
        top.addStretch()
        if profile.locked:
            top.addWidget(Pill("READ-ONLY", COL["bad_dim"], COL["bad"]))
        lay.addLayout(top)
        if meta["tags"]:
            tags = hbox(4)
            for t in meta["tags"][:3]:
                tags.addWidget(Pill(t, COL["line"], COL["text"]))
            if len(meta["tags"]) > 3:
                tags.addWidget(label(f"+{len(meta['tags']) - 3}", "small", "muted"))
            tags.addStretch()
            lay.addLayout(tags)
        when = datetime.fromtimestamp(profile.mtime) if profile.mtime else None
        lay.addWidget(label(f"{profile.uuid[:8]}" + (f"  ·  {nice_date(when)}" if when else ""), "tiny_mono", "faint"))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.ClickFocus)
        self.on_click = lambda: app.select_profile(profile)
        self.on_menu = lambda pos: app.profile_menu(profile, pos)

    def paint_extra(self, p):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(QRectF(10, 10, 4, self.height() - 20), 2, 2)


class BenchItem(Card):
    def __init__(self, bench, command):
        super().__init__(COL["card"], 10, COL["card_hi"])
        lay = vbox(2, (12, 8, 12, 8))
        self.setLayout(lay)
        top = hbox(6)
        top.addWidget(label(nice_date(bench.when), "bold"))
        top.addStretch()
        fps = bench.stats.get("Fps")
        top.addWidget(label(f"{fps:.0f} fps" if fps else "", "bold", "accent"))
        lay.addLayout(top)
        lo, hi = bench.stats.get("LowestFps"), bench.stats.get("HighestFps")
        lay.addWidget(label(f"low {lo:.0f}  ·  high {hi:.0f}" if lo is not None and hi is not None else "", "small", "muted"))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.ClickFocus)
        self.on_click = command

    def set_selected(self, on):
        self.set_border(COL["accent"] if on else None, 2 if on else 0)


class Toast(QFrame):
    def __init__(self, parent, text, bg, fg):
        super().__init__(parent)
        self.bg, self.fg = QColor(bg), QColor(fg)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = hbox(0, (16, 9, 16, 9))
        self.setLayout(lay)
        lab = label(text, wrap=True)
        lab.setStyleSheet(f"color: {fg};")
        lab.setFixedWidth(min(460, QFontMetrics(F.body).horizontalAdvance(text) + 6))
        lay.addWidget(lab)
        self.adjustSize()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(self.fg, 1))
        p.setBrush(self.bg)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)


class LineChart(QWidget):
    def __init__(self, height=300):
        super().__init__()
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.series, self.duration, self.max, self.mx, self.base = [], 0.0, {}, None, None
        self.empty = ""

    def plot(self, series, duration, empty="No samples in this report"):
        self.series = [s for s in series if len(s["y"])]
        self.duration = duration or 0.0
        self.empty = empty
        self.base = None
        self.update()

    def frame(self):
        right = any(s["axis"] == "r" for s in self.series)
        return 48, 30, self.width() - (48 if right else 18), self.height() - 28

    @staticmethod
    def nice(v):
        if not v or v <= 0:
            return 1.0
        e = 10 ** math.floor(math.log10(v))
        return next(m * e for m in (1, 2, 2.5, 5, 10) if v <= m * e)

    def resizeEvent(self, e):
        self.base = None

    def mouseMoveEvent(self, e):
        self.mx = e.position().x()
        self.update()

    def leaveEvent(self, e):
        self.mx = None
        self.update()

    def paintEvent(self, e):
        # The plot is cached in a pixmap so the hover crosshair repaints cheaply.
        if self.base is None:
            dpr = self.devicePixelRatioF()
            self.base = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
            self.base.setDevicePixelRatio(dpr)
            self.base.fill(QColor(COL["card"]))
            bp = QPainter(self.base)
            self.draw(bp)
            bp.end()
        p = QPainter(self)
        p.drawPixmap(0, 0, self.base)
        if self.mx is not None:
            self.draw_hover(p, self.mx)

    def draw(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(F.chart)
        self.max = {}
        x0, y0, x1, y1 = self.frame()
        if x1 - x0 < 60:
            return
        if not self.series:
            text_at(p, (x0 + x1) / 2, (y0 + y1) / 2, self.empty, COL["muted"])
            return
        for axis in ("l", "r"):
            ys = [s["y"].max() for s in self.series if s["axis"] == axis]
            self.max[axis] = self.nice(max(ys) * 1.05) if ys else None
        p.setPen(QPen(QColor(COL["line"]), 1))
        for i in range(5):
            y = y1 - (y1 - y0) * i / 4
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
        for i in range(5):
            y = y1 - (y1 - y0) * i / 4
            if self.max["l"]:
                text_at(p, x0 - 8, y, f"{self.max['l'] * i / 4:g}", COL["muted"], "e")
            if self.max["r"]:
                text_at(p, x1 + 8, y, f"{self.max['r'] * i / 4:g}", COL["muted"], "w")
        for i in range(6):
            text_at(p, x0 + (x1 - x0) * i / 5, y1 + 14, f"{self.duration * i / 5:.0f} s", COL["muted"])
        lx, fm = x0, p.fontMetrics()
        for s in self.series:
            n = len(s["y"])
            if n > 1:
                xs = np.linspace(x0, x1, n)
                ys = y1 - s["y"] / self.max[s["axis"]] * (y1 - y0)
                pen = QPen(QColor(s["color"]), s.get("width", 2))
                pen.setJoinStyle(Qt.RoundJoin)
                if s.get("dash"):
                    pen.setDashPattern([d / pen.widthF() for d in s["dash"]])
                p.setPen(pen)
                p.drawPolyline(QPolygonF([QPointF(a, b) for a, b in zip(xs.tolist(), ys.tolist())]))
            p.fillRect(QRectF(lx, 9, 10, 10), QColor(s["color"]))
            text_at(p, lx + 16, 14, s["label"], COL["text"], "w")
            lx += 16 + fm.horizontalAdvance(s["label"]) + 18

    def draw_hover(self, p, mx):
        if not self.series or not self.max:
            return
        x0, y0, x1, y1 = self.frame()
        if not x0 <= mx <= x1:
            return
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(F.chart)
        frac = (mx - x0) / (x1 - x0)
        pen = QPen(QColor(COL["faint"]), 1)
        pen.setDashPattern([2, 3])
        p.setPen(pen)
        p.drawLine(QPointF(mx, y0), QPointF(mx, y1))
        lines = [f"{frac * self.duration:.1f} s"]
        for s in self.series:
            v = float(s["y"][round(frac * (len(s["y"]) - 1))])
            y = y1 - v / self.max[s["axis"]] * (y1 - y0)
            p.setPen(QPen(QColor(COL["card"]), 2))
            p.setBrush(QColor(s["color"]))
            p.drawEllipse(QPointF(mx, y), 4, 4)
            lines.append(f"{s['label']}: {v:.1f}{s['unit']}")
        fm = p.fontMetrics()
        lh = fm.height()
        w, h = max(fm.horizontalAdvance(t) for t in lines) + 18, lh * len(lines) + 12
        left = mx + 14 if mx < (x0 + x1) / 2 else mx - 14 - w
        p.setPen(QPen(QColor(COL["line"]), 1))
        p.setBrush(QColor(COL["bg"]))
        p.drawRoundedRect(QRectF(left, y0 + 4, w, h), 6, 6)
        p.setPen(QColor(COL["text"]))
        for j, t in enumerate(lines):
            p.drawText(QRectF(left + 9, y0 + 10 + j * lh, w - 18, lh), Qt.AlignLeft | Qt.AlignVCenter, t)


class BarChart(QWidget):
    def __init__(self, on_pick, height=120):
        super().__init__()
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.on_pick, self.items, self.selected, self.hot, self.compare = on_pick, [], None, None, None

    def plot(self, items, selected, compare=None):
        self.items, self.selected, self.compare = items, selected, compare
        self.update()

    def _slot(self, x):
        w = self.width() - 16
        if not self.items or w <= 0:
            return None
        i = int((x - 8) / (w / len(self.items)))
        return i if 0 <= i < len(self.items) else None

    def paintEvent(self, e):
        w, h = self.width() - 16, self.height()
        if not self.items or w <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(F.chart_small)
        top = max(v or 0 for _, v in self.items) or 1
        slot = w / len(self.items)
        p.setPen(Qt.NoPen)
        for i, (_, v) in enumerate(self.items):
            bh = (h - 34) * (v or 0) / top
            color = (COL["accent"] if i == self.selected else COL["info"] if i == self.compare
                     else COL["faint"] if i == self.hot else COL["line"])
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(8 + i * slot + slot * 0.15, h - 18 - bh, slot * 0.7, bh), 2, 2)
        text, v = self.items[self.hot if self.hot is not None else (self.selected or 0)]
        text_at(p, 8, 10, f"{text}  ·  {v:.0f} fps" if v else text, COL["text"], "w")
        text_at(p, 8, h - 8, self.items[0][0].split(",")[0], COL["faint"], "w")
        text_at(p, w + 8, h - 8, self.items[-1][0].split(",")[0], COL["faint"], "e")

    def set_hot(self, i):
        if i != self.hot:
            self.hot = i
            self.update()

    def mouseMoveEvent(self, e):
        self.set_hot(self._slot(e.position().x()))

    def leaveEvent(self, e):
        self.set_hot(None)

    def mousePressEvent(self, e):
        i = self._slot(e.position().x())
        if i is not None and e.button() == Qt.LeftButton:
            self.on_pick(i)


class Tabs(QWidget):
    def __init__(self, names, command):
        super().__init__()
        self.command, self.current = command, names[0]
        v = vbox(8)
        self.setLayout(v)
        self.bar = Segmented(names, "tabs", 34, TabButton)
        self.bar.picked.connect(self.set)
        v.addWidget(self.bar, 0, Qt.AlignLeft)
        body = Card(COL["panel"], 12)
        bl = vbox(0, (12, 12, 12, 12))
        body.setLayout(bl)
        self.stack = QStackedWidget()
        bl.addWidget(self.stack)
        v.addWidget(body, 1)
        self.pages = {}
        for n in names:
            self.pages[n] = QWidget()
            self.stack.addWidget(self.pages[n])
        self.bar.set(self.current)

    def tab(self, name):
        return self.pages[name]

    def get(self):
        return self.current

    def set(self, name):
        self.current = name
        self.bar.set(name)
        if self.command:
            self.command()
        self.stack.setCurrentWidget(self.pages[name])

    def step(self, d):
        names = list(self.pages)
        self.set(names[(names.index(self.current) + d) % len(names)])

    def set_count(self, name, n):
        self.bar.buttons[name].set_count(n)


class App(QMainWindow):
    TABS = ("Settings", "Profile", "Transfer", "Backups", "Benchmarks")
    game_state = Signal(bool)

    def __init__(self, root=None):
        super().__init__()
        self.store = Store(APP_DIR)
        cfg = self.store.data["settings"]
        last_tab = cfg.get("last_tab")
        self.root_dir = Path(root or cfg.get("root") or DEFAULT_ROOT)
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setMinimumSize(1150, 720)
        self.restore_geometry(cfg.get("geometry"))
        self.profiles, self.current, self.ini, self.disk_bytes = [], None, None, None
        self.orig, self.pending, self.history, self._group = {}, {}, [], None
        self.rows = defaultdict(list)
        self.cat_layout, self.cat_frames, self.nav_items = {}, {}, {}
        self.cat, self.search_frame = None, None
        self.hints, self.datacenters, self._auto, self.watchers = {}, [], {}, []
        self.modes = display_modes()
        self.running, self.file_mtime, self._last_poll, self._polling = False, None, 0.0, False
        self._timers, self._toast, self.action_bars = {}, None, {}
        self.review_dlg, self.s_info, self.p_info, self._sidebar_dirty = None, None, {}, False
        self.stale = set()
        self.x_extra, self.x_sel, self.x_rows, self.x_vars, self.x_groups = {}, set(), {}, {}, []
        self.x_summary, self._x_src_last, self._x_tgt_last, self.x_targets, self.x_same = "", "", "", [], set()
        self.benches, self.bench_sel, self.b_items, self.b_cmp_i = None, None, [], None
        self._bench_dir, self._bench_names, self._bench_wait = None, {}, {}
        self.game_state.connect(self._on_game)
        self.build()
        self.rescan(select=cfg.get("last_profile"))
        self.tabs.command = self.on_tab
        self.tabs.set(last_tab if last_tab in self.TABS else "Settings")
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(1000)
        QTimer.singleShot(400, self.poll)
        self.watch_bench()
        self.bench_timer = QTimer(self)
        self.bench_timer.timeout.connect(self.watch_bench)
        self.bench_timer.start(2000)
        self.shortcuts = []
        for keys, fn in (("Ctrl+S", self.save), ("Ctrl+Z", self.undo), ("Ctrl+F", self.focus_search), ("F5", self.rescan),
                         ("Ctrl+Tab", lambda: self.tabs.step(1)), ("Ctrl+Shift+Tab", lambda: self.tabs.step(-1)),
                         ("Ctrl+PgDown", lambda: self.tabs.step(1)), ("Ctrl+PgUp", lambda: self.tabs.step(-1))):
            sc = QShortcut(QKeySequence(keys), self)
            sc.activated.connect(lambda fn=fn: fn())
            self.shortcuts.append(sc)

    def restore_geometry(self, geo):
        m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", geo or "")
        if m:
            w, h, x, y = map(int, m.groups())
            self.resize(w, h)
            if QGuiApplication.screenAt(QPoint(x + 60, y + 30)):
                self.move(x, y)
        elif not (geo and self.restoreGeometry(QByteArray.fromBase64(geo.encode()))):
            self.resize(1380, 880)

    def build(self):
        central = QWidget()
        central.setFocusPolicy(Qt.ClickFocus)
        self.setCentralWidget(central)
        root = hbox(0)
        central.setLayout(root)

        side = Card(COL["panel"], 0)
        side.setFixedWidth(290)
        root.addWidget(side)
        sv = vbox(0)
        side.setLayout(sv)
        sv.addWidget(label("R6 Siege", "h1", margins=(18, 18, 18, 0)))
        sv.addWidget(label("Settings Manager", tone="muted", margins=(18, 0, 18, 10)))
        self.p_filter = entry("Filter by label, tag or ID")
        self.p_filter.textEdited.connect(lambda _: self.debounce("filter", 150, self.render_sidebar))
        line = hbox(0, (14, 6, 14, 8))
        line.addWidget(self.p_filter)
        sv.addLayout(line)
        self.plist, self.plist_lay = scroll_area((10, 0, 8, 4), 8)
        sv.addWidget(self.plist, 1)
        foot = vbox(8, (14, 12, 14, 12))
        self.p_root = label("", "tiny_mono", "faint", wrap=True)
        foot.addWidget(self.p_root)
        line = hbox(6)
        line.addWidget(button("Rescan", self.rescan, width=80))
        line.addWidget(button("Change folder", self.change_root, width=110))
        line.addWidget(button("Open", lambda: self.open_path(self.root_dir), width=56))
        line.addStretch()
        foot.addLayout(line)
        sv.addLayout(foot)

        main = vbox(0, (14, 16, 14, 14))
        root.addLayout(main, 1)
        header = hbox(0, (6, 0, 6, 0))
        self.h_dot = Dot(14)
        header.addWidget(self.h_dot)
        header.addSpacing(10)
        self.h_title = label("No profile", "h1")
        header.addWidget(self.h_title)
        header.addSpacing(12)
        self.h_uuid = button("", self.copy_uuid, "uuid", height=26, role="tiny_mono")
        self.h_uuid.setToolTip("Copy profile ID")
        header.addWidget(self.h_uuid)
        header.addStretch()
        self.h_game = Pill("", font=F.small, height=28, radius=8, pad=12)
        header.addWidget(self.h_game)
        header.addSpacing(16)
        self.h_lock = Switch("Read-only", 42, 22)
        self.h_lock.setToolTip("Siege cannot change this file, so changes made in the game menu are not kept. "
                               "This app can still save it.")
        self.h_lock.clicked.connect(self.toggle_lock)
        header.addWidget(self.h_lock)
        main.addLayout(header)
        main.addSpacing(10)

        self.tabs = Tabs(self.TABS, None)
        main.addWidget(self.tabs, 1)
        self.t = {name: self.tabs.tab(name) for name in self.TABS}
        self.build_settings_tab()
        self.build_transfer_tab()
        self.build_bench_tab()
        self.set_running(False, force=True)
        central.setFocus()

    # helpers

    def debounce(self, name, ms, fn):
        if name not in self._timers:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._timers[name][1]())
            self._timers[name] = [timer, fn]
        self._timers[name][1] = fn
        self._timers[name][0].start(ms)

    def toast(self, text, kind="ok"):
        if self._toast is not None:
            self._toast.hide()
            self._toast.deleteLater()
        colors = {"ok": (COL["ok_dim"], COL["ok"]), "bad": (COL["bad_dim"], COL["bad"]), "info": (COL["card_hi"], COL["text"])}
        t = self._toast = Toast(self.centralWidget(), text, *colors[kind])
        self.place_toast()
        t.show()
        t.raise_()
        QTimer.singleShot(min(8000, max(3000, 2000 + 45 * len(text))), lambda: self._drop_toast(t))

    def _drop_toast(self, t):
        if t is self._toast:
            t.hide()
            t.deleteLater()
            self._toast = None

    def place_toast(self):
        if self._toast is not None:
            # A tab with an action bar at the bottom keeps the toast above it.
            c, t = self.centralWidget(), self._toast
            bar = self.action_bars.get(self.tabs.get())
            bottom = bar.mapTo(c, QPoint(0, 0)).y() - 10 if bar is not None and bar.isVisible() else c.height() - 24
            t.move(c.width() - t.width() - 24, bottom - t.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.place_toast()

    def open_path(self, path):
        try:
            os.startfile(path)
        except (OSError, AttributeError) as e:
            show_msg(self, "error", f"Could not open {path}.\n\n{e}")

    def card(self, lay, title=None, radius=12, margins=(16, 14, 16, 14), spacing=6, grid=False):
        if title:
            lay.addWidget(label(title.upper(), "caps", "faint", margins=(10, 16, 10, 6)))
        c = Card(COL["card"], radius)
        inner = QGridLayout() if grid else vbox(spacing)
        inner.setContentsMargins(*margins)
        c.setLayout(inner)
        lay.addWidget(c)
        return c, inner

    def page_content(self, name, widget=None):
        page = self.t[name]
        lay = page.layout()
        if lay is None:
            lay = vbox(0)
            page.setLayout(lay)
        clear_layout(lay)
        if widget is not None:
            lay.addWidget(widget)

    def spec(self, sec, key):
        s = SPECS.get((sec, key)) or self._auto.get((sec, key))
        if s is None:
            s = self._auto[(sec, key)] = auto_spec(sec, key, self.orig.get((sec, key), ""))
        return s

    def hint(self, spec):
        h = self.hints.get((spec.sec, spec.key))
        if h:
            return h
        for (sec, name), text in self.hints.items():
            base = re.sub(r"(XXX|X/Y)$", "", name)
            if sec == spec.sec and base != name and spec.key.startswith(base):
                return text
        return ""

    def value(self, sec, key):
        return self.pending.get((sec, key), self.orig.get((sec, key)))

    def live_rows(self, key):
        rows = [r for r in self.rows.get(key, []) if r.token.alive]
        self.rows[key] = rows
        return rows

    def profile_by_uuid(self, uuid):
        return next((p for p in self.profiles if p.uuid == uuid), None)

    def blocked(self):
        if self.running:
            show_msg(self, "warning", "Close Rainbow Six Siege first.\n\nThe game rewrites its settings file when it exits, "
                                      "so changes made now would be lost.")
        return self.running

    def commit_focus(self):
        w = QApplication.focusWidget()
        if isinstance(w, ValueEdit):
            w.on_commit()

    def menu_action(self, menu, text, fn):
        menu.addAction(text).triggered.connect(lambda *_: fn())

    # profiles

    def rescan(self, select=None):
        keep = select or (self.current.uuid if self.current else None)
        self.profiles = scan_profiles(self.root_dir)
        self.p_root.setText(soft(self.root_dir))
        self.benches = None
        target = self.profile_by_uuid(keep) or (self.profiles[0] if self.profiles else None)
        same = bool(target and self.current and target.uuid == self.current.uuid)
        if same and target.mtime != self.file_mtime:
            self.backup_disk_copy()
        if same and (target.mtime == self.file_mtime or self.pending):
            # The open profile stays loaded, so edits and the undo history are kept.
            self.current = target
            self.stale.add("Transfer")
            if target.mtime != self.file_mtime:
                self.file_mtime = target.mtime
                self.merge_outside_change()
            else:
                self.on_tab()
            self.update_header()
            self.render_sidebar()
        elif target:
            self.load_profile(target)
        else:
            self.current = None
            self.ini, self.disk_bytes = None, None
            self.orig, self.pending, self.history = {}, {}, []
            self.cat_layout = {}
            self.reset_settings_view()
            self.update_header()
            self.render_sidebar()
            self.update_dirty()
        if self.tabs.get() == "Benchmarks":
            self.refresh_bench()

    def change_root(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the Rainbow Six - Siege settings folder",
                                             str(self.root_dir if self.root_dir.exists() else Path.home()))
        if not d or not self.confirm_discard():
            return
        self.root_dir = Path(d)
        self.store.data["settings"]["root"] = str(self.root_dir)
        self.store.save()
        self.current = None
        self.rescan()

    def confirm_discard(self):
        if not self.pending:
            return True
        answer = ask_save(self, f"Save {plural(len(self.changed_rows()), 'unsaved change')} first?")
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def select_profile(self, profile):
        if self.current and profile.uuid == self.current.uuid:
            return
        if self.confirm_discard():
            self.load_profile(profile)

    def load_profile(self, profile):
        try:
            data = profile.path.read_bytes()
        except OSError as e:
            show_msg(self, "error", f"Could not read {profile.path}.\n\n{e}")
            return
        old, self.current = self.current, profile
        if old and old.uuid != profile.uuid:
            self.follow_selection()
        self.use_file(IniFile.from_bytes(data), data)
        self.pending, self.history = {}, []
        self._auto = {}
        self.store.data["settings"]["last_profile"] = profile.uuid
        self.compute_layout()
        self.reset_settings_view()
        self.update_header()
        self.render_sidebar()
        self.update_dirty()
        self.stale = {"Profile", "Transfer", "Backups"}
        self.on_tab()
        self.refresh_file_info()

    def use_file(self, ini, data):
        self.ini, self.orig, self.disk_bytes = ini, ini.values(), data
        self.hints = ini.hints()
        self.datacenters = DATACENTERS + [d for d in ini.datacenters() if d.startswith("gamelift/") and d not in DATACENTERS]
        self.file_mtime = self.current.mtime

    def reload_current(self):
        # load_profile keeps the shown category and search.
        if self.current:
            self.load_profile(self.current)

    def backup_disk_copy(self):
        # Before an outside change replaces it, the version read last goes to Backups unless the newest backup has it.
        if self.current and self.disk_bytes is not None:
            try:
                if self.store.backup_before(self.current.uuid, self.disk_bytes, "outside change"):
                    self.stale.add("Backups")
            except OSError:
                pass

    def merge_outside_change(self):
        # Pending edits stay. Rows without an edit show the new file.
        self.commit_focus()
        try:
            data = self.current.path.read_bytes()
        except OSError:
            self.on_tab()
            return
        old = self.orig
        self.use_file(IniFile.from_bytes(data), data)
        new = self.orig
        # Undo steps for keys the file changed would bring back values the game replaced.
        self.history = [e for e in self.history if all(old.get(k) == new.get(k) for k, _, _ in e.changes)]
        for k in [k for k, v in self.pending.items() if new.get(k) == v]:
            del self.pending[k]
        removed = self.changed_rows([k for k in self.pending if k not in new])
        conflicts = self.changed_rows([k for k in self.pending if k in new and old.get(k) != new.get(k)])
        if set(old) != set(new):
            self.compute_layout()
            self.reset_settings_view()
        else:
            for r in self.all_rows():
                if not any(k in self.pending for k in r.keys):
                    r.load()
                r.mark()
            self.watchers = [w for w in self.watchers if w()]
        self.update_dirty()
        self.refresh_file_info()
        self.stale |= {"Transfer", "Backups"}
        self.on_tab()
        n = len(self.changed_rows())
        text = "Siege changed the settings file." if self.running else "The settings file changed on disk."
        if not n:
            text += " It now matches your edits."
        else:
            text += " Your edit is kept." if n == 1 else f" Your {n} edits are kept."
            names = list(self.row_names(sorted(conflicts, key=lambda k: RANK.get(k, 1e9))).values())
            if len(names) == 1:
                text += f" Your edit replaces its new {names[0]}."
            elif names:
                what = " and ".join(names) if len(names) == 2 else f"values for {len(names)} settings"
                text += f" Your edits replace its new {what}."
            names = list(self.row_names(removed).values())
            if names:
                text += f" Saving adds back {' and '.join(names) if len(names) < 3 else plural(len(names), 'setting')}, " \
                        f"which the file no longer has."
        self.toast(text, "info")

    def render_sidebar(self):
        self._sidebar_dirty = bool(self.pending)
        clear_layout(self.plist_lay)
        q = self.p_filter.text().strip().lower()
        shown = 0
        for p in self.profiles:
            m = self.store.meta(p.uuid)
            if q and q not in " ".join([m["label"], p.uuid, m["notes"], *m["tags"]]).lower():
                continue
            self.plist_lay.addWidget(ProfileCard(self, p, bool(self.current and p.uuid == self.current.uuid)))
            shown += 1
        if not shown:
            text = "No profile matches the filter." if self.profiles else \
                "No profile folders found.\n\nPick the folder that holds the UUID folders with Change folder."
            self.plist_lay.addWidget(label(text, "small", "muted", wrap=True, margins=(8, 12, 8, 12)))
        self.plist_lay.addStretch()

    def update_header(self):
        p = self.current
        if not p:
            self.h_title.setText("No profile")
            self.h_uuid.hide()
            self.h_dot.set_color(COL["faint"])
            self.h_lock.setEnabled(False)
            return
        meta = self.store.meta(p.uuid)
        self.h_title.setText(self.store.name(p.uuid))
        self.h_uuid.setText(f"{p.uuid}  ⧉")
        self.h_uuid.show()
        self.h_dot.set_color(meta["color"])
        self.h_lock.setEnabled(True)
        self.h_lock.setChecked(p.locked)

    def refresh_file_info(self):
        # The Settings nav and the Profile tab show the saved time, size, key count and read-only state.
        p = self.current
        when = datetime.fromtimestamp(p.mtime) if p and p.mtime else None
        if self.s_info is not None:
            self.s_info.setText(f"{plural(len(self.orig), 'key')}\nsaved {nice_date(when)}" if when else "")
        if p and self.p_info:
            try:
                size = p.path.stat().st_size
            except OSError:
                size = 0
            for k, v in (("Saved", nice_date(when) if when else "unknown"), ("Size", f"{size:,} bytes"),
                         ("Keys", str(len(self.orig))), ("Read-only", "yes" if p.locked else "no")):
                self.p_info[k].setText(v)

    def copy_uuid(self):
        if self.current:
            QGuiApplication.clipboard().setText(self.current.uuid)
            self.toast("Profile ID copied.", "info")

    def toggle_lock(self, *_):
        if not self.current:
            return
        want = self.h_lock.isChecked()
        try:
            set_locked(self.current.path, want)
        except OSError as e:
            show_msg(self, "error", f"Could not change the file attribute.\n\n{e}")
            self.update_header()
            return
        self.update_header()
        self.render_sidebar()
        self.refresh_file_info()
        self.toast("The file is read-only. Siege cannot change it, so changes made in the game menu are not kept."
                   if want else "The file is no longer read-only. Siege can save settings again.", "info")

    def profile_menu(self, profile, pos):
        menu = QMenu(self)
        self.menu_action(menu, "Open folder", lambda: self.open_path(profile.folder))
        self.menu_action(menu, "Copy profile ID", lambda: QGuiApplication.clipboard().setText(profile.uuid))
        for site, url in SITES.items():
            self.menu_action(menu, f"Look up on {site}", lambda u=url: webbrowser.open(u.format(uuid=profile.uuid)))
        menu.addSeparator()
        self.menu_action(menu, "Transfer from this profile", lambda: self.start_transfer(("profile", profile.uuid)))
        menu.exec(pos)

    # settings tab

    def build_settings_tab(self):
        v = vbox(0)
        self.t["Settings"].setLayout(v)
        top = hbox(16, (0, 2, 0, 8))
        self.s_search = SearchEdit("Search all settings  (Ctrl+F)", 340, lambda: (self.s_search.clear(), self.run_search()))
        self.s_search.textEdited.connect(lambda _: self.debounce("search", 220, self.run_search))
        top.addWidget(self.s_search)
        hint = label("Right-click a setting to copy it to other profiles. Ctrl+wheel over a slider fine-tunes it.",
                     "small", "faint")
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(hint, 1)
        v.addLayout(top)
        mid = hbox(10)
        nav = QWidget()
        nav.setFixedWidth(200)
        self.nav_lay = vbox(2)
        nav.setLayout(self.nav_lay)
        mid.addWidget(nav)
        self.s_holder = QStackedWidget()
        mid.addWidget(self.s_holder, 1)
        v.addLayout(mid, 1)
        v.addSpacing(10)
        bar = Card(COL["card"], 10)
        bl = hbox(12, (16, 8, 10, 8))
        bar.setLayout(bl)
        self.s_count = label("", tone="muted")
        bl.addWidget(self.s_count)
        bl.addStretch()
        self.s_undo = button("Undo  (Ctrl+Z)", self.undo, "flat", 120, 34)
        self.s_review = button("Review", self.review_changes, width=90, height=34)
        self.s_revert = button("Revert all", self.revert, width=100, height=34)
        self.s_save = button("Save changes  (Ctrl+S)", self.save, "primary", 180, 34)
        for b in (self.s_undo, self.s_review, self.s_revert, self.s_save):
            bl.addWidget(b)
        v.addWidget(bar)
        self.action_bars["Settings"] = bar

    def focus_search(self):
        self.tabs.set("Settings")
        self.s_search.setFocus()
        self.s_search.selectAll()

    def compute_layout(self):
        layout = defaultdict(lambda: defaultdict(list))
        pos = {k: i for i, k in enumerate(self.orig)}
        for (sec, key) in self.orig:
            spec = self.spec(sec, key)
            if spec.kind == "hidden":
                continue
            if spec.kind == "resolution" and (sec, "ResolutionHeight") not in self.orig:
                spec = self._auto.setdefault((sec, key), auto_spec(sec, key, self.orig[(sec, key)]))
            layout[spec.cat][spec.group].append(spec)

        def rank(s):
            return RANK.get((s.sec, s.key), 100000 + pos.get((s.sec, s.key), 0))

        self.cat_layout = {}
        for cat in CATEGORIES:
            if cat in layout:
                groups = sorted(layout[cat].items(), key=lambda g: GROUP_RANK.get((cat, g[0]), 1000 + min(map(rank, g[1]))))
                self.cat_layout[cat] = {g: sorted(specs, key=rank) for g, specs in groups}

    def reset_settings_view(self):
        for f in [*self.cat_frames.values(), self.search_frame]:
            if f is not None:
                f.token.alive = False
        self.cat_frames, self.search_frame = {}, None
        while self.s_holder.count():
            w = self.s_holder.widget(0)
            self.s_holder.removeWidget(w)
            discard(w)
        self.rows = defaultdict(list)
        self.watchers = []
        clear_layout(self.nav_lay)
        self.nav_items, self.s_info = {}, None
        if not self.cat_layout:
            msg = label("No settings loaded.", tone="muted", margins=(0, 40, 0, 0))
            msg.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.s_holder.addWidget(msg)
            return
        for cat in self.cat_layout:
            item = NavItem(cat, lambda c=cat: self.show_category(c))
            self.nav_lay.addWidget(item)
            self.nav_items[cat] = item
        if self.current:
            self.s_info = label("", "small", "faint", margins=(12, 16, 12, 0))
            self.nav_lay.addWidget(self.s_info)
            self.refresh_file_info()
        self.nav_lay.addStretch()
        self.show_category(self.cat if self.cat in self.cat_layout else next(iter(self.cat_layout)))
        if self.s_search.text().strip():
            self.run_search()

    def settings_page(self):
        area, lay = scroll_area((2, 0, 10, 12))
        area.token = SimpleNamespace(alive=True)
        return area, lay

    def kill_page(self, area):
        area.token.alive = False
        self.s_holder.removeWidget(area)
        discard(area)

    def build_rows(self, lay, items, token, tools=None):
        # tools names the groups that show their tool box. None shows all of them.
        for title, specs in items:
            lay.addWidget(label(title.upper(), "caps", "faint", margins=(8, 16, 8, 2)))
            cat, group = specs[0].cat, specs[0].group
            note = GROUP_NOTES.get((cat, group))
            if note:
                lay.addWidget(label(note, "small", "faint", wrap=True, margins=(8, 0, 8, 2)))
            lay.addSpacing(4)
            _, cl = self.card(lay, margins=(6, 6, 6, 6), spacing=2)
            if tools is None or (cat, group) in tools:
                if (cat, group) == ("Mouse", "Hipfire"):
                    self.sens_panel(cl, token)
                elif (cat, group) == ("Mouse", "Aim down sights"):
                    self.scope_panel(cl, "ADSMouseSensitivity")
                elif (cat, group) == ("Controller", "Aim down sights"):
                    self.scope_panel(cl, "ADSGamepadSensitivity")
            for spec in specs:
                cl.addWidget(SettingRow(self, spec, token))
        lay.addStretch()

    def build_category(self, cat):
        area, lay = self.settings_page()
        self.build_rows(lay, self.cat_layout[cat].items(), area.token)
        self.s_holder.addWidget(area)
        return area

    def show_category(self, cat):
        if self.search_frame:
            self.kill_page(self.search_frame)
            self.search_frame = None
            self.s_search.clear()
        self.cat = cat
        for c, item in self.nav_items.items():
            item.set_active(c == cat)
        if cat not in self.cat_frames:
            self.cat_frames[cat] = self.build_category(cat)
        self.s_holder.setCurrentWidget(self.cat_frames[cat])

    def search_fields(self, spec):
        fields = [spec.label, spec.key, spec.help, self.hint(spec), spec.group, spec.cat]
        if spec.kind in ("choice", "combo"):
            fields += list((spec.choices or {}).values())
        elif spec.kind == "datacenter":
            fields += [region_label(v) for v in self.datacenters]
        return fields

    def run_search(self):
        q = self.s_search.text().strip().lower()
        clauses = search_query(q)
        if not clauses:
            if self.search_frame:
                self.kill_page(self.search_frame)
                self.search_frame = None
            if self.cat in self.cat_layout:
                self.show_category(self.cat)
            return
        if self.search_frame:
            self.kill_page(self.search_frame)
        for item in self.nav_items.values():
            item.set_active(False)
        # The sensitivity calculator answers words like dpi and cm/360 that no setting holds.
        calc = ("Mouse", "Hipfire")
        calc_hit = search_hit(search_index(CALCULATOR_TEXT), clauses)
        groups = []
        for cat, cat_groups in self.cat_layout.items():
            for group, specs in cat_groups.items():
                extra = SENS_KEYS if calc_hit and (cat, group) == calc else ()
                hits = [s for s in specs
                        if (s.sec, s.key) in extra or search_hit(search_index(self.search_fields(s)), clauses)]
                if hits:
                    groups.append((f"{cat}  ·  {group}", hits))
        area, lay = self.settings_page()
        if groups:
            self.build_rows(lay, groups, area.token, tools={calc} if calc_hit else ())
        else:
            msg = label(f'No setting matches "{q}".', tone="muted", margins=(0, 40, 0, 0))
            msg.setAlignment(Qt.AlignHCenter)
            lay.addWidget(msg)
            lay.addStretch()
        self.s_holder.addWidget(area)
        self.s_holder.setCurrentWidget(area)
        self.search_frame = area

    def tool_box(self, cl, title):
        box = Card(COL["bg"], 10)
        bl = vbox(6, (14, 10, 14, 12))
        box.setLayout(bl)
        bl.addWidget(label(title.upper(), "caps", "accent"))
        cl.addWidget(box)
        cl.addSpacing(4)
        return bl

    def tool_entry(self, line, text, width=70, value=""):
        line.addWidget(label(text, "small", "muted"))
        e = entry(width=width, height=30, role="mono", center=True, text=value)
        line.addWidget(e)
        return e

    def watch(self, fn):
        self.watchers.append(fn)
        fn()

    def sens_panel(self, cl, token):
        yk, pk, mk = SENS_KEYS
        if not all(k in self.orig for k in (yk, pk, mk)):
            return
        meta, cfg = self.store.meta(self.current.uuid), self.store.data["settings"]
        bl = self.tool_box(cl, "Sensitivity calculator")
        read = label("", "mono")
        bl.addWidget(read)
        line = hbox(6)
        # A profile without its own DPI starts from the last DPI entered on any profile.
        dpi = self.tool_entry(line, "Mouse DPI", 70, f"{meta.get('dpi') or cfg.get('last_dpi') or 800:g}")
        line.addSpacing(18)
        cm = self.tool_entry(line, "Target cm/360", 70)
        line.addWidget(button("Match", lambda: match_cm(), width=70))
        line.addStretch()
        bl.addLayout(line)
        line2 = hbox(6)
        slot = self.tool_entry(line2, "Finer steps: move the horizontal slider to", 60, "50")
        line2.addWidget(button("Keep my feel", lambda: finer(), width=110))
        line2.addStretch()
        bl.addLayout(line2)
        bl.addWidget(label("The multiplier is changed so the effective sensitivity stays the same. cm/360 uses "
                           f"{DEG_PER_COUNT:.6f}° per count at slider 1 and multiplier 0.02, a community-measured value.",
                           "small", "faint", wrap=True))

        def vals():
            return [num(self.value(*k)) or 0 for k in (yk, pk, mk)]

        def dpi_value():
            return num(dpi.text()) or 0

        def dpi_edited():
            d = dpi_value()
            if d and d != meta.get("dpi"):
                meta["dpi"] = cfg["last_dpi"] = d
                self.debounce("dpi", 600, self.store.save)
            refresh()

        def refresh():
            if not token.alive:
                return False
            yaw, pitch, mult = vals()
            deg = DEG_PER_COUNT * yaw * mult / 0.02
            d = dpi_value()
            text = f"Effective   {yaw * mult:.6f} horizontal   {pitch * mult:.6f} vertical"
            if deg and d:
                text += f"\ncm/360      {2.54 * 360 / (deg * d):.2f} cm at {d:g} DPI   ·   {deg:.6f}° per count"
            read.setText(text)
            return True

        def finer():
            target = num(slot.text())
            yaw, pitch, mult = vals()
            if not target or not 1 <= target <= 100 or not yaw or not mult:
                self.toast("Enter a slider value from 1 to 100.", "bad")
                return
            target = int(round(target))
            new_mult = yaw * mult / target
            new_pitch = min(100, max(1, round(pitch * mult / new_mult)))
            with self.group("Keep my feel"):
                self.set_value(*mk, f"{new_mult:.6f}")
                self.set_value(*yk, str(target))
                self.set_value(*pk, str(new_pitch))
            # The vertical slider takes whole steps, so its feel can drift a little.
            drift = new_pitch * new_mult / (pitch * mult) - 1 if pitch * mult else 0
            pct = f"{abs(drift):.0%}" if abs(drift) >= 0.005 else "less than 1%"
            self.toast(f"Slider {target}, multiplier {new_mult:.6f}. Horizontal is unchanged"
                       + (f", vertical is {pct} {'faster' if drift > 0 else 'slower'}." if abs(drift) > 1e-9
                          else ", vertical too."), "info")

        def match_cm():
            target, d = num(cm.text()), dpi_value()
            yaw, _pitch, _mult = vals()
            if not target or not d or not yaw:
                self.toast("Enter your DPI and a target cm/360.", "bad")
                return
            deg = 2.54 * 360 / (target * d)
            with self.group("Match cm/360"):
                self.set_value(*mk, f"{deg / DEG_PER_COUNT / yaw * 0.02:.6f}")
            self.toast(f"Multiplier set for {target:g} cm/360 at {d:g} DPI.", "info")

        dpi.textEdited.connect(lambda _: dpi_edited())
        self.watch(refresh)

    def scope_panel(self, cl, prefix):
        keys = [("INPUT", f"{prefix}{s}") for s in SCOPES if ("INPUT", f"{prefix}{s}") in self.orig]
        if not keys:
            return
        bl = self.tool_box(cl, "All scopes at once")
        line = hbox(8)
        value = self.tool_entry(line, "Value", 60, "50")

        def set_all(v, name):
            v = num(v)
            if v is None:
                return
            with self.group(name):
                for k in keys:
                    self.set_value(*k, str(int(round(min(100, max(1, v))))))

        line.addWidget(button("Set every scope", lambda: set_all(value.text(), "Set every scope"), width=130))
        line.addWidget(button("Copy 1x to all", lambda: set_all(self.value("INPUT", f"{prefix}1x"), "Copy 1x to all"),
                              width=120))
        line.addWidget(button("Match hipfire (50)", lambda: set_all(50, "Match hipfire"), width=140))
        line.addStretch()
        bl.addLayout(line)

    def set_value(self, sec, key, value, src=None, record=True):
        k = (sec, key)
        before = self.value(sec, key)
        if value == before:
            return
        if record:
            self.record(k, before, value)
        if value == self.orig.get(k):
            self.pending.pop(k, None)
        else:
            self.pending[k] = value
        for row in self.live_rows(k):
            if row is not src:
                row.load()
            row.mark()
        self.watchers = [w for w in self.watchers if w()]
        self.update_dirty()

    def record(self, k, before, after):
        # A history entry holds a label and a list of (key, before, after). Only single-key entries have a time.
        if self._group is not None:
            self._group.append((k, before, after))
            return
        now, last = time.monotonic(), self.history[-1] if self.history else None
        if last and last.t and last.changes[0][0] == k and now - last.t < 1.2:
            last.changes[0], last.t = (k, last.changes[0][1], after), now
        else:
            self.history.append(SimpleNamespace(label=self.spec(*k).label, changes=[(k, before, after)], t=now))
            del self.history[:-500]

    @contextmanager
    def group(self, label):
        # Collects the set_value calls inside into one undo step. A nested group joins the outer one.
        if self._group is not None:
            yield
            return
        self._group = []
        try:
            yield
        finally:
            changes, self._group = self._group, None
            if changes:
                self.history.append(SimpleNamespace(label=label, changes=changes, t=None))
                del self.history[:-500]
                self.update_dirty()

    def reset_keys(self, keys):
        with self.group(self.spec(*keys[0]).label):
            for k in keys:
                if k in self.pending:
                    self.set_value(*k, self.orig.get(k))

    def undo(self):
        if not self.history:
            return
        entry = self.history.pop()
        for k, before, _after in reversed(entry.changes):
            self.set_value(*k, before, record=False)
        self.update_dirty()
        self.toast(f"Undid {entry.label}.", "info")

    def revert(self):
        if not self.pending:
            return
        n = len(self.changed_rows())
        with self.group("Revert all"):
            for k in list(self.pending):
                self.set_value(*k, self.orig.get(k))
        self.toast(f"Reverted {plural(n, 'change')}. Press Ctrl+Z to bring {'it' if n == 1 else 'them'} back.", "info")

    def changed_rows(self, keys=None):
        # The hidden height key belongs to the resolution row, so the pair counts once.
        return list(dict.fromkeys((k[0], "ResolutionWidth") if self.spec(*k).kind == "hidden" else k
                                  for k in (self.pending if keys is None else keys)))

    def row_names(self, keys):
        # Rows with the same label get their category in front, as in "Mouse  ·  ADS 1x".
        names = {k: self.spec(*k).label for k in keys}
        twice = {n for n, c in Counter(names.values()).items() if c > 1}
        return {k: f"{self.spec(*k).cat}  ·  {n}" if n in twice else n for k, n in names.items()}

    def all_rows(self):
        return list(dict.fromkeys(r for rows in self.rows.values() for r in rows if r.token.alive))

    def update_dirty(self):
        rows = self.changed_rows()
        n = len(rows)
        text = plural(n, "unsaved change") if n else "No unsaved changes"
        self.s_count.setText(f"{text}. Saving waits until Siege closes." if n and self.running else text)
        restyle(self.s_count, tone="accent" if n else "muted")
        self.s_revert.setEnabled(bool(n))
        self.s_review.setEnabled(bool(n))
        self.s_undo.setEnabled(bool(self.history))
        self.s_save.setEnabled(bool(n) and not self.running)
        self.s_save.setToolTip("Close Siege to save. The game rewrites its settings file when it exits, so changes "
                               "saved now would be lost." if self.running else "")
        counts = Counter(self.spec(*k).cat for k in rows)
        for cat, item in self.nav_items.items():
            item.set_count(counts.get(cat, 0))
        self.tabs.set_count("Settings", n)
        if bool(self.pending) != self._sidebar_dirty:
            self.render_sidebar()
        if self.review_dlg is not None:
            self.debounce("review", 0, self.review_dlg.fill)
        self.setWindowTitle(f"{APP_NAME} {__version__}{'  (unsaved changes)' if n else ''}")

    def save(self):
        self.commit_focus()
        if not self.pending or not self.current:
            return True
        if self.blocked():
            return False
        try:
            ini = IniFile.load(self.current.path)
            for (sec, key), v in self.pending.items():
                ini.set(sec, key, v)
            write_profile(self.store, self.current, ini, "edit")
        except OSError as e:
            show_msg(self, "error", f"Saving failed. The file was not changed.\n\n{e}")
            return False
        n = len(self.changed_rows())
        self.pending.clear()
        self.history.clear()
        self.use_file(ini, ini.to_bytes())
        # The file was read again before the write, so every row shows what is on disk now.
        for r in self.all_rows():
            r.load()
            r.mark()
        self.watchers = [w for w in self.watchers if w()]
        self.render_sidebar()
        self.update_dirty()
        self.refresh_file_info()
        self.stale |= {"Backups", "Transfer"}
        locked = " The file stays read-only." if self.current.locked else ""
        self.toast(f"Saved {plural(n, 'change')}. The previous file is in Backups.{locked}")
        return True

    def review_changes(self):
        if not self.pending:
            return
        if self.review_dlg is not None:
            self.review_dlg.raise_()
            self.review_dlg.activateWindow()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Unsaved changes in {self.store.name(self.current.uuid)}")
        dlg.resize(720, 480)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        v = vbox(10, (12, 12, 12, 12))
        dlg.setLayout(v)
        area, lay = scroll_area((0, 0, 8, 0), 6)
        v.addWidget(area, 1)

        def fill():
            # update_dirty calls this, so the list follows the edits while the dialog is open.
            if self.review_dlg is not dlg:
                return
            if not self.pending:
                dlg.close()
                return
            clear_layout(lay)
            rows = sorted(self.changed_rows(), key=lambda k: RANK.get(k, 1e9))
            for k, name in self.row_names(rows).items():
                spec = self.spec(*k)
                if spec.kind == "resolution" and (k[0], "ResolutionHeight") in self.orig:
                    keys = [k, (k[0], "ResolutionHeight")]
                    was = ResolutionControl.res_label(*(self.orig.get(x) or "0" for x in keys))
                    now = ResolutionControl.res_label(*(self.value(*x) or "0" for x in keys))
                else:
                    keys, was, now = [k], spec.show(self.orig.get(k)), spec.show(self.value(*k))
                row = Card(COL["card"], 8)
                rl = hbox(8, (12, 6, 8, 6))
                row.setLayout(rl)
                rl.addWidget(label(name, wrap=True, width=250))
                rl.addWidget(label(f"{was}   →   {now}", "mono", "accent"), 1)
                undo = button("Undo", lambda keys=keys: self.reset_keys(keys), width=70)
                undo.setAutoDefault(False)
                rl.addWidget(undo)
                lay.addWidget(row)
            lay.addStretch()

        def closed(*_):
            if self.review_dlg is dlg:
                self.review_dlg = None

        dlg.fill = fill
        dlg.finished.connect(closed)
        self.review_dlg = dlg
        fill()
        bar = hbox(8)
        bar.addStretch()
        close = button("Close", dlg.close)
        close.setAutoDefault(False)
        bar.addWidget(close)
        save = button("Save changes", lambda: self.save() and dlg.close(), "primary")
        save.setDefault(True)
        bar.addWidget(save)
        v.addLayout(bar)
        dlg.show()

    def row_menu(self, row, pos):
        menu = QMenu(self)
        spec = row.spec
        if any(k in self.pending for k in row.keys):
            self.menu_action(menu, "Reset to saved value", lambda: self.reset_keys(row.keys))
        self.menu_action(menu, f"Copy key name  ({spec.path})", lambda: QGuiApplication.clipboard().setText(spec.path))
        others = [p for p in self.profiles if p.uuid != self.current.uuid]
        if others:
            menu.addSeparator()
            sub = menu.addMenu("Apply this value to")
            for p in others:
                self.menu_action(sub, self.store.name(p.uuid), lambda p=p: self.push_keys(row.keys, [p]))
            self.menu_action(menu, "Apply this value to all other profiles", lambda: self.push_keys(row.keys, others))
        menu.exec(pos)

    def push_keys(self, keys, targets):
        if self.blocked():
            return
        values = {k: self.value(*k) for k in keys}
        done, same = [], []
        for p in targets:
            try:
                ini = IniFile.load(p.path)
                if all(ini.get(*k) == v for k, v in values.items()):
                    same.append(self.store.name(p.uuid))
                    continue
                for k, v in values.items():
                    ini.set(*k, v)
                write_profile(self.store, p, ini, f"copy of {self.spec(*keys[0]).label}")
                done.append(self.store.name(p.uuid))
            except OSError as e:
                show_msg(self, "error", f"Could not update {self.store.name(p.uuid)}.\n\n{e}")
        parts = []
        if done:
            self.stale |= {"Transfer"}
            self.render_sidebar()
            parts.append(f"Applied {self.spec(*keys[0]).label} to {', '.join(done)}. "
                         + ("A backup was saved." if len(done) == 1 else "Backups were saved."))
        if same:
            parts.append(f"{', '.join(same)} already {'has' if len(same) == 1 else 'have'} this value.")
        if parts:
            self.toast(" ".join(parts), "ok" if done else "info")

    # profile tab

    def build_profile_tab(self):
        p = self.current
        if not p:
            self.p_info = {}
            self.page_content("Profile")
            return
        meta = self.store.meta(p.uuid)
        area, wrap = scroll_area((0, 0, 10, 12))

        def commit():
            self.store.save()
            self.update_header()
            self.render_sidebar()

        _, g = self.card(wrap, "Identity", grid=True, margins=(16, 14, 16, 16))
        g.setColumnStretch(1, 1)
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(12)
        g.addWidget(label("Label", tone="muted"), 0, 0)
        name = entry("For example Main account", height=36, role="h2", text=meta["label"])

        def set_name():
            if name.text().strip() != meta["label"]:
                meta["label"] = name.text().strip()
                commit()

        name.editingFinished.connect(set_name)
        g.addWidget(name, 0, 1)

        g.addWidget(label("Color", tone="muted"), 1, 0)
        swatches = hbox(8)
        for c in PROFILE_COLORS:
            s = Swatch(c, c == meta["color"])
            s.clicked.connect(lambda *_, c=c: (meta.update(color=c), commit(), self.build_profile_tab()))
            swatches.addWidget(s)
        swatches.addStretch()
        g.addLayout(swatches, 1, 1)

        g.addWidget(label("Tags", tone="muted", margins=(0, 6, 0, 0)), 2, 0, Qt.AlignTop)
        tagbox = vbox(8)
        if meta["tags"]:
            chips = QWidget()
            flow = FlowLayout(chips, 6)
            for tag in meta["tags"]:
                flow.addWidget(button(f"{tag}   ×", lambda tag=tag: (meta["tags"].remove(tag), commit(), self.build_profile_tab()),
                                      "chip", height=28))
            tagbox.addWidget(chips)
        add = entry("Add a tag and press Enter", 260, 32)
        tagbox.addWidget(add)

        def add_tag(tag):
            # A tag that exists with other capitals is reused, so "Main" and "main" stay one tag.
            tag = tag.strip()
            tag = next((t for t in self.store.all_tags() if t.lower() == tag.lower()), tag)
            if tag and tag.lower() not in {t.lower() for t in meta["tags"]}:
                meta["tags"].append(tag)
                commit()
                self.build_profile_tab()
                self.p_tag_entry.setFocus()

        add.returnPressed.connect(lambda: add_tag(add.text()))
        self.p_tag_entry = add
        have, suggestions = {t.lower() for t in meta["tags"]}, {}
        for tg in self.store.all_tags() + ["main", "smurf", "stretched", "stream"]:
            if tg.lower() not in have:
                suggestions.setdefault(tg.lower(), tg)
        if suggestions:
            sug = QWidget()
            flow = FlowLayout(sug, 4)
            flow.addWidget(label("Quick add", "small", "faint", margins=(0, 3, 4, 0)))
            for tg in list(suggestions.values())[:9]:
                flow.addWidget(button(f"+ {tg}", lambda tg=tg: add_tag(tg), "quick", height=24))
            tagbox.addWidget(sug)
        g.addLayout(tagbox, 2, 1)

        g.addWidget(label("Notes", tone="muted", margins=(0, 6, 0, 0)), 3, 0, Qt.AlignTop)
        notes = QPlainTextEdit(meta["notes"])
        notes.setFixedHeight(90)

        def set_notes():
            text = notes.toPlainText()
            if text != meta["notes"]:
                meta["notes"] = text
                self.debounce("notes", 600, self.store.save)

        notes.textChanged.connect(set_notes)
        g.addWidget(notes, 3, 1)

        _, who = self.card(wrap, "Whose account is this?", spacing=10)
        who.addWidget(label("The folder name is a Ubisoft profile ID. Open it on a stats site to see the account name, "
                            "then use that name as the label. This opens your browser; the app sends nothing itself.",
                            "small", "muted", wrap=True))
        line = hbox(10)
        site = Segmented(list(SITES), "choice", 32)
        site.set(self.store.data["settings"].get("site", next(iter(SITES))))
        line.addWidget(site)

        def lookup():
            name_ = site.get() or next(iter(SITES))
            self.store.data["settings"]["site"] = name_
            webbrowser.open(SITES[name_].format(uuid=p.uuid))

        line.addWidget(button("Open in browser", lookup, "primary", height=32))
        line.addStretch()
        who.addLayout(line)

        _, fl = self.card(wrap, "Settings file", spacing=12)
        info = QGridLayout()
        info.setHorizontalSpacing(8)
        info.setVerticalSpacing(3)
        info.setColumnStretch(1, 1)
        self.p_info = {}
        for i, k in enumerate(("Path", "Saved", "Size", "Keys", "Read-only")):
            info.addWidget(label(k, "small", "muted", width=90), i, 0, Qt.AlignTop)
            value = label(soft(p.path) if k == "Path" else "", "tiny_mono", wrap=True)
            info.addWidget(value, i, 1)
            if k != "Path":
                self.p_info[k] = value
        self.refresh_file_info()
        fl.addLayout(info)
        btns = hbox(8)
        btns.addWidget(button("Open folder", lambda: self.open_path(p.folder)))
        btns.addWidget(button("Back up now", self.backup_now))
        btns.addWidget(button("Export a copy", self.export_copy))
        btns.addWidget(button("Import from file", self.import_file))
        btns.addStretch()
        fl.addLayout(btns)
        wrap.addStretch()
        self.page_content("Profile", area)

    def backup_now(self):
        # A named backup is kept. Returns True when a new backup was saved.
        p = self.current
        if not p:
            return False
        name = ask_text(self, "Back up now", "Name this backup to keep it. Leave it empty for a normal backup.")
        if name is None:
            return False
        name = name.strip()
        try:
            dest = self.store.backup(p.uuid, p.path.read_bytes(), name or "manual", keep=bool(name))
        except OSError as e:
            show_msg(self, "error", f"The backup failed.\n\n{e}")
            return False
        if not dest:
            self.toast("Nothing changed since the last backup.", "info")
            return False
        self.stale.add("Backups")
        self.toast(f'Backup "{name}" saved. It stays until you delete it.' if name else "Backup saved.")
        return True

    def export_copy(self):
        p = self.current
        if not p:
            return
        name = re.sub(r"[^\w-]+", "_", self.store.meta(p.uuid)["label"]) or p.uuid[:8]
        dest, _ = QFileDialog.getSaveFileName(self, "Export a copy", str(documents_dir() / f"{name}-GameSettings.ini"),
                                              "Settings file (*.ini)")
        if dest:
            if not Path(dest).suffix:
                dest += ".ini"
            try:
                shutil.copyfile(p.path, dest)
            except OSError as e:
                show_msg(self, "error", f"The export failed.\n\n{e}")
                return
            self.toast(f"Exported to {dest}.")

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import from file", "", "Settings file (*.ini);;All files (*.*)")
        ref = self.add_file_source(path) if path else None
        if ref:
            self.start_transfer(ref)

    # transfer tab

    def build_transfer_tab(self):
        v = vbox(0)
        self.t["Transfer"].setLayout(v)
        top = Card(COL["card"], 12)
        tl = hbox(6, (16, 12, 16, 12))
        top.setLayout(tl)
        tl.addWidget(label("From", "small", "muted"))
        self.x_src = Combo([""], height=34)
        self.x_src.activated.connect(lambda i: self.pick_source(self.x_src.itemText(i)))
        self.x_tgt = Combo([""], height=34)
        self.x_tgt.activated.connect(lambda i: self.refresh_transfer(rebuild_menus=False))
        for c in (self.x_src, self.x_tgt):
            c.setMinimumWidth(150)
            c.setMaximumWidth(280)
            c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tl.addWidget(self.x_src, 1)
        tl.addSpacing(2)
        tl.addWidget(button("⇄", self.swap_transfer, width=38, height=34, role="h2"))
        tl.addSpacing(2)
        tl.addWidget(label("To", "small", "muted"))
        tl.addWidget(self.x_tgt, 1)
        tl.addStretch()
        self.x_diff = Switch("Differences only", 40, 20)
        self.x_diff.setChecked(True)
        self.x_diff.clicked.connect(lambda *_: self.refresh_transfer(rebuild_menus=False))
        self.x_hw = Switch("Hardware keys", 40, 20)
        self.x_hw.clicked.connect(lambda *_: self.refresh_transfer(rebuild_menus=False))
        tl.addWidget(self.x_diff)
        tl.addSpacing(10)
        tl.addWidget(self.x_hw)
        v.addWidget(top)
        v.addSpacing(8)
        self.x_area, self.x_lay = scroll_area((2, 0, 10, 12))
        v.addWidget(self.x_area, 1)
        v.addSpacing(10)
        bar = Card(COL["card"], 10)
        bl = hbox(12, (16, 8, 10, 8))
        bar.setLayout(bl)
        self.x_count = label("", tone="muted")
        bl.addWidget(self.x_count)
        bl.addStretch()
        bl.addWidget(button("Select all shown", lambda: self.select_rows(set(self.x_rows)), width=130, height=34))
        bl.addWidget(button("Clear", lambda: self.select_rows(set()), width=80, height=34))
        self.x_preset = button("Save as preset", self.save_preset, width=130, height=34)
        self.x_delpreset = button("Delete preset", self.delete_preset, "danger", 120, 34)
        self.x_delpreset.hide()
        self.x_apply = button("Apply to target", self.apply_transfer, "primary", 170, 34)
        for b in (self.x_preset, self.x_delpreset, self.x_apply):
            bl.addWidget(b)
        v.addWidget(bar)
        self.action_bars["Transfer"] = bar

    def plabel(self, uuid):
        name = self.store.meta(uuid)["label"]
        return f"{name}  ({uuid[:8]})" if name else self.store.name(uuid)

    def source_options(self):
        opts = {}
        for p in self.profiles:
            opts[self.plabel(p.uuid)] = ("profile", p.uuid)
        for name in sorted(self.store.data["presets"], key=str.lower):
            opts[f"Preset: {name}"] = ("preset", name)
        opts.update(self.x_extra)
        opts["Open a file..."] = ("open", None)
        return opts

    def target_options(self):
        opts = {self.plabel(p.uuid): p.uuid for p in self.profiles}
        if len(self.profiles) > 1:
            opts["All other profiles"] = "all"
        return opts

    def transfer_targets(self, src_ref):
        # All other profiles means every profile except the source, or except the selected one for other sources.
        tgt = self.target_options().get(self.x_tgt.get())
        if tgt == "all":
            skip = src_ref[1] if src_ref and src_ref[0] == "profile" else self.current and self.current.uuid
            return [p for p in self.profiles if p.uuid != skip]
        p = self.profile_by_uuid(tgt)
        return [p] if p else []

    def add_extra_source(self, kind, path, name=None):
        # A name that another file already uses gets a number.
        base = name = name or f"File: {path.name}"
        n = 1
        while self.x_extra.get(name, (kind, path)) != (kind, path):
            n += 1
            name = f"{base} ({n})"
        self.x_extra[name] = (kind, path)
        return self.x_extra[name]

    def add_file_source(self, path):
        # A file joins From only when it holds Siege settings.
        try:
            keys = IniFile.load(path).values()
        except OSError as e:
            show_msg(self, "error", f"Could not read {path}.\n\n{e}")
            return None
        if not any(k in SPECS or k in self.orig for k in keys):
            show_msg(self, "info", "This file has no Rainbow Six Siege settings.")
            return None
        return self.add_extra_source("file", Path(path))

    def update_backup_source(self, path, new=None):
        # From follows a backup that was renamed, and loses one that was deleted.
        for name, ref in list(self.x_extra.items()):
            if ref == ("backup", path):
                if new is None:
                    del self.x_extra[name]
                else:
                    self.x_extra[name] = ("backup", new)
        self.stale.add("Transfer")

    def start_transfer(self, src_ref, target_uuid=None):
        self.stale.discard("Transfer")
        last = self.target_options().get(self.x_tgt.get())
        src_label = next((k for k, v in self.source_options().items() if v == src_ref), None)
        self.refresh_transfer()
        if src_label:
            self.x_src.set(src_label)
        tgt = target_uuid or (self.current.uuid if self.current else None)
        if src_ref == ("profile", tgt):
            # The source is never the target. The last target stays, or the first other profile is used.
            others = [p.uuid for p in self.profiles if p.uuid != tgt]
            tgt = last if last and last != tgt else next(iter(others), None)
        tgt_label = next((k for k, v in self.target_options().items() if v == tgt), None)
        if tgt_label:
            self.x_tgt.set(tgt_label)
        self.refresh_transfer(rebuild_menus=False, clear=True)
        self.tabs.set("Transfer")

    def follow_selection(self):
        # To follows the sidebar selection. When From was that profile, it takes the previous To.
        new = next((k for k, v in self.target_options().items() if v == self.current.uuid), None)
        old = self.x_tgt.get()
        if not new or not old:
            return
        if self.source_options().get(self.x_src.get()) == ("profile", self.current.uuid):
            if self.target_options().get(old) in (None, "all"):
                old = next((self.plabel(p.uuid) for p in self.profiles if p.uuid != self.current.uuid), "")
            self.x_src.set(old)
        self.x_tgt.set(new)

    def pick_source(self, text):
        ref = self.source_options().get(text)
        if ref and ref[0] == "open":
            path, _ = QFileDialog.getOpenFileName(self, "Open a settings file", "", "Settings file (*.ini);;All files (*.*)")
            ref = self.add_file_source(path) if path else None
            if not ref:
                self.x_src.set(self._x_src_last)
                return
            self.x_src.set_values(list(self.source_options()))
            self.x_src.set(next(k for k, v in self.source_options().items() if v == ref))
        self.refresh_transfer(rebuild_menus=False, clear=True)

    def swap_transfer(self):
        src = self.source_options().get(self.x_src.get())
        tgt = self.target_options().get(self.x_tgt.get())
        if not src or src[0] != "profile" or not tgt or tgt == "all":
            self.toast("Swap works when both sides are profiles.", "info")
            return
        self.start_transfer(("profile", tgt), src[1])

    def source_values(self, ref):
        kind, ident = ref
        if kind == "profile":
            return IniFile.load(self.profile_by_uuid(ident).path).values()
        if kind == "preset":
            preset = self.store.data["presets"].get(ident, {})
            return {(s, k): v for s, d in preset.get("values", {}).items() for k, v in d.items()}
        return IniFile.load(ident).values()

    def transfer_message(self, text, tone="muted"):
        msg = label(text, tone=tone, margins=(0, 40, 0, 0))
        msg.setAlignment(Qt.AlignHCenter)
        self.x_lay.addWidget(msg)

    def refresh_transfer(self, rebuild_menus=True, clear=False):
        if rebuild_menus:
            src_opts, tgt_opts = list(self.source_options()), list(self.target_options())
            self.x_src.set_values(src_opts)
            self.x_tgt.set_values(tgt_opts or [""])
            if self.x_src.get() not in src_opts:
                others = [k for k, v in self.source_options().items()
                          if v[0] == "profile" and (not self.current or v[1] != self.current.uuid)]
                self.x_src.set(others[0] if others else src_opts[0])
            if self.x_tgt.get() not in tgt_opts:
                cur = next((k for k, v in self.target_options().items() if self.current and v == self.current.uuid), None)
                self.x_tgt.set(cur or (tgt_opts[0] if tgt_opts else ""))
        src_text, tgt_text = self.x_src.get(), self.x_tgt.get()
        # A new source starts with no ticks. A preset ticks what differs, again for each new target.
        clear = clear or src_text != self._x_src_last
        retick = clear or tgt_text != self._x_tgt_last
        self._x_src_last, self._x_tgt_last = src_text, tgt_text
        if clear:
            self.x_sel = set()
        clear_layout(self.x_lay)
        self.x_rows, self.x_vars, self.x_groups, self.x_same, self.x_summary = {}, {}, [], set(), ""
        src_ref = self.source_options().get(src_text)
        targets = self.x_targets = self.transfer_targets(src_ref)
        preset = bool(src_ref and src_ref[0] == "preset")
        self.x_delpreset.setVisible(preset)
        if not src_ref or not targets or src_ref[0] == "open":
            self.update_transfer_count()
            return
        if src_ref[0] == "profile" and src_ref[1] in {t.uuid for t in targets}:
            self.transfer_message("Pick two different profiles.")
            self.x_lay.addStretch()
            self.update_transfer_count()
            return
        try:
            src = self.source_values(src_ref)
            tgts = [IniFile.load(t.path) for t in targets]
        except (OSError, AttributeError) as e:
            gone = src_ref[0] == "backup" and not Path(src_ref[1]).exists()
            self.transfer_message("This backup no longer exists." if gone else f"Could not read the files.\n{e}", "bad")
            self.x_lay.addStretch()
            self.update_transfer_count()
            return
        diff_only, include_hw = self.x_diff.isChecked(), self.x_hw.isChecked()
        now = [t.values() for t in tgts]
        # Keys of a file in sections that no target has are left out, so a benchmark report adds no keys.
        secs = set().union(*(t.sections for t in tgts))
        grouped, differ = defaultdict(list), set()
        total_diff = hw_diff = 0
        for k, sv in src.items():
            if src_ref[0] == "file" and k[0] not in secs:
                continue
            spec = self.spec(*k)
            tv = [vals.get(k) for vals in now]
            same = all(v == sv for v in tv)
            if same:
                self.x_same.add(k)
            if spec.hw and not include_hw:
                hw_diff += not same
                continue
            total_diff += not same
            if not same:
                differ.add(k)
            elif diff_only and not preset:
                continue
            grouped[spec.cat].append((spec, k, sv, tv))
        if preset and retick:
            self.x_sel = differ
        if not grouped:
            self.transfer_message(f"Only hardware keys differ ({hw_diff}). Turn on Hardware keys to see them." if hw_diff
                                  else "Nothing to transfer. The shown settings already match.")
        else:
            self.transfer_header(src_text, targets)
        for cat in CATEGORIES:
            rows = sorted(grouped.get(cat, []), key=lambda r: RANK.get(r[1], 1e9))
            if not rows:
                continue
            keys = {k for _, k, _, _ in rows}
            head = Check(f"{cat.upper()}   {len(rows)}", 18, F.caps, COL["faint"])
            head.setChecked(keys <= self.x_sel)
            head.clicked.connect(lambda *_, keys=keys, head=head: self.select_rows(
                self.x_sel | keys if head.isChecked() else self.x_sel - keys))
            holder = QWidget()
            hl = hbox(0, (6, 14, 6, 4))
            holder.setLayout(hl)
            hl.addWidget(head)
            hl.addStretch()
            self.x_lay.addWidget(holder)
            self.x_groups.append((keys, head))
            _, grid = self.card(self.x_lay, grid=True, margins=(14, 6, 14, 6))
            grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(2, 200)
            grid.setColumnMinimumWidth(4, 200)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(4)
            for i, (spec, k, sv, tv) in enumerate(rows):
                same = k in self.x_same
                chk = Check("", 20)
                chk.setChecked(k in self.x_sel)
                chk.clicked.connect(lambda *_, k=k, chk=chk: self.toggle_row(k, chk.isChecked()))
                self.x_vars[k] = chk
                self.x_rows[k] = sv
                name = QWidget()
                nl = vbox(0)
                name.setLayout(nl)
                nl.addWidget(label("Resolution width" if spec.kind == "resolution" else spec.label))
                nl.addWidget(label(spec.path, "tiny_mono", "faint"))
                src_label = label(spec.show(sv), "mono", "muted" if same else "accent")
                src_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                mark = label("=" if same else "→", tone="faint", width=16)
                mark.setAlignment(Qt.AlignCenter)
                grid.addWidget(chk, i, 0)
                grid.addWidget(name, i, 1)
                grid.addWidget(src_label, i, 2)
                grid.addWidget(mark, i, 3)
                grid.addWidget(self.target_value(spec, tv, targets), i, 4)
        self.x_lay.addStretch()
        hidden = "" if include_hw else "  Hardware keys are hidden."
        self.x_summary = f"{plural(total_diff, 'difference')}.{hidden}"
        self.update_transfer_count()

    def target_value(self, spec, tv, targets):
        # Several targets show their value when they agree, or "varies" with each value in the tooltip.
        if len(set(tv)) > 1:
            w = label("varies", "mono", "muted")
            w.setToolTip("\n".join(f"{self.store.name(t.uuid)}: {spec.show(v)}" for t, v in zip(targets, tv)))
            return w
        return label(spec.show(tv[0]) if tv[0] is not None else "new key", "mono", "muted" if tv[0] is not None else "ok")

    def transfer_header(self, src_text, targets):
        # One line names the value columns. Its grid matches the cards below.
        head = QWidget()
        grid = QGridLayout(head)
        grid.setContentsMargins(14, 10, 14, 0)
        grid.setHorizontalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 200)
        grid.setColumnMinimumWidth(4, 200)
        # The grid adds spacing only around columns that hold a widget.
        grid.addWidget(label(width=20), 0, 0)
        grid.addWidget(label(width=16), 0, 3)
        fm = QFontMetrics(F.small)
        name = self.store.name(targets[0].uuid) if len(targets) == 1 else "Other profiles"
        src = label(fm.elidedText(src_text.split("  (")[0], Qt.ElideRight, 200), "small", "muted")
        src.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(src, 0, 2)
        grid.addWidget(label(fm.elidedText(f"{name} now", Qt.ElideRight, 200), "small", "muted"), 0, 4)
        self.x_lay.addWidget(head)

    def toggle_row(self, k, on):
        (self.x_sel.add if on else self.x_sel.discard)(k)
        self.sync_checks()

    def select_rows(self, keys):
        self.x_sel = set(keys)
        self.sync_checks()

    def sync_checks(self):
        for k, chk in self.x_vars.items():
            chk.setChecked(k in self.x_sel)
        for keys, head in self.x_groups:
            head.setChecked(keys <= self.x_sel)
        self.update_transfer_count()

    def update_transfer_count(self):
        # Only ticked rows that are shown count. Ticks of hidden rows stay for when the rows show again.
        n = len(self.x_sel & self.x_rows.keys())
        self.x_count.setText(f"{n} selected   ·   {self.x_summary}" if self.x_rows else self.x_summary)
        restyle(self.x_count, tone="accent" if n else "muted")
        tgt = self.x_tgt.get().split("  (")[0]
        if len(self.x_targets) > 1:
            self.x_apply.setText(f"Apply to {plural(len(self.x_targets), 'profile')}")
        else:
            self.x_apply.setText(f"Apply to {tgt[:22]}" if tgt else "Apply to target")
        self.x_apply.setEnabled(bool(n) and not self.running)
        self.x_preset.setEnabled(bool(n))

    def apply_transfer(self):
        keys = [k for k in self.x_rows if k in self.x_sel]
        src_ref = self.source_options().get(self.x_src.get())
        targets = list(self.x_targets)
        if not keys or not src_ref or not targets or self.blocked():
            return
        cur = self.current.uuid if self.current else None
        if self.pending and cur in {t.uuid for t in targets}:
            show_msg(self, "info", f"{self.store.name(cur)} has unsaved edits in the Settings tab. Save or revert them first.")
            return
        src_name = self.x_src.get().split("  (")[0]
        try:
            src = self.source_values(src_ref)
        except (OSError, AttributeError) as e:
            show_msg(self, "error", f"Could not read {src_name}.\n\n{e}")
            return
        changes, failed = {}, []
        for t in targets:
            try:
                ini = IniFile.load(t.path)
            except OSError as e:
                failed.append((self.store.name(t.uuid), e))
                continue
            changes[t.uuid] = sum(ini.get(*k) != src[k] for k in keys)
        changing = [t for t in targets if changes.get(t.uuid)]
        same = [self.store.name(t.uuid) for t in targets if changes.get(t.uuid) == 0]
        if len(changing) == 1 and len(targets) == 1:
            text = f"Copy {plural(changes[changing[0].uuid], 'setting')} from {src_name} to " \
                   f"{self.store.name(changing[0].uuid)}?\n\nA backup of the target is saved first."
        elif changing:
            lines = "\n".join(f"{self.store.name(t.uuid)}: {plural(changes[t.uuid], 'change')}" for t in changing)
            if same:
                lines += f"\n{', '.join(same)} already {'has' if len(same) == 1 else 'have'} these values."
            text = f"Copy {plural(len(keys), 'setting')} from {src_name} to {plural(len(changing), 'profile')}?\n\n" \
                   f"{lines}\n\nA backup of each profile is saved first."
        if changing and not ask_yes_no(self, text):
            return
        done = []
        for t in changing:
            try:
                ini = IniFile.load(t.path)
                for k in keys:
                    ini.set(*k, src[k])
                write_profile(self.store, t, ini, f"transfer from {src_name}")
                done.append(t)
            except OSError as e:
                failed.append((self.store.name(t.uuid), e))
        if failed:
            show_msg(self, "error", "\n\n".join(f"Could not update {n}. It was not changed.\n{e}" for n, e in failed))
        parts = []
        if done:
            n = changes[done[0].uuid] if len(done) == 1 else len(keys)
            parts.append(f"Copied {plural(n, 'setting')} to {', '.join(self.store.name(t.uuid) for t in done)}. "
                         + ("A backup was saved." if len(done) == 1 else "Backups were saved."))
        if same:
            parts.append(f"{', '.join(same)} already {'has' if len(same) == 1 else 'have'} these values.")
        if parts:
            self.toast(" ".join(parts), "ok" if done else "info")
        if not done:
            return
        if cur in {t.uuid for t in done}:
            self.reload_current()
            self.tabs.set("Transfer")
        self.render_sidebar()
        self.stale.add("Backups")
        self.refresh_transfer(rebuild_menus=False, clear=True)

    def save_preset(self):
        picked = [k for k in self.x_rows if k in self.x_sel]
        src_ref = self.source_options().get(self.x_src.get())
        if not picked or not src_ref:
            return
        try:
            src = self.source_values(src_ref)
        except (OSError, AttributeError) as e:
            show_msg(self, "error", f"Could not read the source.\n\n{e}")
            return
        # Resolution width and height are saved together.
        pair = ("ResolutionWidth", "ResolutionHeight")
        keys = list(dict.fromkeys(x for k in picked for x in ([(k[0], p) for p in pair] if k[1] in pair else [k])
                                  if x in src))
        total = plural(len(keys), "setting")
        name = (ask_text(self, "Save preset", f"Saves {total}.\n\nName for this preset, for example Sens only") or "").strip()
        if not name:
            return
        presets = self.store.data["presets"]
        if name in presets and not ask_yes_no(self, f'Replace the preset "{name}"?'):
            return
        values = {}
        for sec, key in keys:
            values.setdefault(sec, {})[key] = src[(sec, key)]
        presets[name] = {"values": values, "created": datetime.now().isoformat(timespec="seconds"),
                         "source": self.x_src.get()}
        self.store.save()
        self.x_sel = set()
        self.refresh_transfer()
        self.toast(f'Preset "{name}" saved with {total}. Pick it in From to apply it.')

    def delete_preset(self):
        ref = self.source_options().get(self.x_src.get())
        if ref and ref[0] == "preset" and ask_yes_no(self, f'Delete the preset "{ref[1]}"?'):
            self.store.data["presets"].pop(ref[1], None)
            self.store.save()
            self.refresh_transfer()

    # backups tab

    def refresh_backups(self):
        p = self.current
        if not p:
            self.page_content("Backups")
            return
        page = QWidget()
        v = vbox(8)
        page.setLayout(v)
        top = hbox(12, (6, 2, 0, 0))
        top.addWidget(label(f"Backups of {self.store.name(p.uuid)}", "h2"))
        note = label(f"A copy is saved before and after every change. The newest {MAX_BACKUPS} stay, plus kept ones.",
                     "small", "faint")
        note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(note, 1)
        top.addWidget(button("Back up now", lambda: self.backup_now() and self.refresh_backups()))
        top.addWidget(button("Open folder", lambda: (self.store.backup_dir(p.uuid).mkdir(parents=True, exist_ok=True),
                                                     self.open_path(self.store.backup_dir(p.uuid)))))
        v.addLayout(top)
        area, lst = scroll_area((2, 0, 10, 12), 6)
        v.addWidget(area, 1)
        self.page_content("Backups", page)
        backups = self.store.backups(p.uuid)
        if not backups:
            msg = label("No backups yet. One is saved automatically before the first change.", tone="muted",
                        margins=(0, 40, 0, 0))
            msg.setAlignment(Qt.AlignHCenter)
            lst.addWidget(msg)
            lst.addStretch()
            return
        try:
            cur = IniFile.load(p.path).values()
        except OSError:
            cur = {}
        for b in backups:
            try:
                vals = IniFile.load(b.path).values()
            except OSError:
                continue
            n, hw = self.diff_counts(vals, cur)
            row = Card(COL["card"], 10)
            rl = hbox(8, (14, 8, 12, 8))
            row.setLayout(rl)
            rl.addWidget(label(nice_date(b.when), "bold", width=190))
            what = hbox(8)
            kept = Pill("KEPT", COL["accent_dim"], COL["accent"])
            kept.setVisible(b.keep)
            what.addWidget(kept)
            what.addWidget(label(b.reason, tone="muted", wrap=True), 1)
            rl.addLayout(what, 1)
            counts = vbox(0)
            for text, tone in ((plural(n, "difference") if n else "same settings", "accent" if n else "faint"),
                               (f"+{hw} hardware" if hw else "", "faint")):
                if text:
                    diff = label(text, "small", tone, width=110)
                    diff.setAlignment(Qt.AlignCenter)
                    counts.addWidget(diff)
            rl.addLayout(counts)
            keep = Switch("Keep", 32, 18)
            keep.setChecked(b.keep)
            keep.setToolTip(f"A kept backup stays when there are more than {MAX_BACKUPS}.")
            keep.clicked.connect(lambda *_, b=b, keep=keep, kept=kept: self.keep_backup(b, keep, kept))
            rl.addWidget(keep)
            rl.addWidget(button("Compare", lambda b=b: self.start_transfer(self.add_extra_source(
                "backup", b.path, f"Backup: {nice_date(b.when)} ({b.reason})"), p.uuid), width=80))
            restore = button("Restore", lambda b=b: self.restore_backup(b), width=80)
            restore.setEnabled(not self.running and vals != cur)
            rl.addWidget(restore)
            rl.addWidget(button("Delete", lambda b=b, row=row: self.delete_backup(b, row), "ghost", 70))
            lst.addWidget(row)
        lst.addStretch()

    def diff_counts(self, src, tgt):
        # Keys that a restore of src over tgt changes or removes, with hardware keys apart.
        diff = [k for k in src.keys() | tgt.keys() if src.get(k) != tgt.get(k)]
        hw = sum(self.spec(*k).hw for k in diff)
        return len(diff) - hw, hw

    def restore_backup(self, b):
        p = self.current
        if not p or self.blocked() or not self.confirm_discard():
            return
        try:
            ini = IniFile.load(b.path)
            n, hw = self.diff_counts(ini.values(), IniFile.load(p.path).values())
        except OSError as e:
            show_msg(self, "error", f"Restore failed.\n\n{e}")
            return
        what = " and ".join(x for x in (plural(n, "setting") if n else "", plural(hw, "hardware key") if hw else "") if x)
        if not ask_yes_no(self, f"Restore {what or 'the backup'} from {nice_date(b.when)}?\n\n"
                                "The current file is backed up first."):
            return
        try:
            write_profile(self.store, p, ini, f"restore of {nice_date(b.when)}")
        except OSError as e:
            show_msg(self, "error", f"Restore failed.\n\n{e}")
            return
        self.reload_current()
        self.tabs.set("Backups")
        self.refresh_backups()
        self.toast("Backup restored.")

    def keep_backup(self, b, switch, pill):
        # Keeping renames the file, so From follows it.
        old = b.path
        try:
            self.store.set_keep(b, switch.isChecked())
        except OSError as e:
            show_msg(self, "error", f"Could not rename the backup.\n\n{e}")
            switch.setChecked(b.keep)
            return
        pill.setVisible(b.keep)
        self.update_backup_source(old, b.path)

    def delete_backup(self, b, row=None):
        if not ask_yes_no(self, f"Delete the backup from {nice_date(b.when)}?"):
            return
        try:
            b.path.unlink(missing_ok=True)
        except OSError as e:
            show_msg(self, "error", f"Could not delete the backup.\n\n{e}")
            return
        self.update_backup_source(b.path)
        # Removing only the row keeps the scroll position.
        if row is not None and self.current and self.store.backups(self.current.uuid):
            discard(row)
        else:
            self.refresh_backups()

    # benchmarks tab

    def build_bench_tab(self):
        h = hbox(12)
        self.t["Benchmarks"].setLayout(h)
        left = QWidget()
        left.setFixedWidth(310)
        lv = vbox(0)
        left.setLayout(lv)
        lv.addWidget(label("AVERAGE FPS PER RUN", "caps", "faint", margins=(6, 4, 6, 6)))
        _, hl = self.card(lv, margins=(8, 8, 8, 8))
        self.b_hist = BarChart(lambda i: self.show_bench(i))
        hl.addWidget(self.b_hist)
        lv.addSpacing(8)
        self.b_list, self.b_list_lay = scroll_area((0, 0, 8, 4), 6)
        lv.addWidget(self.b_list, 1)
        h.addWidget(left)
        right, rv = scroll_area((0, 0, 10, 12), 0)
        h.addWidget(right, 1)
        self.b_title = label("", "h2", margins=(6, 4, 6, 8))
        rv.addWidget(self.b_title)
        self.b_empty = label("", tone="muted", wrap=True, margins=(6, 0, 6, 8))
        self.b_empty.hide()
        rv.addWidget(self.b_empty)
        # The run details hide as one block when the folder has no runs.
        self.b_body = QWidget()
        bv = vbox(0)
        self.b_body.setLayout(bv)
        rv.addWidget(self.b_body)
        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.b_tiles = []
        for i in range(8):
            tile = Card(COL["card"], 12)
            tv = vbox(0, (14, 10, 14, 10))
            tile.setLayout(tv)
            v, c, d = label("", "tile"), label("", "small", "muted"), label("", "small", wrap=True)
            d.hide()
            tv.addWidget(v)
            tv.addWidget(c)
            tv.addWidget(d)
            tv.addStretch()
            tiles.addWidget(tile, i // 4, i % 4)
            tiles.setColumnStretch(i % 4, 1)
            self.b_tiles.append((v, c, d))
        bv.addLayout(tiles)
        _, cl = self.card(bv, "Frame rate over the run", margins=(8, 10, 8, 8))
        opts = hbox(12, (4, 0, 4, 0))
        self.b_cpu = Switch("CPU frame time", 36, 18)
        self.b_gpu = Switch("GPU frame time", 36, 18)
        for s in (self.b_cpu, self.b_gpu):
            s.clicked.connect(lambda *_: self.show_bench(self.bench_sel))
            opts.addWidget(s)
        opts.addStretch()
        self.b_cmp = Combo(["Compare with: none"], 300, 28, role="small")
        self.b_cmp.setCurrentIndex(0)
        self.b_cmp.activated.connect(self.pick_compare)
        opts.addWidget(self.b_cmp)
        cl.addLayout(opts)
        self.b_chart = LineChart()
        cl.addWidget(self.b_chart)
        info = QGridLayout()
        info.setHorizontalSpacing(8)
        info.setColumnStretch(0, 1)
        info.setColumnStretch(1, 1)
        boxes = []
        for col in range(2):
            box = Card(COL["card"], 12)
            bl = vbox(4, (14, 12, 14, 12))
            box.setLayout(bl)
            info.addWidget(box, 0, col)
            boxes.append(bl)
        self.b_sys_lay, self.b_set_lay = boxes
        bv.addSpacing(12)
        bv.addLayout(info)
        btns = hbox(8, (0, 10, 0, 0))
        self.b_open = button("Open full report", self.open_bench_report)
        btns.addWidget(self.b_open)
        btns.addWidget(button("Open benchmark folder", self.open_bench_folder))
        btns.addStretch()
        rv.addLayout(btns)
        rv.addStretch()

    def refresh_bench(self, keep=None):
        # Shows the run from the date in keep, else the newest. Compare starts at the run before it.
        # A report read while the game was still writing it parses empty and is read again.
        cache = {(b.ini, b.html): b.detail for b in self.benches or [] if b.detail and len(b.detail.get("fps", ()))}
        self.benches = scan_benchmarks(self.root_dir)
        for b in self.benches:
            b.detail = cache.get((b.ini, b.html))
        clear_layout(self.b_list_lay)
        self.b_items = []
        if not self.benches:
            self.clear_bench()
            return
        self.b_empty.hide()
        self.b_body.show()
        for i in range(len(self.benches) - 1, -1, -1):
            item = BenchItem(self.benches[i], lambda i=i: self.show_bench(i))
            self.b_list_lay.addWidget(item)
            self.b_items.append((i, item))
        self.b_list_lay.addStretch()
        self.b_cmp.set_values(["Compare with: none"] + [f"Compare with: {nice_date(b.when)}"
                                                        + (f" · {b.stats['Fps']:.0f} fps" if b.stats.get("Fps") else "")
                                                        for b in reversed(self.benches)])
        self.b_cmp.setEnabled(True)
        i = next((j for j, b in enumerate(self.benches) if b.when == keep), len(self.benches) - 1)
        self.b_cmp_i = i - 1 if i else None
        self.show_bench(i)

    def clear_bench(self):
        self.bench_sel = self.b_cmp_i = None
        for tile in self.b_tiles:
            for w in tile:
                w.setText("")
        self.b_hist.plot([], None)
        self.b_chart.plot([], None, "")
        clear_layout(self.b_sys_lay)
        clear_layout(self.b_set_lay)
        self.b_cmp.set_values(["Compare with: none"])
        self.b_cmp.setCurrentIndex(0)
        self.b_cmp.setEnabled(False)
        self.b_open.setEnabled(False)
        self.b_title.setText("No benchmark runs")
        self.b_empty.setText(f"The game saves its benchmark reports in {soft(self.root_dir / 'Benchmark')}. To make a run, "
                             "start the benchmark from the game's graphics settings. New runs show here on their own.")
        self.b_body.hide()
        self.b_empty.show()

    def bench_detail(self, b):
        if b.detail is None:
            try:
                b.detail = parse_bench_html(b.html) if b.html else {}
            except (OSError, ValueError):
                b.detail = {}
        return b.detail

    def bench_figures(self, b):
        # Values for BENCH_TILES. The CPU and GPU frame times are the means of the report's series.
        d = self.bench_detail(b)
        s, x = b.stats, d.get("extra", {})
        fps, cpu, gpu = (d.get(k, np.array([])) for k in ("fps", "cpu", "gpu"))
        return [(s.get("Fps"),), (float(np.percentile(fps, 1)) if fps.size else None,), (s.get("LowestFps"),),
                (s.get("HighestFps"),), (s.get("RawFrameTimes"),),
                (float(np.mean(cpu)) if cpu.size else None, float(np.mean(gpu)) if gpu.size else None),
                (x.get("gpu_load"),), (x.get("gpu_temperature"),)]

    def pick_compare(self, k):
        # The rows after "none" list the runs newest first.
        if self.benches:
            self.b_cmp_i = len(self.benches) - k if k else None
            self.show_bench(self.bench_sel)

    def show_bench(self, i):
        if not self.benches or i is None or not 0 <= i < len(self.benches):
            return
        if self.b_cmp_i is not None and not 0 <= self.b_cmp_i < len(self.benches):
            self.b_cmp_i = None
        if self.b_cmp_i == i:
            # Picking the compared run moves the compare to the run before it.
            self.b_cmp_i = i - 1 if i else None
        self.bench_sel = i
        b = self.benches[i]
        other = self.benches[self.b_cmp_i] if self.b_cmp_i is not None else None
        self.b_cmp.setCurrentIndex(len(self.benches) - self.b_cmp_i if other else 0)
        d = self.bench_detail(b)
        s, x = b.stats, d.get("extra", {})
        fps = d.get("fps", np.array([]))
        self.b_title.setText(f"Run from {nice_date(b.when)}")

        def text(vals, digits, unit):
            if all(v is None for v in vals):
                return "n/a"
            return " / ".join("n/a" if v is None else f"{v:.{digits}f}" for v in vals) + unit

        def delta(vals, ovals, digits, unit, better):
            # The difference of the shown values, so "same" never sits beside two different numbers.
            parts, signs = [], set()
            for v, o in zip(vals, ovals):
                dv = None if v is None or o is None else round(v, digits) - round(o, digits)
                if dv is None or abs(dv) < 0.5 / 10 ** digits:
                    parts.append("n/a" if dv is None else "same")
                else:
                    parts.append(f"{dv:+.{digits}f}")
                    signs.add((dv > 0) == (better > 0))
            diff = parts[0] if len(set(parts)) == 1 and parts[0] in ("n/a", "same") else " / ".join(parts)
            tone = "ok" if better and signs == {True} else "bad" if better and signs == {False} else "muted"
            return f"vs {text(ovals, digits, unit)} · {diff}", tone

        figs = self.bench_figures(b)
        ofigs = self.bench_figures(other) if other else None
        for k, ((vl, cl, dl), (digits, unit, caption, better)) in enumerate(zip(self.b_tiles, BENCH_TILES)):
            vl.setText(text(figs[k], digits, unit))
            cl.setText(caption)
            if other:
                t, tone = delta(figs[k], ofigs[k], digits, unit, better)
                dl.setText(t)
                restyle(dl, tone=tone)
            dl.setVisible(other is not None)
        series = [{"y": fps, "color": COL["accent"], "label": "FPS", "unit": " fps", "axis": "l", "width": 2}]
        if other:
            ofps = self.bench_detail(other).get("fps", np.array([]))
            series.append({"y": ofps, "color": COL["info"], "label": f"FPS {nice_date(other.when)}", "unit": " fps",
                           "axis": "l", "width": 1.5, "dash": (4, 3)})
        if self.b_cpu.isChecked():
            series.append({"y": d.get("cpu", np.array([])), "color": COL["ok"], "label": "CPU ms", "unit": " ms", "axis": "r", "width": 1.5})
        if self.b_gpu.isChecked():
            series.append({"y": d.get("gpu", np.array([])), "color": COL["violet"], "label": "GPU ms", "unit": " ms", "axis": "r", "width": 1.5})
        self.b_chart.plot(series, s.get("RawFrameTimes"))
        rows = list(d.get("system", {}).items())
        if x.get("vram_used") is not None:
            rows.append(("vram", f"{x['vram_used']:.2f} of {x.get('sys_vram_total') or 0:.2f} GB"))
        if x.get("ram_used") is not None:
            rows.append(("ram", f"{x['ram_used']:.2f} of {x.get('sys_ram_total') or 0:.2f} GB"))
        if s.get("LoadingTime") is not None:
            rows.append(("loading time", f"{s['LoadingTime']:,.0f} (unit not documented, likely ms)"))
        self.fill_kv(self.b_sys_lay, "System", [(k.replace("_", " ").upper() if len(k) <= 4 else k.capitalize(), v) for k, v in rows])
        self.fill_kv(self.b_set_lay, *self.bench_settings(b, other))
        self.b_open.setEnabled(bool(b.html))
        self.b_hist.plot([(nice_date(o.when), o.stats.get("Fps")) for o in self.benches], i, self.b_cmp_i)
        for j, item in self.b_items:
            item.set_selected(j == i)

    def bench_settings(self, b, other):
        # Rows that differ from the compare run read "old → new" by run date.
        mine = [(k, v.strip()) for k, v in self.bench_detail(b).get("settings", [])]
        theirs = dict((k, v.strip()) for k, v in self.bench_detail(other).get("settings", [])) if other else {}
        if not mine:
            return "Settings used", [("", "Open the full report for details.")]
        if not theirs:
            return "Settings used", mine
        rows, n, ours = [], 0, dict(mine)
        for k in list(ours) + [k for k in theirs if k not in ours]:
            a, c = ours.get(k), theirs.get(k)
            if a == c:
                rows.append((k, a))
                continue
            n += 1
            old, new = (c, a) if b.when > other.when else (a, c)
            rows.append((k, f"{old or 'not recorded'} → {new or 'not recorded'}", "accent"))
        when = nice_date(other.when)
        return (f"Settings used · {n} {'differs' if n == 1 else 'differ'} from {when}" if n
                else f"Same recorded settings as {when}"), rows

    def fill_kv(self, lay, title, rows):
        # A row may add a tone for its value.
        clear_layout(lay)
        lay.addWidget(label(title.upper(), "caps", "faint", wrap=True, margins=(0, 0, 0, 4)))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 120)
        for i, (k, v, *tone) in enumerate(rows):
            grid.addWidget(label(k, "small", "muted"), i, 0, Qt.AlignTop)
            grid.addWidget(label(v.strip(), "small", *tone, wrap=True), i, 1)
        lay.addLayout(grid)
        lay.addStretch()

    def open_bench_report(self):
        i, benches = self.bench_sel, self.benches or []
        if i is not None and i < len(benches) and benches[i].html:
            self.open_path(benches[i].html)

    def open_bench_folder(self):
        folder = self.root_dir / "Benchmark"
        self.open_path(folder if folder.is_dir() else self.root_dir)

    def watch_bench(self):
        # Runs on its own timer. A new run is read once its HTML report exists, or after 3 s.
        folder = self.root_dir / "Benchmark"
        names = bench_names(folder)
        if folder != self._bench_dir:
            self._bench_dir, self._bench_names, self._bench_wait = folder, names, {}
            return
        if names.keys() == self._bench_names.keys() and not self._bench_wait:
            return
        now = time.monotonic()
        added = names.keys() - self._bench_names.keys()
        for n in added:
            if names[n][1] == "ini" and n not in self._bench_wait:
                self._bench_wait[n] = now
                QTimer.singleShot(3050, self.watch_bench)
        htmls = [(t, n) for n, (t, kind) in names.items() if kind == "html"]
        for n, t0 in list(self._bench_wait.items()):
            if n not in names or bench_html(names[n][0], htmls) or now - t0 >= 3:
                del self._bench_wait[n]
        if self._bench_wait:
            return
        self._bench_names = names
        self.bench_arrived({n for n in added if names[n][1] == "ini"})

    def bench_arrived(self, new):
        benches = scan_benchmarks(self.root_dir)
        runs = [j for j, b in enumerate(benches) if b.ini.name in new]
        if self.tabs.get() == "Benchmarks":
            sel = self.benches[self.bench_sel] if self.benches and self.bench_sel is not None else None
            newest = bool(runs) and runs[-1] == len(benches) - 1
            self.refresh_bench(None if newest or sel is None else sel.when)
        else:
            self.benches = None
        if not runs:
            return
        j = runs[-1]
        fps, prev = benches[j].stats.get("Fps"), benches[j - 1].stats.get("Fps") if j else None
        head = "New benchmark run" if len(runs) == 1 else f"{len(runs)} new benchmark runs, the newest"
        if not fps:
            text = f"{head} from {nice_date(benches[j].when)}."
        elif not prev:
            text = f"{head}: {fps:.0f} fps."
        else:
            pct = (fps - prev) / prev * 100
            than = "the same as" if round(pct, 1) == 0 else f"{abs(pct):.1f}% {'more' if pct > 0 else 'less'} than"
            text = f"{head}: {fps:.0f} fps, {than} the previous run."
        self.toast(text, "info")

    # tabs, polling, close

    def on_tab(self):
        name = self.tabs.get()
        self.store.data["settings"]["last_tab"] = name
        if name in self.stale:
            self.stale.discard(name)
            {"Profile": self.build_profile_tab, "Transfer": self.refresh_transfer, "Backups": self.refresh_backups}[name]()
        if name == "Benchmarks" and self.benches is None:
            self.refresh_bench()
        QTimer.singleShot(0, self.place_toast)

    def set_running(self, running, force=False):
        if running == self.running and not force:
            return
        self.running = running
        if running:
            self.h_game.set("Siege is running · saving is paused", COL["bad_dim"], COL["bad"])
        else:
            self.h_game.set("Siege is closed", COL["ok_dim"], COL["ok"])
        self.update_dirty()
        self.update_transfer_count()
        if self.tabs.get() == "Backups":
            self.refresh_backups()
        else:
            self.stale.add("Backups")

    def _poll_game(self):
        running = siege_running()
        try:
            self.game_state.emit(running)
        except (RuntimeError, TypeError):
            pass

    def _on_game(self, running):
        self._polling = False
        self.set_running(running)

    def poll(self):
        now = time.monotonic()
        if not self._polling and now - self._last_poll > 4:
            self._polling, self._last_poll = True, now
            threading.Thread(target=self._poll_game, daemon=True).start()
        if self.current:
            m = self.current.mtime
            if m != self.file_mtime:
                self.file_mtime = m
                self.commit_focus()
                self.backup_disk_copy()
                if self.pending:
                    self.merge_outside_change()
                else:
                    self.reload_current()
                    self.toast("The settings file changed on disk and was reloaded.", "info")

    def closeEvent(self, e):
        self.commit_focus()
        if not self.confirm_discard():
            e.ignore()
            return
        self.poll_timer.stop()
        self.bench_timer.stop()
        self.store.data["settings"]["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        try:
            self.store.save()
        except OSError:
            pass
        e.accept()


def main():
    parser = argparse.ArgumentParser(prog="r6_siege_settings_manager")
    parser.add_argument("--root", help="folder that holds the profile UUID folders")
    args = parser.parse_args()
    app = qt_app()
    win = App(args.root)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
