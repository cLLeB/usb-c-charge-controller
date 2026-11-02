# Builds SmartSocket.apk with the Android command-line tools only (no Android Studio, no Gradle).
# Needs: JDK 17 and the Android SDK with "platforms;android-34" and "build-tools;34.0.0".
# Run from this folder:  powershell -ExecutionPolicy Bypass -File build.ps1
# Add -Install to install it on a device connected with USB debugging or wireless debugging (adb).
param([switch]$Install)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$sdk = "$env:LOCALAPPDATA\Android\Sdk"
$bt = "$sdk\build-tools\34.0.0"
$jar = "$sdk\platforms\android-34\android.jar"
$jdk = (Get-ChildItem "C:\Program Files\Microsoft" -Directory -Filter "jdk-17*" | Select-Object -First 1).FullName
$env:JAVA_HOME = $jdk
$env:Path = "$jdk\bin;$env:Path"

if (Test-Path build) { Remove-Item build -Recurse -Force }
New-Item -ItemType Directory build, build\gen, build\obj, build\dex | Out-Null

# 1. Resources (the icon) and the manifest
& "$bt\aapt2.exe" compile --dir res -o build\res.zip
if ($LASTEXITCODE) { throw "aapt2 compile failed" }
& "$bt\aapt2.exe" link -o build\base.apk -I $jar --manifest AndroidManifest.xml -R build\res.zip --java build\gen --auto-add-overlay
if ($LASTEXITCODE) { throw "aapt2 link failed" }

# 2. Java to class files, class files to Android's dex format
$sources = Get-ChildItem src, build\gen -Recurse -Filter *.java | ForEach-Object { $_.FullName }
& javac -nowarn -Xlint:none -source 11 -target 11 -encoding UTF-8 -classpath $jar -d build\obj $sources
if ($LASTEXITCODE) { throw "javac failed" }
$classes = Get-ChildItem build\obj -Recurse -Filter *.class | ForEach-Object { $_.FullName }
& "$bt\d8.bat" --release --min-api 26 --lib $jar --output build\dex $classes
if ($LASTEXITCODE) { throw "d8 failed" }

# 3. Put classes.dex into the package, align it, sign it
Copy-Item build\base.apk build\unsigned.apk
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open("$PSScriptRoot\build\unsigned.apk", "Update")
[System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, "$PSScriptRoot\build\dex\classes.dex", "classes.dex") | Out-Null
$zip.Dispose()
& "$bt\zipalign.exe" -f -p 4 build\unsigned.apk build\aligned.apk
if ($LASTEXITCODE) { throw "zipalign failed" }
# The app's own signing key. Keep this file: updates must be signed with the same key to install over the old app.
if (-not (Test-Path smartsocket.keystore)) {
    & keytool -genkeypair -keystore smartsocket.keystore -storepass smartsocket -keypass smartsocket -alias smartsocket `
        -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Smart Socket, O=Caleb Kyere Boateng, C=FR" | Out-Null
}
& "$bt\apksigner.bat" sign --ks smartsocket.keystore --ks-pass pass:smartsocket --key-pass pass:smartsocket --out SmartSocket.apk build\aligned.apk
if ($LASTEXITCODE) { throw "apksigner failed" }
& "$bt\apksigner.bat" verify SmartSocket.apk
if ($LASTEXITCODE) { throw "signature check failed" }
"built SmartSocket.apk, {0:N0} kB" -f ((Get-Item SmartSocket.apk).Length / 1KB)

if ($Install) { & "$sdk\platform-tools\adb.exe" install -r SmartSocket.apk }
