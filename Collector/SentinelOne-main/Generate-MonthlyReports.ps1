# SentinelOne Monthly Customer Security Report Generator
# IP Dimension - MSP EDR Reporting
# Covers: Threats, Classifications, MITRE ATT&CK, MTTR, Endpoint Health,
#         Protection Mode, Agent Health Issues, LOLBins, Daily Trend, MoM, Users

param(
    [string]$Month = "2026-06",
    [string]$ReportDir = "$PSScriptRoot\Reports"
)

$envFile    = "$PSScriptRoot\.env"
$envContent = Get-Content $envFile
$token      = ($envContent | Where-Object { $_ -match "^SENTINELONE_API_TOKEN=" }) -replace "^SENTINELONE_API_TOKEN=",""
$baseUrl    = ($envContent | Where-Object { $_ -match "^SENTINELONE_BASE_URL=" })  -replace "^SENTINELONE_BASE_URL=",""
$headers    = @{ "Authorization" = "ApiToken $token"; "Content-Type" = "application/json" }

$monthStart  = "$Month-01T00:00:00Z"
$monthEnd    = ([datetime]"$Month-01").AddMonths(1).AddSeconds(-1).ToString("yyyy-MM-ddTHH:mm:ssZ")
$monthLabel  = ([datetime]"$Month-01").ToString("MMMM yyyy")
$prevMonth   = ([datetime]"$Month-01").AddMonths(-1).ToString("yyyy-MM")
$prevLabel   = ([datetime]"$Month-01").AddMonths(-1).ToString("MMMM yyyy")
$prev2Month  = ([datetime]"$Month-01").AddMonths(-2).ToString("yyyy-MM")
$prev2Label  = ([datetime]"$Month-01").AddMonths(-2).ToString("MMMM yyyy")
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

function Get-Paged {
    param($Url)
    $all = @(); $cursor = $null
    do {
        $u = if ($cursor) { "$Url&cursor=$cursor" } else { $Url }
        $r = Invoke-RestMethod -Uri $u -Headers $headers -Method GET
        $all += $r.data
        $cursor = $r.pagination.nextCursor
    } while ($cursor)
    return $all
}

function Get-Count { param($Url)
    (Invoke-RestMethod -Uri $Url -Headers $headers -Method GET).pagination.totalItems
}

Write-Host "[$monthLabel] Fetching data..." -ForegroundColor Cyan

# ── Pull all data ──────────────────────────────────────────────────────────────
Write-Host "  Loading threats..."
$allThreats = Get-Paged "$baseUrl/web/api/v2.1/threats?limit=100&createdAt__gte=$monthStart&createdAt__lte=$monthEnd&sortBy=createdAt&sortOrder=desc"

Write-Host "  Loading agents..."
$allAgents  = Get-Paged "$baseUrl/web/api/v2.1/agents?limit=100"

Write-Host "  Getting latest agent version and MoM counts..."
$latestPkg  = (Invoke-RestMethod -Uri "$baseUrl/web/api/v2.1/update/agent/packages?limit=1&sortBy=version&sortOrder=desc&osTypes=windows&fileExtension=.msi&status=ga" -Headers $headers).data[0].version
$prevCount  = Get-Count "$baseUrl/web/api/v2.1/threats?limit=1&createdAt__gte=$prevMonth-01T00:00:00Z&createdAt__lte=$(([datetime]"$prevMonth-01").AddMonths(1).AddSeconds(-1).ToString("yyyy-MM-ddTHH:mm:ssZ"))"
$prev2Count = Get-Count "$baseUrl/web/api/v2.1/threats?limit=1&createdAt__gte=$prev2Month-01T00:00:00Z&createdAt__lte=$(([datetime]"$prev2Month-01").AddMonths(1).AddSeconds(-1).ToString("yyyy-MM-ddTHH:mm:ssZ"))"

Write-Host "  Done. Threats: $($allThreats.Count)  Agents: $($allAgents.Count)  Latest: $latestPkg"
Write-Host ""

