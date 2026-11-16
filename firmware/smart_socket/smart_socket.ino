// Smart charge switch for a Seeed XIAO ESP32-C3. Circuit: ../../DESIGN.md.
//
// Build: Arduino IDE or arduino-cli, board package "esp32" by Espressif, board "XIAO_ESP32C3",
// Partition Scheme "Minimal SPIFFS (1.9MB APP with OTA)" (Bluetooth makes the program too big for
// the default 1.2 MB). FQBN: esp32:esp32:XIAO_ESP32C3:PartitionScheme=min_spiffs
// No extra libraries are needed.
//
// The Logic section is a transcription of ../../tools/logic.py, which carries the tests.
//
// Version 1.6 adds:
// - Up to 8 known networks (home, school, hotspot...). When not connected it scans, joins the strongest
//   one it knows, and moves between them by itself. WPA2, WPA3, open, hidden, and username networks
//   (PEAP or TTLS, e.g. eduroam). No known network near: setup opens by itself.
// - Bluetooth always on, with a command channel (the same commands as the USB serial port), so a phone
//   or laptop can set it up or control it even where Wi-Fi does not let devices see each other.
// - USB serial commands for everything the apps need (see "Commands" below).
// - API: GET /api/nets, POST /api/nets/forget?ssid=, /api/wifi takes user= for username networks.
//
// Version 1.5 adds:
// - Its own name and address, so several sockets can share one Wi-Fi: smartsocket-xxxxxx.local
//   (the last six characters of its Wi-Fi hardware address), a name the user chooses, and a
//   "find me" request that flashes its light.
// - An optional PIN. Once set, every request that reads or changes anything must carry it.
// - Four ways to join the home Wi-Fi: Bluetooth from the Android app, the WPS button of the
//   router (press the socket's button three times), the setup network whose page now opens by
//   itself, and a line typed on the USB serial port.
// - Charge timer, charging hours, heat cut-out from the controller's own temperature sensor,
//   charging history, light brightness, and program updates uploaded from the app or page, over
//   Wi-Fi or over Bluetooth (so a network that keeps devices apart, like a school's, is no obstacle).
//
// HTTP API (all answers JSON; "pin" may be a query argument or an X-PIN header):
//   GET  /api/info                      no PIN: id, name, fw, pin_set, host, ip, setup
//   POST /api/identify                  no PIN: flash the light for 8 s
//   GET  /api/status                    everything the app shows
//   ANY  /api/battery?level=N           battery report from the phone
//   POST /api/config?upper=&lower=&mode=&max_ma=&safety_h=&timer_min=&sched_on=&sched_start=&sched_end=&led=
//   POST /api/boost?on=1|0              charge to 100 % once
//   POST /api/name?name=                rename
//   POST /api/pin?new=                  set, change ("" removes); the old PIN is needed if one is set
//   GET  /api/history                   last 20 charging runs
//   GET  /api/scan                      Wi-Fi networks the socket can see
//   POST /api/wifi?ssid=&pass=          join a network (answer at once; poll /api/setup_status)
//   GET  /api/setup_status              result of the last join attempt
//   POST /api/update                    new program, multipart file upload

#include <ArduinoOTA.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <DNSServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <Update.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_mac.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <esp_task_wdt.h>
#include <esp_wifi.h>
#include <esp_wps.h>
#include <host/ble_gatt.h>
#include <host/ble_hs_mbuf.h>
#include <freertos/semphr.h>
#include <time.h>

#include <functional>

#include "index_html.h"

#define FW_VERSION "1.9"

// Password for loading a new program over Wi-Fi from the Arduino IDE. It lives in secrets.h, which is
// not committed: copy secrets.example.h to secrets.h and choose your own before the first upload.
#include "secrets.h"

// Time zone for the charging hours: France (CET/CEST).
#define TIME_ZONE "CET-1CEST,M3.5.0,M10.5.0/3"

// ---------------------------------------------------------------- Pins (GPIO numbers)
const int PIN_SRC_SENSE = 3;   // D1: charger CC1 + CC2 through 100 k each, halved
const int PIN_PHONE_SENSE = 4; // D2: phone CC through 100 k
const int PIN_RP_BASIC = 5;    // D3: 33 k to phone CC
const int PIN_RP_MID = 6;      // D4: 12 k to phone CC
const int PIN_RP_HIGH = 7;     // D5: 4.7 k to phone CC
const int PIN_LED = 20;        // D7
const int PIN_POWER = 10;      // D10: high turns the 5 V switch on
const int PIN_BUTTON = 9;      // BOOT button on the board, low when pressed

// ---------------------------------------------------------------- Logic (transcription of logic.py)
const int64_t BOOT_WAIT_MS = 5000;
const int64_t ATTACH_DEBOUNCE_MS = 150;
const int64_t DETACH_DEBOUNCE_MS = 100;
const int BASIC_MA = 500, MID_MA = 1500, HIGH_MA = 3000;
const float HOT_C = 75, COOL_C = 65;
enum Mode { MODE_AUTO, MODE_ON, MODE_OFF };
enum Reason { R_NONE, R_BOOT, R_NO_PHONE, R_HOT, R_OFF, R_SCHEDULE, R_TIMER, R_LIMIT };
const char *REASON_NAMES[] = {"", "boot", "no_phone", "hot", "off", "schedule", "timer", "limit"};

struct Config {
  int upper = 80;
  int lower = 40;
  Mode mode = MODE_AUTO;
  int maxMa = BASIC_MA;
  int safetyH = 0;
  int timerMin = 0;
  bool schedOn = false;
  int schedStart = 22 * 60;
  int schedEnd = 6 * 60;
  int led = 100; // light brightness, percent (not part of logic.py)
} cfg;

struct State {
  bool allow = true;
  bool boost = false;
  int level = -1;
  int64_t reportMs = -1;
  int64_t presentSince = -1;
  int64_t absentSince = -1;
  bool vbus = false;
  int outMa = BASIC_MA;
  bool dirty = false;
  bool hot = false;
  int64_t sessionMs = 0;
  int64_t lastMs = -1;
  Reason reason = R_BOOT;
} st;

int srcMa = BASIC_MA;
int offerMv = -1;            // smoothed reading of what the charger offers, -1 = no reading yet
bool currentLimited = false; // true after a restart caused by the supply sagging: offer basic current only
int64_t chargeSince = -1;    // when the present charging run started, -1 = not charging
float tempC = 0;             // controller temperature, smoothed

int64_t nowMs() { return esp_timer_get_time() / 1000; }

void clampConfig() {
  cfg.upper = constrain(cfg.upper, 20, 100);
  cfg.lower = constrain(cfg.lower, 5, cfg.upper - 5);
  if (cfg.mode != MODE_AUTO && cfg.mode != MODE_ON && cfg.mode != MODE_OFF) cfg.mode = MODE_AUTO;
  if (cfg.maxMa != BASIC_MA && cfg.maxMa != MID_MA && cfg.maxMa != HIGH_MA) cfg.maxMa = BASIC_MA;
  cfg.safetyH = constrain(cfg.safetyH, 0, 72);
  cfg.timerMin = constrain(cfg.timerMin, 0, 24 * 60);
  cfg.schedStart = constrain(cfg.schedStart, 0, 24 * 60 - 1);
  cfg.schedEnd = constrain(cfg.schedEnd, 0, 24 * 60 - 1);
  cfg.led = constrain(cfg.led, 0, 100);
}

void reportBattery(int level, int64_t now) {
  level = constrain(level, 0, 100);
  st.level = level;
  st.reportMs = now;
  bool before = st.allow;
  if (level >= cfg.upper) st.allow = false;
  else if (level <= cfg.lower) st.allow = true;
  if (level >= 100) st.boost = false;
  st.dirty = st.dirty || (st.allow != before);
}

int sourceMa(int d1Mv) {
  if (d1Mv > 620) return HIGH_MA;
  if (d1Mv > 330) return MID_MA;
  return BASIC_MA;
}

bool phonePresent(int d2Mv, int outMa) {
  int openMv = (outMa == HIGH_MA) ? 2300 : 1600;
  return d2Mv > 150 && d2Mv < openMv;
}

bool inWindow(int minute, int start, int end) {
  if (minute < 0 || start == end) return true;
  if (start < end) return start <= minute && minute < end;
  return minute >= start || minute < end;
}

