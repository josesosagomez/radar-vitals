# DCA1000EVM + IWR1642 Capture Protocol Reference

Extracted from: `DCA1000EVM_User_Guide.pdf` (SPRUIJ4A), `ADC_Raw_Data_Capture.pdf` (SWRA581B),
`mmwave_sdk_user_guide.pdf` (SDK 3.6), `IWR1642BOOST_User_Guide.pdf` (SWRU521C).
Date extracted: 2026-06-23.

---

## 1. Network Settings

| Parameter | Default value |
|---|---|
| DCA1000 FPGA IP | `192.168.33.180` |
| DCA1000 FPGA MAC | `12-34-56-78-90-12` |
| Host (PC) IP | `192.168.33.30` |
| Command port (UDP) | **4096** |
| ADC data port (UDP) | **4098** |
| CP data port | 4099 |
| CQ data port | 4100 |

Switch SW2 pin 6 selects EEPROM vs FPGA-register config. Default (pin 11 = FPGA registers) uses the table above. Ethernet packet delay: **default 25 µs**, configurable 5–500 µs via CONFIG_PACKET_DATA command (lower = higher throughput, higher = fewer UDP drops).

---

## 2. DCA1000 UDP Command Format

Every command packet sent to port **4096**:

```
Offset  Size  Field
0       2     Header = 0xA55A  (bytes: 0x5A 0xA5, little-endian)
2       2     Command code     (see table below)
4       2     Data size N      (number of payload bytes that follow)
6       N     Command-specific payload
6+N     2     Footer = 0xEEAA  (bytes: 0xAA 0xEE)
```

Response packet received on the same port 4096:

```
Offset  Size  Field
0       2     Header = 0xA55A
2       2     Command code (mirrors request)
4       2     Status: 0 = success, 1 = failure
6       2     Footer = 0xEEAA
```

### 2.1 Command Code Table

| Command name | Code |
|---|---|
| RESET_FPGA | 0x01 |
| RESET_AR_DEV | 0x02 |
| CONFIG_FPGA_GEN | 0x03 |
| CONFIG_EEPROM | 0x04 |
| RECORD_START | 0x05 |
| RECORD_STOP | 0x06 |
| PLAYBACK_START | 0x07 |
| PLAYBACK_STOP | 0x08 |
| SYSTEM_CONNECT | 0x09 |
| SYSTEM_ERROR | 0x0A |
| CONFIG_PACKET_DATA | 0x0B |
| CONFIG_DATA_MODE_AR_DEV | 0x0C |
| INIT_FPGA_PLAYBACK | 0x0D |
| READ_FPGA_VERSION | 0x0E |

### 2.2 Zero-data commands (data size = 0 → 8-byte fixed packet)

Confirmed from `commandsprotocol.cpp`: the following commands have no payload (data size field = 0).

| Command | Code | Full packet (hex) |
|---|---|---|
| RESET_FPGA | 0x01 | `5A A5  01 00  00 00  AA EE` |
| RESET_AR_DEV | 0x02 | `5A A5  02 00  00 00  AA EE` |
| RECORD_START | 0x05 | `5A A5  05 00  00 00  AA EE` |
| RECORD_STOP | 0x06 | `5A A5  06 00  00 00  AA EE` |
| SYSTEM_CONNECT | 0x09 | `5A A5  09 00  00 00  AA EE` |
| READ_FPGA_VERSION | 0x0E | `5A A5  0E 00  00 00  AA EE` |

### 2.3 CONFIG_FPGA_GEN (0x03) — 6-byte payload

Confirmed from `rf_api.cpp` lines 1100–1105: each struct field is cast to uint8 (not the
raw enum int) and packed one byte at a time.

```
Byte  Field                Value for IWR1642 raw Ethernet capture
  0   eLogMode             1  (RAW_MODE)
  1   eLvdsMode            2  (TWO_LANE — IWR1642 only supports 2 lanes)
  2   eDataXferMode        1  (CAPTURE, not PLAYBACK)
  3   eDataCaptureMode     2  (ETH_STREAM, not SD_STORAGE)
  4   eDataFormatMode      3  (BIT16)
  5   u8Timer              30 (0x1E — LVDS timeout; matches config/vital_signs.lua)
```

Full 14-byte packet:

```
5A A5  03 00  06 00  01 02 01 02 03 1E  AA EE
```

Source for the values: `config/vital_signs.lua` (~line 216):
`ar1.CaptureCardConfig_Mode(1, 2, 1, 2, 3, 30)` for partId 1642.

### 2.4 CONFIG_PACKET_DATA (0x0B) — 6-byte payload

Confirmed from `rf_api.cpp` `ConfigureRFDCCard_Record()`:

```
Bytes 0-1  MAX_BYTES_PER_PACKET = 1470  (uint16 LE: 0xBE 0x05)
Bytes 2-3  delay_us × 1000 / 8 = delay_us × 125
           For 25 µs delay: 25 × 125 = 3125 = 0x0C35  (uint16 LE: 0x35 0x0C)
Bytes 4-5  0x0000  (reserved)
```

Full 14-byte packet for the default 25 µs delay:

