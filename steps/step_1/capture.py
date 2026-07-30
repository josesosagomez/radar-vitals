"""
scripts/capture.py — DCA1000EVM + IWR1642BOOST raw ADC capture

Talks to the DCA1000 directly over UDP (no mmWave Studio / CLI binary required).
Sends the full SDK 3.x CLI sequence to the IWR1642 over the Application/User UART.

Protocol reference : notes/dca1000_protocol.md
mmWave Studio ref  : config/vital_signs.lua
Config             : steps/step_1/capture_config.yaml

Usage:
    python steps/step_1/capture.py         # full session — prompts for session ID, posture, etc.
    python steps/step_1/capture.py --test  # 100-frame smoke test (same prompts apply)
"""

import argparse
import csv
import hashlib
import json
import socket
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

import serial
import yaml


# ── DCA1000 protocol constants ────────────────────────────────────────────────
# All confirmed from TI source: commandsprotocol.cpp, rf_api.cpp, rf_api_internal.h
# See notes/dca1000_protocol.md §2

_HDR = b'\x5a\xa5'   # 0xA55A little-endian
_FTR = b'\xaa\xee'   # 0xEEAA little-endian

CMD_SYSTEM_CONNECT    = 0x09
CMD_READ_FPGA_VERSION = 0x0e
CMD_CONFIG_FPGA_GEN   = 0x03
CMD_CONFIG_PKT_DATA   = 0x0b
CMD_RECORD_START      = 0x05
CMD_RECORD_STOP       = 0x06

PREPARATION_TIME = 10

# CONFIG_FPGA_GEN 6-byte payload (dca1000_protocol.md §2.3):
# [RAW_MODE=1, TWO_LANE=2, CAPTURE=1, ETH_STREAM=2, BIT16=3, timer=30]
_FPGA_GEN_PAYLOAD = bytes([0x01, 0x02, 0x01, 0x02, 0x03, 0x1e])

# CONFIG_PACKET_DATA 6-byte payload (dca1000_protocol.md §2.4):
# [MAX_BYTES=1470 → 0xBE05 LE, delay=25µs → 25×125=3125 → 0x350C LE, reserved=0]
_PKT_DATA_PAYLOAD = bytes([0xbe, 0x05, 0x35, 0x0c, 0x00, 0x00])

PAYLOAD_BYTES_PER_PKT = 1456   # rf_api_internal.h PAYLOAD_BYTES_PER_PACKET
SOCKET_RECV_BUF       = 1470   # MAX_BYTES_PER_PACKET
CMD_TIMEOUT_S         = 3.0
DATA_RECV_BUF_BYTES   = 8 * 1024 * 1024   # 8 MB SO_RCVBUF


