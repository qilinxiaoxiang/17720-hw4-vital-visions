"""Match iPhone recordings in ~/Downloads to transmit logs by start time and copy them into Data/part3.

Each transmit log records when SPACE was pressed (startedAt). The matching video is the one
whose creation time is the latest one before that moment, within 3 minutes.

Usage: python file_recordings.py [--dry-run]
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DOWNLOADS = Path.home() / "Downloads"
DATA = Path(__file__).resolve().parents[2] / "Data" / "part3"


def created(mov: Path) -> datetime:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time",
                          "-of", "csv=p=0", str(mov)], capture_output=True, text=True, check=True)
    return datetime.fromisoformat(out.stdout.strip().replace("Z", "+00:00"))


def main():
    dry = "--dry-run" in sys.argv
    movs = {m: created(m) for m in sorted(DOWNLOADS.glob("IMG_*/IMG_*.MOV"))}
    logs = sorted(DOWNLOADS.glob("p3_*_txlog*.json"))
    used = set()
    # Newest log first, so a re-recorded label ("... (1).json") wins over the older take.
    entries = []
    for log in logs:
        d = json.loads(log.read_text())
        entries.append((datetime.fromisoformat(d["startedAt"].replace("Z", "+00:00")), d["label"], log))
    entries.sort(reverse=True)
    for started, label, log in entries:
        cands = [(started - c, m) for m, c in movs.items()
                 if m not in used and timedelta(0) <= started - c <= timedelta(minutes=3)]
        if not cands:
            print(f"{label}: no video found for log {log.name} (started {started})")
            continue
        lag, mov = min(cands)
        used.add(mov)
        dst = DATA / f"{label}.mov"
        if any(e[1] == label and e[0] > started for e in entries):
            dst = DATA / f"{label}_take0.mov"
        print(f"{label}: {mov.parent.name} (SPACE {lag.total_seconds():.0f} s after record start) -> {dst.name}")
        if not dry:
            DATA.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mov, dst)
            shutil.copy2(log, dst.with_name(dst.stem + "_txlog.json"))
    for m in movs:
        if m not in used:
            print(f"unmatched video: {m.parent.name}")


if __name__ == "__main__":
    main()
