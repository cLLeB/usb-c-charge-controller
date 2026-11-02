@echo off
rem Builds SmartSocket.exe with the C# compiler that ships with Windows.
rem Bluetooth uses the Windows Runtime: its metadata (System32\WinMetadata\*.winmd) and the .NET
rem Framework facades that let the compiler map it (System.Runtime, System.Runtime.InteropServices.WindowsRuntime).
set FW=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319
set CSC=%FW%\csc.exe
set WM=%WINDIR%\System32\WinMetadata
"%CSC%" /nologo /target:winexe /optimize+ /out:"%~dp0SmartSocket.exe" ^
  /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll ^
  /r:"%FW%\System.Runtime.dll" /r:"%FW%\System.Runtime.InteropServices.WindowsRuntime.dll" ^
  /r:"%WM%\Windows.Foundation.winmd" /r:"%WM%\Windows.Devices.winmd" /r:"%WM%\Windows.Storage.winmd" ^
  "%~dp0SmartSocket.cs"
