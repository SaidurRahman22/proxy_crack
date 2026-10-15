<#
    Pre-Flight — tasks that require Administrator privileges
    --------------------------------------------------------
    Everything else in the setup was completed without elevation.
    These four are optional; nothing here blocks Flutter/Android development.

    HOW TO RUN
      1. Start Menu -> type "PowerShell" -> right-click -> "Run as administrator"
      2. cd D:\project\Proxy\Pre-Flight
      3. Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
      4. .\ELEVATED-TASKS.ps1

    Each task is independent. Comment out any you don't want.
#>

Write-Host "`n=== Pre-Flight elevated tasks ===" -ForegroundColor Cyan
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "NOT running as Administrator. Re-launch elevated." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# TASK 1 — Windows Defender exclusions   [RECOMMENDED - biggest build speedup]
# ---------------------------------------------------------------------------
# Real-time scanning of build output and caches can triple Gradle build times.
Write-Host "`n[1] Adding Defender exclusions..." -ForegroundColor Yellow
$exclusions = @(
    'D:\dev',                           # jdk-17, android-sdk, flutter
    'D:\project',                       # your code
    "$env:USERPROFILE\.gradle",         # Gradle cache
    "$env:USERPROFILE\.android",        # AVDs
    "$env:USERPROFILE\.pub-cache"       # Dart packages
)
foreach ($p in $exclusions) {
    try { Add-MpPreference -ExclusionPath $p -ErrorAction Stop; Write-Host "    added: $p" -ForegroundColor Green }
    catch { Write-Host "    skipped: $p  ($($_.Exception.Message))" -ForegroundColor DarkGray }
}

# ---------------------------------------------------------------------------
# TASK 2 — Android Studio   [ONLY IF YOU WILL WRITE KOTLIN / NATIVE ANDROID]
# ---------------------------------------------------------------------------
# Not needed for Flutter. VS Code is a poor Kotlin editor - see 03-kotlin-android.md.
# Point its SDK at the EXISTING sdk during first-run setup: D:\dev\android-sdk
# (do NOT let it download a second copy).
Write-Host "`n[2] Android Studio..." -ForegroundColor Yellow
Write-Host "    Uncomment the next line if you want it." -ForegroundColor DarkGray
# winget install --id Google.AndroidStudio --accept-package-agreements --accept-source-agreements

# ---------------------------------------------------------------------------
# TASK 3 — Make JDK 17 the default `java` on PATH   [OPTIONAL / COSMETIC]
# ---------------------------------------------------------------------------
# Oracle Java 23 sits in MACHINE PATH (C:\Program Files\Common Files\Oracle\Java\javapath)
# which Windows resolves BEFORE user PATH, so `java -version` reports 23.
# This does NOT affect Flutter or Gradle - both read JAVA_HOME, already set to JDK 17.
# Only run this if you want the terminal `java` command to be 17 as well.
# NOTE: other installed software may expect Java 23. Backup is written first.
Write-Host "`n[3] JDK 17 PATH precedence (optional)..." -ForegroundColor Yellow
Write-Host "    Uncomment the block below to apply." -ForegroundColor DarkGray
<#
$regPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
$machinePath = (Get-Item $regPath).GetValue('Path','',[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$backup = "$PSScriptRoot\machine-path-backup-$(Get-Date -Format yyyyMMdd-HHmmss).txt"
$machinePath | Out-File -Encoding utf8 $backup
Write-Host "    backup written: $backup" -ForegroundColor Green
if ($machinePath -notlike '*D:\dev\jdk-17\bin*') {
    $newPath = 'D:\dev\jdk-17\bin;' + $machinePath
    Set-ItemProperty -Path $regPath -Name Path -Value $newPath -Type ExpandString
    Write-Host "    JDK 17 prepended to machine PATH. Restart terminals." -ForegroundColor Green
} else {
    Write-Host "    already present." -ForegroundColor DarkGray
}
#>

# ---------------------------------------------------------------------------
# TASK 4 — PowerShell 7   [OPTIONAL quality of life]
# ---------------------------------------------------------------------------
# Windows PowerShell 5.1 lacks &&, ||, ternary and null-coalescing operators.
Write-Host "`n[4] PowerShell 7..." -ForegroundColor Yellow
Write-Host "    Uncomment the next line if you want it." -ForegroundColor DarkGray
# winget install --id Microsoft.PowerShell --accept-package-agreements --accept-source-agreements

Write-Host "`nDone. Restart any open terminals and VS Code to pick up changes.`n" -ForegroundColor Cyan
