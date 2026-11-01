#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import numpy as np
import sounddevice as sd


def detect_audio_devices():
    """
    Detect and list all audio devices (using sounddevice).
    """
    print("\n===== Audio Device Detection (SoundDevice) =====\n")

    # Get default devices
    default_input = sd.default.device[0] if sd.default.device else None
    default_output = sd.default.device[1] if sd.default.device else None

    # Store discovered devices
    input_devices = []
    output_devices = []

    # List all devices
    devices = sd.query_devices()
    for i, dev_info in enumerate(devices):
        # Print device info
        print(f"Device {i}: {dev_info['name']}")
        print(f"  - Input channels: {dev_info['max_input_channels']}")
        print(f"  - Output channels: {dev_info['max_output_channels']}")
        print(f"  - Default sample rate: {dev_info['default_samplerate']}")

        # Mark default devices
        if i == default_input:
            print("  - 🎤 System default input device")
        if i == default_output:
            print("  - 🔊 System default output device")

        # Identify input devices (microphones)
        if dev_info["max_input_channels"] > 0:
            input_devices.append((i, dev_info["name"]))
            if "USB" in dev_info["name"]:
                print("  - Likely a USB microphone 🎤")

        # Identify output devices (speakers)
        if dev_info["max_output_channels"] > 0:
            output_devices.append((i, dev_info["name"]))
            if "Headphones" in dev_info["name"]:
                print("  - Likely headphone output 🎧")
            elif "USB" in dev_info["name"] and dev_info["max_output_channels"] > 0:
                print("  - Likely a USB speaker 🔊")

        print("")

    # Summarize discovered devices
    print("\n===== Device Summary =====\n")

    print("Discovered input devices (microphones):")
    for idx, name in input_devices:
        default_mark = " (default)" if idx == default_input else ""
        print(f"  - Device {idx}: {name}{default_mark}")

    print("\nDiscovered output devices (speakers):")
    for idx, name in output_devices:
        default_mark = " (default)" if idx == default_output else ""
        print(f"  - Device {idx}: {name}{default_mark}")

    # Recommend devices
    print("\nRecommended device configuration:")

    # Recommend microphone
    recommended_mic = None
    if default_input is not None:
        recommended_mic = (default_input, devices[default_input]["name"])
    elif input_devices:
        # Prefer USB devices
        for idx, name in input_devices:
            if "USB" in name:
                recommended_mic = (idx, name)
                break
        if recommended_mic is None:
            recommended_mic = input_devices[0]

    # Recommend speaker
    recommended_speaker = None
    if default_output is not None:
        recommended_speaker = (default_output, devices[default_output]["name"])
    elif output_devices:
        # Prefer headphones
        for idx, name in output_devices:
            if "Headphones" in name:
                recommended_speaker = (idx, name)
                break
        if recommended_speaker is None:
            recommended_speaker = output_devices[0]

    if recommended_mic:
        print(f"  - Microphone: Device {recommended_mic[0]} ({recommended_mic[1]})")
    else:
        print("  - No available microphone found")

    if recommended_speaker:
        print(f"  - Speaker: Device {recommended_speaker[0]} ({recommended_speaker[1]})")
    else:
        print("  - No available speaker found")

    print("\n===== SoundDevice Configuration Example =====\n")

    if recommended_mic:
        print("# Microphone initialization code")
        print(f"input_device_id = {recommended_mic[0]}  # {recommended_mic[1]}")
        print("input_stream = sd.InputStream(")
        print("    samplerate=16000,")
        print("    channels=1,")
        print("    dtype=np.int16,")
        print("    blocksize=1024,")
        print(f"    device={recommended_mic[0]},")
        print("    callback=input_callback)")

    if recommended_speaker:
        print("\n# Speaker initialization code")
        print(
            f"output_device_id = {recommended_speaker[0]}  # "
            f"{recommended_speaker[1]}"
        )
        print("output_stream = sd.OutputStream(")
        print("    samplerate=44100,")
        print("    channels=1,")
        print("    dtype=np.int16,")
        print("    blocksize=1024,")
        print(f"    device={recommended_speaker[0]},")
        print("    callback=output_callback)")

    print("\n===== Device Test =====\n")

    # Test recommended devices
    if recommended_mic:
        print(f"Testing microphone (Device {recommended_mic[0]})...")
        try:
            sd.rec(
                int(1 * 16000),
                samplerate=16000,
                channels=1,
                device=recommended_mic[0],
                dtype=np.int16,
            )
            sd.wait()
            print("✓ Microphone test succeeded")
        except Exception as e:
            print(f"✗ Microphone test failed: {e}")

    if recommended_speaker:
        print(f"Testing speaker (Device {recommended_speaker[0]})...")
        try:
            # Generate test audio (440Hz sine wave)
            duration = 0.5
            sample_rate = 44100
            t = np.linspace(0, duration, int(sample_rate * duration))
            test_audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)

            sd.play(test_audio, samplerate=sample_rate, device=recommended_speaker[0])
            sd.wait()
            print("✓ Speaker test succeeded")
        except Exception as e:
            print(f"✗ Speaker test failed: {e}")

    return recommended_mic, recommended_speaker


if __name__ == "__main__":
    try:
        mic, speaker = detect_audio_devices()
        print("\nDetection completed!")
    except Exception as e:
        print(f"Error during detection: {e}")