void logicStep(int64_t now, bool present, int src, int minute, float temp) {
  if (st.vbus && st.lastMs >= 0) st.sessionMs += now - st.lastMs;
  st.lastMs = now;

  if (present) {
    st.absentSince = -1;
    if (st.presentSince < 0) st.presentSince = now;
  } else {
    if (st.absentSince < 0) st.absentSince = now;
    if (now - st.absentSince >= DETACH_DEBOUNCE_MS) {
      st.presentSince = -1;
      st.boost = false;
      st.sessionMs = 0;
    }
  }

  if (temp >= HOT_C) st.hot = true;
  else if (temp <= COOL_C) st.hot = false;

  if (cfg.mode == MODE_AUTO && !st.allow && cfg.safetyH > 0 && st.reportMs >= 0 &&
      now - st.reportMs > (int64_t)cfg.safetyH * 3600 * 1000) {
    st.allow = true;
    st.dirty = true;
  }

  bool settled = now >= BOOT_WAIT_MS;
  bool debounced = st.presentSince >= 0 && now - st.presentSince >= ATTACH_DEBOUNCE_MS;
  bool timerDone = cfg.timerMin > 0 && st.sessionMs >= (int64_t)cfg.timerMin * 60 * 1000;
  Reason r;
  if (!settled) r = R_BOOT;
  else if (!debounced) r = R_NO_PHONE;
  else if (st.hot) r = R_HOT;
  else if (cfg.mode == MODE_OFF) r = R_OFF;
  else if (st.boost) r = R_NONE;
  else if (cfg.schedOn && !inWindow(minute, cfg.schedStart, cfg.schedEnd)) r = R_SCHEDULE;
  else if (timerDone) r = R_TIMER;
  else if (cfg.mode == MODE_AUTO && !st.allow) r = R_LIMIT;
  else r = R_NONE;

  st.reason = r;
  st.vbus = r == R_NONE;
  st.outMa = st.vbus ? min(src, cfg.maxMa) : BASIC_MA;
}

// ---------------------------------------------------------------- Clock
bool timeStarted = false;

int minuteOfDay() {
  struct tm t;
  if (!timeStarted || !getLocalTime(&t, 0) || t.tm_year < 120) return -1;
  return t.tm_hour * 60 + t.tm_min;
}

uint32_t epochNow() {
  time_t t = time(nullptr);
  return t > 1700000000 ? (uint32_t)t : 0;
}

// ---------------------------------------------------------------- Saved settings
Preferences prefs;
String devName, devPin;
void loadNets();

// ---------------------------------------------------------------- History of charging runs
struct Run {
  uint32_t start;  // Unix time, or seconds since start-up when the time was not known (then clock = 0)
  uint32_t durS;
  int8_t lvl0, lvl1;
  uint8_t why;     // Reason that ended it; R_NO_PHONE = unplugged
  uint8_t clock;
};
const int RUNS = 20;
Run runs[RUNS];
uint8_t runCount = 0, runNext = 0;
Run current;
bool runOpen = false;

void loadHistory() {
  prefs.getBytes("hist", runs, sizeof(runs));
  runCount = prefs.getUChar("histn", 0);
  runNext = prefs.getUChar("histi", 0);
  if (runCount > RUNS || runNext >= RUNS) runCount = runNext = 0;
}

void historyPass(int64_t now) {
  if (st.vbus && !runOpen) {
    uint32_t e = epochNow();
    current = {e ? e : (uint32_t)(now / 1000), 0, (int8_t)st.level, -1, 0, (uint8_t)(e ? 1 : 0)};
    runOpen = true;
  } else if (!st.vbus && runOpen) {
    runOpen = false;
    current.durS = chargeSince >= 0 ? (uint32_t)((now - chargeSince) / 1000) : 0;
    current.lvl1 = st.level;
    current.why = st.reason;
    if (current.durS < 5) return; // a blip, not a run
    runs[runNext] = current;
    runNext = (runNext + 1) % RUNS;
    if (runCount < RUNS) runCount++;
    prefs.putBytes("hist", runs, sizeof(runs));
    prefs.putUChar("histn", runCount);
    prefs.putUChar("histi", runNext);
  }
}

// ---------------------------------------------------------------- Hardware
int appliedMa = 0; // 0 = not yet set

// Exactly one of the three resistors is driven high; the other two pins float.
void applyRp(int ma) {
  if (ma == appliedMa) return;
  int pin = (ma == HIGH_MA) ? PIN_RP_HIGH : (ma == MID_MA) ? PIN_RP_MID : PIN_RP_BASIC;
  const int pins[] = {PIN_RP_BASIC, PIN_RP_MID, PIN_RP_HIGH};
  pinMode(pin, OUTPUT);
  digitalWrite(pin, HIGH);
  for (int p : pins) {
    if (p != pin) pinMode(p, INPUT);
  }
  appliedMa = ma;
}

int readMv(int pin) {
  long sum = 0;
  for (int i = 0; i < 8; i++) sum += analogReadMilliVolts(pin);
  return sum / 8;
}

bool updating = false; // a new program is being written: keep the output off

void applyOutputs() {
  digitalWrite(PIN_POWER, (st.vbus && !updating) ? HIGH : LOW);
  applyRp(st.outMa);
}

void controlPass() {
  int64_t now = nowMs();
  static int64_t lastTemp = -10000;
  if (now - lastTemp >= 2000) { // the temperature sensor is slow and moves slowly
    lastTemp = now;
    float t = temperatureRead();
    tempC = (tempC == 0) ? t : tempC * 0.8f + t * 0.2f;
  }
  // The charger's offer is only read while the output is off, so load on the cable cannot disturb it.
  // It is smoothed over about eight passes so a reading near a threshold cannot flip back and forth.
  if (!st.vbus) {
    int mv = readMv(PIN_SRC_SENSE);
    offerMv = offerMv < 0 ? mv : (offerMv * 7 + mv) / 8;
    srcMa = sourceMa(offerMv);
  }
  bool present = phonePresent(readMv(PIN_PHONE_SENSE), appliedMa);
  logicStep(now, present, currentLimited ? BASIC_MA : srcMa, minuteOfDay(), tempC);
  applyOutputs();
  historyPass(now);
  if (!st.vbus) chargeSince = -1;
  else if (chargeSince < 0) chargeSince = now;
}

void loadSettings() {
  prefs.begin("socket", false);
  cfg.upper = prefs.getInt("upper", cfg.upper);
  cfg.lower = prefs.getInt("lower", cfg.lower);
  cfg.mode = (Mode)prefs.getInt("mode", cfg.mode);
  cfg.maxMa = prefs.getInt("maxma", cfg.maxMa);
  cfg.safetyH = prefs.getInt("safety", cfg.safetyH);
  cfg.timerMin = prefs.getInt("timer", cfg.timerMin);
  cfg.schedOn = prefs.getBool("sch_on", cfg.schedOn);
  cfg.schedStart = prefs.getInt("sch_s", cfg.schedStart);
  cfg.schedEnd = prefs.getInt("sch_e", cfg.schedEnd);
  cfg.led = prefs.getInt("led", cfg.led);
  // Charging is allowed after every start, whatever was saved: the device's first battery report
  // (within a minute) stops it again if it is above the limit. A saved pause could otherwise last
  // until a report that never comes (a network that blocks the reports, the app not running).
  st.allow = true;
  devName = prefs.getString("name", "");
  devPin = prefs.getString("pin", "");
  loadNets();
  clampConfig();
  loadHistory();
}

void saveConfig() {
  prefs.putInt("upper", cfg.upper);
  prefs.putInt("lower", cfg.lower);
  prefs.putInt("mode", cfg.mode);
  prefs.putInt("maxma", cfg.maxMa);
  prefs.putInt("safety", cfg.safetyH);
  prefs.putInt("timer", cfg.timerMin);
  prefs.putBool("sch_on", cfg.schedOn);
  prefs.putInt("sch_s", cfg.schedStart);
  prefs.putInt("sch_e", cfg.schedEnd);
  prefs.putInt("led", cfg.led);
}

void saveAllowIfChanged() {
  if (!st.dirty) return;
  prefs.putBool("allow", st.allow);
  st.dirty = false;
}

// ---------------------------------------------------------------- Identity
String devId;    // last six hex characters of the Wi-Fi hardware address, e.g. "A1B2C3"
String hostName; // smartsocket-a1b2c3

void makeIdentity() {
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char id[7];
  snprintf(id, sizeof(id), "%02X%02X%02X", mac[3], mac[4], mac[5]);
  devId = id;
  hostName = "smartsocket-" + devId;
  hostName.toLowerCase();
  if (devName.isEmpty()) devName = "Socket " + devId;
}

// ---------------------------------------------------------------- Small JSON helpers
String jsonEscape(const String &s) {
  String out;
  for (char c : s) {
    if (c == '"' || c == '\\') out += '\\';
    if ((unsigned char)c >= 0x20) out += c;
  }
  return out;
}

// Value of a string field in a flat JSON object, e.g. jsonField("{\"ssid\":\"x\"}", "ssid") = "x".
String jsonField(const String &j, const char *key) {
  String k = String("\"") + key + "\"";
  int i = j.indexOf(k);
  if (i < 0) return "";
  i = j.indexOf(':', i + k.length());
  if (i < 0) return "";
  i = j.indexOf('"', i);
  if (i < 0) return "";
  String out;
  for (i = i + 1; i < (int)j.length(); i++) {
    char c = j[i];
    if (c == '\\' && i + 1 < (int)j.length()) out += j[++i];
    else if (c == '"') break;
    else out += c;
  }
  return out;
}

// ---------------------------------------------------------------- Wi-Fi, setup and joining
// The socket remembers up to MAX_NETS networks (home, school, a friend's, a phone hotspot), newest
// first. Whenever it is not connected it scans, picks the strongest network it knows and joins it, so
// it moves between home and school by itself. WPA2 and WPA3 passwords both work; a network that also
// asks for a username (eduroam and the like) is joined with PEAP. If none of the known networks is in
// reach, or a known one keeps refusing for a minute, it opens setup (Bluetooth and its own network)
// and keeps looking in the background.
const char *TXT_SETUP = "SmartSocket-setup-";
const int MAX_NETS = 8;
struct Net {
  String ssid, pass, user; // user is empty for a network that only asks for a password
};
Net nets[MAX_NETS];
int netCount = 0;

