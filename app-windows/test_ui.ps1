# Presses buttons in SmartSocket.exe and checks the stand-in socket. Run tools/mock_server.py 8765 2 first, then: powershell -File test_ui.ps1
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$exe = "C:\Users\kyere\Documents\codes\Project_work\First\01-lock-in-amplifier\smart-socket\app-windows\SmartSocket.exe"
$base = "http://127.0.0.1:8765"
$results = @()
function Check($name, $ok) { $script:results += $ok; "{0,-48} {1}" -f $name, $(if ($ok) { "PASS" } else { "FAIL" }) }
function St { Invoke-RestMethod "$base/api/status" }

# Point the app at the stand-in only, and put its own list back afterwards
$known = "$env:APPDATA\SmartSocket\sockets.txt"
$saved = if (Test-Path $known) { Get-Content $known -Raw } else { $null }
$info = Invoke-RestMethod "$base/api/info"
New-Item -ItemType Directory -Force (Split-Path $known) | Out-Null
Set-Content -Path $known -Value ("{0}={1}|{2}" -f $info.id, $base, $info.name) -Encoding ascii
Invoke-RestMethod -Method Post "$base/api/config?mode=auto&timer_min=0&sched_on=0&led=100" | Out-Null
$p = Start-Process $exe -PassThru
Start-Sleep 8
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, $p.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
function Find($name) {
    $c = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $name)
    # several elements can share a name (a tab and its page): take the one that is on screen
    foreach ($e in $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $c)) {
        $r = $e.Current.BoundingRectangle
        if (-not $e.Current.IsOffscreen -and $r.Width -gt 0 -and $r.Height -gt 0) { return $e }
    }
    return $null
}
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class M {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(int f, int x, int y, int d, IntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public static void Click(int x, int y) { SetCursorPos(x, y); mouse_event(2, 0, 0, 0, IntPtr.Zero); mouse_event(4, 0, 0, 0, IntPtr.Zero); }
}
"@
function Press($name) {
    $e = Find $name
    if ($e -eq $null) { return $false }
    [M]::SetForegroundWindow([IntPtr]$p.MainWindowHandle) | Out-Null
    $r = $e.Current.BoundingRectangle
    if ($r.Width -le 0) { return $false }
    [M]::Click([int]($r.X + 12), [int]($r.Y + $r.Height / 2))
    Start-Sleep 2
    return $true
}

# Tab headings are not exposed to UI Automation: click them where they are (window picture coordinates)
function Tab($dx) {
    [M]::SetForegroundWindow([IntPtr]$p.MainWindowHandle) | Out-Null
    $r = $win.Current.BoundingRectangle
    $k = $r.Width / 806.0   # the window picture is 806 wide at 100 % display scaling
    [M]::Click([int]($r.X + $dx * $k), [int]($r.Y + 51 * $k)); Start-Sleep 2
}

Check "window opened" ($win -ne $null)
Check "'Always charge' button found and pressed" (Press "Always charge")
Check "socket mode is now 'on'" ((St).mode -eq "on")
Press "Use limits" | Out-Null
Check "'Use limits' sets it back to 'auto'" ((St).mode -eq "auto")
Press "Charge to 100 % this once" | Out-Null
Check "'Charge to 100 %' turns boost on" ((St).boost -eq $true)
Press "Going to 100 % (click to cancel)" | Out-Null
Check "pressing again cancels it" ((St).boost -eq $false)
Tab 384
Press "Stop charging after" | Out-Null
Check "'Save these settings' pressed" (Press "Save these settings")
Check "timer saved on the socket (60 min)" ((St).timer_min -eq 60)
Press "Stop charging after" | Out-Null
Press "Save these settings" | Out-Null
Check "timer switched off again" ((St).timer_min -eq 0)
Tab 517
Check "'Find me' pressed" (Press "Find me (flash its light)")
Check "the socket's light is flashing (find me arrived)" ((St).identify -eq $true)

$p.CloseMainWindow() | Out-Null; Start-Sleep 1; if (!$p.HasExited) { $p.Kill() }
if ($saved -ne $null) { Set-Content -Path $known -Value $saved.TrimEnd() -Encoding ascii -NoNewline } else { Remove-Item $known -Confirm:$false }
"`n{0} of {1} passed" -f ($results | Where-Object { $_ }).Count, $results.Count