class DCA1000:
    def __init__(self, cfg: dict):
        self._dca_ip   = cfg['dca1000_ip']
        self._host_ip  = cfg['host_ip']
        self._cmd_port = cfg['cmd_port']
        self._dat_port = cfg['data_port']
        self._sock_cmd = None
        self._sock_dat = None

    # ── internal ──────────────────────────────────────────────────────────────

    def _build_pkt(self, code: int, payload: bytes = b'') -> bytes:
        return (_HDR
                + struct.pack('<H', code)
                + struct.pack('<H', len(payload))
                + payload
                + _FTR)

    def _send_cmd(self, code: int, payload: bytes = b'',
                  allow_status: frozenset | None = frozenset({0})) -> bytes:
        """
        Send a DCA1000 command and return the full response bytes.
        allow_status=None skips the status check (used for READ_FPGA_VERSION,
        whose response bytes carry version data, not a success/fail code).
        """
        self._sock_cmd.sendto(
            self._build_pkt(code, payload),
            (self._dca_ip, self._cmd_port)
        )
        try:
            resp = self._sock_cmd.recv(SOCKET_RECV_BUF)
        except socket.timeout:
            raise RuntimeError(f'DCA1000: no response to command 0x{code:02x}')
        if allow_status is not None:
            status = struct.unpack_from('<H', resp, 4)[0]
            if status not in allow_status:
                raise RuntimeError(
                    f'DCA1000: command 0x{code:02x} failed — status=0x{status:04x}'
                )
        return resp

    # ── public API ────────────────────────────────────────────────────────────

    def configure(self):
        """Open sockets and send the four FPGA configuration commands."""
        self._sock_cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock_cmd.bind((self._host_ip, self._cmd_port))
        self._sock_cmd.settimeout(CMD_TIMEOUT_S)

        self._sock_dat = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock_dat.bind((self._host_ip, self._dat_port))
        self._sock_dat.setsockopt(
            socket.SOL_SOCKET, socket.SO_RCVBUF, DATA_RECV_BUF_BYTES
        )

        print('  DCA1000 SYSTEM_CONNECT ...', end=' ', flush=True)
        self._send_cmd(CMD_SYSTEM_CONNECT)
        print('ok')

        # READ_FPGA_VERSION: response bytes at offset 4-5 carry the version number,
        # not a success/fail code — so we skip the status check and log the version.
        print('  DCA1000 READ_FPGA_VERSION ...', end=' ', flush=True)
        resp = self._send_cmd(CMD_READ_FPGA_VERSION, allow_status=None)
        major = resp[5]
        minor = resp[4]
        print(f'ok (FPGA version {major}.{minor})')

        print('  DCA1000 CONFIG_FPGA_GEN ...', end=' ', flush=True)
        self._send_cmd(CMD_CONFIG_FPGA_GEN, _FPGA_GEN_PAYLOAD)
        print('ok')

        # Brief pause: FPGA re-initialises its internal state after CONFIG_FPGA_GEN
        # and may not respond to the next command for ~100 ms.
        time.sleep(0.15)

        print('  DCA1000 CONFIG_PACKET_DATA ...', end=' ', flush=True)
        try:
            self._send_cmd(CMD_CONFIG_PKT_DATA, _PKT_DATA_PAYLOAD)
            print('ok')
        except RuntimeError:
            # Non-fatal: DCA1000 default packet settings (1470 B, 25 µs delay)
            # match our payload exactly, so the capture proceeds unchanged.
            print('no response — using FPGA defaults (1470 B / 25 µs), continuing')

    def start(self):
        print('  DCA1000 RECORD_START ...', end=' ', flush=True)
        self._send_cmd(CMD_RECORD_START)
        print('ok')

    def stop(self):
        print('  DCA1000 RECORD_STOP ...', end=' ', flush=True)
        # Status 0x100 is expected and non-fatal (dca1000_protocol.md §2.1)
        resp = self._send_cmd(
            CMD_RECORD_STOP,
            allow_status=frozenset({0, 0x100})
        )
        status = struct.unpack_from('<H', resp, 4)[0]
        print('ok' if status == 0 else 'ok (status 0x100, non-fatal as expected)')

    def receive(self, n_frames: int, bytes_per_frame: int, out_path: Path) -> dict:
        """
        Collect UDP ADC data packets into a .bin file.
        Returns a stats dict with packet counts, timestamps, and byte totals.

        Packet layout (dca1000_protocol.md §3.1):
          [0:4]  seq number  (uint32 LE)
          [4:10] byte count  (uint48 LE, cumulative ADC bytes sent so far)
          [10:]  ADC payload (up to 1456 bytes)

        Gap handling: missing packets are zero-filled (bytearray is pre-zeroed).
        """
        total_bytes = n_frames * bytes_per_frame
        buf = bytearray(total_bytes)

        write_offset     = 0
        first_seq        = None
        last_seq         = 0
        n_received       = 0
        n_dropped        = 0
        zero_filled_bytes = 0

        timeout_s    = n_frames * (bytes_per_frame / 1e6) * 2 + 30
        t_start_wall = datetime.now()
        t_start      = time.monotonic()
        t_last_print = t_start

        self._sock_dat.settimeout(5.0)
        print(f'\n  Receiving {n_frames} frames  '
              f'({total_bytes / 1e6:.1f} MB expected) ...')

        while write_offset < total_bytes:
            if time.monotonic() - t_start > timeout_s:
                print(f'\n  WARNING: receive timeout ({timeout_s:.0f} s)')
                break
            try:
                pkt = self._sock_dat.recv(SOCKET_RECV_BUF)
            except socket.timeout:
                print('\n  WARNING: UDP data socket timed out waiting for packet')
                break

            if len(pkt) < 10:
                continue

            seq     = struct.unpack_from('<I', pkt, 0)[0]
            payload = pkt[10:]
            plen    = len(payload)

            # Discard duplicates and out-of-order late arrivals
            if last_seq and seq <= last_seq:
                continue

            if first_seq is None:
                first_seq = seq
                # Bug fix: zero-fill leading loss (first received seq > 1 means
                # the FPGA already sent seq-1 packets we never saw).
                if seq > 1:
                    gap        = seq - 1
                    n_dropped += gap
                    gap_bytes  = min(gap * PAYLOAD_BYTES_PER_PKT, total_bytes)
                    write_offset     += gap_bytes
                    zero_filled_bytes += gap_bytes

            # Zero-fill mid-stream gaps
            elif seq > last_seq + 1:
                gap        = seq - last_seq - 1
                n_dropped += gap
                gap_bytes  = min(gap * PAYLOAD_BYTES_PER_PKT,
                                 total_bytes - write_offset)
                write_offset      += gap_bytes
                zero_filled_bytes += gap_bytes

            # Clip payload if near end
            if write_offset + plen > total_bytes:
                plen = total_bytes - write_offset

            buf[write_offset:write_offset + plen] = payload[:plen]
            write_offset += plen
            last_seq      = seq
            n_received   += 1

            now = time.monotonic()
            if now - t_last_print >= 10.0:
                pct  = write_offset / total_bytes * 100
                rate = write_offset / (now - t_start) / 1e6
                print(f'  {pct:5.1f}%  '
                      f'{write_offset / 1e6:.0f}/{total_bytes / 1e6:.0f} MB  '
                      f'{rate:.1f} MB/s  drops={n_dropped}')
                t_last_print = now

        elapsed    = time.monotonic() - t_start
        t_end_wall = datetime.now()
        print(f'  Done: {write_offset / 1e6:.1f} MB in {elapsed:.1f} s  '
              f'pkts={n_received}  drops={n_dropped}')

        out_path.write_bytes(buf[:write_offset])
        return {
            'bytes_written':     write_offset,
            'first_seq':         first_seq or 1,
            'last_seq':          last_seq,
            'n_received':        n_received,
            'n_dropped':         n_dropped,
            'zero_filled_bytes': zero_filled_bytes,
            't_start':           t_start_wall,
            't_end':             t_end_wall,
            'elapsed_s':         elapsed,
        }

    def close(self):
        for s in (self._sock_cmd, self._sock_dat):
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass


# ── IWR1642 UART CLI ──────────────────────────────────────────────────────────
# CLI port: XDS110 Class Application/User UART, 115200 baud
# (the LOWER-numbered of the two COM ports; the higher one is the 921600 debug port)

CLI_CMD_TIMEOUT_S = 5.0

class IWR1642:
    def __init__(self, port: str, baud: int = 115200):
        self._ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=CLI_CMD_TIMEOUT_S,
        )
        time.sleep(0.1)
        self._ser.reset_input_buffer()

    def _send(self, line: str, ignore_error: bool = False):
        """
        Send one CLI command; wait for 'Done' or raise on 'Error'/timeout.
        ignore_error=True: log a warning instead of raising (used for sensorStop
        and flushCfg which legitimately fail when no session is active).
        """
        # Flush stale bytes from any previous command's trailing prompt.
        self._ser.reset_input_buffer()
        self._ser.write((line.strip() + '\r\n').encode('ascii'))
        t0   = time.monotonic()
        resp = b''
        while time.monotonic() - t0 < CLI_CMD_TIMEOUT_S:
            chunk = self._ser.read(self._ser.in_waiting or 1)
            resp += chunk
            if b'Done' in resp:
                time.sleep(0.05)
                self._ser.reset_input_buffer()
                return
            if b'Error' in resp:
                msg = resp.decode('ascii', errors='replace').strip()
                if ignore_error:
                    print(f'  (IWR1642 "{line}" ignored: {msg})')
                    time.sleep(0.05)
                    self._ser.reset_input_buffer()
                    return
                raise RuntimeError(f'IWR1642 CLI Error on "{line}": {msg}')
        raise TimeoutError(
            f'IWR1642 CLI timeout on "{line}" — '
            f'got: {resp.decode("ascii", errors="replace").strip()!r}'
        )

    def configure(self, n_frames: int, cfg: dict):
        """
        Send the full SDK 3.x legacy-frame CLI sequence.

        CRITICAL: adcbufCfg SampleSwap=1 must not be changed.
        SampleSwap=0 silently disables LVDS output in the SDK demo firmware.
        See notes/dca1000_protocol.md §5.4 and HANDOFF.md §5.
        """
        p = cfg['profile']
        f = cfg['frame']

        # sensorStop and flushCfg reset any stale firmware state from a previous
        # run. Both legitimately fail (Error) when no session is active — ignore.
        self._send('sensorStop', ignore_error=True)
        self._send('flushCfg',   ignore_error=True)

        commands = [
            'dfeDataOutputMode 1',
            f'channelCfg {p["rx_channel_en"]} {p["tx_channel_en"]} 0',
            'adcCfg 2 1',
            'adcbufCfg -1 0 1 1 1',
            (f'profileCfg 0 '
             f'{p["start_freq_ghz"]} '
             f'{p["idle_time_us"]} '
             f'{p["adc_start_time_us"]} '
             f'{p["ramp_end_time_us"]} '
             f'0 0 '
             f'{p["freq_slope_mhz_us"]} '
             f'0 '
             f'{p["num_adc_samples"]} '
             f'{p["dig_out_sample_rate"]} '
             f'0 0 '
             f'{p["rx_gain_db"]}'),
            'chirpCfg 0 0 0 0 0 0 0 1',
            'bpmCfg -1 0 0 1',                   # must come before frameCfg (SDK 3.x requirement)
            f'frameCfg 0 0 {f["num_loops"]} {n_frames} {f["period_ms"]:.1f} 1 0',
            'lowPower 0 1',
            'guiMonitor -1 0 0 0 0 0 0',
            'cfarCfg -1 0 2 8 4 3 0 15 0',   # range direction;  threshScale in dB (max 100)
            'cfarCfg -1 1 0 4 2 3 1 15 0',   # Doppler direction; cyclicMode=1 for wrap-around
            'multiObjBeamForming -1 1 0.5',
            'clutterRemoval -1 0',
            'calibDcRangeSig -1 0 -5 8 256',
            'extendedMaxVelocity -1 0',
            'lvdsStreamCfg -1 0 1 0',
            'compRangeBiasAndRxChanPhase 0.0 1 0 1 0 1 0 1 0 1 0 1 0 1 0 1 0',
            'measureRangeBiasAndRxChanPhase 0 1.5 0.2',
            'CQRxSatMonitor 0 3 4 99 0',
            'CQSigImgMonitor 0 31 4',
            'analogMonitor 0 0',
            'aoaFovCfg -1 -90 90 -90 90',
            'cfarFovCfg -1 0 0 8.25',
            'cfarFovCfg -1 1 -2 2.00',
            'calibData 0 0 0',
        ]

        print(f'  IWR1642: configuring ({len(commands)} commands) ...')
        for cmd in commands:
            self._send(cmd)
        print('  IWR1642: configuration done')

    def start(self):
        print('  IWR1642: sensorStart ...', end=' ', flush=True)
        self._send('sensorStart')
        print('ok')

    def stop(self):
        print('  IWR1642: sensorStop ...', end=' ', flush=True)
        self._send('sensorStop')
        print('ok')

    def close(self):
        try:
            self._ser.close()
        except Exception:
            pass


