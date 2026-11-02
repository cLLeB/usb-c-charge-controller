# Regenerates the smart socket board from the schematic netlist, checks it, and exports the files to order.
# Uses only what KiCad installs. Run from this folder:  powershell -File build_socket.ps1
# Add -Schematic to regenerate the schematic from gen_socket_schematic.py first.
# Do not run after editing the schematic or board by hand in KiCad: it overwrites them.
param([switch]$Schematic)
$bin = "$env:LOCALAPPDATA\Programs\KiCad\10.0\bin"
Set-Location $PSScriptRoot
if ($Schematic) { & "$bin\python.exe" gen_socket_schematic.py }
& "$bin\kicad-cli.exe" sch export netlist --format kicadsexpr -o smartsocket.net smartsocket.kicad_sch | Out-Null
& "$bin\python.exe" check_netlist.py smartsocket.net
& "$bin\kicad-cli.exe" sch erc --severity-all -o erc.rpt smartsocket.kicad_sch
& "$bin\python.exe" gen_socket_board.py
& "$bin\kicad-cli.exe" pcb drc --severity-all --schematic-parity --refill-zones --save-board -o drc.rpt smartsocket.kicad_pcb
New-Item -ItemType Directory -Force fab | Out-Null
& "$bin\kicad-cli.exe" pcb export gerbers --layers "F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,F.Paste,B.Paste,Edge.Cuts" --subtract-soldermask -o fab smartsocket.kicad_pcb | Out-Null
& "$bin\kicad-cli.exe" pcb export drill --format excellon --drill-origin absolute --excellon-units mm --excellon-separate-th -o fab smartsocket.kicad_pcb | Out-Null
if (Test-Path smartsocket-gerbers.zip) { [IO.File]::Delete("$PSScriptRoot\smartsocket-gerbers.zip") }
Compress-Archive -Path fab\* -DestinationPath smartsocket-gerbers.zip
Remove-Item fab -Recurse -Confirm:$false
& "$bin\kicad-cli.exe" sch export bom --fields 'Reference,Value,Footprint,${QUANTITY}' --labels 'Refs,Value,Footprint,Qty' --group-by 'Value,Footprint' -o bom.csv smartsocket.kicad_sch | Out-Null
& "$bin\kicad-cli.exe" sch export pdf -o smartsocket-schematic.pdf smartsocket.kicad_sch | Out-Null
# Full size, for printing at 100 % and laying the real parts on: pads, outlines, names, module outlines
& "$bin\kicad-cli.exe" pcb export pdf --layers "F.Cu,F.Silkscreen,F.Fab,Edge.Cuts" --mode-single --black-and-white -o smartsocket-print-1to1.pdf smartsocket.kicad_pcb | Out-Null
& "$bin\kicad-cli.exe" pcb render --side top --width 1600 --height 700 --zoom 2.4 --quality high -o board-3d.png smartsocket.kicad_pcb | Out-Null
& "$bin\python.exe" draw_board.py
"gerbers: smartsocket-gerbers.zip"