bool setupMode = false;
int64_t setupUntil = 0;         // setup opened on request stays open until this time (0 = until on Wi-Fi)
int64_t lastWifiTry = -100000;  // when the last join attempt began
int64_t lostSince = -1;         // when the socket last found itself without Wi-Fi
int wifiFailReason = 0;         // last reason a network refused or dropped us (0 = none yet)
int wifiSeenRssi = 0;           // signal of the chosen network at the last scan: 0 = not scanned, 1 = not found
String currentSsid;             // the network being joined or used
String failedSsid;              // the network tried last without success (the next scan prefers another)
int hiddenTry = 0;              // next known network to try blind when none shows in the scan (hidden names)
bool ttlsNext = false;          // username networks: alternate PEAP and TTLS until one works
DNSServer dns;
String setupApName;

// A join requested by the app (Bluetooth), the setup page, the Windows app or the serial port
String joinSsid, joinPass, joinUser;
bool joinPending = false;
int64_t joinStart = -1;
String joinState = "idle"; // idle, connecting, ok, fail
int64_t joinDoneAt = -1;
String netsJson = "[]";    // last Wi-Fi scan, for the apps and the setup page
int64_t lastScan = -100000;
bool scanWanted = false;   // an app asked for a fresh list

// WPS (the router's push button)
bool wpsActive = false;
int64_t wpsStart = -1;
volatile int wpsEvent = 0; // 1 success, 2 failed or timed out

void setCountryFrance() {
  wifi_country_t country = {};
  memcpy(country.cc, "FR", 2);
  country.schan = 1;
  country.nchan = 13;
  country.policy = WIFI_COUNTRY_POLICY_MANUAL;
  esp_wifi_set_country(&country);
}

void bleStart();
void bleStop();
void bleNotifyResult();

// ---- the list of known networks, kept in flash as lines of "name<TAB>password<TAB>username"
void saveNets() {
  String s;
  for (int i = 0; i < netCount; i++) s += nets[i].ssid + "\t" + nets[i].pass + "\t" + nets[i].user + "\n";
  prefs.putString("nets", s);
}

int findNet(const String &ssid) {
  for (int i = 0; i < netCount; i++)
    if (nets[i].ssid == ssid) return i;
  return -1;
}

// Newest first; a network already known moves to the front with its new password; when the list is
// full the oldest one is dropped.
void rememberNet(const String &ssid, const String &pass, const String &user) {
  if (ssid.isEmpty()) return;
  int i = findNet(ssid);
  if (i < 0) i = netCount < MAX_NETS ? netCount++ : MAX_NETS - 1;
  for (int k = i; k > 0; k--) nets[k] = nets[k - 1];
  nets[0].ssid = ssid;
  nets[0].pass = pass;
  nets[0].user = user;
  saveNets();
}

bool forgetNet(const String &ssid) {
  int i = findNet(ssid);
  if (i < 0) return false;
  for (int k = i; k < netCount - 1; k++) nets[k] = nets[k + 1];
  netCount--;
  saveNets();
  if (currentSsid == ssid) WiFi.disconnect(false, false); // the scan then picks another one
  return true;
}

void loadNets() {
  netCount = 0;
  String s = prefs.getString("nets", "");
  int p = 0;
  while (p < (int)s.length() && netCount < MAX_NETS) {
    int e = s.indexOf('\n', p);
    if (e < 0) e = s.length();
    String line = s.substring(p, e);
    p = e + 1;
    int a = line.indexOf('\t');
    if (a <= 0) continue;
    int b = line.indexOf('\t', a + 1);
    nets[netCount].ssid = line.substring(0, a);
    nets[netCount].pass = b < 0 ? line.substring(a + 1) : line.substring(a + 1, b);
    nets[netCount].user = b < 0 ? String("") : line.substring(b + 1);
    netCount++;
  }
  // Program 1.5 kept a single network under "ssid" and "pass": move it into the list once.
  String old = prefs.getString("ssid", "");
  if (old.length()) {
    String oldPass = prefs.getString("pass", "");
    prefs.remove("ssid");
    prefs.remove("pass");
    if (findNet(old) < 0) {
      if (netCount == MAX_NETS) netCount--;
      for (int k = netCount; k > 0; k--) nets[k] = nets[k - 1];
      nets[0].ssid = old;
      nets[0].pass = oldPass;
      nets[0].user = "";
      netCount++;
      saveNets();
    }
  }
}

// JSON list of the known networks, without their passwords
String savedNetsJson() {
  String j = "[";
  for (int i = 0; i < netCount; i++) {
    if (i) j += ",";
    j += "{\"ssid\":\"" + jsonEscape(nets[i].ssid) + "\",\"user\":\"" + jsonEscape(nets[i].user) + "\"}";
  }
  return j + "]";
}

// ---- setup mode: the socket's own network and Bluetooth
void openSetup(int64_t keepMs) {
  if (!setupMode) {
    setupMode = true;
    setupApName = TXT_SETUP + devId;
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(setupApName.c_str());
    setCountryFrance();
    dns.start(53, "*", WiFi.softAPIP()); // every name leads to the setup page
    bleStart();
  }
  setupUntil = keepMs > 0 ? nowMs() + keepMs : 0;
}

void closeSetup() {
  if (!setupMode) return;
  dns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  setupMode = false;
}

// ---- joining one network
void connectTo(const String &ssid, const String &pass, const String &user) {
  WiFi.disconnect(false, false);
  currentSsid = ssid;
  lastWifiTry = nowMs();
#if CONFIG_ESP_WIFI_ENTERPRISE_SUPPORT
  if (user.length()) {
    // Username and password: PEAP (eduroam and most school and company networks), and TTLS on the
    // next attempt if PEAP is refused. The server certificate is not checked: there is no way to give
    // the socket the network's certificate.
    WiFi.begin(ssid.c_str(), ttlsNext ? WPA2_AUTH_TTLS : WPA2_AUTH_PEAP, user.c_str(), user.c_str(), pass.c_str());
    ttlsNext = !ttlsNext;
    return;
  }
#endif
  // Password only (WPA2 or WPA3) or an open network. Set up first, then allow both WPA3 methods
  // and protected management frames before connecting: WPA3-only networks need them.
  WiFi.begin(ssid.c_str(), pass.length() ? pass.c_str() : NULL, 0, NULL, false);
  wifi_config_t c;
  if (esp_wifi_get_config(WIFI_IF_STA, &c) == ESP_OK) {
    c.sta.pmf_cfg.capable = true;
    c.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    esp_wifi_set_config(WIFI_IF_STA, &c);
  }
  esp_wifi_connect();
}

void beginJoin(const String &ssid, const String &pass, const String &user) {
  joinSsid = ssid;
  joinPass = pass;
  joinUser = user;
  joinPending = true;
}

void startWifi() {
  static bool eventsAdded = false;
  if (!eventsAdded) {
    eventsAdded = true;
    WiFi.setHostname(hostName.c_str());
    WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) { wifiFailReason = info.wifi_sta_disconnected.reason; },
                 ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
    WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t) { wpsEvent = 1; }, ARDUINO_EVENT_WPS_ER_SUCCESS);
    WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t) { wpsEvent = 2; }, ARDUINO_EVENT_WPS_ER_FAILED);
    WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t) { wpsEvent = 2; }, ARDUINO_EVENT_WPS_ER_TIMEOUT);
  }
  WiFi.mode(setupMode ? WIFI_AP_STA : WIFI_STA);
  setCountryFrance();
  WiFi.setAutoReconnect(true);
  lastWifiTry = -100000;
  lastScan = -100000; // look around at once; the scan picks the network to join
  if (netCount == 0) openSetup(0);
}

void startWps() {
  if (wpsActive) return;
  closeSetup();
  WiFi.disconnect();
  WiFi.mode(WIFI_STA);
  esp_wps_config_t config = WPS_CONFIG_INIT_DEFAULT(WPS_TYPE_PBC);
  esp_wifi_wps_enable(&config);
  esp_wifi_wps_start(0);
  wpsActive = true;
  wpsStart = nowMs();
  wpsEvent = 0;
}

void wpsPass() {
  if (!wpsActive) return;
  if (wpsEvent == 1) { // the router sent its network name and password
    esp_wifi_wps_disable();
    wpsActive = false;
    WiFi.begin(); // connect with what WPS stored; remembered below once connected
    joinSsid = "";
    joinStart = nowMs();
    joinState = "connecting";
  } else if (wpsEvent == 2 || nowMs() - wpsStart > 130000) {
    esp_wifi_wps_disable();
    wpsActive = false;
    startWifi();
  }
}