# ── helpers ───────────────────────────────────────────────────────────────────

def _range_res_m(cfg: dict) -> float:
    """
    Range resolution per bin (metres).
    δR = c / (2 × BW), where BW = slope_hz_per_s × (num_samples / sample_rate_sps).
    Verified: 0.0436 m for freq_slope=70.006 MHz/µs, 256 samples @ 5209 ksps.
    """
    p = cfg['profile']
    return (3e8 * p['dig_out_sample_rate'] * 1e3) / (
        2.0 * p['freq_slope_mhz_us'] * 1e12 * p['num_adc_samples']
    )


def _write_logfile(path: Path, stats: dict):
    """Write a LogFile.csv in the same format mmWave Studio produces."""
    t_fmt = '%a %b %d %H:%M:%S %Y'
    lines = [
        'Start record configuration : ',
        ',',
        'Log mode : Raw',
        'LVDS lane mode : 2 lane',
        'Record stop mode : Infinite',
        'Max file size (MB) : 1024,',
        ',*DT 1,',
        '',
        'Raw Data :',
        f'Out of sequence count - {stats["n_dropped"]}',
        'Out of sequence seen from 0 to 0',
        f'First Packet ID - {stats["first_seq"]}',
        f'Last Packet ID - {stats["last_seq"]}',
        f'Number of received packets - {stats["n_received"]}',
        f'Number of zero filled packets - {stats["n_dropped"]}',
        f'Number of zero filled bytes - {stats["zero_filled_bytes"]}',
        f'Capture start time - {stats["t_start"].strftime(t_fmt)}',
        f'Capture end time - {stats["t_end"].strftime(t_fmt)}',
        f'Duration(sec) - {int(stats["elapsed_s"])}',
    ]
    path.write_text('\n'.join(lines))


