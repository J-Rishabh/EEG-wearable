# BLE RSSI Experiment Protocol

## Goal
Measure how RSSI changes with distance and attenuation conditions for your BLE wearable.

## Recommended independent variables
- Distance: 0.25 m, 0.5 m, 1 m, 2 m, 3 m, 5 m
- Condition:
  - `line_of_sight`
  - `near_body`
  - `body_between`
  - `object_between`

## Recommended controlled variables
- Same room for all trials
- Same laptop position/orientation
- Same wearable position/orientation
- Same antenna orientation if possible
- Same battery state if possible
- Minimal movement during a recording
- Same Wi-Fi/Bluetooth environment if possible

## Trial structure
- 3 to 5 trials per distance per condition
- 15 to 30 seconds per trial
- Record all advertisement RSSI values during each trial
- Use per-trial mean RSSI for the main summary plot

## How to measure distance
Best option:
- Use a measuring tape and measure antenna-to-antenna center distance

Good enough fallback:
- Use painter's tape on the floor with marked positions based on a tape measure

Avoid as the primary method:
- Floor tiles unless you have verified the tile size exactly

## Suggested notes field examples
- `standing still`
- `device on desk`
- `human body 0.3 m from receiver`
- `foam wall between devices`
- `laptop lid half open`

## Suggested thesis plots
1. Mean RSSI vs distance, one line per condition, with 95% CI
2. Trial means scatter plot to show variability
3. Mean RSSI vs log10(distance)

## About AirPods comparison
AirPods are not an ideal baseline for this experiment because they are not a clean, controllable BLE beacon target from Windows.
A better comparison device is:
- another nRF BLE board
- a phone advertising as a BLE peripheral
- a simple BLE beacon with known Tx power

That gives you a more defensible apples-to-apples radio comparison.
