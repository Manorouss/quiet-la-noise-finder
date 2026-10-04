# Freeze, resume or report the Quiet LA NoiseModelling engine listening on one port.
# Installed on the PC as D:\quietla\tools\engine_control.ps1 and called over SSH by
# compute_dashboard.py. Only java.exe processes whose command line names
# LoopbackNoiseModellingServer and the given --port are touched.
#   engine_control.ps1 -Action suspend|resume|status -Port 9111
param([Parameter(Mandatory)][ValidateSet('suspend', 'resume', 'status')][string]$Action,
      [Parameter(Mandatory)][int]$Port)
$ErrorActionPreference = 'Stop'
Add-Type -Namespace QuietLA -Name Nt -MemberDefinition @'
[DllImport("ntdll.dll")] public static extern int NtSuspendProcess(IntPtr handle);
[DllImport("ntdll.dll")] public static extern int NtResumeProcess(IntPtr handle);
'@
$engines = @(Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
  Where-Object { $_.CommandLine -like '*LoopbackNoiseModellingServer*' -and $_.CommandLine -match "--port $Port(\s|$)" })
foreach ($engine in $engines) {
  $process = Get-Process -Id $engine.ProcessId
  if ($Action -eq 'suspend') { [void][QuietLA.Nt]::NtSuspendProcess($process.Handle) }
  elseif ($Action -eq 'resume') { [void][QuietLA.Nt]::NtResumeProcess($process.Handle) }
  $process.Refresh()
  $suspended = @($process.Threads | Where-Object { $_.WaitReason -eq 'Suspended' }).Count
  Write-Output ("{0} pid={1} threads={2} suspended={3} cpu_s={4:N0}" -f $Action, $process.Id, $process.Threads.Count, $suspended, $process.TotalProcessorTime.TotalSeconds)
}
if ($engines.Count -eq 0) { Write-Output "none port=$Port" }