```
5A A5  0B 00  06 00  BE 05  35 0C  00 00  AA EE
```

---

## 3. UDP Data Stream Packet Layout

The DCA1000 streams ADC data to host port **4098** in raw mode.
The file format for the **raw ADC .bin** has two variants: with or without a sequence-number prefix.

### 3.1 UDP data packet layout — CONFIRMED from TI source code

Source: `recorddatarecv.cpp` lines 338 and 558; `defines.h` RECORD_DATA_BUF_INDEX=10;
`rf_api_internal.h` PAYLOAD_BYTES_PER_PACKET=1456, MAX_BYTES_PER_PACKET=1470.

```
Offset  Size  Content
0       4     Sequence number (uint32, little-endian) — increments by 1 per packet
4       6     Byte count of ADC data sent so far (uint48, little-endian)
10      ≤1456 ADC payload (PAYLOAD_BYTES_PER_PACKET = 1456 for full packets;
              last packet may be smaller)
─────────────────────────────────────────────────────────────
Total       1466 bytes (full packet); recvfrom buffer = MAX_BYTES_PER_PACKET = 1470
```

Packet reordering logic (from `recorddatarecv.cpp`):
- Packets arrive in order in normal conditions.
- Out-of-order packets are detected by sequence number discontinuity.
- Gaps (dropped packets) are zero-filled with `PKT_ADC_BYTES = 1456` bytes per missing packet.
- The final .bin is a contiguous stream of ADC bytes with no headers.

---

## 4. ADC Data Format in the .bin File (xWR16xx + DCA1000, complex, 4 RX)

Source: SWRA581B §6.  Confirmed by `src/radar_io.py` de-interleaving code (SWRA581 §3.3).

- **2 LVDS lanes** (IWR1642 only supports 2 lanes; 3-RX not allowed — only 1, 2, or 4 RX).
- **16-bit two's complement**, little-endian.
- **Non-interleaved** (chInterleave = 1): all samples for RX0 precede RX1, etc.
- **Complex output**: Lane 1 carries I (real) samples; Lane 2 carries Q (imaginary) samples.
- Per chirp, layout is: [RX0_I1, RX0_I2, RX0_Q1, RX0_Q2, RX0_I3, RX0_I4, RX0_Q3, RX0_Q4, …]
  then RX1, RX2, RX3 in the same pattern.
- The 4-word-packet de-interleaving done in `read_adc_bin()` (lines 215–218) is correct.
- The file after packet reorder is a pure stream of int16 values — no headers, no sequence numbers.

---

## 5. IWR1642 UART CLI Reference

### 5.1 COM Port Assignment

When the IWR1642BOOST is connected via USB (SWRU521C §2.4):

| Windows Device Manager label | Purpose | Baud rate |
|---|---|---|
| **XDS110 Class Application/User UART** | **CLI command port** (send config + sensorStart) | **115200** |
| XDS110 Class Auxiliary Data Port | MSS logger / debug output | 921600 |

The CLI port is the **lower-numbered** of the two COM ports that appear.
Send all CLI commands to the Application/User UART port only.

### 5.2 SOP Jumper for Functional Mode

SOP[2:0] = **001** = functional mode (P2 closed, P3 open, P4 open).
This is the default shipping state of the board. Do not change unless flashing.

### 5.3 Mandatory Command Sequence (Legacy Frame Mode)

Send each line as a terminated ASCII string (`\n`) and wait for `Done` or `Error` response before
sending the next. Order is mandatory.

```
dfeDataOutputMode 1
channelCfg        <rxChannelEn> <txChannelEn> 0
adcCfg            2 1
adcbufCfg         -1 0 1 1 1
lowPower          0 0
profileCfg        <profileId> <startFreq> <idleTime> <adcStartTime> <rampEndTime> 0 0 <freqSlopeConst> <txStartTime> <numAdcSamples> <digOutSampleRate> <hpfCornerFreq1> <hpfCornerFreq2> <rxGain>
chirpCfg          0 0 <profileId> 0 0 0 0 <txEnable>
frameCfg          0 0 <numLoops> <numFrames> <framePeriodicity_ms> 1 0
lvdsStreamCfg     -1 0 1 0
analogMonitor     0 0
sensorStart
```

To stop: `sensorStop`

### 5.4 Parameter Values for This Project

From the mmWave Studio export `config/vital_signs.lua` (referred to as
`custom_capture.lua` when this note was written; that filename no longer exists) and the
capture profile in `steps/step_1/capture_config.yaml`. The `experiments/` tree this note
originally cited no longer exists.

Full CLI sequence (send each line, wait for `Done` before the next):

```
dfeDataOutputMode 1
channelCfg 15 1 0
adcCfg 2 1
adcbufCfg -1 0 1 1 1
profileCfg 0 77 7 5 57 0 0 70.006 0 256 5209 0 0 30
chirpCfg 0 0 0 0 0 0 0 1
bpmCfg -1 0 0 1
frameCfg 0 0 32 <N> 50.0 1 0
lowPower 0 1
guiMonitor -1 0 0 0 0 0 0
... (full sequence in capture.py _configure_iwr1642)
sensorStart
```

