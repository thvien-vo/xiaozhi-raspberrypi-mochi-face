// servo_bridge.ino - Yolo UNO (ESP32-S3) nhan lenh tu Pi 5 qua USB Serial, dieu khien 2 servo MG996R
// Lenh: go 2 so cach nhau boi dau cach roi Enter: "<pan> <tilt>", moi so 0..180
// Tra loi: "OK <goc xoay> <goc gat>" hoac "ERR ..."
// Arduino IDE: Tools -> USB CDC On Boot: Enabled
#include <ESP32Servo.h>

const int PAN_PIN  = 18;  // D9  -> servo xoay ngang
const int TILT_PIN = 17;  // D8  -> servo gat
const int PAN_MIN = 0,  PAN_MAX = 180;
const int TILT_MIN = 0, TILT_MAX = 180;
const int CENTER = 90;
const int PULSE_MIN = 500, PULSE_MAX = 2500;

Servo panServo, tiltServo;
float panNow = CENTER, tiltNow = CENTER;
int panTarget = CENTER, tiltTarget = CENTER;
int stepDeg = 3;
unsigned long lastStep = 0;
String line;

void reply() { Serial.printf("OK %d %d\n", panTarget, tiltTarget); }

void handleLine(String s) {
  s.trim();
  if (s.length() == 0) return;
  int sp = s.indexOf(' ');
  if (sp < 0) { Serial.println("ERR can 2 so, vi du: 10 10"); return; }
  String a = s.substring(0, sp), b = s.substring(sp + 1);
  a.trim(); b.trim();
  for (String t : {a, b}) {
    if (t.length() == 0) { Serial.println("ERR can 2 so, vi du: 10 10"); return; }
    for (int i = 0; i < (int)t.length(); i++)
      if (!isDigit(t[i])) { Serial.println("ERR chi go so, vi du: 10 10"); return; }
  }
  panTarget = constrain(a.toInt(), PAN_MIN, PAN_MAX);
  tiltTarget = constrain(b.toInt(), TILT_MIN, TILT_MAX);
  reply();
}

float approach(float now, int target) {
  if (fabs(target - now) <= stepDeg) return target;
  return now + (target > now ? stepDeg : -stepDeg);
}

void setup() {
  Serial.begin(115200);
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  panServo.setPeriodHertz(50);
  tiltServo.setPeriodHertz(50);
  panServo.attach(PAN_PIN, PULSE_MIN, PULSE_MAX);
  tiltServo.attach(TILT_PIN, PULSE_MIN, PULSE_MAX);
  panServo.write(CENTER);
  tiltServo.write(CENTER);
  delay(300);
  Serial.println("READY servo_bridge");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') { handleLine(line); line = ""; }
    else if (line.length() < 40) line += c;
  }
  if (millis() - lastStep >= 20) {
    lastStep = millis();
    panNow = approach(panNow, panTarget);
    tiltNow = approach(tiltNow, tiltTarget);
    panServo.write((int)round(panNow));
    tiltServo.write((int)round(tiltNow));
  }
}