// ---- scanning: keeps the list for the apps, and picks the network to join
void scanDone(int n, bool mayJoin) {
  String j = "[";
  int shown = 0;
  int best = -1, bestRssi = -1000, fallback = -1, fallbackRssi = -1000;
  wifiSeenRssi = 1;
  for (int i = 0; i < n; i++) {
    String s = WiFi.SSID(i);
    if (s.isEmpty()) continue;
    int rssi = WiFi.RSSI(i);
    if (s == currentSsid && (wifiSeenRssi == 1 || rssi > wifiSeenRssi)) wifiSeenRssi = rssi;
    int k = findNet(s);
    if (k >= 0) {
      if (s != failedSsid && rssi > bestRssi) { best = k; bestRssi = rssi; }
      if (rssi > fallbackRssi) { fallback = k; fallbackRssi = rssi; }
    }
    if (shown < 15 && j.indexOf("\"" + jsonEscape(s) + "\"") < 0) {
      if (shown++) j += ",";
      wifi_auth_mode_t a = WiFi.encryptionType(i);
      j += "{\"ssid\":\"" + jsonEscape(s) + "\",\"rssi\":" + String(rssi) +
           ",\"open\":" + (a == WIFI_AUTH_OPEN ? "true" : "false") +
           ",\"user\":" + (a == WIFI_AUTH_WPA2_ENTERPRISE || a == WIFI_AUTH_WPA3_ENTERPRISE ? "true" : "false") +
           ",\"known\":" + (k >= 0 ? "true" : "false") + "}";
    }
  }
  netsJson = j + "]";
  WiFi.scanDelete();
  if (!mayJoin) return;
  if (best < 0) best = fallback; // only the one that failed is near: try it again
  if (best >= 0) {
    failedSsid = nets[best].ssid; // cleared once connected; makes the next round try another one first
    connectTo(nets[best].ssid, nets[best].pass, nets[best].user);
    return;
  }
  // None of the known names is on the air. A hidden network does not show its name, so try the
  // known networks blind, one per round; and open setup so a phone or laptop can give it a new one.
  if (!setupMode) openSetup(0);
  if (netCount > 0) {
    hiddenTry = (hiddenTry + 1) % netCount;
    connectTo(nets[hiddenTry].ssid, nets[hiddenTry].pass, nets[hiddenTry].user);
  }
}

void scanPass(int64_t now, bool connected, bool busy) {
  int n = WiFi.scanComplete();
  if (n == WIFI_SCAN_RUNNING) return;
  if (n >= 0) {
    scanDone(n, !connected && !busy && netCount > 0);
    return;
  }
  bool trying = !connected && now - lastWifiTry < 25000; // a join attempt is still running: do not disturb it
  bool due = scanWanted || (!busy && !trying && (!connected || setupMode) && now - lastScan > (connected ? 30000 : 10000));
  if (due && !busy) {
    scanWanted = false;
    lastScan = now;
    WiFi.scanNetworks(true, false, false, 120); // 120 ms per channel: a full sweep in about 1.5 s
  }
}

// Wi-Fi housekeeping, every pass of the loop.
void wifiHousekeeping() {
  int64_t now = nowMs();
  bool connected = WiFi.status() == WL_CONNECTED && WiFi.localIP() != IPAddress(0, 0, 0, 0);

  if (joinPending) {
    joinPending = false;
    if (!setupMode) openSetup(5 * 60 * 1000);
    if (WiFi.scanComplete() == WIFI_SCAN_RUNNING) WiFi.scanDelete();
    connectTo(joinSsid, joinPass, joinUser);
    joinStart = now;
    joinState = "connecting";
    bleNotifyResult();
    return; // `connected` above still describes the old network
  }
  if (joinState == "connecting") {
    // Only the network asked for counts, with an address (not the one left a moment ago)
    bool joined = connected && (joinSsid.isEmpty() || WiFi.SSID() == joinSsid);
    if (joined) {
      if (joinSsid.isEmpty()) rememberNet(WiFi.SSID(), WiFi.psk(), ""); // after WPS: name and password from the router
      else rememberNet(joinSsid, joinPass, joinUser);
      joinState = "ok";
      joinDoneAt = now;
      bleNotifyResult();
    } else if (now - joinStart > (joinUser.length() ? 30000 : 20000)) {
      joinState = "fail";
      joinDoneAt = now;
      bleNotifyResult();
      lastWifiTry = -100000; // back to the known networks at once
      lastScan = -100000;
    }
  }
  bool busy = joinState == "connecting" || wpsActive;

  if (connected) {
    lostSince = -1;
    failedSsid = "";
    currentSsid = WiFi.SSID();
    // Close the setup network once on Wi-Fi: 60 s after a successful join (time for the app to
    // read the result), or when a setup window opened by the button runs out.
    bool joinedLately = joinState == "ok" && now - joinDoneAt < 60000;
    if (setupMode && !joinedLately && (setupUntil == 0 || now > setupUntil)) closeSetup();
  } else {
    if (lostSince < 0) lostSince = now;
    // Known networks near but none lets it in for a minute (wrong password, a portal page...):
    // open setup as well, so it can be given another one; keep trying in the background.
    if (!setupMode && !busy && now - lostSince > 60000) openSetup(0);
  }

  scanPass(now, connected, busy);
  if (setupMode) dns.processNextRequest();
}

String setupStatusJson() {
  bool connected = WiFi.status() == WL_CONNECTED;
  return "{\"state\":\"" + joinState + "\",\"ip\":\"" + (connected ? WiFi.localIP().toString() : String("")) +
         "\",\"ssid\":\"" + jsonEscape(connected ? WiFi.SSID() : String("")) +
         "\",\"host\":\"" + hostName + ".local\",\"reason\":" + String(wifiFailReason) + "}";
}

String infoJson() {
  bool connected = WiFi.status() == WL_CONNECTED;
  return "{\"id\":\"" + devId + "\",\"name\":\"" + jsonEscape(devName) + "\",\"fw\":\"" FW_VERSION "\"" +
         ",\"pin_set\":" + (devPin.isEmpty() ? "false" : "true") + ",\"host\":\"" + hostName + ".local\"" +
         ",\"ip\":\"" + (connected ? WiFi.localIP().toString() : String("")) + "\"" +
         ",\"ssid\":\"" + jsonEscape(connected ? WiFi.SSID() : String("")) + "\"" +
         ",\"setup\":" + (setupMode ? "true" : "false") + ",\"wifi\":" + (connected ? "true" : "false") +
         ",\"nets\":" + String(netCount) + "}";
}

// Apply the setup fields sent by the app, the setup page or the serial port.
// Returns "" if accepted, or why not.
String applySetup(const String &ssid, const String &pass, const String &user, const String &name, const String &pin,
                  const String &oldPin) {
  if (!devPin.isEmpty() && oldPin != devPin) return "pin";
  if (!name.isEmpty()) {
    devName = name.substring(0, 32);
    prefs.putString("name", devName);
  }
  if (!pin.isEmpty()) {
    devPin = pin.substring(0, 12);
    prefs.putString("pin", devPin);
  }
  if (!ssid.isEmpty()) beginJoin(ssid, pass, user);
  return "";
}

// ---------------------------------------------------------------- Bluetooth (setup and commands)
// Always advertising as "SmartSocket-xxxxxx". INFO, NETS, SETUP and RESULT are the setup service the
// Android app has used since 1.5. CMD takes one command line (the same as on the USB serial port);
// the answer comes back on REPLY as notifications, in pieces, ending with a newline. If a PIN is set,
// a connection must first send "auth <pin>".
#define BLE_SERVICE "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e50"
#define BLE_INFO "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e51"
#define BLE_NETS "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e52"
#define BLE_SETUP "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e53"
#define BLE_RESULT "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e54"
#define BLE_CMD "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e55"
#define BLE_REPLY "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e56"
#define BLE_OTA "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e57"

// Program update over Bluetooth: "ota begin <size> <md5>" on CMD, then the program in pieces
// written to OTA (without response, every 16th with response so the sender keeps pace), then "ota end", which checks the MD5 and restarts into it.
// "ota abort" or a lost connection or 20 s without data cancels it; the old program stays.
bool otaActive = false, otaFailed = false;
size_t otaSize = 0, otaGot = 0;
int64_t otaLastData = 0, restartAt = 0;

bool bleReady = false, bleOn = false;
BLEServer *bleServer = nullptr;
BLECharacteristic *chInfo, *chNets, *chSetup, *chResult, *chCmd, *chReply, *chOta;
String bleSetupError = "";
uint16_t bleConnId = 0;
// Program 1.9: several phones and laptops can talk at once. Each command is queued with the connection
// that wrote it, and its answer goes back to that connection only, in pieces sized to that connection's
// MTU. (Before, one shared slot meant a second device's command could replace the first one's, and the
// answer went to everyone in pieces sized for whoever connected last.) The PIN is also per connection.
const int BLE_QUEUE = 8;
struct BleCmd {
  uint16_t conn;
  String line;
};
BleCmd bleQueue[BLE_QUEUE];
int bleQHead = 0, bleQCount = 0;
uint16_t bleAuthedConns[4];
int bleAuthedCount = 0;
SemaphoreHandle_t bleLock = nullptr;

void bleLockTake() { if (bleLock) xSemaphoreTake(bleLock, portMAX_DELAY); }
void bleLockGive() { if (bleLock) xSemaphoreGive(bleLock); }

bool bleIsAuthed(uint16_t conn) {
  bleLockTake();
  bool a = false;
  for (int i = 0; i < bleAuthedCount; i++)
    if (bleAuthedConns[i] == conn) a = true;
  bleLockGive();
  return a;
}

