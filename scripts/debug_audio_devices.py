#!/usr/bin/env python3
"""Diagnose audio device issues on Ubuntu/Linux.

Run this script on Ubuntu to inspect how sounddevice reports audio devices.
Usage: python scripts/debug_audio_devices.py
"""
import sounddevice as sd

print("=" * 60)
print("sounddevice version:", sd.__version__)
print("=" * 60)

# Default device info
print("\nDefault devices:")
print(f"  sd.default.device = {sd.default.device}")
print(f"  Type: {type(sd.default.device)}")

# Query all devices
devices = sd.query_devices()
print(f"\nFound {len(devices)} devices in total:\n")

input_devices = []
output_devices = []

for i, d in enumerate(devices):
    name = d.get("name", "Unknown")
    in_ch = d.get("max_input_channels", 0)
    out_ch = d.get("max_output_channels", 0)
    sr = d.get("default_samplerate", 0)
    hostapi = d.get("hostapi", -1)

    print(f"[{i}] {name}")
    print(f"    Input channels: {in_ch}, Output channels: {out_ch}")
    print(f"    Sample rate: {sr}, hostapi: {hostapi}")

    if in_ch > 0:
        input_devices.append((i, name, in_ch))
    if out_ch > 0:
        output_devices.append((i, name, out_ch))
    print()

print("=" * 60)
print(f"Input device list ({len(input_devices)}):")
for idx, name, ch in input_devices:
    print(f"  [{idx}] {name} ({ch}ch)")

print(f"\nOutput device list ({len(output_devices)}):")
for idx, name, ch in output_devices:
    print(f"  [{idx}] {name} ({ch}ch)")

# Host APIs
print("\n" + "=" * 60)
print("Host APIs:")
for i, api in enumerate(sd.query_hostapis()):
    print(f"  [{i}] {api['name']}")
    print(f"      Default input: {api['default_input_device']}")
    print(f"      Default output: {api['default_output_device']}")
    print(f"      Device count: {api['device_count']}")
