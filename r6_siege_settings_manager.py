# /// script
# requires-python = ">=3.11"
# dependencies = ["customtkinter>=5.2.2", "numpy>=1.26"]
# ///
import argparse
import codecs
import ctypes
import json
import math
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from types import SimpleNamespace

import customtkinter as ctk
import numpy as np

APP_NAME = "R6 Siege Settings Manager"
__version__ = "0.2"
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


if sys.platform == "win32":
    from ctypes import wintypes
    _send_message = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)(
        ("SendMessageW", ctypes.windll.user32))
    _redraw_window = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)(
        ("RedrawWindow", ctypes.windll.user32))

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
    ("Graphics", "Quality"): "Labels follow the in-game menu order. The index mapping comes from community files, "
                             "not from Ubisoft.",
    ("Mouse", "Aim down sights"): "At 50, every sight matches hipfire by monitor distance.",
}
DEG_PER_COUNT = 0.00572957795

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
        "Display (0) comes from the file's own comment and is not in the game menu.",
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
        "Custom keeps the values below. Only the spellings Custom and High are confirmed.",
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
    add(G, "Quality", "CUSTOM_QUALITY.Atmospheric", "Atmospheric", "number", "Custom files hold -1. Meaning not documented.")
    up = "Upscaling"
    add(G, up, "CUSTOM_QUALITY.Upscaler", "Upscaling technique", "choice",
        "Off and Temporal are confirmed. The order of DLSS and the two FSR versions is inferred.",
        choices={"0": "Off", "1": "Temporal", "2": "NVIDIA DLSS", "3": "AMD FSR 1.0", "4": "AMD FSR 2.0"})
    add(G, up, "CUSTOM_QUALITY.TemporalUpscalerMode", "Upscaling mode", "choice",
        choices={"0": "Fixed render scaling", "1": "Adaptive render scaling"})
    add(G, up, "CUSTOM_QUALITY.RenderScalingFactor", "Render scaling", "range", "Used in fixed mode.",
        lo=25, hi=100, step=5, scale=5, offset=25, unit="%")
    add(G, up, "CUSTOM_QUALITY.AdaptiveRenderScalingTargetFPS", "Adaptive render scaling target", "range",
        "Used in adaptive mode. The range is read from the menu slider and may be slightly off.",
        lo=30, hi=330, step=5, scale=5, offset=5, unit=" fps")
    add(G, up, "CUSTOM_QUALITY.AntiAliasing", "Anti-aliasing mode", "choice",
        "T-AA is confirmed. FXAA and Off are inferred.", choices={"0": "T-AA", "1": "FXAA", "2": "Off"})
    add(G, up, "CUSTOM_QUALITY.Sharpness", "Anti-aliasing sharpness", "range", lo=0, hi=100, step=5, scale=5)
    modes = ["Performance", "Balanced", "Quality", "Ultra Performance"]
    for key, label, names in [("DLSSPerfQual", "DLSS mode", modes), ("FSR2PerfQual", "FSR 2.0 mode", modes),
                              ("FSRPerfQual", "FSR 1.0 mode", modes[:3] + ["Ultra Quality"])]:
        add(G, up, f"CUSTOM_QUALITY.{key}", label, "choice",
            "The game keeps this value while the upscaler is not in use. The mode names are inferred from the "
            "NVIDIA DLSS order.", choices={str(i): n for i, n in enumerate(names)})
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
        "Meaning not documented. The two sliders above are the in-game controls.", lo=1, hi=100)
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
        "Uses http_proxy, https_proxy, all_proxy and no_proxy for HTTP calls.")

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

    def backup(self, uuid, src, reason):
        d = self.backup_dir(uuid)
        d.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")[:48] or "backup"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest, n = d / f"{stamp}_{slug}.ini", 1
        while dest.exists():
            n += 1
            dest = d / f"{stamp}_{slug}-{n}.ini"
        shutil.copyfile(src, dest)
        for old in self.backups(uuid)[MAX_BACKUPS:]:
            old.path.unlink(missing_ok=True)
        return dest

    def backups(self, uuid):
        out = []
        for p in self.backup_dir(uuid).glob("*.ini"):
            m = re.match(r"(\d{8}-\d{6})_(.*)\.ini$", p.name)
            try:
                when = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S") if m else datetime.fromtimestamp(p.stat().st_mtime)
            except (ValueError, OSError):
                continue
            out.append(Backup(p, when, (m.group(2) if m else p.stem).replace("-", " ")))
        return sorted(out, key=lambda b: (b.when, b.path.name), reverse=True)


def write_profile(store, profile, ini, reason):
    store.backup(profile.uuid, profile.path, reason)
    locked = profile.locked
    if locked:
        set_locked(profile.path, False)
    try:
        tmp = profile.path.with_name(profile.path.name + ".tmp")
        tmp.write_bytes(ini.to_bytes())
        os.replace(tmp, profile.path)
    finally:
        if locked:
            set_locked(profile.path, True)


@dataclass
class Bench:
    when: datetime
    ini: Path
    html: Path | None
    stats: dict
    detail: dict | None = None


def scan_benchmarks(root):
    folder = Path(root) / "Benchmark"
    inis, htmls = [], []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    for p in entries:
        m = BENCH_RE.match(p.name)
        if m:
            when = datetime(*map(int, m.groups()[:6]))
            (inis if m.group(7).lower() == "ini" else htmls).append((when, p))
    out = []
    for when, p in sorted(inis):
        html = next((h for t, h in htmls if 0 <= (t - when).total_seconds() <= 15), None)
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


def apply_theme():
    ctk.set_appearance_mode("dark")
    t = ctk.ThemeManager.theme

    def put(widget, **kw):
        for k, v in kw.items():
            if widget in t and k in t[widget]:
                t[widget][k] = v if isinstance(v, (int, float)) else [v, v]

    put("CTk", fg_color=COL["bg"])
    put("CTkToplevel", fg_color=COL["bg"])
    put("CTkFrame", fg_color=COL["panel"], top_fg_color=COL["card"], border_color=COL["line"])
    put("CTkButton", fg_color=COL["card_hi"], hover_color=COL["line"], border_color=COL["line"], text_color=COL["text"],
        corner_radius=8)
    put("CTkLabel", text_color=COL["text"])
    put("CTkEntry", fg_color=COL["bg"], border_color=COL["line"], text_color=COL["text"],
        placeholder_text_color=COL["faint"], corner_radius=8, border_width=1)
    put("CTkCheckBox", fg_color=COL["accent"], hover_color=COL["accent_hi"], border_color=COL["faint"],
        checkmark_color=COL["ink"], text_color=COL["text"], corner_radius=6, border_width=2)
    put("CTkSwitch", fg_color=COL["line"], progress_color=COL["accent"], button_color=COL["text"],
        button_hover_color="#ffffff", text_color=COL["text"])
    put("CTkSlider", fg_color=COL["line"], progress_color=COL["accent"], button_color=COL["text"],
        button_hover_color=COL["accent_hi"])
    put("CTkSegmentedButton", fg_color=COL["card_hi"], selected_color=COL["accent_deep"],
        selected_hover_color=COL["accent"], unselected_color=COL["card_hi"], unselected_hover_color=COL["line"],
        text_color=COL["text"], corner_radius=8)
    put("CTkOptionMenu", fg_color=COL["card_hi"], button_color=COL["line"], button_hover_color=COL["faint"],
        text_color=COL["text"], corner_radius=8)
    put("CTkComboBox", fg_color=COL["bg"], border_color=COL["line"], button_color=COL["line"],
        button_hover_color=COL["faint"], text_color=COL["text"], corner_radius=8, border_width=1)
    put("DropdownMenu", fg_color=COL["card_hi"], hover_color=COL["line"], text_color=COL["text"])
    put("CTkScrollbar", button_color=COL["line"], button_hover_color=COL["faint"])
    put("CTkTextbox", fg_color=COL["bg"], border_color=COL["line"], text_color=COL["text"],
        scrollbar_button_color=COL["line"], scrollbar_button_hover_color=COL["faint"], border_width=1)
    put("CTkScrollableFrame", label_fg_color=COL["card"])


def set_primary(button, enabled):
    button.configure(state="normal" if enabled else "disabled", fg_color=COL["accent"] if enabled else COL["card_hi"],
                     text_color_disabled=COL["faint"])


def descendants(widget):
    out = [widget]
    for child in widget.winfo_children():
        out += descendants(child)
    return out


def bind_tree(widget, sequence, callback):
    for w in descendants(widget):
        tk.Misc.bind(w, sequence, callback, "+")


def tk_font(widget, font):
    s = ctk.ScalingTracker.get_widget_scaling(widget)
    return font.cget("family"), -round(abs(font.cget("size")) * s), font.cget("weight")


def scroll_ancestor(widget, event):
    w = widget
    while w is not None and not isinstance(w, ctk.CTkScrollableFrame):
        w = w.master
    if w is not None and w._parent_canvas.yview() != (0.0, 1.0):
        w._parent_canvas.yview("scroll", -int(event.delta / 6), "units")


class Hover:
    def hover_bind(self, normal, hot):
        self._hover_colors = (normal, hot)
        tk.Frame.bind(self, "<Enter>", lambda e: self.configure(fg_color=self._hover_colors[1]), "+")
        tk.Frame.bind(self, "<Leave>", self._hover_leave, "+")

    def _hover_leave(self, _event):
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except (KeyError, tk.TclError):
            w = None
        if w is None or not str(w).startswith(str(self)):
            self.configure(fg_color=self._hover_colors[0])


class RepeatButton(ctk.CTkButton):
    def __init__(self, master, text, action, **kw):
        super().__init__(master, text=text, width=30, height=30, corner_radius=8, fg_color=COL["card_hi"],
                         hover_color=COL["line"], text_color=COL["text"], command=None, **kw)
        self.action, self.job = action, None
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _press(self, _e):
        if self.cget("state") == "disabled":
            return
        self.action()
        self.job = self.after(380, self._repeat)

    def _repeat(self):
        self.action()
        self.job = self.after(55, self._repeat)

    def _release(self, _e):
        if self.job:
            self.after_cancel(self.job)
            self.job = None


class Control(ctk.CTkFrame):
    def __init__(self, row, app, spec):
        super().__init__(row, fg_color="transparent")
        self.row, self.app, self.spec = row, app, spec

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
        self.var = tk.StringVar(value=self.value)
        self.state = ctk.CTkLabel(self, text="", width=40, font=app.f.small, text_color=COL["muted"], anchor="e")
        self.state.pack(side="left", padx=(0, 10))
        self.switch = ctk.CTkSwitch(self, text="", variable=self.var, onvalue="1", offvalue="0", width=48,
                                    switch_width=46, switch_height=24, command=self._flip)
        self.switch.pack(side="left")
        self.state.bind("<Button-1>", lambda e: self.switch.toggle())
        self.load()

    def _flip(self):
        self.emit(self.var.get())
        self.state.configure(text=self.spec.choices.get(self.var.get(), ""))

    def load(self):
        v = self.value
        self.var.set(v)
        self.state.configure(text=self.spec.choices.get(v, v))


class ChoiceControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.options = dict(self.choices())
        self._ensure(self.value, rebuild=False)
        labels = list(self.options.values())
        if len(labels) <= 6 and sum(map(len, labels)) <= 42:
            self.widget = ctk.CTkSegmentedButton(self, values=labels, command=self._pick, font=app.f.body, height=32)
        else:
            self.widget = ctk.CTkOptionMenu(self, values=labels, command=self._pick, width=250, height=32,
                                            dynamic_resizing=False, font=app.f.body, dropdown_font=app.f.body)
        self.widget.pack()
        self.load()

    def choices(self):
        return self.spec.choices or {}

    def _ensure(self, v, rebuild=True):
        if v is not None and v not in self.options:
            self.options[v] = f"{v} (custom)"
            if rebuild:
                self.widget.configure(values=list(self.options.values()))

    def _pick(self, label):
        self.emit(next(v for v, l in self.options.items() if l == label))

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
        self.widget.configure(values=list(self.options.values()))
        self.widget.set(self.options.get(self.value, ""))


class ComboControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.box = ctk.CTkComboBox(self, values=list(spec.choices or {}), width=220, height=32, font=app.f.body,
                                   dropdown_font=app.f.body, command=lambda v: self.commit())
        self.box.pack()
        self.box.bind("<Return>", self.commit)
        self.box.bind("<FocusOut>", self.commit)
        self.load()

    def commit(self, _e=None):
        v = self.box.get().strip()
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
        self.minus = RepeatButton(self, "−", lambda: self.nudge(-1), font=self.app.f.body)
        self.minus.pack(side="left")
        steps = max(1, round((self.hi - self.lo) / self.spec.step))
        self.slider = ctk.CTkSlider(self, from_=self.lo, to=self.hi, number_of_steps=steps, width=self.slider_width,
                                    height=18, command=self._slide)
        self.slider.pack(side="left", padx=6)
        self.slider._canvas.bind("<MouseWheel>", self._slider_wheel)
        self.plus = RepeatButton(self, "+", lambda: self.nudge(1), font=self.app.f.body)
        self.plus.pack(side="left")
        self.build_entry()

    def build_entry(self):
        self.entry = ctk.CTkEntry(self, width=86 if self.spec.digits > 2 else 70, height=30, justify="center",
                                  font=self.app.f.mono)
        self.entry.pack(side="left", padx=(8, 0))
        for seq, fn in (("<Return>", self.commit), ("<FocusOut>", self.commit), ("<Up>", lambda e: self.nudge(1)),
                        ("<Down>", lambda e: self.nudge(-1)), ("<MouseWheel>", self._entry_wheel)):
            self.entry.bind(seq, fn)
        unit = self.spec.unit.strip()
        self.unit = ctk.CTkLabel(self, text=unit, width=26 if unit else 0, font=self.app.f.small,
                                 text_color=COL["muted"], anchor="w")
        self.unit.pack(side="left", padx=(4, 0))

    def _slider_wheel(self, event):
        if event.state & 0x0004:
            self.nudge(1 if event.delta > 0 else -1)
        else:
            scroll_ancestor(self, event)
        return "break"

    def _entry_wheel(self, event):
        self.nudge(1 if event.delta > 0 else -1)
        return "break"

    def clamp(self, v):
        return min(self.hi, max(self.lo, v))

    def snap(self, v):
        step = self.spec.step
        return round(self.lo + round((v - self.lo) / step) * step, 6)

    def to_file(self, v):
        v = (v - self.spec.offset) / self.spec.scale
        return f"{v:.6f}" if self.float_style else str(int(round(v)))

    def show(self, v):
        self.shown = f"{v:.{self.spec.digits}f}"
        self.entry.delete(0, "end")
        self.entry.insert(0, self.shown)

    def current(self):
        v = num(self.value)
        return self.lo if v is None else self.spec.offset + self.spec.scale * v

    def _slide(self, v):
        v = self.clamp(self.snap(v))
        self.show(v)
        self.emit(self.to_file(v))

    def nudge(self, direction):
        v = self.clamp(self.snap(self.current() + direction * self.spec.step))
        self.set_shown(v)
        self.emit(self.to_file(v))

    def commit(self, _e=None):
        text = self.entry.get().strip()
        if text == self.shown:
            return
        v = num(re.sub(r"[^\d.\-]", "", text))
        if v is None:
            self.load()
            return
        v = self.clamp(self.snap(v) if self.spec.scale != 1 else v)
        self.set_shown(v)
        self.emit(self.to_file(v))

    def set_shown(self, v):
        if hasattr(self, "slider"):
            self.slider.set(v)
        self.show(v)

    def load(self):
        self.set_shown(self.current())


class NumberControl(RangeControl):
    def bounds(self, v):
        return -1e12, 1e12

    def build(self):
        self.minus = RepeatButton(self, "−", lambda: self.nudge(-1), font=self.app.f.body)
        self.minus.pack(side="left")
        self.build_entry()
        self.unit.pack_forget()
        self.plus = RepeatButton(self, "+", lambda: self.nudge(1), font=self.app.f.body)
        self.plus.pack(side="left", padx=(6, 0))
        self.unit.pack(side="left", padx=(4, 0))

    def snap(self, v):
        return round(v, 6)


class LimitControl(RangeControl):
    def bounds(self, v):
        lo, hi = self.spec.lo, self.spec.hi
        if v is not None and v >= lo:
            hi = max(hi, v)
        return lo, hi

    def build(self):
        self.var = tk.StringVar()
        self.switch = ctk.CTkSwitch(self, text="", variable=self.var, onvalue="on", offvalue="off", width=48,
                                    switch_width=46, switch_height=24, command=self._flip)
        self.switch.pack(side="left", padx=(0, 10))
        self.last = 144.0
        super().build()

    def _flip(self):
        self.emit(self.to_file(self.last) if self.var.get() == "on" else "0")
        self.load()

    def current(self):
        v = num(self.value) or 0
        return v if v >= self.lo else self.last

    def load(self):
        v = num(self.value) or 0
        active = v >= self.lo
        if active:
            self.last = v
        self.var.set("on" if active else "off")
        state = "normal" if active else "disabled"
        for w in (self.slider, self.minus, self.plus):
            w.configure(state=state)
        self.slider.set(self.current())
        if active:
            self.entry.configure(state="normal")
            self.show(v)
        else:
            self.entry.configure(state="normal")
            self.entry.delete(0, "end")
            self.entry.insert(0, "Off")
            self.shown = "Off"
            self.entry.configure(state="disabled")

    def _slide(self, v):
        self.last = self.clamp(self.snap(v))
        super()._slide(v)


class TextControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.entry = ctk.CTkEntry(self, width=260, height=30, font=app.f.mono)
        self.entry.pack()
        self.entry.bind("<Return>", self.commit)
        self.entry.bind("<FocusOut>", self.commit)
        self.load()

    def commit(self, _e=None):
        if self.entry.get() != self.value:
            self.emit(self.entry.get())

    def load(self):
        self.entry.delete(0, "end")
        self.entry.insert(0, self.value or "")


class InfoControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.label = ctk.CTkLabel(self, text="", font=app.f.mono, text_color=COL["muted"])
        self.label.pack()
        self.load()

    def load(self):
        self.label.configure(text=self.value or "")


class ResolutionControl(Control):
    CUSTOM = "Custom..."

    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.menu = ctk.CTkOptionMenu(self, values=[], command=self._pick, width=190, height=32,
                                      dynamic_resizing=False, font=app.f.body, dropdown_font=app.f.body)
        self.menu.pack(side="left")
        self.aspect = ctk.CTkLabel(self, text="", width=56, font=app.f.small, text_color=COL["muted"])
        self.aspect.pack(side="left", padx=(10, 0))
        self.load()

    def size(self):
        return self.app.value(self.spec.sec, "ResolutionWidth") or "0", self.app.value(self.spec.sec, "ResolutionHeight") or "0"

    @staticmethod
    def label(w, h):
        return "Auto" if (w, h) == ("0", "0") else f"{w} × {h}"

    def load(self):
        w, h = self.size()
        modes = sorted({(mw, mh) for mw, mh, _ in self.app.modes}, key=lambda m: (m[0] * m[1], m[0]), reverse=True)
        opts = ["Auto"] + [f"{mw} × {mh}" for mw, mh in modes]
        cur = self.label(w, h)
        if cur not in opts:
            opts.insert(1, cur)
        self.menu.configure(values=opts + [self.CUSTOM])
        self.menu.set(cur)
        self.aspect.configure(text=aspect_name(int(num(w) or 0), int(num(h) or 0)))

    def _pick(self, label):
        if label == "Auto":
            w, h = "0", "0"
        else:
            if label == self.CUSTOM:
                label = ctk.CTkInputDialog(title="Custom resolution", text="Width x height, for example 1728x1080").get_input() or ""
            m = re.match(r"\s*(\d{3,5})\s*[x×*, ]\s*(\d{3,5})\s*$", label)
            if not m:
                self.load()
                return
            w, h = m.groups()
        self.emit(w, "ResolutionWidth")
        self.emit(h, "ResolutionHeight")
        self.load()


class RefreshControl(Control):
    def __init__(self, row, app, spec):
        super().__init__(row, app, spec)
        self.menu = ctk.CTkOptionMenu(self, values=[], command=self._pick, width=190, height=32,
                                      dynamic_resizing=False, font=app.f.body, dropdown_font=app.f.body)
        self.menu.pack()
        self.load()

    def load(self):
        self.map = {"Auto": 0.0}
        for hz in sorted({f for _, _, f in self.app.modes if f > 1}, reverse=True):
            self.map[f"{hz} Hz"] = float(hz)
        v = num(self.value)
        cur = "Auto" if not v else next((k for k, x in self.map.items() if abs(x - v) < 1e-6), f"{v:g} Hz")
        self.map.setdefault(cur, v or 0.0)
        self.menu.configure(values=list(self.map))
        self.menu.set(cur)

    def _pick(self, label):
        v = self.map[label]
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


class Pill(ctk.CTkLabel):
    def __init__(self, master, text, fg, color, font):
        super().__init__(master, text=f" {text} ", fg_color=fg, text_color=color, corner_radius=6, height=20, font=font)


class SettingRow(Hover, ctk.CTkFrame):
    # Text and spacers are plain Tk widgets: a page holds hundreds of rows, and each customtkinter widget adds a
    # canvas and extra windows that Windows has to paint whenever the page is shown.
    def __init__(self, master, app, spec):
        super().__init__(master, fg_color=COL["card"], corner_radius=8)
        self.app, self.spec, self.bg, self.plain, self.dirty = app, spec, COL["card"], [], False
        self.keys = [(spec.sec, spec.key)] + ([(spec.sec, "ResolutionHeight")] if spec.kind == "resolution" else [])
        s = ctk.ScalingTracker.get_widget_scaling(self)
        px = lambda v: round(v * s)
        font = lambda f: tk_font(self, f)

        def plain(cls, parent, **kw):
            w = cls(parent, bg=self.bg, **kw)
            self.plain.append(w)
            return w

        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(3, minsize=px(40))
        self.bar = plain(tk.Frame, self, width=px(4))
        self.bar.grid(row=0, column=0, sticky="ns", padx=(px(4), 0), pady=px(8))
        text = plain(tk.Frame, self)
        text.grid(row=0, column=1, sticky="w", padx=(px(10), px(12)), pady=px(7))
        title = plain(tk.Frame, text)
        title.pack(anchor="w")
        plain(tk.Label, title, text=spec.label, font=font(app.f.body), fg=COL["text"]).pack(side="left")
        key = plain(tk.Label, title if len(spec.label) + len(spec.key) <= 44 else text, text=spec.key,
                    font=font(app.f.tiny_mono), fg=COL["faint"])
        key.pack(side="left", padx=(px(8), 0)) if key.master is title else key.pack(anchor="w")
        if spec.ini_only:
            self.pill = Pill(title, "NOT IN GAME MENU", COL["accent_dim"], COL["accent"], app.f.tiny)
            self.pill.pack(side="left", padx=(8, 0))
        help_text = spec.help or ("" if spec.choices else app.hint(spec))
        if help_text:
            plain(tk.Label, text, text=help_text, font=font(app.f.small), fg=COL["muted"], justify="left",
                  wraplength=px(330)).pack(anchor="w")
        self.control = make_control(self, app, spec)
        self.control.grid(row=0, column=2, sticky="e", padx=4, pady=6)
        self.reset = ctk.CTkButton(self, text="↺", width=30, height=30, corner_radius=8, fg_color="transparent",
                                   hover_color=COL["line"], text_color=COL["accent"], font=app.f.body,
                                   command=lambda: app.reset_keys(self.keys))
        self.hover_bind(COL["card"], COL["card_hi"])
        bind_tree(self, "<Button-3>", self._menu)
        for k in self.keys:
            app.rows[k].append(self)
        self.mark()

    def configure(self, **kw):
        if "fg_color" in kw:
            self.bg = kw["fg_color"]
            for w in getattr(self, "plain", ()):
                if w is not self.bar or not self.dirty:
                    w.configure(bg=self.bg)
            if getattr(self, "pill", None):
                self.pill.configure(bg_color=self.bg)
        super().configure(**kw)

    def load(self):
        self.control.load()

    def mark(self):
        self.dirty = any(k in self.app.pending for k in self.keys)
        self.bar.configure(bg=COL["accent"] if self.dirty else self.bg)
        if self.dirty:
            self.reset.grid(row=0, column=3, padx=(2, 8))
        else:
            self.reset.grid_remove()

    def _menu(self, event):
        self.app.row_menu(self, event)


class NavItem(ctk.CTkFrame):
    def __init__(self, master, app, name, command):
        super().__init__(master, fg_color="transparent", height=38)
        self.btn = ctk.CTkButton(self, text=name, anchor="w", fg_color="transparent", hover_color=COL["card_hi"],
                                 text_color=COL["muted"], font=app.f.body, height=38, corner_radius=8, command=command)
        self.btn.pack(fill="x")
        self.badge = ctk.CTkLabel(self, text="", width=24, height=20, corner_radius=10, fg_color=COL["accent"],
                                  text_color=COL["ink"], font=app.f.tiny)
        self.badge.bind("<Button-1>", lambda e: command())

    def set_active(self, on):
        self.btn.configure(fg_color=COL["card_hi"] if on else "transparent",
                           text_color=COL["text"] if on else COL["muted"])

    def set_count(self, n):
        if n:
            self.badge.configure(text=str(n))
            self.badge.place(relx=1, rely=0.5, x=-10, anchor="e")
        else:
            self.badge.place_forget()