void bleSetAuthed(uint16_t conn, bool on) {
  bleLockTake();
  int k = 0;
  for (int i = 0; i < bleAuthedCount; i++)
    if (bleAuthedConns[i] != conn) bleAuthedConns[k++] = bleAuthedConns[i];
  bleAuthedCount = k;
  if (on && bleAuthedCount < 4) bleAuthedConns[bleAuthedCount++] = conn;
  bleLockGive();
}

// Forget everything about a connection that has gone (its PIN and its queued commands).
void bleForget(uint16_t conn) {
  bleSetAuthed(conn, false);
  bleLockTake();
  BleCmd keep[BLE_QUEUE];
  int k = 0;
  for (int i = 0; i < bleQCount; i++) {
    BleCmd &c = bleQueue[(bleQHead + i) % BLE_QUEUE];
    if (c.conn != conn) keep[k++] = c;
  }
  for (int i = 0; i < k; i++) bleQueue[i] = keep[i];
  bleQHead = 0;
  bleQCount = k;
  bleLockGive();
}

String runCommand(String line, bool trusted, bool &authed);

void otaCancel() {
  if (otaActive) Update.abort();
  otaActive = false;
  updating = false;
}

// Up to 3 Bluetooth connections at once. Phones and Windows sometimes leave a connection open after
// the app is gone; once all 3 are taken the socket can no longer be found. So the oldest connection is
// dropped when the last place fills, and advertising is restarted every few seconds (blePass).
const int BLE_SLOTS = 3;
uint16_t bleIds[BLE_SLOTS];
int64_t bleSince[BLE_SLOTS];
int bleCount = 0;
portMUX_TYPE bleMux = portMUX_INITIALIZER_UNLOCKED;

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *srv, ble_gap_conn_desc *desc) override {
    bleConnId = desc->conn_handle;
    portENTER_CRITICAL(&bleMux);
    if (bleCount < BLE_SLOTS) {
      bleIds[bleCount] = bleConnId;
      bleSince[bleCount] = nowMs();
      bleCount++;
    }
    portEXIT_CRITICAL(&bleMux);
    bleSetAuthed(bleConnId, false);
    BLEDevice::startAdvertising(); // stay visible to other phones while this one is connected
  }
  void onDisconnect(BLEServer *srv, ble_gap_conn_desc *desc) override {
    bleForget(desc->conn_handle);
    // which connection went is not reported here: rebuild the list from the stack's own
    std::map<uint16_t, conn_status_t> peers = srv->getPeerDevices(false);
    portENTER_CRITICAL(&bleMux);
    int k = 0;
    for (int i = 0; i < bleCount; i++)
      if (peers.count(bleIds[i])) {
        bleIds[k] = bleIds[i];
        bleSince[k] = bleSince[i];
        k++;
      }
    bleCount = k;
    portEXIT_CRITICAL(&bleMux);
    // an update in progress stops only when no one is left; otherwise its own 20 s timeout applies
    if (otaActive && srv->getConnectedCount() <= 1) otaCancel();
    if (bleOn) BLEDevice::startAdvertising();
  }
};

class ReadCallbacks : public BLECharacteristicCallbacks {
  void onRead(BLECharacteristic *c) override {
    if (c == chInfo) c->setValue(infoJson());
    else if (c == chNets) c->setValue(netsJson);
    else if (c == chResult) {
      String j = setupStatusJson();
      if (!bleSetupError.isEmpty()) j = "{\"state\":\"rejected\",\"why\":\"" + bleSetupError + "\"}";
      c->setValue(j);
    }
  }
  void onWrite(BLECharacteristic *c, ble_gap_conn_desc *desc) override {
    if (c == chCmd) {
      String line = c->getValue();
      bleLockTake();
      if (bleQCount < BLE_QUEUE) {
        bleQueue[(bleQHead + bleQCount) % BLE_QUEUE] = {desc->conn_handle, line};
        bleQCount++;
      }
      bleLockGive();
      return;
    }
    onWrite(c);
  }
  void onWrite(BLECharacteristic *c) override {
    if (c == chOta) {
      if (!otaActive || otaFailed) return;
      String v = c->getValue();
      if (otaGot + v.length() > otaSize || Update.write((uint8_t *)v.c_str(), v.length()) != v.length()) otaFailed = true;
      else otaGot += v.length();
      otaLastData = nowMs();
      return;
    }
    if (c != chSetup) return;
    String j = c->getValue();
    bleSetupError = applySetup(jsonField(j, "ssid"), jsonField(j, "pass"), jsonField(j, "user"), jsonField(j, "name"),
                               jsonField(j, "pin"), jsonField(j, "old_pin"));
    bleNotifyResult();
  }
};

void bleStart() {
  if (!bleReady) {
    bleLock = xSemaphoreCreateMutex();
    BLEDevice::init(("SmartSocket-" + devId).c_str());
    BLEDevice::setMTU(247);
    bleServer = BLEDevice::createServer();
    bleServer->setCallbacks(new ServerCallbacks());
    BLEService *svc = bleServer->createService(BLEUUID(BLE_SERVICE), 20);
    auto *cb = new ReadCallbacks();
    chInfo = svc->createCharacteristic(BLE_INFO, BLECharacteristic::PROPERTY_READ);
    chNets = svc->createCharacteristic(BLE_NETS, BLECharacteristic::PROPERTY_READ);
    chSetup = svc->createCharacteristic(BLE_SETUP, BLECharacteristic::PROPERTY_WRITE);
    chResult = svc->createCharacteristic(BLE_RESULT, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
    chResult->addDescriptor(new BLE2902());
    chCmd = svc->createCharacteristic(BLE_CMD, BLECharacteristic::PROPERTY_WRITE);
    chReply = svc->createCharacteristic(BLE_REPLY, BLECharacteristic::PROPERTY_NOTIFY);
    chReply->addDescriptor(new BLE2902());
    chOta = svc->createCharacteristic(BLE_OTA, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    for (auto *c : {chInfo, chNets, chSetup, chResult, chCmd, chOta}) c->setCallbacks(cb);
    svc->start();
    BLEAdvertising *adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(BLE_SERVICE);
    adv->setScanResponse(true);
    bleReady = true;
  }
  bleOn = true;
  BLEDevice::startAdvertising();
}

void bleStop() {
  if (!bleReady) return;
  bleOn = false;
  BLEDevice::stopAdvertising();
}

void bleNotifyResult() {
  if (!bleReady) return;
  String j = setupStatusJson();
  if (!bleSetupError.isEmpty()) j = "{\"state\":\"rejected\",\"why\":\"" + bleSetupError + "\"}";
  chResult->setValue(j);
  chResult->notify();
}

// Runs a command written to CMD and sends the answer back on REPLY in pieces the link can carry.
void bleKeepFindable() {
  static int64_t last = 0;
  int64_t now = nowMs();
  if (!bleOn || !bleServer || now - last < 5000) return;
  last = now;
  if (bleServer->getConnectedCount() >= BLE_SLOTS && !otaActive) {
    uint16_t oldest = 0;
    bool found = false;
    portENTER_CRITICAL(&bleMux);
    if (bleCount > 0) {
      oldest = bleIds[0];
      found = true;
    }
    portEXIT_CRITICAL(&bleMux);
    if (found) bleServer->disconnect(oldest);
  }
  if (bleServer->getConnectedCount() < BLE_SLOTS && !BLEDevice::getAdvertising()->isAdvertising()) BLEDevice::startAdvertising();
}

void blePass() {
  bleKeepFindable();
  for (int n = 0; n < 2; n++) { // at most two commands per pass, so the rest of the loop keeps running
    BleCmd job;
    bleLockTake();
    bool have = bleQCount > 0;
    if (have) {
      job = bleQueue[bleQHead];
      bleQHead = (bleQHead + 1) % BLE_QUEUE;
      bleQCount--;
    }
    bleLockGive();
    if (!have) return;
    bool authed = bleIsAuthed(job.conn);
    bool was = authed;
    String reply = runCommand(job.line, false, authed) + "\n";
    if (authed != was) bleSetAuthed(job.conn, authed);
    int piece = 20;
    int mtu = bleServer ? bleServer->getPeerMTU(job.conn) : 0;
    if (mtu > 23) piece = mtu - 3;
    for (int i = 0; i < (int)reply.length(); i += piece) {
      int len = min((int)reply.length(), i + piece) - i;
      // to the connection that asked only; retry briefly if the stack is out of buffers
      for (int tries = 0; tries < 20; tries++) {
        struct os_mbuf *om = ble_hs_mbuf_from_flat(reply.c_str() + i, len);
        if (om && ble_gatts_notify_custom(job.conn, chReply->getHandle(), om) == 0) break;
        delay(5);
      }
      delay(4);
    }
  }
}

// ---------------------------------------------------------------- Web server
WebServer server(80);
int pinFails = 0;
int64_t pinLockedUntil = 0;

const char *modeName(Mode m) { return m == MODE_ON ? "on" : m == MODE_OFF ? "off" : "auto"; }

void sendJson(int code, const String &j) {
  server.sendHeader("Cache-Control", "no-store");
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(code, "application/json", j);
}

// True if the request may go ahead. Five wrong PINs lock it for a minute.
bool authed() {
  if (devPin.isEmpty()) return true;
  int64_t now = nowMs();
  if (now < pinLockedUntil) {
    sendJson(429, "{\"error\":\"locked\",\"wait_s\":" + String((long)((pinLockedUntil - now) / 1000) + 1) + "}");
    return false;
  }
  String given = server.hasArg("pin") ? server.arg("pin") : server.header("X-PIN");
  if (given == devPin) {
    pinFails = 0;
    return true;
  }
  if (++pinFails >= 5) {
    pinFails = 0;
    pinLockedUntil = now + 60000;
  }
  sendJson(401, "{\"error\":\"pin\"}");
  return false;
}

String hhmm(int minutes) {
  char b[6];
  snprintf(b, sizeof(b), "%02d:%02d", minutes / 60, minutes % 60);
  return b;
}

// Why the socket last started, for finding faults afterwards ("power" = plugged in, "watchdog" =
// it got stuck and restarted itself, "brownout" = the supply sagged, "panic" = a program fault).
uint32_t bootCount = 0;
const char *resetWhy() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power";
    case ESP_RST_SW: return "restart";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT: case ESP_RST_WDT: return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_EXT: return "button";
    default: return "other";
  }
}

