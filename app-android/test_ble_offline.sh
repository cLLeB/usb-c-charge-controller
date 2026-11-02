# Runs ON the tablet (adb push, then: adb shell "nohup sh /sdcard/Download/bletest/run.sh >/dev/null 2>&1 &").
# Switches Wi-Fi off so the app can only reach the socket over Bluetooth, presses the app's buttons by their
# text (Find, Never, Use limits, Charge to 100 % once twice, Load a new program with the file given), logs
# every screen to /sdcard/Download/bletest/log.txt, then switches Wi-Fi back on. Read the log over adb after.
D=/sdcard/Download/bletest
L=$D/log.txt
FILE=${1:-smart_socket-1.7.3.bin}
echo "start $(date)" > $L

screen() { uiautomator dump $D/ui.xml >/dev/null 2>&1; }
# tap the centre of the first element whose text contains $1
tap() {
  screen
  b=$(grep -o "text=\"[^\"]*$1[^\"]*\"[^>]*bounds=\"[^\"]*\"" $D/ui.xml | head -1 | grep -o 'bounds="[^"]*"' | grep -o '[0-9]*')
  if [ -z "$b" ]; then echo "$(date +%T) NOT FOUND: $1" >> $L; return 1; fi
  set -- $b
  input tap $(( ($1 + $3) / 2 )) $(( ($2 + $4) / 2 ))
  echo "$(date +%T) tapped: $1" >> $L
}
show() { screen; echo "--- $(date +%T) $1" >> $L; grep -oE 'text="[^"]+"' $D/ui.xml | head -${2:-12} >> $L; }

svc wifi disable
sleep 5
input keyevent KEYCODE_WAKEUP
am force-stop org.smartsocket.app
monkey -p org.smartsocket.app -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
sleep 25                                 # the app finds the socket over Bluetooth
show "list" 6
tap "Socket "; sleep 8
show "socket page" 6
tap "Find"; sleep 4
tap "Never"; sleep 8
show "after Never" 4
tap "Use limits"; sleep 8
show "after Use limits" 4
tap "Charge to 100"; sleep 8
show "after boost on" 4
tap "Charge to 100"; sleep 8
show "after boost off" 4
input swipe 600 1500 600 500 400; sleep 2
tap "Load a new program"; sleep 4
screen
grep -q 'content-desc="Search"' $D/ui.xml && {
  b=$(grep -o 'content-desc="Search"[^>]*bounds="[^"]*"' $D/ui.xml | grep -o 'bounds="[^"]*"' | grep -o '[0-9]*'); set -- $b
  input tap $(( ($1 + $3) / 2 )) $(( ($2 + $4) / 2 )); sleep 2
  input text smart_socket; input keyevent KEYCODE_ENTER; sleep 4
}
tap "$FILE"; sleep 4
show "confirm" 6
tap "Load"; sleep 5
i=0
while [ $i -lt 40 ]; do                  # up to about 4 minutes
  screen
  t=$(grep -oE 'text="(Loading|Loaded|Did not|This socket)[^"]*"' $D/ui.xml | head -1)
  echo "$(date +%T) $t" >> $L
  case "$t" in *Loaded*|*"Did not"*|*"This socket"*) break;; esac
  sleep 6; i=$((i+1))
done
sleep 20
show "after restart" 6
svc wifi enable
echo "end $(date)" >> $L
rm -f $D/ui.xml
