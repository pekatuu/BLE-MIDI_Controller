Import-Module 'C:\Program Files\Windows MIDI Services\PowerShell\WindowsMidiServices\WindowsMidiServices.psd1'
Start-Midi | Out-Null
$session = Start-MidiSession 'BLE Bridge Forwarder'
if ($session -eq $null) { Write-Output 'SESSION_FAIL'; exit 1 }
$connection = Open-MidiEndpointConnection $session '\\?\swd#midisrv#midiu_loop_a_default#{e7cce071-3c03-423f-88d3-f1045d02552b}'
if ($connection -eq $null) { Write-Output 'CONNECT_FAIL'; exit 1 }
Write-Output 'READY'
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    if ($line -eq 'EXIT') { break }
    $word = [uint32]$line
    Send-MidiMessage $connection $word -Timestamp 0 | Out-Null
}
Close-MidiEndpointConnection $session $connection | Out-Null
Stop-MidiSession $session
Stop-Midi
