"""
Generate sample WAV audio files simulating field sensor captures.

These are synthetic waveforms, not real environmental recordings.
In production, you'd use actual field audio. For the lab, these
test the full pipeline: upload → classify → store.
"""
import struct
import wave
import math
import os
import random

OUTPUT_DIR = os.path.expanduser("~/audio-classifier/samples")


def generate_wav(filepath: str, duration: float, sample_rate: int = 16000,
                 frequencies: list = None, noise_level: float = 0.1,
                 amplitude: float = 0.5):
    """
    Generate a WAV file with mixed sine waves and noise.
    
    Args:
        filepath: Output file path
        duration_sec: Duration in seconds
        sample_rate: Samples per second (16kHz is good for speech/environmental)
        frequencies: List of (freq_hz, rel_amplitude) tuples
        noise_level: Random noise amplitude (0.0 to 1.0)
        amplitude: Overall amplitude scaling
    """
    if frequencies is None:
        frequencies = [(440, 1.0)]

    num_samples = int(duration * sample_rate)
    samples = []

    for i in range(num_samples):
        t = i / sample_rate
        value = 0.0

        # Mix sine waves
        for freq, rel_amp in frequencies:
            value += rel_amp * math.sin(2 * math.pi * freq * t)

        # Add noise
        value += noise_level * (random.random() * 2 - 1)

        # Normalize and scale
        value = max(-1.0, min(1.0, value * amplitude))
        samples.append(value)

    # Write WAV file (16-bit PCM)
    with wave.open(filepath, 'w') as wav:
        wav.setnchannels(1)          # Mono
        wav.setsampwidth(2)          # 16-bit
        wav.setframerate(sample_rate)

        for sample in samples:
            packed = struct.pack('<h', int(sample * 32767))
            wav.writeframes(packed)


def create_test_samples():
    """Create audio samples simulating different environmental sounds."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    samples = {
        # Flowing water: broadband noise with low-frequency emphasis
        "flowing_water_normal": {
            "duration": 5.0,
            "frequencies": [(80, 0.3), (160, 0.2), (320, 0.15), (640, 0.1)],
            "noise_level": 0.4,
            "amplitude": 0.3,
        },
        # Heavy water / rapids: more energy, wider frequency range
        "flowing_water_heavy": {
            "duration": 5.0,
            "frequencies": [(60, 0.5), (120, 0.4), (240, 0.3), (480, 0.25), (960, 0.2)],
            "noise_level": 0.6,
            "amplitude": 0.6,
        },
        # Rain: high-frequency noise bursts
        "rain": {
            "duration": 5.0,
            "frequencies": [(2000, 0.1), (4000, 0.15), (6000, 0.1)],
            "noise_level": 0.5,
            "amplitude": 0.3,
        },
        # Thunder: low-frequency rumble
        "thunder": {
            "duration": 3.0,
            "frequencies": [(30, 0.8), (60, 0.5), (90, 0.3)],
            "noise_level": 0.2,
            "amplitude": 0.7,
        },
        # Boat engine: low-frequency drone with harmonic content
        "boat_engine": {
            "duration": 5.0,
            "frequencies": [(80, 0.6), (160, 0.4), (240, 0.2)],
            "noise_level": 0.15,
            "amplitude": 0.5,
        },
        # Quiet ambient: near-silence with minor noise
        "quiet_ambient": {
            "duration": 5.0,
            "frequencies": [(200, 0.05)],
            "noise_level": 0.05,
            "amplitude": 0.1,
        },
        # Crowd/beach noise: broadband mid-frequency human activity
        "crowd_activity": {
            "duration": 4.0,
            "frequencies": [(300, 0.3), (600, 0.25), (900, 0.2), (1200, 0.15)],
            "noise_level": 0.4,
            "amplitude": 0.4,
        },
        # Wave impact: low-frequency burst with broadband decay
        "wave_impact": {
            "duration": 3.0,
            "frequencies": [(40, 0.7), (80, 0.5), (200, 0.3)],
            "noise_level": 0.5,
            "amplitude": 0.7,
        },
    }

    for name, params in samples.items():
        filepath = os.path.join(OUTPUT_DIR, f"gauge-001-{name}.wav")
        generate_wav(filepath, **params)
        
        # File size
        size_kb = os.path.getsize(filepath) / 1024
        print(f"Created: {filepath} ({size_kb:.0f} KB, {params['duration']}s)")

    print(f"\nGenerated {len(samples)} audio samples in {OUTPUT_DIR}")


if __name__ == "__main__":
    create_test_samples()