_MANIFEST_FIELDS = [
    'session_id', 'participant_id', 'split',
    'locked_participant_never_used_for_tuning', 'timezone',
    'radar_start_epoch_seconds', 'radar_to_reference_offset_seconds',
    'radar_sync_elapsed_seconds', 'reference_sync_epoch_seconds',
    'posture', 'distance_cm', 'locked_bin', 'radar_orientation',
    'masimo_model', 'stationary_intervals', 'exclusion_reason',
    'notes', 'range_resolution_m', 'iq_swap',
]


def _load_manifest(path: Path):
    if not path.exists():
        return [], _MANIFEST_FIELDS
    with path.open(newline='') as f:
        reader = csv.DictReader(f)
        rows   = list(reader)
        fields = list(reader.fieldnames or _MANIFEST_FIELDS)
    return rows, fields


def _save_manifest(path: Path, rows: list, fields: list):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


_POSTURE_CHOICES = [
    'seated_no_back',
    'seated_chair_back',
    'supine',
    'standing',
]


def _prompt_session(out_dir: Path, manifest_path: Path, capture_duration_s: float):
    """
    Prompt for session metadata. Returns (session_dict, manifest_rows, manifest_fields).
    Handles override: if session_id already exists, asks to confirm and deletes old files.
    """
    print('\nSession info:')

    while True:
        session_id = input('  Session ID: ').strip()
        if session_id:
            break
        print('  (session ID cannot be empty)')

    participant_id = input('  Participant ID [T_001]: ').strip() or 'T_001'

    print('  Posture:')
    for i, label in enumerate(_POSTURE_CHOICES, 1):
        print(f'    {i}) {label}')
    while True:
        choice = input('  Choice [1]: ').strip() or '1'
        try:
            posture = _POSTURE_CHOICES[int(choice) - 1]
            break
        except (ValueError, IndexError):
            print(f'  (enter a number 1–{len(_POSTURE_CHOICES)})')

    while True:
        dist_str = input('  Distance (cm): ').strip()
        try:
            distance_cm = int(dist_str)
            break
        except ValueError:
            print('  (enter an integer number of cm)')

    default_start = 30 if capture_duration_s > 30 else 0
    default_end   = int(capture_duration_s)
    default_intervals = f'{default_start}-{default_end}'
    while True:
        raw = input(f'  Stationary intervals (e.g. {default_intervals}): ').strip()
        raw = raw or default_intervals
        try:
            start_s, end_s = (int(x) for x in raw.split('-'))
            if not (0 <= start_s < end_s <= capture_duration_s):
                raise ValueError
            stationary_intervals = raw
            break
        except ValueError:
            print(f'  (must be start-end with 0 <= start < end <= {int(capture_duration_s)})')

    session = {
        'session_id':           session_id,
        'participant_id':       participant_id,
        'posture':              posture,
        'distance_cm':          str(distance_cm),
        'stationary_intervals': stationary_intervals,
    }

    rows, fields = _load_manifest(manifest_path)

    if any(r['session_id'] == session_id for r in rows):
        print(f'\n  WARNING: session "{session_id}" already exists in manifest.')
        resp = input(
            '  Override (delete existing .bin, _LogFile.csv, _meta.json)? [y/N] '
        ).strip().lower()
        if resp != 'y':
            print('Aborted.')
            sys.exit(0)
        for suffix in ['.bin', '_LogFile.csv', '_meta.json']:
            old = out_dir / f'{session_id}{suffix}'
            if old.exists():
                old.unlink()
                print(f'  Deleted: {old}')
        for i in range(10):
            old = out_dir / f'{session_id}_{i}.bin'
            if old.exists():
                old.unlink()
                print(f'  Deleted: {old}')
        rows = [r for r in rows if r['session_id'] != session_id]

    return session, rows, fields


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description='DCA1000EVM + IWR1642BOOST raw ADC capture'
    )
    ap.add_argument(
        '--config', default='steps/step_1/capture_config.yaml',
        help='Path to capture_config.yaml (default: steps/step_1/capture_config.yaml)'
    )
    ap.add_argument(
        '--test', action='store_true',
        help='Smoke test: capture 100 frames only'
    )
    ap.add_argument(
        '--duration', type=float, default=None, metavar='SECONDS',
        help='Override capture duration (default: duration_s from config)'
    )
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f'ERROR: config not found: {cfg_path}')
    with cfg_path.open() as fh:
        cfg = yaml.safe_load(fh)

    p  = cfg['profile']
    fr = cfg['frame']

    duration_s = cfg['capture']['duration_s'] if args.duration is None else args.duration
    n_frames = (
        100
        if args.test
        else round(duration_s / (fr['period_ms'] / 1000.0))
    )

    # bytes per frame: num_rx × num_adc_samples × 2(IQ) × 2(int16) × num_loops
    bytes_per_frame = (
        p['num_rx']
        * p['num_adc_samples']
        * 2   # I and Q
        * 2   # 16-bit → 2 bytes
        * fr['num_loops']
    )
    expected_bytes = n_frames * bytes_per_frame
    range_res      = _range_res_m(cfg)

    out_dir       = Path(cfg['capture']['output_dir'])
    manifest_path = Path('data/manifest.local.csv')
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── pre-session summary ───────────────────────────────────────────────────
    print('\n' + '='*60)
    print(f'  Capture:  {n_frames} frames x {bytes_per_frame} B = '
          f'{expected_bytes / 1e6:.1f} MB')
    print(f'  Duration: ~{n_frames * fr["period_ms"] / 1000:.0f} s')
    print('='*60)

    # ── session metadata prompts ───────────────────────────────────────────────
    capture_duration_s = n_frames * fr['period_ms'] / 1000.0
    session, manifest_rows, manifest_fields = _prompt_session(
        out_dir, manifest_path, capture_duration_s
    )
    session_id = session['session_id']

    out_bin  = out_dir / f'{session_id}.bin'
    out_log  = out_dir / f'{session_id}_LogFile.csv'
    out_meta = out_dir / f'{session_id}_meta.json'

    print(f'\n  Output: {out_bin}\n')

    print('Starting in ', end='', flush=True)
    for i in range(PREPARATION_TIME, 0, -1):
        print(f'{i} ', end='', flush=True)
        time.sleep(1)
    print()

    radar = IWR1642(cfg['uart']['port'], cfg['uart']['baud'])
    dca   = DCA1000(cfg)
    stats            = {}
    radar_start_epoch = None

    try:
        print('[1/5] Configuring DCA1000 ...')
        dca.configure()

        print('\n[2/5] Configuring IWR1642 ...')
        radar.configure(n_frames, cfg)

        print('\n[3/5] Starting capture ...')
        dca.start()
        radar_start_epoch = int(datetime.now().timestamp())
        radar.start()

        print('\n[4/5] Receiving data ...')
        stats = dca.receive(n_frames, bytes_per_frame, out_bin)

        print('\n[5/5] Stopping ...')
        radar.stop()
        dca.stop()

    except Exception as exc:
        print(f'\nERROR during capture: {exc}')
        for fn, obj in [('sensorStop', radar), ('RECORD_STOP', dca)]:
            try:
                obj.stop()
            except Exception as e:
                print(f'  (cleanup {fn} failed: {e})')
        raise
    finally:
        radar.close()
        dca.close()

    # ── write LogFile.csv ─────────────────────────────────────────────────────
    _write_logfile(out_log, stats)

    # ── write metadata JSON ───────────────────────────────────────────────────
    sha256 = hashlib.sha256(out_bin.read_bytes()).hexdigest()
    meta = {
        'session_id':          session_id,
        'participant_id':      session['participant_id'],
        'posture':             session['posture'],
        'distance_cm':         int(session['distance_cm']),
        'stationary_intervals': session['stationary_intervals'],
        'radar_start_epoch':   radar_start_epoch,
        'bin_file':            str(out_bin),
        'bytes_written':       stats['bytes_written'],
        'bytes_expected':      expected_bytes,
        'n_frames':            n_frames,
        'bytes_per_frame':     bytes_per_frame,
        'range_res_m':         round(range_res, 6),
        'sha256':              sha256,
        'config':              cfg,
    }

    # ── update manifest.local.csv ─────────────────────────────────────────────
    written = stats['bytes_written']
    if written != expected_bytes:
        short = expected_bytes - written
        exclusion_reason = (
            f'incomplete_capture: {short} bytes short '
            f'({short / bytes_per_frame:.1f} frames missing — likely UDP drops)'
        )
    else:
        exclusion_reason = ''

    new_row = {
        'session_id':                               session_id,
        'participant_id':                           session['participant_id'],
        'split':                                    'development',
        'locked_participant_never_used_for_tuning': '',
        'timezone':                                 'Asia/Riyadh',
        'radar_start_epoch_seconds':                str(radar_start_epoch),
        'radar_to_reference_offset_seconds':        '0',
        'radar_sync_elapsed_seconds':               '',
        'reference_sync_epoch_seconds':             '',
        'posture':                                  session['posture'],
        'distance_cm':                              session['distance_cm'],
        'locked_bin':                               '',
        'radar_orientation':                        'frontal_chest',
        'masimo_model':                             'MightySat',
        'stationary_intervals':                     session['stationary_intervals'],
        'exclusion_reason':                         exclusion_reason,
        'notes':                                    '',
        'range_resolution_m':                       f'{range_res:.4f}',
        'iq_swap':                                  'True',
    }
    manifest_rows.append(new_row)
    manifest_rows.sort(key=lambda r: r['session_id'])
    _save_manifest(manifest_path, manifest_rows, manifest_fields)

    out_meta.write_text(json.dumps(meta, indent=2), newline='\n')

    # ── final summary ─────────────────────────────────────────────────────────
    print('\n' + '='*60)
    print(f'  Session:  {session_id}')
    print(f'  Output:   {out_bin}')
    print(f'  LogFile:  {out_log}')
    print(f'  Metadata: {out_meta}')
    print(f'  SHA256:   {sha256}')
    print(f'  Written:  {written} / {expected_bytes} bytes', end='  ')
    if exclusion_reason:
        print('INCOMPLETE -- marked excluded in manifest')
        print(f'  Reason:   {exclusion_reason}')
    else:
        print('PASS')
    print(f'  Manifest: {manifest_path}  ({len(manifest_rows)} sessions)')
    print('='*60)


if __name__ == '__main__':
    main()
