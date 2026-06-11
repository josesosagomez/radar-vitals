"""Unit test for radar_io.parse_logfile.

Uses the exact LogFile format produced by the DCA1000EVM for exp001
(Tue Jun 09 11:59:55 2026 local / UTC+3 → UTC epoch 1780995595).
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.radar_io import parse_logfile  # noqa: E402

_LOGFILE_CONTENT = """\
Start record configuration :
,
Log mode : Raw
LVDS lane mode : 2 lane
Record stop mode : Infinite
Max file size (MB) : 1024,
,*DT 1,

Raw Data :
Out of sequence count - 0
Out of sequence seen from 0 to 0
First Packet ID - 1
Last Packet ID - 270066
Number of received packets - 270066
Number of zero filled packets - 0
Number of zero filled bytes - 0
Capture start time - Tue Jun 09 11:59:55 2026
Capture end time - Tue Jun 09 12:02:27 2026
Duration(sec) - 152"""


def test_parse_logfile_utc_conversion(tmp_path):
    logfile = tmp_path / "exp001_sit_140cm_radar_LogFile.csv"
    logfile.write_text(_LOGFILE_CONTENT, encoding="utf-8")

    result = parse_logfile(logfile, utc_offset_hours=3)

    assert result == {
        "start_epoch_utc": 1780995595,
        "end_epoch_utc": 1780995747,
        "duration_s": 152,
    }


def test_parse_logfile_missing_rows_raises(tmp_path):
    logfile = tmp_path / "bad_LogFile.csv"
    logfile.write_text("Log mode : Raw\nSome other content\n", encoding="utf-8")

    with pytest.raises(ValueError, match="rows not found"):
        parse_logfile(logfile, utc_offset_hours=3)
