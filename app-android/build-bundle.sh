#!/usr/bin/env bash
# Builds the Google Play bundle (SmartSocket.aab) with the Android command-line tools and bundletool, no Gradle.
# Play knows the app as com.calebkyereboateng.smartsocket; the code keeps its own package (org.smartsocket.app).
# Needs ANDROID_HOME with platforms;android-36 and build-tools;36.0.0, JDK 17, and bundletool.jar beside it.
# Signs with the upload key when UPLOAD_KEYSTORE (a path) and UPLOAD_PASSWORD are set.
set -euo pipefail
cd "$(dirname "$0")"
PLAY_ID=com.calebkyereboateng.smartsocket
BT="$ANDROID_HOME/build-tools/36.0.0"
JAR="$ANDROID_HOME/platforms/android-36/android.jar"
rm -rf bundle && mkdir -p bundle/gen bundle/obj bundle/dex bundle/module/manifest bundle/module/dex

# 1. Resources and manifest, in the form a bundle keeps them
"$BT/aapt2" compile --dir res -o bundle/res.zip
"$BT/aapt2" link --proto-format -o bundle/linked.zip -I "$JAR" --manifest AndroidManifest.xml -R bundle/res.zip \
  --java bundle/gen --custom-package org.smartsocket.app --rename-manifest-package "$PLAY_ID" --auto-add-overlay

# 2. Java to class files to dex
javac -nowarn -Xlint:none -source 11 -target 11 -encoding UTF-8 -classpath "$JAR" -d bundle/obj $(find src bundle/gen -name '*.java')
"$BT/d8" --release --min-api 26 --lib "$JAR" --output bundle/dex $(find bundle/obj -name '*.class')

# 3. The base module: manifest/, dex/, res/, resources.pb
(cd bundle/module && unzip -q ../linked.zip && mv AndroidManifest.xml manifest/)
cp bundle/dex/classes.dex bundle/module/dex/
(cd bundle/module && zip -qr ../base.zip .)
rm -f SmartSocket.aab
java -jar bundletool.jar build-bundle --modules=bundle/base.zip --output=SmartSocket.aab

# 4. Signed with the upload key; Play re-signs it with the app's own key
if [ -n "${UPLOAD_KEYSTORE:-}" ]; then
  jarsigner -keystore "$UPLOAD_KEYSTORE" -storepass "$UPLOAD_PASSWORD" -keypass "$UPLOAD_PASSWORD" \
    -sigalg SHA256withRSA -digestalg SHA-256 SmartSocket.aab upload
  jarsigner -verify SmartSocket.aab | tail -1
fi

# 5. Checked: the app a phone would get from this bundle, its name and version
java -jar bundletool.jar build-apks --bundle=SmartSocket.aab --output=bundle/check.apks --mode=universal --overwrite
(cd bundle && unzip -qo check.apks universal.apk)
"$BT/aapt2" dump badging bundle/universal.apk | grep -E "^package|targetSdk|launchable-activity"
