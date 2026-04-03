This folder is for programming the AD8232 pre-built module for validating against a known ECG source.

It is using platformio (vscode extension) to build and flash things.

---

## Wiring — AD8232 to Arduino Uno R3

| AD8232 Pin | Arduino Pin | Notes                        |
|------------|-------------|------------------------------|
| GND        | GND         |                              |
| 3.3V       | 3.3V        |                              |
| OUTPUT     | A0          | Analog ECG signal            |
| LO+        | D10         | Lead-off detection (input)   |
| LO-        | D11         | Lead-off detection (input)   |
| SDN        | —           | Leave unconnected (always on)|

## Wiring — PulseSensor to Arduino Uno R3

| PulseSensor Pin | Arduino Pin | Notes              |
|-----------------|-------------|--------------------|
| GND (black)     | GND         |                    |
| VCC (red)       | 3.3V or 5V  | Either works       |
| SIGNAL (purple) | A1          | Analog PPG signal  |

---

## Electrode Placement

The AD8232 uses **3 electrodes**.
The third electrode (RL) is a driven ground — without it, common-mode noise (from
power lines, movement, etc.) will overwhelm the signal.

![Electrode placement diagram](https://cdn.sparkfun.com/r/600-600/assets/learn_tutorials/2/5/0/body.png)

The cable connectors are color-coded:

| Color  | Label | Placement                                        |
|--------|-------|--------------------------------------------------|
| Red    | RA    | Right side of chest, just below the collarbone   |
| Yellow | RL    |  Lower left abdomen (reference/driven ground)    |
| Green  | LA    | Left side of chest, just below the collarbone    |

(In the placement diagram, RA = right arm, LA = left arm, RL = right leg)



---

## Run the ECG Plotter

```bash
pip install pyserial matplotlib scipy numpy

python plot_ecg.py              # auto-detects Arduino port
python plot_ecg.py --port COM3  # or specify port manually
```

The plotter shows:
- Rolling 5-second ECG waveform (AD8232, A0) — raw + bandpass-filtered overlay, R-peaks marked
- Rolling 5-second PPG waveform (PulseSensor, A1) — raw + bandpass-filtered overlay, peaks marked
- BPM panel with three numbers: ECG BPM (left), PPG BPM (right), averaged BPM (center, large)
- R-R interval trend over the last 30 beats (from ECG)