# LOLBins list
$lolBinNames = @("powershell","cmd.exe","wscript","cscript","mshta","rundll32","regsvr32","certutil","bitsadmin","wmic","msiexec","psexec","reg.exe","sc.exe","net.exe","osascript","bash","sh-thd","curl","wget","wevtutil","nltest","dsquery","klist","mimikatz")

# ── Per-customer reports ───────────────────────────────────────────────────────
$agentSites  = $allAgents  | ForEach-Object { $_.siteName }  | Sort-Object -Unique | Where-Object { $_ }
$threatSites = $allThreats | ForEach-Object { $_.agentRealtimeInfo.siteName } | Sort-Object -Unique | Where-Object { $_ }
$allCustomers = ($agentSites + $threatSites) | Sort-Object -Unique

$summaryRows = @()

foreach ($site in $allCustomers) {
    $st = $allThreats | Where-Object { $_.agentRealtimeInfo.siteName -eq $site }
    $ag = $allAgents  | Where-Object { $_.siteName -eq $site }
    if ($ag.Count -eq 0 -and $st.Count -eq 0) { continue }

    # ── Threat metrics ──────────────────────────────────────────────────────────
    $total        = $st.Count
    $mitigated    = ($st | Where-Object { $_.threatInfo.mitigationStatus -eq "mitigated" }).Count
    $notMitigated = ($st | Where-Object { $_.threatInfo.mitigationStatus -eq "not_mitigated" }).Count
    $benign       = ($st | Where-Object { $_.threatInfo.mitigationStatus -eq "marked_as_benign" }).Count
    $malicious    = ($st | Where-Object { $_.threatInfo.confidenceLevel  -eq "malicious" }).Count
    $suspicious   = ($st | Where-Object { $_.threatInfo.confidenceLevel  -eq "suspicious" }).Count
    $mitigRate    = if ($total -gt 0) { [math]::Round($mitigated / $total * 100, 1) } else { 100 }

    # ── Classification breakdown ────────────────────────────────────────────────
    $classBreak  = $st | Group-Object { $_.threatInfo.classification } | Sort-Object Count -Descending
    $ransomware  = ($st | Where-Object { $_.threatInfo.classification -eq "Ransomware" }).Count
    $infostealer = ($st | Where-Object { $_.threatInfo.classification -eq "Infostealer" }).Count
    $backdoor    = ($st | Where-Object { $_.threatInfo.classification -eq "Backdoor" }).Count
    $pua         = ($st | Where-Object { $_.threatInfo.classification -eq "PUA" }).Count

    # ── Detection engines ───────────────────────────────────────────────────────
    $behavioral  = ($st | Where-Object { $_.threatInfo.detectionType -eq "dynamic" }).Count
    $static      = ($st | Where-Object { $_.threatInfo.detectionType -eq "static"  }).Count

    # ── MITRE tactics ───────────────────────────────────────────────────────────
    $tactics = $st | ForEach-Object { $_.indicators } | ForEach-Object { $_ } |
               ForEach-Object { $_.tactics } | ForEach-Object { $_ } |
               ForEach-Object { $_.name } | Sort-Object -Unique

    $lateralMov = ($st | Where-Object {
        $_.threatInfo.threatName -like "*Lateral*" -or
        ($_.indicators | ForEach-Object { $_.tactics } | ForEach-Object { $_ } |
         Where-Object { $_.name -eq "Lateral Movement" })
    }).Count

    # ── LOLBins / Living-off-the-Land ───────────────────────────────────────────
    $lolDetections = $st | Where-Object {
        $n = $_.threatInfo.threatName.ToLower()
        $lolBinNames | Where-Object { $n -like "*$_*" }
    }
    $lolCount = $lolDetections.Count
    $lolTop   = $lolDetections | Group-Object { $_.threatInfo.threatName } | Sort-Object Count -Descending | Select-Object -First 5

    # ── Top threats, devices, users ─────────────────────────────────────────────
    $topThreats  = $st | Group-Object { $_.threatInfo.threatName } | Sort-Object Count -Descending | Select-Object -First 8
    $topDevices  = $st | Group-Object { $_.agentRealtimeInfo.agentComputerName } | Sort-Object Count -Descending | Select-Object -First 8
    $topUsers    = $st | Where-Object { $_.threatInfo.processUser -and $_.threatInfo.processUser -notmatch "^NT AUTHORITY" } |
                   Group-Object { $_.threatInfo.processUser } | Sort-Object Count -Descending | Select-Object -First 5
    $unresolved  = $st | Where-Object { $_.threatInfo.mitigationStatus -eq "not_mitigated" }

    # ── MTTR ────────────────────────────────────────────────────────────────────
    $rtimes = $st | Where-Object { $_.threatInfo.mitigationStatus -eq "mitigated" -and $_.threatInfo.createdAt -and $_.threatInfo.updatedAt } | ForEach-Object {
        $diff = ([datetime]$_.threatInfo.updatedAt - [datetime]$_.threatInfo.createdAt).TotalMinutes
        if ($diff -ge 0 -and $diff -lt 10080) { $diff }
    }
    $avgMTTR = if ($rtimes.Count -gt 0) { [math]::Round(($rtimes | Measure-Object -Average).Average, 1) } else { "N/A" }

    # ── Daily trend ─────────────────────────────────────────────────────────────
    $dailyTrend = $st | Group-Object { [datetime]$_.threatInfo.createdAt | Get-Date -Format "yyyy-MM-dd" } |
                  Sort-Object Name | ForEach-Object { [PSCustomObject]@{ Date = $_.Name; Count = $_.Count } }

    # ── Endpoint health ─────────────────────────────────────────────────────────
    $totalAg       = $ag.Count
    $infectedAg    = ($ag | Where-Object { $_.infected -eq $true }).Count
    $outdatedAg    = ($ag | Where-Object { $_.isUpToDate -eq $false }).Count
    $staleAg       = ($ag | Where-Object { $_.lastActiveDate -and [datetime]$_.lastActiveDate -lt (Get-Date).AddDays(-7) }).Count
    $pendingUnin   = ($ag | Where-Object { $_.isPendingUninstall -eq $true }).Count
    $detectOnly    = ($ag | Where-Object { $_.mitigationMode -eq "detect" }).Count
    $protectMode   = ($ag | Where-Object { $_.mitigationMode -eq "protect" }).Count
    $healthIssues  = ($ag | Where-Object { $_.userActionsNeeded -and $_.userActionsNeeded.Count -gt 0 }).Count
    $scanAborted   = ($ag | Where-Object { $_.scanStatus -eq "aborted" }).Count
    $osSpread      = $ag | Group-Object { $_.osName } | Sort-Object Count -Descending
    $versionSpread = $ag | Group-Object { $_.agentVersion } | Sort-Object Count -Descending

    # ── Risk score ──────────────────────────────────────────────────────────────
    $riskScore  = 0
    $riskScore += $notMitigated   * 10
    $riskScore += $infectedAg     * 20
    $riskScore += $ransomware     * 15
    $riskScore += $infostealer    * 20
    $riskScore += $backdoor       * 25
    $riskScore += $lateralMov     * 30
    $riskScore += $detectOnly     * 3
    $riskScore += $healthIssues   * 5
    $riskScore += $outdatedAg     * 2
    $riskScore += $staleAg        * 3
    $riskLevel  = if ($riskScore -ge 50) { "CRITICAL" } elseif ($riskScore -ge 20) { "HIGH" } elseif ($riskScore -ge 5) { "MEDIUM" } else { "LOW" }

    $summaryRows += [PSCustomObject]@{
        Customer     = $site
        Risk         = $riskLevel
        Threats      = $total
        MitigRate    = "$mitigRate%"
        Unresolved   = $notMitigated
        Ransomware   = $ransomware
        Infostealer  = $infostealer
        LateralMov   = $lateralMov
        Devices      = $totalAg
        Infected     = $infectedAg
        DetectOnly   = $detectOnly
        HealthIssues = $healthIssues
        Outdated     = $outdatedAg
        Stale7d      = $staleAg
        MTTR_min     = $avgMTTR
    }

    # ══════════════════════════════════════════════════════════════════════════
    # BUILD REPORT
    # ══════════════════════════════════════════════════════════════════════════
    $L   = @()
    $sep = "=" * 72
    $div = "-" * 72

    $L += $sep
    $L += "  IP DIMENSION - MONTHLY SECURITY REPORT"
    $L += "  Customer  : $site"
    $L += "  Period    : $monthLabel"
    $L += "  Generated : $(Get-Date -Format 'dd MMM yyyy HH:mm')"
    $L += "  Risk Level: $riskLevel"
    $L += $sep
    $L += ""

    # 1. Executive Summary
    $L += "1. EXECUTIVE SUMMARY"
    $L += $div
    if ($total -eq 0 -and $detectOnly -eq 0 -and $healthIssues -eq 0 -and $outdatedAg -eq 0) {
        $L += "   No threats detected. All endpoints healthy. Security posture: GOOD."
    } else {
        if ($total -gt 0) {
            $L += "   $total threat(s) detected this month. $mitigated auto-mitigated ($mitigRate% rate)."
        }
        if ($notMitigated  -gt 0) { $L += "   !! $notMitigated threat(s) UNRESOLVED - immediate action required." }
        if ($ransomware    -gt 0) { $L += "   !! $ransomware Ransomware detection(s) detected." }
        if ($infostealer   -gt 0) { $L += "   !! $infostealer Infostealer detection(s) - credential/data theft risk." }
        if ($backdoor      -gt 0) { $L += "   !! $backdoor Backdoor detection(s) - possible persistent access." }
        if ($lateralMov    -gt 0) { $L += "   !! $lateralMov Lateral Movement - attacker may be moving within network." }
        if ($infectedAg    -gt 0) { $L += "   !! $infectedAg device(s) currently marked as infected." }
        if ($detectOnly    -gt 0) { $L += "   !! $detectOnly device(s) in DETECT-ONLY mode - threats will NOT be blocked automatically." }
        if ($healthIssues  -gt 0) { $L += "   !  $healthIssues device(s) have agent health issues (modules not running)." }
        if ($outdatedAg    -gt 0) { $L += "   !  $outdatedAg device(s) running outdated SentinelOne agent." }
    }
    $L += ""

    # 2. Threat Statistics
    $L += "2. THREAT STATISTICS"
    $L += $div
    $L += "   Total Detected            : $total"
    $L += "   Auto-Mitigated            : $mitigated  ($mitigRate%)"
    $L += "   Unresolved                : $notMitigated"
    $L += "   Marked as Benign          : $benign"
    $L += "   Mean Time to Respond      : $avgMTTR minutes (auto-mitigation avg)"
    $L += ""
    $L += "   By Confidence Level:"
    $L += "     Malicious               : $malicious"
    $L += "     Suspicious              : $suspicious"
    $L += ""
    $L += "   By Detection Engine:"
    $L += "     Behavioral AI (dynamic) : $behavioral"
    $L += "     Static Analysis         : $static"
    $L += ""
    if ($classBreak.Count -gt 0) {
        $L += "   By Classification:"
        foreach ($c in $classBreak) { $L += ("     {0,-28}: {1}" -f $c.Name, $c.Count) }
        $L += ""
    }

    # 3. High-Risk Threat Categories
    if ($ransomware -gt 0 -or $infostealer -gt 0 -or $backdoor -gt 0 -or $lateralMov -gt 0 -or $lolCount -gt 0) {
        $L += "3. HIGH-RISK THREAT CATEGORIES"
        $L += $div
        if ($ransomware    -gt 0) { $L += "   [!!] RANSOMWARE       : $ransomware  - verify backups, review affected devices" }
        if ($infostealer   -gt 0) { $L += "   [!!] INFOSTEALER      : $infostealer  - change passwords, check for data exfiltration" }
        if ($backdoor      -gt 0) { $L += "   [!!] BACKDOOR         : $backdoor  - verify all remote access tools are authorized" }
        if ($lateralMov    -gt 0) { $L += "   [!!] LATERAL MOVEMENT : $lateralMov  - review network segmentation" }
        if ($lolCount      -gt 0) {
            $L += "   [!]  LOLBins/Built-in Tools: $lolCount detection(s) using OS-native tools"
            foreach ($l in $lolTop) { $L += ("         {0,4}x  {1}" -f $l.Count, $l.Name.ToString()) }
        }
        if ($pua -gt 0) { $L += "        PUA/Adware        : $pua  - confirm user awareness" }
        $L += ""
    } else {
        $L += "3. HIGH-RISK THREAT CATEGORIES"
        $L += $div
        $L += "   No high-risk threat categories detected this period."
        $L += ""
    }

    # 4. MITRE ATT&CK
    $L += "4. MITRE ATT&CK TACTICS OBSERVED"
    $L += $div
    if ($tactics.Count -gt 0) {
        foreach ($t in $tactics) { $L += "   - $t" }
    } else {
        $L += "   None detected."
    }
    $L += ""

    # 5. Top Threats & Devices
    $L += "5. TOP THREATS BY FREQUENCY"
    $L += $div
    if ($topThreats.Count -gt 0) {
        foreach ($t in $topThreats) { $L += ("   {0,5}x  {1}" -f $t.Count, $t.Name) }
    } else { $L += "   No threats this period." }
    $L += ""

    $L += "6. MOST AFFECTED DEVICES"
    $L += $div
    if ($topDevices.Count -gt 0) {
        foreach ($d in $topDevices) { $L += ("   {0,5}x  {1}" -f $d.Count, $d.Name) }
    } else { $L += "   No affected devices." }
    $L += ""

    # 6. User Accounts Involved
    if ($topUsers.Count -gt 0) {
        $L += "7. USER ACCOUNTS INVOLVED IN THREATS"
        $L += $div
        foreach ($u in $topUsers) { $L += ("   {0,5}x  {1}" -f $u.Count, $u.Name) }
        $L += ""
    }

    # 7. Daily Threat Trend
    if ($dailyTrend.Count -gt 0) {
        $L += "8. DAILY THREAT TREND - $monthLabel"
        $L += $div
        $maxDay = ($dailyTrend | Measure-Object -Property Count -Maximum).Maximum
        foreach ($d in $dailyTrend) {
            $barLen = if ($maxDay -gt 0) { [math]::Min([math]::Floor($d.Count / [math]::Max($maxDay/40,1)), 40) } else { 0 }
            $bar = "#" * $barLen
            $L += ("   {0}  {1,4}  {2}" -f $d.Date, $d.Count, $bar)
        }
        $L += ""
    }

    # 8. Endpoint Health
    $L += "9. ENDPOINT HEALTH & COVERAGE"
    $L += $div
    $L += "   Total Managed Devices     : $totalAg"
    $coveragePct = if ($totalAg -gt 0) { "100%" } else { "0%" }
    $L += "   Agent Coverage            : $coveragePct"
    $L += "   Protect Mode (auto-block) : $protectMode"
    $L += ("   Detect-Only Mode          : {0}{1}" -f $detectOnly, $(if($detectOnly -gt 0){" !! Threats will NOT be blocked automatically"}else{""}))
    $L += "   Infected Devices          : $infectedAg"
    $L += "   Outdated Agent Version    : $outdatedAg  (Latest: $latestPkg)"
    $L += "   Agent Health Issues       : $healthIssues"
    $L += "   Not Seen in 7+ Days       : $staleAg"
    $L += "   Pending Uninstall         : $pendingUnin"
    $L += "   Scan Aborted              : $scanAborted"
    $L += ""

    if ($ag.Count -gt 0) {
        $L += "   OS Distribution:"
        foreach ($o in $osSpread) { $L += ("     {0,-42}: {1}" -f $o.Name, $o.Count) }
        $L += ""
        $L += "   Agent Version Distribution:"
        foreach ($v in $versionSpread) { $L += ("     {0,-22}: {1}{2}" -f $v.Name, $v.Count, $(if($v.Name -ne $latestPkg -and $v.Name -lt $latestPkg){"  [UPDATE AVAILABLE]"}else{""})) }
        $L += ""
    }

    if ($detectOnly -gt 0) {
        $L += "   DETECT-ONLY DEVICES (no automatic blocking):"
        $ag | Where-Object { $_.mitigationMode -eq "detect" } | ForEach-Object {
            $L += "     [DETECT] $($_.computerName) [$($_.osName)]"
        }
        $L += ""
    }

    if ($healthIssues -gt 0) {
        $L += "   AGENT HEALTH ISSUES:"
        $ag | Where-Object { $_.userActionsNeeded -and $_.userActionsNeeded.Count -gt 0 } | ForEach-Object {
            $issues = $_.userActionsNeeded -join ", "
            $L += "     [HEALTH] $($_.computerName) - $issues"
        }
        $L += ""
    }

    if ($outdatedAg -gt 0) {
        $L += "   OUTDATED AGENT DEVICES:"
        $ag | Where-Object { $_.isUpToDate -eq $false } | ForEach-Object {
            $L += "     [UPDATE] $($_.computerName) - v$($_.agentVersion) on $($_.osName)"
        }
        $L += ""
    }

    if ($infectedAg -gt 0) {
        $L += "   INFECTED DEVICES:"
        $ag | Where-Object { $_.infected -eq $true } | ForEach-Object {
            $L += "     [INFECTED] $($_.computerName) [$($_.osName)] last seen $($_.lastActiveDate)"
        }
        $L += ""
    }

    if ($staleAg -gt 0) {
        $L += "   DEVICES NOT REPORTING (>7 days):"
        $ag | Where-Object { $_.lastActiveDate -and [datetime]$_.lastActiveDate -lt (Get-Date).AddDays(-7) } |
            Sort-Object { [datetime]$_.lastActiveDate } | ForEach-Object {
            $L += "     [STALE] $($_.computerName) - last seen $($_.lastActiveDate)"
        }
        $L += ""
    }

    # 9. Unresolved Threats
    if ($unresolved.Count -gt 0) {
        $L += "10. UNRESOLVED THREATS - IMMEDIATE ACTION REQUIRED"
        $L += $div
        $unresolved | Sort-Object { $_.threatInfo.createdAt } -Descending | ForEach-Object {
            $date  = ($_.threatInfo.createdAt -split 'T')[0]
            $name  = $_.threatInfo.threatName
            $dev   = $_.agentRealtimeInfo.agentComputerName
            $conf  = $_.threatInfo.confidenceLevel
            $class = $_.threatInfo.classification
            $user  = $_.threatInfo.processUser
            $L += "   [$date] [$conf] $name"
            $L += "            Device: $dev  |  Class: $class  |  User: $user"
        }
        $L += ""
    }

    # 10. Recommendations
    $L += "11. RECOMMENDATIONS & ACTION ITEMS"
    $L += $div
    $hasIssue = $false
    if ($notMitigated   -gt 0) { $L += "   [URGENT] Investigate and resolve $notMitigated unresolved threat(s)."; $hasIssue = $true }
    if ($infectedAg     -gt 0) { $L += "   [URGENT] Isolate and fully remediate $infectedAg infected device(s)."; $hasIssue = $true }
    if ($infostealer    -gt 0) { $L += "   [URGENT] $infostealer Infostealer(s) found - change all account passwords, audit data access logs."; $hasIssue = $true }
    if ($ransomware     -gt 0) { $L += "   [HIGH]   $ransomware Ransomware detection(s) - verify backups are complete and tested."; $hasIssue = $true }
    if ($lateralMov     -gt 0) { $L += "   [HIGH]   $lateralMov Lateral Movement event(s) - segment network, audit admin accounts."; $hasIssue = $true }
    if ($backdoor       -gt 0) { $L += "   [HIGH]   $backdoor Backdoor(s) detected - audit all remote access and admin tools."; $hasIssue = $true }
    if ($detectOnly     -gt 0) { $L += "   [HIGH]   $detectOnly device(s) in detect-only mode - switch to protect mode to enable automatic blocking."; $hasIssue = $true }
    if ($healthIssues   -gt 0) { $L += "   [HIGH]   $healthIssues device(s) have agent health issues (Network Extension/Content Filter not active)."; $hasIssue = $true }
    if ($outdatedAg     -gt 0) { $L += "   [MEDIUM] Update SentinelOne agent on $outdatedAg device(s) to latest version $latestPkg."; $hasIssue = $true }
    if ($staleAg        -gt 0) { $L += "   [MEDIUM] $staleAg device(s) not reporting in 7+ days - verify powered on and connected."; $hasIssue = $true }
    if ($scanAborted    -gt 0) { $L += "   [LOW]    $scanAborted device(s) with aborted scan - re-run scan."; $hasIssue = $true }
    if ($pua            -gt 0) { $L += "   [LOW]    $pua PUA/Adware detection(s) - review with user, consider policy update."; $hasIssue = $true }
    if (-not $hasIssue)        { $L += "   [OK]     No issues detected. Security posture is healthy." }
    $L += ""

    $L += $sep
    $L += "   Report generated by IP Dimension Managed Security Services"
    $L += "   Questions: security@ipdimension.co.za"
    $L += $sep

    $safeName = $site -replace '[\\/:*?"<>|]', '_'
    $L | Out-File -FilePath "$ReportDir\$safeName`_$Month.txt" -Encoding UTF8
    Write-Host "  [OK] $($site.PadRight(35)) Risk: $($riskLevel.PadRight(8)) Threats: $total"
}