class ProfileCard(Hover, ctk.CTkFrame):
    def __init__(self, master, app, profile, selected):
        meta = app.store.meta(profile.uuid)
        base = COL["card_hi"] if selected else COL["card"]
        super().__init__(master, fg_color=base, corner_radius=10, border_width=2 if selected else 0,
                         border_color=meta["color"])
        ctk.CTkFrame(self, width=4, height=1, corner_radius=2, fg_color=meta["color"]).pack(side="left", fill="y", padx=(8, 0), pady=10)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=app.store.name(profile.uuid), font=app.f.bold, anchor="w", height=22).pack(side="left")
        if profile.locked:
            Pill(top, "LOCKED", COL["bad_dim"], COL["bad"], app.f.tiny).pack(side="right")
        if meta["tags"]:
            tags = ctk.CTkFrame(body, fg_color="transparent")
            tags.pack(fill="x", pady=(3, 1))
            for t in meta["tags"][:3]:
                Pill(tags, t, COL["line"], COL["text"], app.f.tiny).pack(side="left", padx=(0, 4))
            if len(meta["tags"]) > 3:
                ctk.CTkLabel(tags, text=f"+{len(meta['tags']) - 3}", font=app.f.tiny, text_color=COL["muted"]).pack(side="left")
        when = datetime.fromtimestamp(profile.mtime) if profile.mtime else None
        sub = f"{profile.uuid[:8]}" + (f"  ·  {nice_date(when)}" if when else "")
        ctk.CTkLabel(body, text=sub, font=app.f.tiny_mono, text_color=COL["faint"], anchor="w", height=16).pack(fill="x")
        self.hover_bind(base, COL["card_hi"])
        bind_tree(self, "<Button-1>", lambda e: app.select_profile(profile))
        bind_tree(self, "<Button-3>", lambda e: app.profile_menu(profile, e))


class LineChart(tk.Canvas):
    def __init__(self, master, height=300):
        super().__init__(master, height=height, bg=COL["card"], highlightthickness=0, bd=0)
        self.series, self.duration = [], 0.0
        self.font = ("Segoe UI", 9)
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def plot(self, series, duration):
        self.series = [s for s in series if len(s["y"])]
        self.duration = duration or 0.0
        self.draw()

    def frame(self):
        right = any(s["axis"] == "r" for s in self.series)
        return 48, 30, self.winfo_width() - (48 if right else 18), self.winfo_height() - 28

    @staticmethod
    def nice(v):
        if not v or v <= 0:
            return 1.0
        e = 10 ** math.floor(math.log10(v))
        return next(m * e for m in (1, 2, 2.5, 5, 10) if v <= m * e)

    def draw(self):
        self.delete("all")
        x0, y0, x1, y1 = self.frame()
        if x1 - x0 < 60:
            return
        if not self.series:
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="No samples in this report", fill=COL["muted"], font=self.font)
            return
        self.max = {}
        for axis in ("l", "r"):
            ys = [s["y"].max() for s in self.series if s["axis"] == axis]
            self.max[axis] = self.nice(max(ys) * 1.05) if ys else None
        for i in range(5):
            y = y1 - (y1 - y0) * i / 4
            self.create_line(x0, y, x1, y, fill=COL["line"])
            if self.max["l"]:
                self.create_text(x0 - 8, y, text=f"{self.max['l'] * i / 4:g}", anchor="e", fill=COL["muted"], font=self.font)
            if self.max["r"]:
                self.create_text(x1 + 8, y, text=f"{self.max['r'] * i / 4:g}", anchor="w", fill=COL["muted"], font=self.font)
        for i in range(6):
            x = x0 + (x1 - x0) * i / 5
            self.create_text(x, y1 + 14, text=f"{self.duration * i / 5:.0f} s", fill=COL["muted"], font=self.font)
        lx = x0
        for s in self.series:
            n = len(s["y"])
            xs = np.linspace(x0, x1, n)
            ys = y1 - s["y"] / self.max[s["axis"]] * (y1 - y0)
            coords = np.column_stack([xs, ys]).ravel().tolist()
            if n > 1:
                self.create_line(*coords, fill=s["color"], width=s.get("width", 2), dash=s.get("dash"))
            self.create_rectangle(lx, 9, lx + 10, 19, fill=s["color"], outline="")
            t = self.create_text(lx + 16, 14, text=s["label"], anchor="w", fill=COL["text"], font=self.font)
            lx = self.bbox(t)[2] + 18

    def _hover(self, event):
        self.delete("hover")
        if not self.series or not getattr(self, "max", None):
            return
        x0, y0, x1, y1 = self.frame()
        if not x0 <= event.x <= x1:
            return
        frac = (event.x - x0) / (x1 - x0)
        self.create_line(event.x, y0, event.x, y1, fill=COL["faint"], dash=(2, 3), tags="hover")
        lines = [f"{frac * self.duration:.1f} s"]
        for s in self.series:
            i = round(frac * (len(s["y"]) - 1))
            v = s["y"][i]
            y = y1 - v / self.max[s["axis"]] * (y1 - y0)
            self.create_oval(event.x - 4, y - 4, event.x + 4, y + 4, fill=s["color"], outline=COL["card"], width=2, tags="hover")
            lines.append(f"{s['label']}: {v:.1f}{s['unit']}")
        tx = event.x + 14 if event.x < (x0 + x1) / 2 else event.x - 14
        t = self.create_text(tx, y0 + 10, text="\n".join(lines), anchor="nw" if tx > event.x else "ne",
                             fill=COL["text"], font=self.font, tags="hover")
        bx = self.bbox(t)
        r = self.create_rectangle(bx[0] - 8, bx[1] - 6, bx[2] + 8, bx[3] + 6, fill=COL["bg"], outline=COL["line"], tags="hover")
        self.tag_lower(r, t)


class BarChart(tk.Canvas):
    def __init__(self, master, on_pick, height=120):
        super().__init__(master, height=height, bg=COL["card"], highlightthickness=0, bd=0)
        self.on_pick, self.items, self.selected, self.hot = on_pick, [], None, None
        self.font = ("Segoe UI", 8)
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hot(None))
        self.bind("<Button-1>", self._click)

    def plot(self, items, selected):
        self.items, self.selected = items, selected
        self.draw()

    def _slot(self, x):
        w = self.winfo_width() - 16
        if not self.items or w <= 0:
            return None
        i = int((x - 8) / (w / len(self.items)))
        return i if 0 <= i < len(self.items) else None

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width() - 16, self.winfo_height()
        if not self.items or w <= 0:
            return
        top = max(v or 0 for _, v in self.items) or 1
        slot = w / len(self.items)
        for i, (label, v) in enumerate(self.items):
            bh = (h - 34) * (v or 0) / top
            x = 8 + i * slot
            color = COL["accent"] if i == self.selected else (COL["faint"] if i == self.hot else COL["line"])
            self.create_rectangle(x + slot * 0.15, h - 18 - bh, x + slot * 0.85, h - 18, fill=color, outline="")
        label, v = self.items[self.hot if self.hot is not None else (self.selected or 0)]
        self.create_text(8, 10, text=f"{label}  ·  {v:.0f} fps" if v else label, anchor="w", fill=COL["text"], font=self.font)
        self.create_text(8, h - 8, text=self.items[0][0].split(",")[0], anchor="w", fill=COL["faint"], font=self.font)
        self.create_text(w + 8, h - 8, text=self.items[-1][0].split(",")[0], anchor="e", fill=COL["faint"], font=self.font)

    def _set_hot(self, i):
        if i != self.hot:
            self.hot = i
            self.draw()

    def _motion(self, event):
        self._set_hot(self._slot(event.x))

    def _click(self, event):
        i = self._slot(event.x)
        if i is not None:
            self.on_pick(i)


@contextmanager
def frozen(widget):
    # Pauses painting of the widget's window while pages are swapped, then paints the result in one pass.
    hwnd = widget.winfo_id() if sys.platform == "win32" and widget.winfo_viewable() else None
    if hwnd:
        _send_message(hwnd, 0x000B, 0, 0)
    try:
        yield
    finally:
        widget.update_idletasks()
        if hwnd:
            _send_message(hwnd, 0x000B, 1, 0)
            _redraw_window(hwnd, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)
            widget.update_idletasks()


class Tabs(ctk.CTkFrame):
    def __init__(self, master, names, command, font):
        super().__init__(master, fg_color="transparent")
        self.command, self.current = command, names[0]
        self.bar = ctk.CTkSegmentedButton(self, values=list(names), command=self.set, font=font, height=34,
                                          fg_color=COL["bg"], selected_color=COL["accent_deep"],
                                          selected_hover_color=COL["accent"], unselected_color=COL["bg"],
                                          unselected_hover_color=COL["card_hi"], text_color=COL["text"])
        self.bar.pack(anchor="w", pady=(0, 8))
        body = ctk.CTkFrame(self, fg_color=COL["panel"], corner_radius=12)
        body.pack(fill="both", expand=True)
        self.inner = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        self.inner.pack(fill="both", expand=True, padx=12, pady=12)
        self.pages = {n: ctk.CTkFrame(self.inner, fg_color=COL["panel"], corner_radius=0) for n in names}

    def tab(self, name):
        return self.pages[name]

    def get(self):
        return self.current

    def set(self, name):
        old, self.current = self.current, name
        self.bar.set(name)
        with frozen(self.inner):
            if self.command:
                self.command()
            if old != name:
                self.pages[old].place_forget()
            self.pages[name].place(x=0, y=0, relwidth=1, relheight=1)