String statusJson() {
  controlPass();
  int64_t now = nowMs();
  bool connected = WiFi.status() == WL_CONNECTED;
  int minute = minuteOfDay();
  long timerLeft = -1;
  if (cfg.timerMin > 0) timerLeft = max<int64_t>(0, (int64_t)cfg.timerMin * 60000 - st.sessionMs) / 1000;
  String j = "{";
  j += "\"id\":\"" + devId + "\",\"name\":\"" + jsonEscape(devName) + "\"";
  j += ",\"settled\":" + String(now >= BOOT_WAIT_MS ? "true" : "false");
  j += ",\"phone\":" + String(st.presentSince >= 0 ? "true" : "false");
  j += ",\"charging\":" + String(st.vbus ? "true" : "false");
  j += ",\"reason\":\"" + String(REASON_NAMES[st.reason]) + "\"";
  j += ",\"allow\":" + String(st.allow ? "true" : "false");
  j += ",\"boost\":" + String(st.boost ? "true" : "false");
  j += ",\"mode\":\"" + String(modeName(cfg.mode)) + "\"";
  j += ",\"level\":" + String(st.level);
  j += ",\"level_age_s\":" + String(st.reportMs < 0 ? -1 : (long)((now - st.reportMs) / 1000));
  j += ",\"upper\":" + String(cfg.upper) + ",\"lower\":" + String(cfg.lower);
  j += ",\"max_ma\":" + String(cfg.maxMa) + ",\"safety_h\":" + String(cfg.safetyH);
  j += ",\"timer_min\":" + String(cfg.timerMin) + ",\"timer_left_s\":" + String(timerLeft);
  j += ",\"sched_on\":" + String(cfg.schedOn ? "true" : "false");
  j += ",\"sched_start\":\"" + hhmm(cfg.schedStart) + "\",\"sched_end\":\"" + hhmm(cfg.schedEnd) + "\"";
  j += ",\"time\":\"" + (minute >= 0 ? hhmm(minute) : String("")) + "\"";
  j += ",\"temp_c\":" + String(tempC, 1) + ",\"hot\":" + String(st.hot ? "true" : "false");
  j += ",\"led\":" + String(cfg.led);
  j += ",\"src_ma\":" + String(srcMa) + ",\"out_ma\":" + String(st.outMa);
  j += ",\"offer_mv\":" + String(offerMv) + ",\"phone_mv\":" + String(readMv(PIN_PHONE_SENSE));
  j += ",\"reset\":\"" + String(resetWhy()) + "\",\"boots\":" + String(bootCount) + ",\"ble_links\":" + String(bleServer ? (int)bleServer->getConnectedCount() : 0);
  j += ",\"charge_s\":" + String(chargeSince < 0 ? -1 : (long)((now - chargeSince) / 1000));
  j += ",\"limited\":" + String(currentLimited ? "true" : "false");
  j += ",\"uptime_s\":" + String((long)(now / 1000));
  j += ",\"ssid\":\"" + jsonEscape(connected ? WiFi.SSID() : setupApName) + "\"";
  j += ",\"rssi\":" + String(connected ? WiFi.RSSI() : 0);
  j += ",\"ip\":\"" + (connected ? WiFi.localIP() : WiFi.softAPIP()).toString() + "\"";
  j += ",\"host\":\"" + hostName + ".local\"";
  j += ",\"pin_set\":" + String(devPin.isEmpty() ? "false" : "true");
  j += ",\"fw\":\"" FW_VERSION "\"}";
  return j;
}

int parseHhmm(const String &s, int fallback) {
  int c = s.indexOf(':');
  if (c < 0) return s.length() ? s.toInt() : fallback; // plain minutes are accepted too
  return s.substring(0, c).toInt() * 60 + s.substring(c + 1).toInt();
}

// Settings from any source: `arg(name, value)` gives a field if it was sent.
void applyConfig(std::function<bool(const char *, String &)> arg) {
  String v;
  if (arg("upper", v)) cfg.upper = v.toInt();
  if (arg("lower", v)) cfg.lower = v.toInt();
  if (arg("max_ma", v)) cfg.maxMa = v.toInt();
  if (arg("safety_h", v)) cfg.safetyH = v.toInt();
  if (arg("timer_min", v)) {
    cfg.timerMin = v.toInt();
    st.sessionMs = 0; // the timer runs from now, not from when the device was plugged in
  }
  if (arg("sched_on", v)) cfg.schedOn = v == "1" || v == "true";
  if (arg("sched_start", v)) cfg.schedStart = parseHhmm(v, cfg.schedStart);
  if (arg("sched_end", v)) cfg.schedEnd = parseHhmm(v, cfg.schedEnd);
  if (arg("led", v)) cfg.led = v.toInt();
  bool limits = arg("upper", v) || arg("lower", v);
  if (arg("mode", v)) cfg.mode = v == "on" ? MODE_ON : v == "off" ? MODE_OFF : MODE_AUTO;
  clampConfig();
  saveConfig();
  // New limits apply at once to the last battery level reported, instead of waiting for the next report:
  // charge while below the new upper limit, stop at or above it.
  // With no level known yet (no report since the socket started), charge: a pause saved earlier must not
  // last until the next report, which may never come (a network that blocks the app's reports).
  if (limits) {
    bool before = st.allow;
    st.allow = st.level < 0 || st.level < cfg.upper;
    st.dirty = st.dirty || st.allow != before;
    saveAllowIfChanged();
  }
}

void handleConfig() {
  if (!authed()) return;
  applyConfig([](const char *k, String &v) {
    if (!server.hasArg(k)) return false;
    v = server.arg(k);
    return true;
  });
  sendJson(200, statusJson());
}

String setName(String n) {
  n.trim();
  if (n.isEmpty()) return "name is empty";
  devName = n.substring(0, 32);
  prefs.putString("name", devName);
  MDNS.addServiceTxt("smartsocket", "tcp", "name", devName);
  return "";
}

String setPin(String p) {
  p.trim();
  if (!p.isEmpty() && (p.length() < 4 || p.length() > 12)) return "PIN must be 4 to 12 characters";
  devPin = p;
  prefs.putString("pin", devPin);
  MDNS.addServiceTxt("smartsocket", "tcp", "pin", devPin.isEmpty() ? "0" : "1");
  return "";
}

String netsInfoJson() {
  return "{\"saved\":" + savedNetsJson() + ",\"current\":\"" +
         jsonEscape(WiFi.status() == WL_CONNECTED ? WiFi.SSID() : String("")) + "\",\"seen\":" + netsJson + "}";
}

String historyJson() {
  String j = "{\"now\":" + String(epochNow()) + ",\"runs\":[";
  for (int k = 0; k < runCount; k++) {
    const Run &r = runs[(runNext - 1 - k + RUNS) % RUNS]; // newest first
    if (k) j += ",";
    j += "{\"start\":" + String(r.start) + ",\"clock\":" + String(r.clock) + ",\"dur_s\":" + String(r.durS) +
         ",\"lvl0\":" + String(r.lvl0) + ",\"lvl1\":" + String(r.lvl1) + ",\"why\":\"" +
         String(r.why < 8 ? REASON_NAMES[r.why] : "") + "\"}";
  }
  return j + "]}";
}

void handleHistory() {
  if (!authed()) return;
  sendJson(200, historyJson());
}

int64_t identifyUntil = 0;