# ── Master summary ─────────────────────────────────────────────────────────────
$momLine  = "  $prev2Label : $prev2Count  |  $prevLabel : $prevCount  |  $monthLabel : $($allThreats.Count)"
$totalDevices = $allAgents.Count
$detectTotal  = ($allAgents | Where-Object { $_.mitigationMode -eq "detect" }).Count
$healthTotal  = ($allAgents | Where-Object { $_.userActionsNeeded -and $_.userActionsNeeded.Count -gt 0 }).Count

Write-Host ""
Write-Host "MASTER SUMMARY - $monthLabel" -ForegroundColor Cyan
Write-Host $momLine
Write-Host ("=" * 120)
$summaryRows | Sort-Object @{E={ switch ($_.Risk) { "CRITICAL"{0} "HIGH"{1} "MEDIUM"{2} "LOW"{3} default{4} }}}, Threats -Descending | Format-Table -AutoSize

$masterLines = @()
$masterLines += "IP DIMENSION - MASTER SECURITY SUMMARY"
$masterLines += "Period    : $monthLabel  |  Generated: $(Get-Date -Format 'dd MMM yyyy HH:mm')"
$masterLines += "Portfolio : $($allCustomers.Count) customers  |  $totalDevices total devices  |  $detectTotal in detect-only mode  |  $healthTotal with health issues"
$masterLines += "Trend     : $momLine"
$masterLines += "=" * 120
$masterLines += ($summaryRows | Sort-Object @{E={ switch ($_.Risk) { "CRITICAL"{0} "HIGH"{1} "MEDIUM"{2} "LOW"{3} default{4} }}}, Threats -Descending | Format-Table -AutoSize | Out-String)
$masterLines | Out-File -FilePath "$ReportDir\_MASTER_SUMMARY_$Month.txt" -Encoding UTF8

Write-Host ""
Write-Host "Reports saved to: $ReportDir" -ForegroundColor Green
Write-Host "Run for other months: .\Generate-MonthlyReports.ps1 -Month '2026-05'" -ForegroundColor Gray
