#requires -Version 5.1
[CmdletBinding()]
param([switch]$Purge, [Parameter(ValueFromRemainingArguments = $true)][string[]]$RemainingArguments)
$ErrorActionPreference = 'Stop'
foreach ($argument in $RemainingArguments) {
    if ($argument -eq '--purge') { $Purge = $true }
    else { throw 'Usage: uninstall-talos-computer.ps1 [-Purge | --purge]' }
}
$TalosOperation = 'uninstall'
$TalosPurge = [bool]$Purge
$TalosInstallerEmbedded = $false
# TALOS_WINDOWS_BOOTSTRAP
if (-not $TalosInstallerEmbedded) { throw 'Use the generated /install/uninstall-talos-computer.ps1 entrypoint; this file is a release template.' }
