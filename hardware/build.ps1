# Regenerate schematic -> netlist -> PCB and run ERC/DRC (KiCad 10, Windows)
$ErrorActionPreference = 'Stop'
$k = 'C:\Program Files\KiCad\10.0\bin\kicad-cli.exe'
$py = 'C:\Program Files\KiCad\10.0\bin\python.exe'
$hw = $PSScriptRoot
$out = Join-Path $hw 'out'
New-Item -ItemType Directory -Force $out | Out-Null
& $py (Join-Path $hw 'gen\gen_sch.py')
Push-Location (Join-Path $hw 'kicad')
& $k sch erc --format json --severity-all -o "$out\erc.json" ionos_maximus.kicad_sch
& $k sch export netlist --format kicadsexpr -o "$out\ionos_maximus.net" ionos_maximus.kicad_sch
& $py (Join-Path $hw 'gen\check_pinmap.py') "$out\ionos_maximus.net"
& $k sch export pdf -o (Join-Path $hw 'ionos_maximus_schematic.pdf') ionos_maximus.kicad_sch
if ($args -contains '-pcb') { & $py (Join-Path $hw 'gen\gen_pcb.py') "$out\ionos_maximus.net" }
& $k pcb drc --format json --severity-all --schematic-parity --refill-zones --save-board -o "$out\drc.json" ionos_maximus.kicad_pcb
Pop-Location
