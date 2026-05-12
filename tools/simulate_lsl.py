from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
from pylsl import StreamInfo, StreamOutlet

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DEFAULT_TARGET_CHANNELS


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a synthetic Curry-like EEG LSL stream.")
    parser.add_argument("--rate", type=float, default=250.0)
    parser.add_argument("--frequency", type=float, default=10.0)
    parser.add_argument("--name", default="Synthetic-Curry9-EEG")
    args = parser.parse_args()

    info = StreamInfo(args.name, "EEG", len(DEFAULT_TARGET_CHANNELS), args.rate, "float32", "neuroadaptive-synthetic")
    channels = info.desc().append_child("channels")
    for label in DEFAULT_TARGET_CHANNELS:
        channel = channels.append_child("channel")
        channel.append_child_value("label", label)
        channel.append_child_value("unit", "microvolts")
        channel.append_child_value("type", "EEG")
    outlet = StreamOutlet(info)

    print(f"Publishing {args.name} with channels: {', '.join(DEFAULT_TARGET_CHANNELS)}")
    sample_index = 0
    rng = np.random.default_rng(7)
    interval = 1.0 / args.rate
    while True:
        timestamp = sample_index / args.rate
        sample = [
            math.sin(2 * math.pi * args.frequency * timestamp + idx * 0.2) + 0.05 * rng.standard_normal()
            for idx, _ in enumerate(DEFAULT_TARGET_CHANNELS)
        ]
        outlet.push_sample(sample)
        sample_index += 1
        time.sleep(interval)


if __name__ == "__main__":
    main()
