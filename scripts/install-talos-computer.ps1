#requires -Version 5.1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$TalosOperation = 'install'
$TalosPurge = $false
$TalosInstallerEmbedded = $false
# TALOS_WINDOWS_BOOTSTRAP
if (-not $TalosInstallerEmbedded) { throw 'Use the generated /install/talos-computer.ps1 entrypoint; this file is a release template.' }