void startServer() {
  const char *headers[] = {"X-PIN"};
  server.collectHeaders(headers, 1);
  server.on("/", HTTP_GET, []() { server.send_P(200, "text/html; charset=utf-8", INDEX_HTML); });
  server.on("/api/info", HTTP_GET, []() { sendJson(200, infoJson()); });
  server.on("/api/identify", HTTP_ANY, []() {
    identifyUntil = nowMs() + 8000;
    sendJson(200, "{\"ok\":true}");
  });
  server.on("/api/status", HTTP_GET, []() {
    if (authed()) sendJson(200, statusJson());
  });
  server.on("/api/battery", HTTP_ANY, []() {
    if (!authed()) return;
    if (!server.hasArg("level")) return sendJson(400, "{\"error\":\"level is missing\"}");
    reportBattery(server.arg("level").toInt(), nowMs());
    saveAllowIfChanged();
    sendJson(200, statusJson());
  });
  server.on("/api/config", HTTP_POST, handleConfig);
  server.on("/api/boost", HTTP_POST, []() {
    if (!authed()) return;
    st.boost = server.arg("on") == "1";
    sendJson(200, statusJson());
  });
  server.on("/api/name", HTTP_POST, []() {
    if (!authed()) return;
    String err = setName(server.arg("name"));
    if (err.length()) return sendJson(400, "{\"error\":\"" + err + "\"}");
    sendJson(200, infoJson());
  });
  server.on("/api/pin", HTTP_POST, []() {
    if (!authed()) return;
    String err = setPin(server.arg("new"));
    if (err.length()) return sendJson(400, "{\"error\":\"" + err + "\"}");
    sendJson(200, infoJson());
  });
  server.on("/api/nets", HTTP_GET, []() {
    if (!authed()) return;
    sendJson(200, netsInfoJson());
  });
  server.on("/api/nets/forget", HTTP_POST, []() {
    if (!authed()) return;
    bool ok = forgetNet(server.arg("ssid"));
    sendJson(ok ? 200 : 404, ok ? netsInfoJson() : "{\"error\":\"not a known network\"}");
  });
  server.on("/api/history", HTTP_GET, handleHistory);
  server.on("/api/scan", HTTP_GET, []() {
    if (!setupMode && !authed()) return;
    scanWanted = true; // the list is refreshed in a few seconds; ask again to see it
    sendJson(200, netsJson);
  });
  server.on("/api/wifi", HTTP_POST, []() {
    if (!authed()) return;
    if (server.arg("ssid").isEmpty()) return sendJson(400, "{\"error\":\"ssid is missing\"}");
    if (server.hasArg("name") && !server.arg("name").isEmpty()) {
      devName = server.arg("name").substring(0, 32);
      prefs.putString("name", devName);
    }
    beginJoin(server.arg("ssid"), server.arg("pass"), server.arg("user"));
    sendJson(200, "{\"ok\":true}");
  });
  server.on("/api/setup_status", HTTP_GET, []() { sendJson(200, setupStatusJson()); });
  server.on(
      "/api/update", HTTP_POST,
      []() {
        if (!devPin.isEmpty() && server.arg("pin") != devPin) { // the upload was ignored: say so, do not restart
          sendJson(401, "{\"error\":\"pin\"}");
          return;
        }
        bool ok = !Update.hasError() && Update.isFinished();
        sendJson(ok ? 200 : 500, ok ? "{\"ok\":true}" : "{\"error\":\"update failed\"}");
        if (ok) {
          delay(500);
          ESP.restart();
        }
        updating = false;
      },
      []() {
        HTTPUpload &up = server.upload();
        if (up.status == UPLOAD_FILE_START) {
          if (!devPin.isEmpty() && server.arg("pin") != devPin) return;
          updating = true;
          digitalWrite(PIN_POWER, LOW); // no charging while the program is replaced
          Update.begin(UPDATE_SIZE_UNKNOWN);
        } else if (up.status == UPLOAD_FILE_WRITE && updating) {
          Update.write(up.buf, up.currentSize);
          esp_task_wdt_reset();
        } else if (up.status == UPLOAD_FILE_END && updating) {
          Update.end(true);
        }
      });
  // Phones and laptops test a known address when they join a network; answering with the setup
  // page makes them open it by themselves ("captive portal").
  for (const char *p : {"/generate_204", "/gen_204", "/hotspot-detect.html", "/connecttest.txt", "/ncsi.txt", "/fwlink"}) {
    server.on(p, HTTP_ANY, []() {
      server.sendHeader("Location", "http://" + WiFi.softAPIP().toString() + "/", true);
      server.send(302, "text/plain", "");
    });
  }
  server.onNotFound([]() {
    if (setupMode) {
      server.sendHeader("Location", "http://" + WiFi.softAPIP().toString() + "/", true);
      server.send(302, "text/plain", "");
    } else {
      server.send(404, "text/plain", "not found");
    }
  });
  server.begin();
}

// ---------------------------------------------------------------- Commands (USB serial and Bluetooth)
// One line each. New-style answers start with "#<command> " followed by JSON, on one line.
//   info                        identity and Wi-Fi state (also plain, without "#info ", for program 1.5 tools)
//   join                        result of the last join request
//   setup {"ssid":..,"pass":..,"user":..,"name":..,"pin":..,"old_pin":..}   join a network (and remember it)
//   nets                        known networks (no passwords), the one in use, and the networks in reach
//   scan                        refresh the list of networks in reach (ask "nets" a few seconds later)
//   forget <name>               forget a known network
//   full                        everything the apps show (same as /api/status)
//   config upper=80&lower=40&mode=auto&...   same fields as /api/config
//   battery <0..100>            a battery report (same as /api/battery?level=N)
//   boost 1|0   name <text>   pin <new or empty>   identify   history   reboot
//   auth <pin>                  Bluetooth only, when a PIN is set
// The USB port is trusted (someone holding the socket); Bluetooth needs the PIN if one is set.
String urlDecode(const String &s) {
  String out;
  for (int i = 0; i < (int)s.length(); i++) {
    char c = s[i];
    if (c == '+') out += ' ';
    else if (c == '%' && i + 2 < (int)s.length()) {
      out += (char)strtol(s.substring(i + 1, i + 3).c_str(), nullptr, 16);
      i += 2;
    } else out += c;
  }
  return out;
}

bool queryArg(const String &q, const char *key, String &v) {
  String k = String(key) + "=";
  int p = 0;
  while (p <= (int)q.length()) {
    int e = q.indexOf('&', p);
    if (e < 0) e = q.length();
    if (q.substring(p, p + k.length()) == k) {
      v = urlDecode(q.substring(p + k.length(), e));
      return true;
    }
    p = e + 1;
  }
  return false;
}

String runCommand(String line, bool trusted, bool &authed) {
  line.trim();
  int sp = line.indexOf(' ');
  String cmd = sp < 0 ? line : line.substring(0, sp);
  String arg = sp < 0 ? String("") : line.substring(sp + 1);
  arg.trim();
  bool ok = trusted || authed || devPin.isEmpty();
  auto answer = [&](const String &json) { return "#" + cmd + " " + json; };
  auto fail = [&](const String &why) { return "#" + cmd + " {\"error\":\"" + jsonEscape(why) + "\"}"; };

  if (cmd == "auth") {
    authed = devPin.isEmpty() || arg == devPin;
    return answer(String("{\"ok\":") + (authed ? "true" : "false") + "}");
  }
  if (cmd == "info") return answer(infoJson());
  if (cmd == "join") return answer(setupStatusJson());
  if (cmd == "identify") {
    identifyUntil = nowMs() + 8000;
    return answer("{\"ok\":true}");
  }
  if (cmd == "setup") {
    String err = applySetup(jsonField(arg, "ssid"), jsonField(arg, "pass"), jsonField(arg, "user"), jsonField(arg, "name"),
                            jsonField(arg, "pin"), ok ? devPin : jsonField(arg, "old_pin"));
    return err.isEmpty() ? answer("{\"ok\":true}") : fail(err);
  }
  if (!ok) return fail("pin");
  if (cmd == "battery") { // battery <0..100>: same as /api/battery?level=N (phones on Bluetooth)
    if (arg.isEmpty() || arg.toInt() < 0 || arg.toInt() > 100) return fail("level is missing");
    reportBattery(arg.toInt(), nowMs());
    saveAllowIfChanged();
    return answer(statusJson());
  }
  if (cmd == "full") return answer(statusJson());
  if (cmd == "nets") return answer(netsInfoJson());
  if (cmd == "scan") {
    scanWanted = true;
    return answer("{\"ok\":true}");
  }
  if (cmd == "forget") return forgetNet(arg) ? answer(netsInfoJson()) : fail("not a known network");
  if (cmd == "config") {
    applyConfig([&](const char *k, String &v) { return queryArg(arg, k, v); });
    return answer(statusJson());
  }
  if (cmd == "boost") {
    st.boost = arg == "1";
    return answer(statusJson());
  }
  if (cmd == "name") {
    String err = setName(arg);
    return err.isEmpty() ? answer(infoJson()) : fail(err);
  }
  if (cmd == "pin") {
    String err = setPin(arg);
    return err.isEmpty() ? answer(infoJson()) : fail(err);
  }
  if (cmd == "history") return answer(historyJson());
  if (cmd == "ota") { // Bluetooth program update, see BLE_OTA
    String sub = arg, rest;
    int s2 = arg.indexOf(' ');
    if (s2 >= 0) { sub = arg.substring(0, s2); rest = arg.substring(s2 + 1); rest.trim(); }
    auto progress = [&]() {
      return "{\"active\":" + String(otaActive ? "true" : "false") + ",\"got\":" + String((long)otaGot) +
             ",\"size\":" + String((long)otaSize) + ",\"failed\":" + String(otaFailed ? "true" : "false") + "}";
    };
    if (sub == "begin") {
      int s3 = rest.indexOf(' ');
      long size = (s3 < 0 ? rest : rest.substring(0, s3)).toInt();
      String md5 = s3 < 0 ? String("") : rest.substring(s3 + 1);
      md5.trim();
      if (size < 100000 || size > 1900000) return fail("size");
      if (md5.length() != 32) return fail("md5");
      if (otaActive) otaCancel();
      updating = true;
      digitalWrite(PIN_POWER, LOW); // no charging while the program is replaced
      if (!Update.begin(size)) {
        updating = false;
        return fail(String("begin: ") + Update.errorString());
      }
      Update.setMD5(md5.c_str());
      otaActive = true;
      otaFailed = false;
      otaSize = size;
      otaGot = 0;
      otaLastData = nowMs();
      return answer(progress());
    }
    if (sub == "status") return answer(progress());
    if (sub == "abort") {
      otaCancel();
      return answer(progress());
    }
    if (sub == "end") {
      if (!otaActive) return fail("not started");
      if (otaFailed || otaGot != otaSize) {
        String p = progress();
        otaCancel();
        return fail("incomplete " + p);
      }
      otaActive = false;
      if (!Update.end(true)) {
        updating = false;
        return fail(String("check: ") + Update.errorString());
      }
      restartAt = nowMs() + 1500; // after this answer has gone out
      return answer("{\"ok\":true,\"restart_s\":2}");
    }
    return fail("ota begin|status|end|abort");
  }
  if (cmd == "reboot") {
    Serial.println(answer("{\"ok\":true}"));
    delay(200);
    ESP.restart();
  }
  return fail("unknown command");
}