class App(ctk.CTk):
    TABS = ("Settings", "Profile", "Transfer", "Backups", "Benchmarks")

    def __init__(self, root=None):
        apply_theme()
        super().__init__()
        self.store = Store(APP_DIR)
        cfg = self.store.data["settings"]
        self.root_dir = Path(root or cfg.get("root") or DEFAULT_ROOT)
        self.title(f"{APP_NAME} {__version__}")
        self.geometry(cfg.get("geometry") or "1380x880")
        self.minsize(1150, 720)
        F = ctk.CTkFont
        self.f = SimpleNamespace(
            h1=F("Segoe UI", 22, "bold"), h2=F("Segoe UI", 15, "bold"), bold=F("Segoe UI", 13, "bold"),
            body=F("Segoe UI", 13), small=F("Segoe UI", 12), tiny=F("Segoe UI", 10, "bold"),
            caps=F("Segoe UI", 11, "bold"), mono=F("Consolas", 13), tiny_mono=F("Consolas", 11),
            tile=F("Segoe UI", 24, "bold"))
        self.profiles, self.current, self.ini = [], None, None
        self.orig, self.pending, self.history = {}, {}, []
        self.rows = defaultdict(list)
        self.layout, self.cat_frames, self.nav_items = {}, {}, {}
        self.cat, self.search_frame = None, None
        self.hints, self.datacenters, self._auto, self.watchers = {}, [], {}, []
        self.modes = display_modes()
        self.running, self.file_mtime, self._last_poll = False, None, 0.0
        self._jobs, self._toast, self._poll_q, self._polling = {}, None, queue.Queue(), False
        self.stale = set()
        self.x_extra, self.x_sel, self.x_rows, self.x_vars = {}, set(), {}, {}
        self.benches, self.bench_sel = None, None
        self.write_buttons = []
        self.build()
        self.rescan(select=cfg.get("last_profile"))
        self.tabs.command = self.on_tab
        self.tabs.set(cfg.get("last_tab") if cfg.get("last_tab") in self.TABS else "Settings")
        self.after(400, self.poll)
        self.bind_all("<Control-s>", lambda e: self.save())
        self.bind_all("<Control-z>", lambda e: self.undo())
        self.bind_all("<Control-f>", lambda e: self.focus_search())
        self.bind_all("<F5>", lambda e: self.rescan())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def build(self):
        side = ctk.CTkFrame(self, width=290, fg_color=COL["panel"], corner_radius=0)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        head = ctk.CTkFrame(side, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(18, 10))
        ctk.CTkLabel(head, text="R6 Siege", font=self.f.h1, anchor="w").pack(fill="x")
        ctk.CTkLabel(head, text="Settings Manager", font=self.f.body, text_color=COL["muted"], anchor="w", height=18).pack(fill="x")
        self.p_filter = ctk.CTkEntry(side, placeholder_text="Filter by label, tag or ID", height=34)
        self.p_filter.pack(fill="x", padx=14, pady=(6, 8))
        self.p_filter.bind("<KeyRelease>", lambda e: self.debounce("filter", 150, self.render_sidebar))
        self.plist = ctk.CTkScrollableFrame(side, fg_color="transparent")
        self.plist.pack(fill="both", expand=True, padx=6)
        foot = ctk.CTkFrame(side, fg_color="transparent")
        foot.pack(fill="x", padx=14, pady=12)
        self.p_root = ctk.CTkLabel(foot, text="", font=self.f.tiny_mono, text_color=COL["faint"], anchor="w",
                                   justify="left", wraplength=250)
        self.p_root.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(foot, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkButton(row, text="Rescan", width=80, height=30, command=self.rescan).pack(side="left")
        ctk.CTkButton(row, text="Change folder", width=110, height=30, command=self.change_root).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Open", width=56, height=30, command=lambda: self.open_path(self.root_dir)).pack(side="left")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True)
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 4))
        self.h_dot = ctk.CTkFrame(header, width=14, height=14, corner_radius=7, fg_color=COL["faint"])
        self.h_dot.pack(side="left", padx=(0, 10))
        self.h_title = ctk.CTkLabel(header, text="No profile", font=self.f.h1)
        self.h_title.pack(side="left")
        self.h_uuid = ctk.CTkButton(header, text="", font=self.f.tiny_mono, fg_color="transparent", hover_color=COL["card_hi"],
                                    text_color=COL["faint"], width=10, height=26, command=self.copy_uuid)
        self.h_uuid.pack(side="left", padx=(12, 0))
        self.h_lock = ctk.CTkSwitch(header, text="Read-only", font=self.f.small, command=self.toggle_lock,
                                    switch_width=42, switch_height=22)
        self.h_lock.pack(side="right")
        self.h_game = ctk.CTkLabel(header, text="", corner_radius=8, height=28, font=self.f.small)
        self.h_game.pack(side="right", padx=16)

        self.tabs = Tabs(main, self.TABS, None, self.f.bold)
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        self.t = {name: self.tabs.tab(name) for name in self.TABS}
        self.build_settings_tab()
        self.build_transfer_tab()
        self.build_bench_tab()
        self.set_running(False, force=True)

    # helpers

    def debounce(self, name, ms, fn):
        job = self._jobs.pop(name, None)
        if job:
            self.after_cancel(job)
        self._jobs[name] = self.after(ms, fn)

    def toast(self, text, kind="ok"):
        if self._toast is not None and self._toast.winfo_exists():
            self._toast.destroy()
        colors = {"ok": (COL["ok_dim"], COL["ok"]), "bad": (COL["bad_dim"], COL["bad"]), "info": (COL["card_hi"], COL["text"])}
        bg, fg = colors[kind]
        box = ctk.CTkFrame(self, fg_color=bg, corner_radius=10, border_width=1, border_color=fg)
        ctk.CTkLabel(box, text=text, text_color=fg, font=self.f.body, wraplength=460, justify="left").pack(padx=16, pady=8)
        box.place(relx=1, rely=1, x=-24, y=-24, anchor="se")
        self._toast = box
        self.after(3200, lambda: box.destroy() if box.winfo_exists() else None)

    def open_path(self, path):
        try:
            os.startfile(path)
        except (OSError, AttributeError) as e:
            messagebox.showerror(APP_NAME, f"Could not open {path}.\n\n{e}", parent=self)

    def card(self, master, title=None):
        if title:
            ctk.CTkLabel(master, text=title.upper(), font=self.f.caps, text_color=COL["faint"], anchor="w",
                         height=18).pack(fill="x", padx=10, pady=(16, 6))
        c = ctk.CTkFrame(master, fg_color=COL["card"], corner_radius=12)
        c.pack(fill="x", padx=4)
        return c

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
        rows = [r for r in self.rows.get(key, []) if r.winfo_exists()]
        self.rows[key] = rows
        return rows

    def profile_by_uuid(self, uuid):
        return next((p for p in self.profiles if p.uuid == uuid), None)

    def blocked(self):
        if self.running:
            messagebox.showwarning(APP_NAME, "Close Rainbow Six Siege first.\n\nThe game rewrites its settings file when "
                                             "it exits, so changes made now would be lost.", parent=self)
        return self.running

    # profiles

    def rescan(self, select=None):
        keep = select or (self.current.uuid if self.current else None)
        self.profiles = scan_profiles(self.root_dir)
        self.p_root.configure(text=str(self.root_dir))
        self.benches = None
        target = self.profile_by_uuid(keep) or (self.profiles[0] if self.profiles else None)
        if target:
            self.load_profile(target)
        else:
            self.current = None
            self.ini = None
            self.orig, self.pending = {}, {}
            self.layout = {}
            self.reset_settings_view()
            self.update_header()
            self.render_sidebar()
        if self.tabs.get() == "Benchmarks":
            self.refresh_bench()

    def change_root(self):
        d = filedialog.askdirectory(initialdir=self.root_dir if self.root_dir.exists() else Path.home(), parent=self,
                                    title="Choose the Rainbow Six - Siege settings folder")
        if d:
            self.root_dir = Path(d)
            self.store.data["settings"]["root"] = str(self.root_dir)
            self.store.save()
            self.current = None
            self.rescan()

    def confirm_discard(self):
        if not self.pending:
            return True
        answer = messagebox.askyesnocancel(APP_NAME, f"Save {len(self.pending)} unsaved changes first?", parent=self)
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
            ini = IniFile.load(profile.path)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not read {profile.path}.\n\n{e}", parent=self)
            return
        self.current, self.ini = profile, ini
        self.orig, self.pending, self.history = ini.values(), {}, []
        self._auto = {}
        self.hints = ini.hints()
        self.datacenters = DATACENTERS + [d for d in ini.datacenters() if d.startswith("gamelift/") and d not in DATACENTERS]
        self.file_mtime = profile.mtime
        self.store.data["settings"]["last_profile"] = profile.uuid
        self.compute_layout()
        with frozen(self.tabs.inner):
            self.reset_settings_view()
            self.update_header()
            self.render_sidebar()
            self.update_dirty()
            self.stale = {"Profile", "Transfer", "Backups"}
            self.on_tab()

    def reload_current(self):
        if self.current:
            cat = self.cat
            self.load_profile(self.current)
            if cat in self.layout:
                self.show_category(cat)

    def render_sidebar(self):
        for w in self.plist.winfo_children():
            w.destroy()
        q = self.p_filter.get().strip().lower()
        shown = 0
        for p in self.profiles:
            m = self.store.meta(p.uuid)
            if q and q not in " ".join([m["label"], p.uuid, m["notes"], *m["tags"]]).lower():
                continue
            ProfileCard(self.plist, self, p, bool(self.current and p.uuid == self.current.uuid)).pack(fill="x", pady=4, padx=4)
            shown += 1
        if not shown:
            text = "No profile matches the filter." if self.profiles else f"No profile folders found.\n\nPick the folder that holds the UUID folders with Change folder."
            ctk.CTkLabel(self.plist, text=text, text_color=COL["muted"], font=self.f.small, wraplength=230,
                         justify="left").pack(fill="x", padx=8, pady=12)

    def update_header(self):
        p = self.current
        if not p:
            self.h_title.configure(text="No profile")
            self.h_uuid.configure(text="")
            self.h_dot.configure(fg_color=COL["faint"])
            self.h_lock.configure(state="disabled")
            return
        meta = self.store.meta(p.uuid)
        self.h_title.configure(text=self.store.name(p.uuid))
        self.h_uuid.configure(text=f"{p.uuid}  ⧉")
        self.h_dot.configure(fg_color=meta["color"])
        self.h_lock.configure(state="normal")
        self.h_lock.select() if p.locked else self.h_lock.deselect()

    def copy_uuid(self):
        if self.current:
            self.clipboard_clear()
            self.clipboard_append(self.current.uuid)
            self.toast("Profile ID copied.", "info")

    def toggle_lock(self):
        if not self.current:
            return
        want = bool(self.h_lock.get())
        try:
            set_locked(self.current.path, want)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not change the file attribute.\n\n{e}", parent=self)
        self.update_header()
        self.render_sidebar()
        self.toast("Locked. The game cannot overwrite this file, and changes made in the game menu will not save."
                   if want else "Unlocked. The game can save settings again.", "info")

    def profile_menu(self, profile, event):
        menu = self.menu()
        menu.add_command(label="Open folder", command=lambda: self.open_path(profile.folder))
        menu.add_command(label="Copy profile ID", command=lambda: (self.clipboard_clear(), self.clipboard_append(profile.uuid)))
        for site, url in SITES.items():
            menu.add_command(label=f"Look up on {site}", command=lambda u=url: webbrowser.open(u.format(uuid=profile.uuid)))
        menu.add_separator()
        menu.add_command(label="Transfer from this profile", command=lambda: self.start_transfer(("profile", profile.uuid)))
        menu.tk_popup(event.x_root, event.y_root)

    def menu(self):
        return tk.Menu(self, tearoff=0, bg=COL["card_hi"], fg=COL["text"], activebackground=COL["accent_deep"],
                       activeforeground="#ffffff", bd=0, font=("Segoe UI", 10))

    # settings tab

    def build_settings_tab(self):
        t = self.t["Settings"]
        t.grid_columnconfigure(1, weight=1)
        t.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(t, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(2, 8))
        self.s_search = ctk.CTkEntry(top, placeholder_text="Search all settings  (Ctrl+F)", width=340, height=34)
        self.s_search.pack(side="left")
        self.s_search.bind("<KeyRelease>", lambda e: self.debounce("search", 220, self.run_search))
        self.s_search.bind("<Escape>", lambda e: (self.s_search.delete(0, "end"), self.run_search()))
        ctk.CTkLabel(top, text="Right-click a setting to copy it to other profiles. Ctrl+wheel over a slider fine-tunes it.",
                     font=self.f.small, text_color=COL["faint"]).pack(side="left", padx=16)
        self.s_nav = ctk.CTkFrame(t, fg_color="transparent", width=200)
        self.s_nav.grid(row=1, column=0, sticky="ns", padx=(0, 10))
        self.s_nav.pack_propagate(False)
        self.s_holder = ctk.CTkFrame(t, fg_color="transparent")
        self.s_holder.grid(row=1, column=1, sticky="nsew")
        bar = ctk.CTkFrame(t, fg_color=COL["card"], corner_radius=10)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.s_count = ctk.CTkLabel(bar, text="", font=self.f.body, text_color=COL["muted"])
        self.s_count.pack(side="left", padx=16, pady=10)
        self.s_save = ctk.CTkButton(bar, text="Save changes  (Ctrl+S)", width=180, height=34, fg_color=COL["accent"],
                                    hover_color=COL["accent_hi"], text_color=COL["ink"], font=self.f.bold, command=self.save)
        self.s_save.pack(side="right", padx=(6, 10), pady=8)
        self.s_revert = ctk.CTkButton(bar, text="Revert all", width=100, height=34, command=self.revert)
        self.s_revert.pack(side="right", padx=6)
        self.s_review = ctk.CTkButton(bar, text="Review", width=90, height=34, command=self.review_changes)
        self.s_review.pack(side="right", padx=6)
        self.s_undo = ctk.CTkButton(bar, text="Undo  (Ctrl+Z)", width=120, height=34, fg_color="transparent",
                                    hover_color=COL["card_hi"], text_color=COL["muted"], command=self.undo)
        self.s_undo.pack(side="right", padx=6)
        self.write_buttons.append(self.s_save)

    def focus_search(self):
        self.tabs.set("Settings")
        self.on_tab()
        self.s_search.focus_set()
        self.s_search.select_range(0, "end")

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

        self.layout = {}
        for cat in CATEGORIES:
            if cat in layout:
                groups = sorted(layout[cat].items(), key=lambda g: GROUP_RANK.get((cat, g[0]), 1000 + min(map(rank, g[1]))))
                self.layout[cat] = {g: sorted(specs, key=rank) for g, specs in groups}

    def reset_settings_view(self):
        for f in self.cat_frames.values():
            f.destroy()
        self.cat_frames = {}
        if self.search_frame:
            self.search_frame.destroy()
            self.search_frame = None
        self.rows = defaultdict(list)
        self.watchers = []
        for w in self.s_nav.winfo_children():
            w.destroy()
        for w in self.s_holder.winfo_children():
            w.destroy()
        self.nav_items = {}
        if not self.layout:
            ctk.CTkLabel(self.s_holder, text="No settings loaded.", text_color=COL["muted"], font=self.f.body).pack(pady=40)
            return
        for cat in self.layout:
            item = NavItem(self.s_nav, self, cat, lambda c=cat: self.show_category(c))
            item.pack(fill="x", pady=1)
            self.nav_items[cat] = item
        if self.current:
            mod = datetime.fromtimestamp(self.current.mtime) if self.current.mtime else None
            ctk.CTkLabel(self.s_nav, text=f"{len(self.orig)} keys\nsaved {nice_date(mod)}" if mod else "",
                         font=self.f.small, text_color=COL["faint"], justify="left", anchor="w").pack(fill="x", padx=12, pady=16)
        self.show_category(self.cat if self.cat in self.layout else next(iter(self.layout)))

    def build_rows(self, frame, items, search=False):
        for title, specs in items:
            ctk.CTkLabel(frame, text=title.upper(), font=self.f.caps, text_color=COL["faint"], anchor="w",
                         height=18).pack(fill="x", padx=10, pady=(16, 2))
            cat, group = (specs[0].cat, specs[0].group)
            note = GROUP_NOTES.get((cat, group))
            if note:
                ctk.CTkLabel(frame, text=note, font=self.f.small, text_color=COL["faint"], anchor="w", justify="left",
                             wraplength=720, height=16).pack(fill="x", padx=10, pady=(0, 2))
            card = ctk.CTkFrame(frame, fg_color=COL["card"], corner_radius=12)
            card.pack(fill="x", padx=(2, 10), pady=(4, 0))
            if not search:
                if (cat, group) == ("Mouse", "Hipfire"):
                    self.sens_panel(card)
                elif (cat, group) == ("Mouse", "Aim down sights"):
                    self.scope_panel(card, "ADSMouseSensitivity")
                elif (cat, group) == ("Controller", "Aim down sights"):
                    self.scope_panel(card, "ADSGamepadSensitivity")
            for spec in specs:
                SettingRow(card, self, spec).pack(fill="x", padx=6, pady=2)
            ctk.CTkFrame(card, height=4, fg_color="transparent").pack()

    def build_category(self, cat):
        frame = ctk.CTkScrollableFrame(self.s_holder, fg_color="transparent")
        self.build_rows(frame, self.layout[cat].items())
        return frame

    def show_category(self, cat):
        with frozen(self.s_holder):
            if self.search_frame:
                self.search_frame.destroy()
                self.search_frame = None
                self.s_search.delete(0, "end")
            self.cat = cat
            for c, item in self.nav_items.items():
                item.set_active(c == cat)
            for f in self.cat_frames.values():
                f.pack_forget()
            if cat not in self.cat_frames:
                self.cat_frames[cat] = self.build_category(cat)
            self.cat_frames[cat].pack(fill="both", expand=True)

    def run_search(self):
        q = self.s_search.get().strip().lower()
        if not q:
            if self.search_frame:
                self.search_frame.destroy()
                self.search_frame = None
            if self.cat in self.layout:
                self.show_category(self.cat)
            return
        with frozen(self.s_holder):
            if self.search_frame:
                self.search_frame.destroy()
            for item in self.nav_items.values():
                item.set_active(False)
            for f in self.cat_frames.values():
                f.pack_forget()
            groups = []
            for cat, cat_groups in self.layout.items():
                for group, specs in cat_groups.items():
                    hits = [s for s in specs if q in f"{s.label} {s.key} {s.help} {group} {cat}".lower()]
                    if hits:
                        groups.append((f"{cat}  ·  {group}", hits))
            frame = ctk.CTkScrollableFrame(self.s_holder, fg_color="transparent")
            if groups:
                self.build_rows(frame, groups, search=True)
            else:
                ctk.CTkLabel(frame, text=f'No setting matches "{q}".', text_color=COL["muted"], font=self.f.body).pack(pady=40)
            frame.pack(fill="both", expand=True)
            self.search_frame = frame

    def tool_box(self, card, title):
        box = ctk.CTkFrame(card, fg_color=COL["bg"], corner_radius=10)
        box.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(box, text=title.upper(), font=self.f.caps, text_color=COL["accent"], anchor="w",
                     height=18).pack(fill="x", padx=14, pady=(10, 2))
        return box

    def tool_entry(self, master, text, width=70, value=""):
        ctk.CTkLabel(master, text=text, font=self.f.small, text_color=COL["muted"]).pack(side="left", padx=(0, 6))
        e = ctk.CTkEntry(master, width=width, height=30, justify="center", font=self.f.mono)
        e.insert(0, value)
        e.pack(side="left", padx=(0, 6))
        return e

    def watch(self, fn):
        self.watchers.append(fn)
        fn()

    def sens_panel(self, card):
        yk, pk, mk = ("INPUT", "MouseYawSensitivity"), ("INPUT", "MousePitchSensitivity"), ("INPUT", "MouseSensitivityMultiplierUnit")
        if not all(k in self.orig for k in (yk, pk, mk)):
            return
        meta = self.store.meta(self.current.uuid)
        box = self.tool_box(card, "Sensitivity calculator")
        read = ctk.CTkLabel(box, text="", font=self.f.mono, text_color=COL["text"], justify="left", anchor="w", height=38)
        read.pack(fill="x", padx=14, pady=(2, 6))
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(fill="x", padx=14, pady=(0, 6))
        dpi = self.tool_entry(line, "Mouse DPI", 70, str(meta.get("dpi", 800)))
        ctk.CTkFrame(line, width=18, height=1, fg_color="transparent").pack(side="left")
        cm = self.tool_entry(line, "Target cm/360", 70)
        ctk.CTkButton(line, text="Match", width=70, height=30, command=lambda: match_cm()).pack(side="left")
        line2 = ctk.CTkFrame(box, fg_color="transparent")
        line2.pack(fill="x", padx=14, pady=(0, 6))
        slot = self.tool_entry(line2, "Finer steps: move the horizontal slider to", 60, "50")
        ctk.CTkButton(line2, text="Keep my feel", width=110, height=30, command=lambda: finer()).pack(side="left")
        ctk.CTkLabel(box, text="The multiplier is changed so the effective sensitivity stays the same. cm/360 uses "
                               f"{DEG_PER_COUNT:.6f}° per count at slider 1 and multiplier 0.02, a community-measured value.",
                     font=self.f.small, text_color=COL["faint"], wraplength=720, justify="left", anchor="w").pack(fill="x", padx=14, pady=(0, 10))

        def vals():
            return [num(self.value(*k)) or 0 for k in (yk, pk, mk)]

        def dpi_value():
            d = num(dpi.get())
            if d and d != meta.get("dpi"):
                meta["dpi"] = d
                self.store.save()
            return d or 0

        def refresh():
            if not read.winfo_exists():
                return False
            yaw, pitch, mult = vals()
            deg = DEG_PER_COUNT * yaw * mult / 0.02
            d = dpi_value()
            text = f"Effective   {yaw * mult:.6f} horizontal   {pitch * mult:.6f} vertical"
            if deg and d:
                text += f"\ncm/360      {2.54 * 360 / (deg * d):.2f} cm at {d:g} DPI   ·   {deg:.6f}° per count"
            read.configure(text=text)
            return True

        def finer():
            target = num(slot.get())
            yaw, pitch, mult = vals()
            if not target or not 1 <= target <= 100 or not yaw or not mult:
                self.toast("Enter a slider value from 1 to 100.", "bad")
                return
            target = int(round(target))
            new_mult = yaw * mult / target
            new_pitch = min(100, max(1, round(pitch * mult / new_mult)))
            self.set_value(*mk, f"{new_mult:.6f}")
            self.set_value(*yk, str(target))
            self.set_value(*pk, str(new_pitch))
            drift = abs(new_pitch * new_mult - pitch * mult)
            self.toast(f"Slider {target}, multiplier {new_mult:.6f}. Horizontal is unchanged"
                       + (f", vertical moved by {drift:.6f}." if drift > 1e-9 else ", vertical too."), "info")

        def match_cm():
            target, d = num(cm.get()), dpi_value()
            yaw, _pitch, _mult = vals()
            if not target or not d or not yaw:
                self.toast("Enter your DPI and a target cm/360.", "bad")
                return
            deg = 2.54 * 360 / (target * d)
            self.set_value(*mk, f"{deg / DEG_PER_COUNT / yaw * 0.02:.6f}")
            self.toast(f"Multiplier set for {target:g} cm/360 at {d:g} DPI.", "info")

        dpi.bind("<KeyRelease>", lambda e: refresh())
        self.watch(refresh)

    def scope_panel(self, card, prefix):
        keys = [("INPUT", f"{prefix}{s}") for s in SCOPES if ("INPUT", f"{prefix}{s}") in self.orig]
        if not keys:
            return
        box = self.tool_box(card, "All scopes at once")
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(fill="x", padx=14, pady=(2, 10))
        value = self.tool_entry(line, "Value", 60, "50")

        def set_all(v):
            v = num(v)
            if v is None:
                return
            for k in keys:
                self.set_value(*k, str(int(round(min(100, max(1, v))))))

        ctk.CTkButton(line, text="Set every scope", width=130, height=30, command=lambda: set_all(value.get())).pack(side="left")
        ctk.CTkButton(line, text="Copy 1x to all", width=120, height=30,
                      command=lambda: set_all(self.value("INPUT", f"{prefix}1x"))).pack(side="left", padx=8)
        ctk.CTkButton(line, text="Match hipfire (50)", width=140, height=30, command=lambda: set_all(50)).pack(side="left")

    def set_value(self, sec, key, value, src=None, record=True):
        k = (sec, key)
        before = self.value(sec, key)
        if value == before:
            return
        if record:
            now = time.monotonic()
            if self.history and self.history[-1][0] == k and now - self.history[-1][3] < 1.2:
                self.history[-1] = (k, self.history[-1][1], value, now)
            else:
                self.history.append((k, before, value, now))
                del self.history[:-500]
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

    def reset_keys(self, keys):
        for k in keys:
            if k in self.pending:
                self.set_value(*k, self.orig.get(k))

    def undo(self):
        if not self.history:
            return
        k, before, _after, _t = self.history.pop()
        self.set_value(*k, before, record=False)
        self.toast(f"Undid {self.spec(*k).label}.", "info")

    def revert(self):
        if not self.pending:
            return
        keys = list(self.pending)
        self.pending.clear()
        self.history.clear()
        for k in keys:
            for row in self.live_rows(k):
                row.load()
                row.mark()
        self.watchers = [w for w in self.watchers if w()]
        self.update_dirty()

    def update_dirty(self):
        n = len(self.pending)
        self.s_count.configure(text=f"{n} unsaved change{'s' if n != 1 else ''}" if n else "No unsaved changes",
                               text_color=COL["accent"] if n else COL["muted"])
        for b in (self.s_revert, self.s_review):
            b.configure(state="normal" if n else "disabled")
        self.s_undo.configure(state="normal" if self.history else "disabled")
        set_primary(self.s_save, bool(n) and not self.running)
        counts = Counter(self.spec(*k).cat for k in self.pending if self.spec(*k).kind != "hidden")
        for cat, item in self.nav_items.items():
            item.set_count(counts.get(cat, 0))
        self.title(f"{APP_NAME} {__version__}{'  (unsaved changes)' if n else ''}")

    def save(self):
        if not self.pending or not self.current:
            return True
        if self.blocked():
            return False
        try:
            ini = IniFile.load(self.current.path)
            for (sec, key), v in self.pending.items():
                ini.set(sec, key, v)
            write_profile(self.store, self.current, ini, "before edit")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Saving failed. The file was not changed.\n\n{e}", parent=self)
            return False
        n = len(self.pending)
        self.ini, self.orig = ini, ini.values()
        self.pending.clear()
        self.history.clear()
        self.file_mtime = self.current.mtime
        for rows in self.rows.values():
            for r in rows:
                if r.winfo_exists():
                    r.mark()
        self.update_dirty()
        self.render_sidebar()
        self.stale |= {"Backups", "Transfer"}
        self.toast(f"Saved {n} change{'s' if n != 1 else ''}. The previous file is in Backups.")
        return True

    def review_changes(self):
        if not self.pending:
            return
        top = ctk.CTkToplevel(self)
        top.title("Unsaved changes")
        top.geometry("720x480")
        top.transient(self)
        top.after(80, top.lift)
        frame = ctk.CTkScrollableFrame(top, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        def fill():
            for w in frame.winfo_children():
                w.destroy()
            for k in sorted(self.pending, key=lambda k: RANK.get(k, 1e9)):
                spec = self.spec(*k)
                row = ctk.CTkFrame(frame, fg_color=COL["card"], corner_radius=8)
                row.pack(fill="x", pady=3)
                ctk.CTkLabel(row, text=spec.label if spec.kind != "resolution" else "Resolution width", font=self.f.body,
                             width=220, anchor="w").pack(side="left", padx=12, pady=8)
                ctk.CTkLabel(row, text=f"{spec.show(self.orig.get(k))}   →   {spec.show(self.pending[k])}",
                             font=self.f.mono, text_color=COL["accent"], anchor="w").pack(side="left", fill="x", expand=True)
                ctk.CTkButton(row, text="Undo", width=70, height=28,
                              command=lambda k=k: (self.set_value(*k, self.orig.get(k)), fill())).pack(side="right", padx=8)
            if not self.pending:
                top.destroy()

        fill()
        bar = ctk.CTkFrame(top, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(bar, text="Save changes", fg_color=COL["accent"], hover_color=COL["accent_hi"], text_color=COL["ink"],
                      command=lambda: (self.save() and top.destroy())).pack(side="right")
        ctk.CTkButton(bar, text="Close", command=top.destroy).pack(side="right", padx=8)

    def row_menu(self, row, event):
        menu = self.menu()
        spec = row.spec
        if any(k in self.pending for k in row.keys):
            menu.add_command(label="Reset to saved value", command=lambda: self.reset_keys(row.keys))
        menu.add_command(label=f"Copy key name  ({spec.path})",
                         command=lambda: (self.clipboard_clear(), self.clipboard_append(spec.path)))
        others = [p for p in self.profiles if p.uuid != self.current.uuid]
        if others:
            menu.add_separator()
            sub = self.menu()
            for p in others:
                sub.add_command(label=self.store.name(p.uuid), command=lambda p=p: self.push_keys(row.keys, [p]))
            menu.add_cascade(label="Apply this value to", menu=sub)
            menu.add_command(label="Apply this value to all other profiles", command=lambda: self.push_keys(row.keys, others))
        menu.tk_popup(event.x_root, event.y_root)

    def push_keys(self, keys, targets):
        if self.blocked():
            return
        values = {k: self.value(*k) for k in keys}
        done = []
        for p in targets:
            try:
                ini = IniFile.load(p.path)
                for k, v in values.items():
                    ini.set(*k, v)
                write_profile(self.store, p, ini, f"before copy of {keys[0][1]}")
                done.append(self.store.name(p.uuid))
            except OSError as e:
                messagebox.showerror(APP_NAME, f"Could not update {self.store.name(p.uuid)}.\n\n{e}", parent=self)
        if done:
            self.stale |= {"Transfer"}
            self.render_sidebar()
            self.toast(f"Applied {self.spec(*keys[0]).label} to {', '.join(done)}. Backups were saved.")

    # profile tab

    def build_profile_tab(self):
        t = self.t["Profile"]
        for w in t.winfo_children():
            w.destroy()
        p = self.current
        if not p:
            return
        meta = self.store.meta(p.uuid)
        wrap = ctk.CTkScrollableFrame(t, fg_color="transparent")
        wrap.pack(fill="both", expand=True)

        def commit():
            self.store.save()
            self.update_header()
            self.render_sidebar()

        ident = self.card(wrap, "Identity")
        ident.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ident, text="Label", font=self.f.body, text_color=COL["muted"]).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))
        name = ctk.CTkEntry(ident, height=36, font=self.f.h2, placeholder_text="For example Main account")
        name.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(14, 6))
        if meta["label"]:
            name.insert(0, meta["label"])

        def set_name(_e=None):
            if name.get().strip() != meta["label"]:
                meta["label"] = name.get().strip()
                commit()

        name.bind("<Return>", set_name)
        name.bind("<FocusOut>", set_name)

        ctk.CTkLabel(ident, text="Color", font=self.f.body, text_color=COL["muted"]).grid(row=1, column=0, sticky="w", padx=16, pady=6)
        swatches = ctk.CTkFrame(ident, fg_color="transparent")
        swatches.grid(row=1, column=1, sticky="w", pady=6)
        for c in PROFILE_COLORS:
            ctk.CTkButton(swatches, text="", width=28, height=28, corner_radius=14, fg_color=c, hover_color=c,
                          border_width=3 if c == meta["color"] else 0, border_color=COL["text"],
                          command=lambda c=c: (meta.update(color=c), commit(), self.build_profile_tab())).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(ident, text="Tags", font=self.f.body, text_color=COL["muted"]).grid(row=2, column=0, sticky="nw", padx=16, pady=10)
        tagbox = ctk.CTkFrame(ident, fg_color="transparent")
        tagbox.grid(row=2, column=1, sticky="ew", padx=(0, 16), pady=6)
        chips = ctk.CTkFrame(tagbox, fg_color="transparent", height=1)
        chips.pack(fill="x")
        for tag in meta["tags"]:
            ctk.CTkButton(chips, text=f"{tag}   ×", height=28, width=10, corner_radius=14, fg_color=COL["line"],
                          hover_color=COL["bad_dim"], font=self.f.small,
                          command=lambda tag=tag: (meta["tags"].remove(tag), commit(), self.build_profile_tab())).pack(side="left", padx=(0, 6), pady=2)
        entry = ctk.CTkEntry(tagbox, height=32, width=260, placeholder_text="Add a tag and press Enter")
        entry.pack(anchor="w", pady=(6, 4))

        def add_tag(tag):
            tag = tag.strip()
            if tag and tag not in meta["tags"]:
                meta["tags"].append(tag)
                commit()
                self.build_profile_tab()

        entry.bind("<Return>", lambda e: add_tag(entry.get()))
        suggestions = [tg for tg in self.store.all_tags() + ["main", "smurf", "stretched", "stream"]
                       if tg not in meta["tags"]]
        if suggestions:
            sug = ctk.CTkFrame(tagbox, fg_color="transparent")
            sug.pack(fill="x")
            ctk.CTkLabel(sug, text="Quick add", font=self.f.small, text_color=COL["faint"]).pack(side="left", padx=(0, 8))
            for tg in list(dict.fromkeys(suggestions))[:9]:
                ctk.CTkButton(sug, text=f"+ {tg}", height=24, width=10, corner_radius=12, fg_color="transparent",
                              border_width=1, border_color=COL["line"], hover_color=COL["card_hi"], text_color=COL["muted"],
                              font=self.f.small, command=lambda tg=tg: add_tag(tg)).pack(side="left", padx=(0, 4))

        ctk.CTkLabel(ident, text="Notes", font=self.f.body, text_color=COL["muted"]).grid(row=3, column=0, sticky="nw", padx=16, pady=10)
        notes = ctk.CTkTextbox(ident, height=90, font=self.f.body, wrap="word")
        notes.grid(row=3, column=1, sticky="ew", padx=(0, 16), pady=(6, 16))
        notes.insert("1.0", meta["notes"])

        def set_notes(_e=None):
            text = notes.get("1.0", "end-1c")
            if text != meta["notes"]:
                meta["notes"] = text
                self.store.save()

        notes.bind("<FocusOut>", set_notes)

        who = self.card(wrap, "Whose account is this?")
        ctk.CTkLabel(who, text="The folder name is a Ubisoft profile ID. Open it on a stats site to see the account name, "
                               "then use that name as the label. This opens your browser; the app sends nothing itself.",
                     font=self.f.small, text_color=COL["muted"], wraplength=760, justify="left", anchor="w").pack(fill="x", padx=16, pady=(14, 8))
        row = ctk.CTkFrame(who, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 14))
        site = ctk.CTkSegmentedButton(row, values=list(SITES), font=self.f.body, height=32)
        site.set(self.store.data["settings"].get("site", next(iter(SITES))))
        site.pack(side="left")

        def lookup():
            self.store.data["settings"]["site"] = site.get()
            webbrowser.open(SITES[site.get()].format(uuid=p.uuid))

        ctk.CTkButton(row, text="Open in browser", height=32, fg_color=COL["accent"], hover_color=COL["accent_hi"],
                      text_color=COL["ink"], font=self.f.bold, command=lookup).pack(side="left", padx=10)

        f = self.card(wrap, "Settings file")
        info = ctk.CTkFrame(f, fg_color="transparent")
        info.pack(fill="x", padx=16, pady=(14, 6))
        when = datetime.fromtimestamp(p.mtime) if p.mtime else None
        size = p.path.stat().st_size if p.path.exists() else 0
        for k, v in (("Path", str(p.path)), ("Saved", nice_date(when) if when else "unknown"), ("Size", f"{size:,} bytes"),
                     ("Keys", str(len(self.orig))), ("Read-only", "yes" if p.locked else "no")):
            line = ctk.CTkFrame(info, fg_color="transparent")
            line.pack(fill="x")
            ctk.CTkLabel(line, text=k, width=90, anchor="w", font=self.f.small, text_color=COL["muted"]).pack(side="left")
            ctk.CTkLabel(line, text=v, anchor="w", justify="left", font=self.f.tiny_mono, wraplength=720).pack(side="left")
        btns = ctk.CTkFrame(f, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(6, 14))
        ctk.CTkButton(btns, text="Open folder", command=lambda: self.open_path(p.folder)).pack(side="left")
        ctk.CTkButton(btns, text="Back up now", command=self.backup_now).pack(side="left", padx=8)
        ctk.CTkButton(btns, text="Export a copy", command=self.export_copy).pack(side="left")
        ctk.CTkButton(btns, text="Import from file", command=self.import_file).pack(side="left", padx=8)

    def backup_now(self):
        if self.current:
            self.store.backup(self.current.uuid, self.current.path, "manual")
            self.stale.add("Backups")
            self.toast("Backup saved.")

    def export_copy(self):
        p = self.current
        if not p:
            return
        label = re.sub(r"[^\w-]+", "_", self.store.meta(p.uuid)["label"]) or p.uuid[:8]
        dest = filedialog.asksaveasfilename(parent=self, defaultextension=".ini", initialfile=f"{label}-GameSettings.ini",
                                            filetypes=[("Settings file", "*.ini")])
        if dest:
            shutil.copyfile(p.path, dest)
            self.toast(f"Exported to {dest}.")

    def import_file(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Settings file", "*.ini"), ("All files", "*.*")])
        if path:
            self.start_transfer(self.add_extra_source("file", Path(path)))

    # transfer tab

    def build_transfer_tab(self):
        t = self.t["Transfer"]
        top = ctk.CTkFrame(t, fg_color=COL["card"], corner_radius=12)
        top.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(top, text="From", font=self.f.small, text_color=COL["muted"]).pack(side="left", padx=(16, 6), pady=12)
        self.x_src = ctk.CTkOptionMenu(top, values=[""], width=280, height=34, dynamic_resizing=False, font=self.f.body,
                                       dropdown_font=self.f.body, command=lambda v: self.pick_source(v))
        self.x_src.pack(side="left")
        ctk.CTkButton(top, text="⇄", width=38, height=34, font=self.f.h2, command=self.swap_transfer).pack(side="left", padx=8)
        ctk.CTkLabel(top, text="To", font=self.f.small, text_color=COL["muted"]).pack(side="left", padx=(0, 6))
        self.x_tgt = ctk.CTkOptionMenu(top, values=[""], width=280, height=34, dynamic_resizing=False, font=self.f.body,
                                       dropdown_font=self.f.body, command=lambda v: self.refresh_transfer(rebuild_menus=False, clear=True))
        self.x_tgt.pack(side="left")
        self.x_hw = ctk.CTkSwitch(top, text="Hardware keys", font=self.f.small, switch_width=40, switch_height=20,
                                  command=lambda: self.refresh_transfer(rebuild_menus=False))
        self.x_hw.pack(side="right", padx=16)
        self.x_diff = ctk.CTkSwitch(top, text="Differences only", font=self.f.small, switch_width=40, switch_height=20,
                                    command=lambda: self.refresh_transfer(rebuild_menus=False))
        self.x_diff.select()
        self.x_diff.pack(side="right")

        self.x_table = ctk.CTkScrollableFrame(t, fg_color="transparent")
        self.x_table.pack(fill="both", expand=True)

        bar = ctk.CTkFrame(t, fg_color=COL["card"], corner_radius=10)
        bar.pack(fill="x", pady=(10, 0))
        self.x_count = ctk.CTkLabel(bar, text="", font=self.f.body, text_color=COL["muted"])
        self.x_count.pack(side="left", padx=16, pady=10)
        self.x_apply = ctk.CTkButton(bar, text="Apply to target", width=170, height=34, fg_color=COL["accent"],
                                     hover_color=COL["accent_hi"], text_color=COL["ink"], font=self.f.bold, command=self.apply_transfer)
        self.x_apply.pack(side="right", padx=(6, 10), pady=8)
        self.x_preset = ctk.CTkButton(bar, text="Save as preset", width=130, height=34, command=self.save_preset)
        self.x_preset.pack(side="right", padx=6)
        self.x_delpreset = ctk.CTkButton(bar, text="Delete preset", width=120, height=34, fg_color="transparent",
                                         border_width=1, border_color=COL["bad_dim"], text_color=COL["bad"],
                                         hover_color=COL["bad_dim"], command=self.delete_preset)
        ctk.CTkButton(bar, text="Clear", width=80, height=34, command=lambda: self.select_rows(set())).pack(side="right", padx=6)
        ctk.CTkButton(bar, text="Select all shown", width=130, height=34,
                      command=lambda: self.select_rows(set(self.x_rows))).pack(side="right", padx=6)
        self.write_buttons.append(self.x_apply)

    def plabel(self, uuid):
        label = self.store.meta(uuid)["label"]
        return f"{label}  ({uuid[:8]})" if label else self.store.name(uuid)

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
        return {self.plabel(p.uuid): p.uuid for p in self.profiles}

    def add_extra_source(self, kind, path, label=None):
        label = label or f"File: {path.name}"
        self.x_extra[label] = (kind, path)
        return self.x_extra[label]

    def start_transfer(self, src_ref, target_uuid=None):
        self.stale.discard("Transfer")
        label = next((k for k, v in self.source_options().items() if v == src_ref), None)
        self.refresh_transfer()
        if label:
            self.x_src.set(label)
        tgt = target_uuid or (self.current.uuid if self.current else None)
        tl = next((k for k, v in self.target_options().items() if v == tgt), None)
        if tl:
            self.x_tgt.set(tl)
        self.refresh_transfer(rebuild_menus=False, clear=True)
        self.tabs.set("Transfer")

    def pick_source(self, label):
        ref = self.source_options().get(label)
        if ref and ref[0] == "open":
            path = filedialog.askopenfilename(parent=self, filetypes=[("Settings file", "*.ini"), ("All files", "*.*")])
            if not path:
                self.x_src.set(self._x_src_last)
                return
            ref = self.add_extra_source("file", Path(path))
            self.x_src.configure(values=list(self.source_options()))
            self.x_src.set(next(k for k, v in self.source_options().items() if v == ref))
        self.refresh_transfer(rebuild_menus=False, clear=True)

    def swap_transfer(self):
        src = self.source_options().get(self.x_src.get())
        tgt = self.target_options().get(self.x_tgt.get())
        if not src or src[0] != "profile" or not tgt:
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

    def refresh_transfer(self, rebuild_menus=True, clear=False):
        with frozen(self.t["Transfer"]):
            self._refresh_transfer(rebuild_menus, clear)

    def _refresh_transfer(self, rebuild_menus, clear):
        if rebuild_menus:
            src_opts, tgt_opts = list(self.source_options()), list(self.target_options())
            self.x_src.configure(values=src_opts)
            self.x_tgt.configure(values=tgt_opts or [""])
            if self.x_src.get() not in src_opts:
                others = [k for k, v in self.source_options().items() if v[0] == "profile" and (not self.current or v[1] != self.current.uuid)]
                self.x_src.set(others[0] if others else src_opts[0])
            if self.x_tgt.get() not in tgt_opts:
                cur = next((k for k, v in self.target_options().items() if self.current and v == self.current.uuid), None)
                self.x_tgt.set(cur or (tgt_opts[0] if tgt_opts else ""))
        self._x_src_last = self.x_src.get()
        if clear:
            self.x_sel = set()
        for w in self.x_table.winfo_children():
            w.destroy()
        self.x_rows, self.x_vars = {}, {}
        src_ref = self.source_options().get(self.x_src.get())
        tgt_uuid = self.target_options().get(self.x_tgt.get())
        is_preset = bool(src_ref and src_ref[0] == "preset")
        self.x_delpreset.pack_forget()
        if is_preset:
            self.x_delpreset.pack(side="right", padx=6, before=self.x_preset)
        if not src_ref or not tgt_uuid or src_ref[0] == "open":
            self.update_transfer_count()
            return
        if src_ref == ("profile", tgt_uuid):
            ctk.CTkLabel(self.x_table, text="Pick two different profiles.", text_color=COL["muted"], font=self.f.body).pack(pady=40)
            self.update_transfer_count()
            return
        try:
            src = self.source_values(src_ref)
            tgt = IniFile.load(self.profile_by_uuid(tgt_uuid).path).values()
        except (OSError, AttributeError) as e:
            ctk.CTkLabel(self.x_table, text=f"Could not read the files.\n{e}", text_color=COL["bad"], font=self.f.body).pack(pady=40)
            return
        diff_only, include_hw = bool(self.x_diff.get()), bool(self.x_hw.get())
        grouped = defaultdict(list)
        total_diff = 0
        for k in src:
            spec = self.spec(*k)
            if spec.hw and not include_hw:
                continue
            same = src[k] == tgt.get(k)
            total_diff += not same
            if diff_only and same:
                continue
            grouped[spec.cat].append((spec, k, src[k], tgt.get(k)))
        self.x_sel &= {k for rows in grouped.values() for _, k, _, _ in rows}
        if not grouped:
            ctk.CTkLabel(self.x_table, text="Nothing to transfer. The shown settings already match.", text_color=COL["muted"],
                         font=self.f.body).pack(pady=40)
        for cat in CATEGORIES:
            rows = sorted(grouped.get(cat, []), key=lambda r: RANK.get(r[1], 1e9))
            if not rows:
                continue
            head = ctk.CTkFrame(self.x_table, fg_color="transparent")
            head.pack(fill="x", padx=6, pady=(14, 4))
            keys = {k for _, k, _, _ in rows}
            gvar = tk.IntVar(value=int(keys <= self.x_sel))
            ctk.CTkCheckBox(head, text=f"{cat.upper()}   {len(rows)}", variable=gvar, font=self.f.caps, text_color=COL["faint"],
                            checkbox_width=18, checkbox_height=18,
                            command=lambda keys=keys, gvar=gvar: self.select_rows(self.x_sel | keys if gvar.get() else self.x_sel - keys)).pack(side="left")
            card = ctk.CTkFrame(self.x_table, fg_color=COL["card"], corner_radius=12)
            card.pack(fill="x", padx=(2, 10))
            card.grid_columnconfigure(1, weight=1)
            sc = ctk.ScalingTracker.get_widget_scaling(card)
            card.grid_columnconfigure(2, minsize=round(200 * sc))
            card.grid_columnconfigure(4, minsize=round(200 * sc))
            label_kw = dict(bg=COL["card"], font=tk_font(card, self.f.mono))
            for i, (spec, k, sv, tv) in enumerate(rows):
                var = tk.IntVar(value=int(k in self.x_sel))
                self.x_vars[k] = var
                self.x_rows[k] = sv
                label = "Resolution width" if spec.kind == "resolution" else spec.label
                ctk.CTkCheckBox(card, text="", variable=var, width=24, checkbox_width=20, checkbox_height=20,
                                command=lambda k=k, var=var: self.toggle_row(k, var.get())).grid(row=i, column=0, padx=(14, 4), pady=4)
                name = tk.Frame(card, bg=COL["card"])
                name.grid(row=i, column=1, sticky="w", pady=round(3 * sc))
                tk.Label(name, text=label, bg=COL["card"], fg=COL["text"], font=tk_font(card, self.f.body)).pack(anchor="w")
                tk.Label(name, text=spec.path, bg=COL["card"], fg=COL["faint"], font=tk_font(card, self.f.tiny_mono)).pack(anchor="w")
                same = sv == tv
                tk.Label(card, text=spec.show(sv), fg=COL["muted"] if same else COL["accent"], **label_kw).grid(
                    row=i, column=2, sticky="e", padx=round(6 * sc))
                tk.Label(card, text="→", bg=COL["card"], fg=COL["faint"], font=tk_font(card, self.f.body)).grid(
                    row=i, column=3, padx=round(6 * sc))
                tk.Label(card, text=spec.show(tv) if tv is not None else "new key",
                         fg=COL["muted"] if tv is not None else COL["ok"], **label_kw).grid(
                    row=i, column=4, sticky="w", padx=(round(6 * sc), round(14 * sc)))
        hidden = "" if include_hw else "  Hardware keys are hidden."
        self.x_summary = f"{total_diff} difference{'s' if total_diff != 1 else ''}.{hidden}"
        self.update_transfer_count()

    def toggle_row(self, k, on):
        (self.x_sel.add if on else self.x_sel.discard)(k)
        self.update_transfer_count()

    def select_rows(self, keys):
        self.x_sel = set(keys) & set(self.x_rows)
        for k, var in self.x_vars.items():
            var.set(int(k in self.x_sel))
        self.refresh_transfer(rebuild_menus=False)

    def update_transfer_count(self):
        n = len(self.x_sel)
        summary = getattr(self, "x_summary", "")
        self.x_count.configure(text=f"{n} selected   ·   {summary}" if self.x_rows else summary,
                               text_color=COL["accent"] if n else COL["muted"])
        tgt = self.x_tgt.get().split("  (")[0]
        self.x_apply.configure(text=f"Apply to {tgt[:22]}" if tgt else "Apply to target")
        set_primary(self.x_apply, bool(n) and not self.running)
        self.x_preset.configure(state="normal" if n else "disabled")

    def apply_transfer(self):
        keys = [k for k in self.x_rows if k in self.x_sel]
        src_ref = self.source_options().get(self.x_src.get())
        target = self.profile_by_uuid(self.target_options().get(self.x_tgt.get()))
        if not keys or not src_ref or not target or self.blocked():
            return
        if self.current and target.uuid == self.current.uuid and self.pending:
            messagebox.showinfo(APP_NAME, "This profile has unsaved edits in the Settings tab. Save or revert them first.", parent=self)
            return
        src_name = self.x_src.get().split("  (")[0]
        if not messagebox.askyesno(APP_NAME, f"Copy {len(keys)} setting{'s' if len(keys) != 1 else ''} from {src_name} to "
                                             f"{self.store.name(target.uuid)}?\n\nA backup of the target is saved first.", parent=self):
            return
        try:
            src = self.source_values(src_ref)
            ini = IniFile.load(target.path)
            for k in keys:
                ini.set(*k, src[k])
            write_profile(self.store, target, ini, f"before transfer from {src_name}")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"The transfer failed. The target was not changed.\n\n{e}", parent=self)
            return
        self.toast(f"Copied {len(keys)} settings to {self.store.name(target.uuid)}. A backup was saved.")
        if self.current and target.uuid == self.current.uuid:
            self.reload_current()
            self.tabs.set("Transfer")
        self.render_sidebar()
        self.stale.add("Backups")
        self.refresh_transfer(rebuild_menus=False, clear=True)

    def save_preset(self):
        keys = [k for k in self.x_rows if k in self.x_sel]
        src_ref = self.source_options().get(self.x_src.get())
        if not keys or not src_ref:
            return
        name = (ctk.CTkInputDialog(title="Save preset", text="Name for this preset, for example Sens only").get_input() or "").strip()
        if not name:
            return
        presets = self.store.data["presets"]
        if name in presets and not messagebox.askyesno(APP_NAME, f'Replace the preset "{name}"?', parent=self):
            return
        src = self.source_values(src_ref)
        values = {}
        for sec, key in keys:
            values.setdefault(sec, {})[key] = src[(sec, key)]
        presets[name] = {"values": values, "created": datetime.now().isoformat(timespec="seconds"),
                         "source": self.x_src.get()}
        self.store.save()
        self.refresh_transfer()
        self.toast(f'Preset "{name}" saved with {len(keys)} settings. Pick it in From to apply it.')

    def delete_preset(self):
        ref = self.source_options().get(self.x_src.get())
        if ref and ref[0] == "preset" and messagebox.askyesno(APP_NAME, f'Delete the preset "{ref[1]}"?', parent=self):
            self.store.data["presets"].pop(ref[1], None)
            self.store.save()
            self.refresh_transfer()

    # backups tab

    def refresh_backups(self):
        t = self.t["Backups"]
        for w in t.winfo_children():
            w.destroy()
        p = self.current
        if not p:
            return
        top = ctk.CTkFrame(t, fg_color="transparent")
        top.pack(fill="x", pady=(2, 8))
        ctk.CTkLabel(top, text=f"Backups of {self.store.name(p.uuid)}", font=self.f.h2).pack(side="left", padx=6)
        ctk.CTkLabel(top, text=f"The app saves one before every change and keeps the last {MAX_BACKUPS}.",
                     font=self.f.small, text_color=COL["faint"]).pack(side="left", padx=12)
        ctk.CTkButton(top, text="Open folder", command=lambda: (self.store.backup_dir(p.uuid).mkdir(parents=True, exist_ok=True),
                                                                self.open_path(self.store.backup_dir(p.uuid)))).pack(side="right")
        ctk.CTkButton(top, text="Back up now", command=lambda: (self.backup_now(), self.refresh_backups())).pack(side="right", padx=8)
        lst = ctk.CTkScrollableFrame(t, fg_color="transparent")
        lst.pack(fill="both", expand=True)
        backups = self.store.backups(p.uuid)
        if not backups:
            ctk.CTkLabel(lst, text="No backups yet. One is saved automatically before the first change.",
                         text_color=COL["muted"], font=self.f.body).pack(pady=40)
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
            n = sum(1 for k in set(vals) | set(cur) if vals.get(k) != cur.get(k))
            row = ctk.CTkFrame(lst, fg_color=COL["card"], corner_radius=10)
            row.pack(fill="x", pady=3, padx=(2, 10))
            ctk.CTkLabel(row, text=nice_date(b.when), font=self.f.bold, width=190, anchor="w").pack(side="left", padx=14, pady=10)
            ctk.CTkLabel(row, text=b.reason, font=self.f.body, text_color=COL["muted"], anchor="w").pack(side="left")
            ctk.CTkButton(row, text="Delete", width=70, height=30, fg_color="transparent", text_color=COL["bad"],
                          hover_color=COL["bad_dim"], command=lambda b=b: self.delete_backup(b)).pack(side="right", padx=(4, 12))
            restore = ctk.CTkButton(row, text="Restore", width=80, height=30, command=lambda b=b: self.restore_backup(b))
            restore.pack(side="right", padx=4)
            restore.configure(state="disabled" if self.running or not n else "normal")
            ctk.CTkButton(row, text="Compare", width=80, height=30,
                          command=lambda b=b: self.start_transfer(self.add_extra_source("backup", b.path, f"Backup: {nice_date(b.when)}"), p.uuid)).pack(side="right", padx=4)
            ctk.CTkLabel(row, text=f"{n} difference{'s' if n != 1 else ''}" if n else "same as current", font=self.f.small,
                         text_color=COL["accent"] if n else COL["faint"], width=110).pack(side="right", padx=10)

    def restore_backup(self, b):
        p = self.current
        if not p or self.blocked() or not self.confirm_discard():
            return
        if not messagebox.askyesno(APP_NAME, f"Restore the backup from {nice_date(b.when)}?\n\nThe current file is backed up first.", parent=self):
            return
        try:
            write_profile(self.store, p, IniFile.load(b.path), "before restore")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Restore failed.\n\n{e}", parent=self)
            return
        self.reload_current()
        self.tabs.set("Backups")
        self.refresh_backups()
        self.toast("Backup restored.")

    def delete_backup(self, b):
        if messagebox.askyesno(APP_NAME, f"Delete the backup from {nice_date(b.when)}?", parent=self):
            b.path.unlink(missing_ok=True)
            self.refresh_backups()

    # benchmarks tab

    def build_bench_tab(self):
        t = self.t["Benchmarks"]
        t.grid_columnconfigure(1, weight=1)
        t.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(t, fg_color="transparent", width=310)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="AVERAGE FPS PER RUN", font=self.f.caps, text_color=COL["faint"], anchor="w").pack(fill="x", padx=6, pady=(4, 6))
        hist = ctk.CTkFrame(left, fg_color=COL["card"], corner_radius=12)
        hist.pack(fill="x")
        self.b_hist = BarChart(hist, lambda i: self.show_bench(len(self.benches) - 1 - i))
        self.b_hist.pack(fill="x", padx=8, pady=8)
        self.b_list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.b_list.pack(fill="both", expand=True, pady=(8, 0))
        right = ctk.CTkScrollableFrame(t, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        self.b_title = ctk.CTkLabel(right, text="", font=self.f.h2, anchor="w")
        self.b_title.pack(fill="x", padx=6, pady=(4, 8))
        tiles = ctk.CTkFrame(right, fg_color="transparent")
        tiles.pack(fill="x")
        self.b_tiles = []
        for i in range(8):
            tile = ctk.CTkFrame(tiles, fg_color=COL["card"], corner_radius=12)
            tile.grid(row=i // 4, column=i % 4, sticky="ew", padx=4, pady=4)
            tiles.grid_columnconfigure(i % 4, weight=1)
            v = ctk.CTkLabel(tile, text="", font=self.f.tile, anchor="w")
            v.pack(fill="x", padx=14, pady=(10, 0))
            c = ctk.CTkLabel(tile, text="", font=self.f.small, text_color=COL["muted"], anchor="w", height=16)
            c.pack(fill="x", padx=14, pady=(0, 10))
            self.b_tiles.append((v, c))
        chart = self.card(right, "Frame rate over the run")
        opts = ctk.CTkFrame(chart, fg_color="transparent")
        opts.pack(fill="x", padx=12, pady=(10, 0))
        self.b_cpu = ctk.CTkSwitch(opts, text="CPU frame time", font=self.f.small, switch_width=36, switch_height=18,
                                   command=lambda: self.show_bench(self.bench_sel))
        self.b_cpu.pack(side="left")
        self.b_gpu = ctk.CTkSwitch(opts, text="GPU frame time", font=self.f.small, switch_width=36, switch_height=18,
                                   command=lambda: self.show_bench(self.bench_sel))
        self.b_gpu.pack(side="left", padx=12)
        self.b_cmp = ctk.CTkOptionMenu(opts, values=["Compare with: none"], width=260, height=28, dynamic_resizing=False,
                                       font=self.f.small, dropdown_font=self.f.small, command=lambda v: self.show_bench(self.bench_sel))
        self.b_cmp.pack(side="right")
        self.b_chart = LineChart(chart)
        self.b_chart.pack(fill="x", padx=8, pady=8)
        info = ctk.CTkFrame(right, fg_color="transparent")
        info.pack(fill="x")
        info.grid_columnconfigure((0, 1), weight=1, uniform="info")
        self.b_sys = ctk.CTkFrame(info, fg_color=COL["card"], corner_radius=12)
        self.b_sys.grid(row=0, column=0, sticky="nsew", padx=(4, 4), pady=(12, 4))
        self.b_set = ctk.CTkFrame(info, fg_color=COL["card"], corner_radius=12)
        self.b_set.grid(row=0, column=1, sticky="nsew", padx=(4, 10), pady=(12, 4))
        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.pack(fill="x", pady=10)
        self.b_open = ctk.CTkButton(btns, text="Open full report", command=lambda: self.bench_sel is not None and self.benches[self.bench_sel].html and self.open_path(self.benches[self.bench_sel].html))
        self.b_open.pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Open benchmark folder", command=lambda: self.open_path(self.root_dir / "Benchmark")).pack(side="left", padx=4)

    def refresh_bench(self):
        self.benches = scan_benchmarks(self.root_dir)
        for w in self.b_list.winfo_children():
            w.destroy()
        if not self.benches:
            ctk.CTkLabel(self.b_list, text="No benchmark runs found.\nRun the benchmark from the game's graphics settings.",
                         text_color=COL["muted"], font=self.f.small, justify="left").pack(pady=20, padx=8)
            self.b_hist.plot([], None)
            self.b_title.configure(text="")
            return
        self.b_items = []
        for i in range(len(self.benches) - 1, -1, -1):
            b = self.benches[i]
            item = BenchItem(self.b_list, self, b, lambda i=i: self.show_bench(i))
            item.pack(fill="x", pady=3, padx=(0, 6))
            self.b_items.append((i, item))
        self.b_cmp.configure(values=["Compare with: none"] + [f"Compare with: {nice_date(b.when)}" for b in reversed(self.benches)])
        self.show_bench(len(self.benches) - 1)

    def bench_detail(self, b):
        if b.detail is None:
            try:
                b.detail = parse_bench_html(b.html) if b.html else {}
            except OSError:
                b.detail = {}
        return b.detail

    def show_bench(self, i):
        if not self.benches or i is None:
            return
        self.bench_sel = i
        b = self.benches[i]
        d = self.bench_detail(b)
        s, x = b.stats, d.get("extra", {})
        fps = d.get("fps", np.array([]))
        p1 = float(np.percentile(fps, 1)) if fps.size else None
        self.b_title.configure(text=f"Run from {nice_date(b.when)}")

        def f(v, fmt):
            return fmt.format(v) if v is not None else "n/a"

        tiles = [(f(s.get("Fps"), "{:.0f}"), "average fps"), (f(p1, "{:.0f}"), "1st percentile of sampled fps"),
                 (f(s.get("LowestFps"), "{:.0f}"), "lowest fps"), (f(s.get("HighestFps"), "{:.0f}"), "highest fps"),
                 (f(s.get("RawFrameTimes"), "{:.1f} s"), "duration"), (f(s.get("RawFrameCounts"), "{:,.0f}"), "frames"),
                 (f(x.get("gpu_load"), "{:.0f}%"), "GPU load"), (f(x.get("gpu_temperature"), "{:.0f} °C"), "GPU temperature")]
        for (vl, cl), (v, c) in zip(self.b_tiles, tiles):
            vl.configure(text=v)
            cl.configure(text=c)
        series = [{"y": fps, "color": COL["accent"], "label": "FPS", "unit": " fps", "axis": "l", "width": 2}]
        cmp_label = self.b_cmp.get().removeprefix("Compare with: ")
        other = next((o for o in self.benches if nice_date(o.when) == cmp_label and o is not b), None)
        if other:
            ofps = self.bench_detail(other).get("fps", np.array([]))
            series.append({"y": ofps, "color": COL["info"], "label": f"FPS {cmp_label}", "unit": " fps", "axis": "l",
                           "width": 1.5, "dash": (4, 3)})
        if self.b_cpu.get():
            series.append({"y": d.get("cpu", np.array([])), "color": COL["ok"], "label": "CPU ms", "unit": " ms", "axis": "r", "width": 1.5})
        if self.b_gpu.get():
            series.append({"y": d.get("gpu", np.array([])), "color": COL["violet"], "label": "GPU ms", "unit": " ms", "axis": "r", "width": 1.5})
        self.b_chart.plot(series, s.get("RawFrameTimes"))
        rows = list(d.get("system", {}).items())
        if x.get("vram_used") is not None:
            rows.append(("vram", f"{x['vram_used']:.2f} of {x.get('sys_vram_total') or 0:.2f} GB"))
        if x.get("ram_used") is not None:
            rows.append(("ram", f"{x['ram_used']:.2f} of {x.get('sys_ram_total') or 0:.2f} GB"))
        if s.get("LoadingTime") is not None:
            rows.append(("loading time", f"{s['LoadingTime']:,.0f} (unit not documented, likely ms)"))
        self.fill_kv(self.b_sys, "System", [(k.replace("_", " ").upper() if len(k) <= 4 else k.capitalize(), v) for k, v in rows])
        self.fill_kv(self.b_set, "Settings used", d.get("settings", []) or [("", "Open the full report for details.")])
        self.b_open.configure(state="normal" if b.html else "disabled")
        self.b_hist.plot([(nice_date(o.when), o.stats.get("Fps")) for o in self.benches], i)
        for j, item in getattr(self, "b_items", []):
            item.set_selected(j == i)

    def fill_kv(self, frame, title, rows):
        for w in frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(frame, text=title.upper(), font=self.f.caps, text_color=COL["faint"], anchor="w").pack(fill="x", padx=14, pady=(12, 4))
        for k, v in rows:
            line = ctk.CTkFrame(frame, fg_color="transparent")
            line.pack(fill="x", padx=14)
            ctk.CTkLabel(line, text=k, width=120, anchor="w", font=self.f.small, text_color=COL["muted"], height=22).pack(side="left")
            ctk.CTkLabel(line, text=v.strip(), anchor="w", font=self.f.small, height=22, wraplength=210, justify="left").pack(side="left", fill="x")
        ctk.CTkFrame(frame, height=10, fg_color="transparent").pack()

    # tabs, polling, close

    def on_tab(self):
        name = self.tabs.get()
        self.store.data["settings"]["last_tab"] = name
        if name in self.stale:
            self.stale.discard(name)
            {"Profile": self.build_profile_tab, "Transfer": self.refresh_transfer, "Backups": self.refresh_backups}[name]()
        if name == "Benchmarks" and self.benches is None:
            self.refresh_bench()

    def set_running(self, running, force=False):
        if running == self.running and not force:
            return
        self.running = running
        if running:
            self.h_game.configure(text="  Siege is running · saving is paused  ", fg_color=COL["bad_dim"], text_color=COL["bad"])
        else:
            self.h_game.configure(text="  Siege is closed  ", fg_color=COL["ok_dim"], text_color=COL["ok"])
        self.update_dirty()
        self.update_transfer_count()
        if self.tabs.get() == "Backups":
            self.refresh_backups()
        else:
            self.stale.add("Backups")

    def poll(self):
        now = time.monotonic()
        if not self._polling and now - self._last_poll > 4:
            self._polling, self._last_poll = True, now
            threading.Thread(target=lambda: self._poll_q.put(siege_running()), daemon=True).start()
        try:
            while True:
                self.set_running(self._poll_q.get_nowait())
                self._polling = False
        except queue.Empty:
            pass
        if self.current:
            m = self.current.mtime
            if m != self.file_mtime:
                self.file_mtime = m
                if self.pending:
                    self.toast("The settings file changed on disk. Saving applies your edits on top of the new file.", "info")
                else:
                    self.reload_current()
                    self.toast("The settings file changed on disk and was reloaded.", "info")
        self.after(1000, self.poll)

    def on_close(self):
        if not self.confirm_discard():
            return
        self.store.data["settings"]["geometry"] = self.geometry()
        try:
            self.store.save()
        except OSError:
            pass
        self.destroy()


class BenchItem(Hover, ctk.CTkFrame):
    def __init__(self, master, app, bench, command):
        super().__init__(master, fg_color=COL["card"], corner_radius=10, border_width=0, border_color=COL["accent"])
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(top, text=nice_date(bench.when), font=app.f.bold, anchor="w", height=20).pack(side="left")
        fps = bench.stats.get("Fps")
        ctk.CTkLabel(top, text=f"{fps:.0f} fps" if fps else "", font=app.f.bold, text_color=COL["accent"], height=20).pack(side="right")
        lo, hi = bench.stats.get("LowestFps"), bench.stats.get("HighestFps")
        sub = f"low {lo:.0f}  ·  high {hi:.0f}" if lo is not None and hi is not None else ""
        ctk.CTkLabel(self, text=sub, font=app.f.small, text_color=COL["muted"], anchor="w", height=18).pack(fill="x", padx=12, pady=(0, 8))
        self.hover_bind(COL["card"], COL["card_hi"])
        bind_tree(self, "<Button-1>", lambda e: command())

    def set_selected(self, on):
        self.configure(border_width=2 if on else 0)


def main():
    parser = argparse.ArgumentParser(prog="r6_siege_settings_manager")
    parser.add_argument("--root", help="folder that holds the profile UUID folders")
    args = parser.parse_args()
    App(args.root).mainloop()


if __name__ == "__main__":
    main()
