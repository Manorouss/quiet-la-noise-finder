# Opt the NoiseModelling engine processes (java) out of Windows power throttling (EcoQoS), so the
# scheduler may use the performance cores. Per-process, not a system setting; priority stays Normal.
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public static class QuietLaQoS {
  [StructLayout(LayoutKind.Sequential)] public struct PPTS { public uint Version; public uint ControlMask; public uint StateMask; }
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetProcessInformation(IntPtr h, int cls, ref PPTS info, uint size);
}
"@
$state = New-Object QuietLaQoS+PPTS
$state.Version = 1; $state.ControlMask = 1; $state.StateMask = 0
foreach ($p in Get-Process java -ErrorAction SilentlyContinue) {
  $ok = [QuietLaQoS]::SetProcessInformation($p.Handle, 4, [ref]$state, [System.Runtime.InteropServices.Marshal]::SizeOf($state))
  "java $($p.Id): unthrottled=$ok priority=$($p.PriorityClass)"
}