// Answers to commands are sent whole: the port then waits up to 0.5 s for room in its buffer (the
// 5-second status line does not wait, so a computer that is not listening never slows the socket).
void serialReply(const String &s) {
  Serial.setTxTimeoutMs(500);
  Serial.println(s);
  Serial.flush();
  Serial.setTxTimeoutMs(0);
}

void serialPass() {
  static String line;
  static bool usbAuthed = true;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (line.length() < 600) line += c;
      continue;
    }
    line.trim();
    // The three commands of program 1.5 keep their old answers, for tools written for it.
    if (line.startsWith("setup ")) {
      String j = line.substring(6);
      String err = applySetup(jsonField(j, "ssid"), jsonField(j, "pass"), jsonField(j, "user"), jsonField(j, "name"),
                              jsonField(j, "pin"), devPin);
      serialReply(err.isEmpty() ? "setup: accepted" : "setup: refused (" + err + ")");
    } else if (line == "info") {
      serialReply(infoJson());
    } else if (line == "status") {
      serialReply(setupStatusJson());
    } else if (line.length()) {
      serialReply(runCommand(line, true, usbAuthed));
    }
    line = "";
  }
}

// ---------------------------------------------------------------- Button and light
// Short press: charge to 100 % once (press again to cancel).
// Three quick presses: join the Wi-Fi by WPS (then press the WPS button on the router).
// Hold 3 s: open setup (Bluetooth and the setup network) for 5 minutes.
// Hold 10 s: forget everything (Wi-Fi, name, PIN, settings, history).
int64_t buttonDownAt = -1;

void factoryReset() {
  prefs.clear();
  delay(200);
  ESP.restart();
}

void buttonPass() {
  static int presses = 0;
  static int64_t lastRelease = 0;
  bool down = digitalRead(PIN_BUTTON) == LOW;
  int64_t now = nowMs();
  if (down && buttonDownAt < 0) buttonDownAt = now;
  if (!down && buttonDownAt >= 0) {
    int64_t held = now - buttonDownAt;
    buttonDownAt = -1;
    if (held >= 10000) factoryReset();
    else if (held >= 3000) openSetup(5 * 60 * 1000);
    else if (held >= 40) {
      presses++;
      lastRelease = now;
    }
  }
  if (presses && !down && now - lastRelease > 600) {
    if (presses >= 3) startWps();
    else if (presses == 1) st.boost = !st.boost;
    presses = 0;
  }
}

void ledPass() {
  int64_t now = nowMs();
  int bright = cfg.led;
  bool on;
  int64_t held = buttonDownAt >= 0 ? now - buttonDownAt : 0;
  if (held >= 10000) { on = (now / 60) % 2; bright = 100; }          // about to forget everything
  else if (held >= 3000) { on = true; bright = 100; }                  // let go now to open setup
  else if (now < identifyUntil) { on = (now / 100) % 2; bright = 100; } // "find me"
  else if (wpsActive) on = (now % 1000) < 100 || ((now % 1000) > 200 && (now % 1000) < 300);
  else if (updating) on = (now / 50) % 2;
  else if (setupMode && WiFi.status() != WL_CONNECTED) on = (now / 150) % 2;
  else if (st.hot) on = (now % 2000) < 200;
  else if (st.presentSince < 0) on = false;
  else if (st.vbus) on = true;
  else on = (now / 1000) % 2;
  ledcWrite(PIN_LED, on ? (bright * 255) / 100 : 0);
}

// One line every 5 s on the USB serial port, for checking the board from a computer.
void reportPass() {
  static int64_t last = -5000;
  int64_t now = nowMs();
  if (now - last < 5000) return;
  last = now;
  bool connected = WiFi.status() == WL_CONNECTED;
  Serial.printf("smartsocket fw=%s id=%s name=%s up=%lds wifi=%s ip=%s fail=%d seen=%d setup=%d wps=%d nets=%d temp=%.1f "
                "offer_mV=%d phone_mV=%d src=%dmA limited=%d phone=%d power=%d reason=%s out=%dmA allow=%d mode=%s "
                "level=%d time=%d\n",
                FW_VERSION, devId.c_str(), devName.c_str(), (long)(now / 1000),
                connected ? WiFi.SSID().c_str() : (setupMode ? setupApName.c_str() : (currentSsid.length() ? currentSsid.c_str() : "searching")),
                (connected ? WiFi.localIP() : WiFi.softAPIP()).toString().c_str(), wifiFailReason, wifiSeenRssi,
                setupMode, wpsActive, netCount, tempC, readMv(PIN_SRC_SENSE), readMv(PIN_PHONE_SENSE), srcMa, currentLimited,
                st.presentSince >= 0, st.vbus, REASON_NAMES[st.reason], st.outMa, st.allow, modeName(cfg.mode), st.level,
                minuteOfDay());
}

// ---------------------------------------------------------------- Arduino entry points
void setup() {
  pinMode(PIN_POWER, OUTPUT);
  digitalWrite(PIN_POWER, LOW);
  ledcAttach(PIN_LED, 2000, 8);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  analogSetAttenuation(ADC_11db);
  applyRp(BASIC_MA);
  // If the last restart was the supply sagging (a charger that cannot give what it offers),
  // stay on basic current until the socket is unplugged and plugged in again.
  currentLimited = esp_reset_reason() == ESP_RST_BROWNOUT;

  Serial.begin(115200);
  Serial.setTxTimeoutMs(0); // never wait for a computer that is not listening
  loadSettings();
  makeIdentity();
  startWifi();
  bleStart(); // always on: setup and commands from a phone or laptop at any time
  startServer();
  bootCount = prefs.getUInt("boots", 0) + 1;
  prefs.putUInt("boots", bootCount);
  // If the main loop ever stops for a minute, restart (the light stays as it was, Bluetooth and Wi-Fi
  // stop answering: this ends that by itself). Program uploads keep it fed while they write.
  esp_task_wdt_config_t wdt = {.timeout_ms = 60000, .idle_core_mask = 0, .trigger_panic = true};
  esp_task_wdt_reconfigure(&wdt);
  esp_task_wdt_add(NULL);
}

void loop() {
  static int64_t lastControl = 0;
  static bool netStarted = false;
  int64_t now = nowMs();

  if (now - lastControl >= 50) {
    lastControl = now;
    controlPass();
    saveAllowIfChanged();
    buttonPass();
    ledPass();
    wpsPass();
    reportPass();
    serialPass();
    blePass();
  }
  esp_task_wdt_reset();
  wifiHousekeeping();
  // Once on the home network: answer to smartsocket-xxxxxx.local, announce the socket for the app,
  // learn the time, and accept program updates over Wi-Fi.
  if (!netStarted && WiFi.status() == WL_CONNECTED) {
    netStarted = true;
    configTzTime(TIME_ZONE, "pool.ntp.org", "time.google.com");
    timeStarted = true;
    ArduinoOTA.setHostname(hostName.c_str());
    ArduinoOTA.setPassword(OTA_PASSWORD);
    ArduinoOTA.onProgress([](unsigned int, unsigned int) { esp_task_wdt_reset(); });
    ArduinoOTA.onStart([]() {
      updating = true;
      digitalWrite(PIN_POWER, LOW); // no charging while the program is replaced
    });
    ArduinoOTA.begin(); // also starts mDNS under hostName
    MDNS.addService("http", "tcp", 80);
    MDNS.addService("smartsocket", "tcp", 80);
    MDNS.addServiceTxt("smartsocket", "tcp", "id", devId);
    MDNS.addServiceTxt("smartsocket", "tcp", "name", devName);
    MDNS.addServiceTxt("smartsocket", "tcp", "fw", FW_VERSION);
    MDNS.addServiceTxt("smartsocket", "tcp", "pin", devPin.isEmpty() ? "0" : "1");
  }
  if (netStarted) ArduinoOTA.handle();
  server.handleClient();
  if (otaActive && now - otaLastData > 20000) otaCancel(); // the sender went quiet: keep the old program
  if (restartAt > 0 && now >= restartAt) ESP.restart();
}
