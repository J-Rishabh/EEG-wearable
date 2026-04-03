#include <Arduino.h>

// AD8232 pin connections
const int ECG_PIN      = A0;  // ECG signal output
const int LO_PLUS_PIN  = 10;  // Leads-off detection +
const int LO_MINUS_PIN = 11;  // Leads-off detection -

// PulseSensor
const int PULSE_PIN    = A1;  // PulseSensor analog output

// Leads-off debounce: require this many consecutive "leads on" readings
// before treating them as actually connected (prevents noise glitches)
const int DEBOUNCE_COUNT = 20;
int leadsOnCounter = 0;
bool leadsConfirmedOn = false;

void setup() {
    Serial.begin(115200);
    pinMode(LO_PLUS_PIN, INPUT);
    pinMode(LO_MINUS_PIN, INPUT);
}

void loop() {
    bool rawLeadsOff = digitalRead(LO_PLUS_PIN) || digitalRead(LO_MINUS_PIN);

    if (rawLeadsOff) {
        leadsOnCounter = 0;
        leadsConfirmedOn = false;
    } else {
        if (leadsOnCounter < DEBOUNCE_COUNT) leadsOnCounter++;
        if (leadsOnCounter >= DEBOUNCE_COUNT) leadsConfirmedOn = true;
    }

    // Always send PPG. Format: "ecg,pulse" or "!,pulse" when ECG leads are off.
    if (!leadsConfirmedOn) {
        Serial.print("!");
    } else {
        Serial.print(analogRead(ECG_PIN));
    }
    Serial.print(",");
    Serial.println(analogRead(PULSE_PIN));

    delay(1);
}