Where `<N>` = number of frames to capture (5700 for a ~4 min 45 s session at 20 Hz).
Parameters match `vital_signs.lua` (the mmWave Studio profile for all sessions).

| Field | Value | Source |
|---|---|---|
| channelCfg rxEn | 15 | 0xF = all 4 RX |
| channelCfg txEn | 1 | TX1 only |
| adcCfg numADCBits | 2 | 16-bit ADC |
| adcCfg adcOutputFmt | 1 | complex 1x |
| adcbufCfg SampleSwap | 1 | Q in LSB, I in MSB — SampleSwap=0 disables LVDS output in the SDK demo firmware (observed 2026-06-23); vital_signs.lua uses IQSwap=0 via SPI but that path is not available via CLI |
| adcbufCfg ChanInterleave | 1 | non-interleaved |
| adcbufCfg ChirpThreshold | 1 | required for LVDS |
| profileCfg startFreq | 77 GHz | vital_signs.lua |
| profileCfg idleTime | 7 µs | vital_signs.lua |
| profileCfg adcStartTime | 5 µs | vital_signs.lua |
| profileCfg rampEndTime | 57 µs | vital_signs.lua |
| profileCfg freqSlopeConst | 70.006 MHz/µs | vital_signs.lua |
| profileCfg txStartTime | 0 µs | vital_signs.lua |
| profileCfg numAdcSamples | 256 | vital_signs.lua |
| profileCfg digOutSampleRate | 5209 ksps | vital_signs.lua |
| profileCfg hpfCornerFreq1 | 0 | vital_signs.lua |
| profileCfg hpfCornerFreq2 | 0 | vital_signs.lua |
| profileCfg rxGain | 30 | vital_signs.lua |
| chirpCfg txEnable | 1 | TX1 only |
| frameCfg numLoops | 32 | chirps_per_frame |
| frameCfg numFrames | 5700 | (set per capture) |
| frameCfg framePeriodicity | 50.0 ms | 20 Hz |
| lvdsStreamCfg dataFmt | 1 | ADC data only |

---

## 6. Hardware Setup Notes (IWR1642BOOST + DCA1000EVM)

From SWRU521C §2.2 and DCA1000EVM User Guide §3:

- Power: 5 V, > 2.5 A supply to IWR1642BOOST barrel jack. DCA1000EVM powered from the same
  board via SW3 power selection switch (or separate 5 V).
- After 5 V applied: press NRST switch (SW2) once for reliable boot.
- Samtec coax micro ribbon cable connects the 60-pin HD connector on IWR1642BOOST to J3 on
  DCA1000EVM — this carries the LVDS data lanes.
- DCA1000EVM SW2 switch position 11 (not 6) → uses FPGA register defaults (192.168.33.180).
- Host PC Ethernet adapter must be set to static IP **192.168.33.30**, subnet 255.255.255.0.
- Confirm connectivity: `ping 192.168.33.180` before sending any UDP commands.

---

## 7. Alternative: Use TI's DCA1000EVM CLI Instead of Raw UDP

mmwave_sdk_user_guide §3.3.2 documents a JSON-driven CLI approach:

```
DCA1000EVM_CLI_Control.exe fpga       datacard_config.json
DCA1000EVM_CLI_Control.exe record     datacard_config.json
DCA1000EVM_CLI_Control.exe start_record datacard_config.json
# ... send sensorStart to IWR1642 via UART ...
DCA1000EVM_CLI_Control.exe stop_record  datacard_config.json
```

This binary is at `C:\ti\mmwave_studio_<ver>\mmWaveStudio\PostProc\`.
Calling it as a subprocess from Python avoids implementing the raw UDP protocol, at the cost of
requiring mmWave Studio to be installed. Worth considering as a fallback.
The output file in raw ADC mode is `<prefix>_Raw_0.bin` — compatible with `read_adc_bin()`.

---

## 8. Summary — all gaps resolved

All protocol details were confirmed from TI source code at
`C:\ti\mmwave_studio_02_01_01_00\mmWaveStudio\ReferenceCode\DCA1000\SourceCode\`.

| Item | Status | Source |
|---|---|---|
| Zero-data command packets (RESET_FPGA, RECORD_START/STOP, SYSTEM_CONNECT) | ✓ Confirmed | commandsprotocol.cpp |
| CONFIG_FPGA_GEN 6-byte payload | ✓ Confirmed | rf_api.cpp lines 1100–1105 |
| CONFIG_PACKET_DATA 6-byte payload | ✓ Confirmed | rf_api.cpp ConfigureRFDCCard_Record() |
| UDP data packet layout (seq, byte-count, ADC payload at offset 10) | ✓ Confirmed | recorddatarecv.cpp + defines.h |
| PAYLOAD_BYTES_PER_PACKET = 1456 | ✓ Confirmed | rf_api_internal.h |
| profileCfg numeric values | ✓ Confirmed | config/vital_signs.lua |
| IWR1642 CLI command sequence | ✓ Confirmed | mmwave_sdk_user_guide + config/vital_signs.lua |

**Implementation:** `scripts/capture.py` (with `scripts/capture_config.yaml`).


Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private