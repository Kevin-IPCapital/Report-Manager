<#
    Samples CPU, memory and per-volume free space, and emits ONE line of JSON.

    This is the N-central side of the metrics route. The REST API exposes monitor
    state only — no measured values anywhere (confirmed on 2026.3.1.10: every
    service-monitor row has lastDataId null, /assets is inventory only, and the
    device resource advertises no metric sub-resource). Custom properties ARE
    exposed by the API, so this writes a reading into one and
    ncentral_metrics.py reads it back out.

    HOW TO WIRE IT UP IN N-CENTRAL
      1. Create a device custom property, type TEXT, named:   IPC Perf Sample
         (any name works — set NCENTRAL_PERF_PROPERTY to match if you change it)
      2. Build an Automation Manager policy that runs this script and writes its
         stdout into that property.
      3. Schedule the policy at whatever interval you want readings.

    The collector's resolution is the AMP schedule, not its own: reading the
    property more often than the AMP writes it yields the same row, not more
    data. Sample every 15 minutes and you get 15-minute resolution.

    OUTPUT — one line, so it fits a TEXT property:
      {"ts":"2026-08-17T09:00:00","cpu_pct":91.2,"mem_pct":78.4,
       "mem_total_mb":32768,"mem_used_mb":25690,"disks":{"C:":8.4},"host":"..."}

    `ts` is this machine's local clock at sample time, and it is what makes the
    reading trustworthy. A custom property keeps its last value forever, so
    without a timestamp a dead AMP looks identical to a quiet server. The
    collector keys on ts, so a stopped policy shows as a gap rather than as a
    flat line of repeated readings.

    CPU is sampled over ~3 seconds rather than instantaneously — a single
    snapshot of a Windows CPU counter is mostly noise.
#>

[CmdletBinding()]
param(
    # Seconds to average CPU over. Keep it short: the AMP holds a session open
    # for the duration, and N-central times long-running policies out.
    [int]$CpuSampleSeconds = 3
)

$ErrorActionPreference = 'Stop'

function Get-CpuPercent {
    param([int]$Seconds)

    # Counter path is localised on non-English Windows, so try the counter first
    # and fall back to CIM, which is language-neutral.
    try {
        $samples = Get-Counter -Counter '\Processor(_Total)\% Processor Time' `
                               -SampleInterval 1 -MaxSamples $Seconds -ErrorAction Stop
        $values = $samples.CounterSamples | ForEach-Object { $_.CookedValue }
        return [math]::Round((($values | Measure-Object -Average).Average), 2)
    } catch {
        $cpu = Get-CimInstance -ClassName Win32_PerfFormattedData_PerfOS_Processor `
                               -Filter "Name='_Total'" -ErrorAction Stop
        return [math]::Round([double]$cpu.PercentProcessorTime, 2)
    }
}

function Get-MemoryInfo {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    # These are KB. TotalVisibleMemorySize excludes memory the firmware reserves,
    # which is why it can read slightly under the sticker RAM size.
    $totalMb = [math]::Round($os.TotalVisibleMemorySize / 1024, 0)
    $freeMb  = [math]::Round($os.FreePhysicalMemory     / 1024, 0)
    $usedMb  = $totalMb - $freeMb

    $pct = if ($totalMb -gt 0) { [math]::Round(100.0 * $usedMb / $totalMb, 2) } else { $null }

    return [pscustomobject]@{
        TotalMb = [int64]$totalMb
        UsedMb  = [int64]$usedMb
        Pct     = $pct
    }
}

function Get-DiskFreePercent {
    # DriveType 3 = local fixed disk. Network and removable volumes are excluded
    # deliberately: a disconnected share would otherwise read as a full disk.
    $disks = @{}
    Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop |
        ForEach-Object {
            if ($_.Size -gt 0) {
                $disks[$_.DeviceID] = [math]::Round(100.0 * $_.FreeSpace / $_.Size, 2)
            }
        }
    return $disks
}

try {
    $memory = Get-MemoryInfo

    $reading = [ordered]@{
        # Round-trip-ish local time, no zone — matches the naive-local convention
        # the database uses throughout, so nothing gets reinterpreted as UTC.
        ts           = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
        cpu_pct      = Get-CpuPercent -Seconds $CpuSampleSeconds
        mem_pct      = $memory.Pct
        mem_total_mb = $memory.TotalMb
        mem_used_mb  = $memory.UsedMb
        disks        = Get-DiskFreePercent
        host         = $env:COMPUTERNAME
    }

    # -Compress keeps it to one line. A TEXT custom property is a single-line
    # field, and a pretty-printed blob would be truncated at the first newline.
    $json = $reading | ConvertTo-Json -Compress -Depth 4
    Write-Output $json
    exit 0
}
catch {
    # Emit nothing parseable on failure. Writing a partial reading would be worse
    # than writing none: the collector would store it as a real sample. The
    # property keeps its previous value, which ages out and shows up as stale in
    # v_perf_sample_current.
    Write-Error "perf sample failed: $($_.Exception.Message)"
    exit 1
}
