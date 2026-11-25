# Shared source, embedded into both published entrypoints by the release builder.
$TalosInstallerEmbedded = $true
$TalosStartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$TalosRelease = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__TALOS_RELEASE_METADATA_BASE64__')) | ConvertFrom-Json
$TalosHelperBase64 = '__TALOS_INSTALLER_HELPER_BASE64__'
$TalosUtf8 = New-Object Text.UTF8Encoding($false)
# Managed tool runtime modules ship beside the CLI. Releases before they
# existed verify without them, so presence decides the checksum entry list.
$TalosManagedToolEntries = @(
    'cli/managed-tools-runtime.mjs', 'cli/managed-tools-install.mjs', 'cli/managed-tools-launchers.mjs',
    'cli/managed-tools-archive.mjs', 'cli/managed-tools-lock.mjs', 'cli/local-computer-node-distributions.mjs')
$TalosSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
if ((New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run Local Computer installation or uninstallation in an ordinary PowerShell window, without Run as administrator.'
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or [Environment]::OSVersion.Version.Major -lt 10) {
    throw 'Local Computer requires native Windows 10 or Windows 11.'
}
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Local Computer requires a 64-bit Windows system.' }
# Bind the bootstrap to the current shell's built-in cmdlets when a parent
# running another PowerShell edition supplies its module path.
foreach ($module in @('Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Security', 'Microsoft.PowerShell.Utility')) {
    Import-Module (Join-Path $PSHOME ("Modules\$module\$module.psd1")) -Scope Local -ErrorAction Stop
}
$TalosArchitecture = $env:PROCESSOR_ARCHITEW6432
if (-not $TalosArchitecture) { $TalosArchitecture = $env:PROCESSOR_ARCHITECTURE }
switch ($TalosArchitecture.ToUpperInvariant()) {
    'ARM64' { $TalosTarget = 'win32-arm64' }
    'AMD64' { $TalosTarget = 'win32-x64' }
    default { throw "Unsupported native Windows architecture: $TalosArchitecture" }
}

if (-not ('TalosInstallerFileIdentity' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class TalosInstallerFileIdentity {
    [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes {
        public int Length;
        public IntPtr Descriptor;
        public int InheritHandle;
    }
    [StructLayout(LayoutKind.Sequential)] public struct Info {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime, AccessTime, WriteTime;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern bool CreateDirectoryW(string path, ref SecurityAttributes attributes);
    public static void CreatePrivateDirectory(string path, byte[] descriptor) {
        IntPtr memory = Marshal.AllocHGlobal(descriptor.Length);
        try {
            Marshal.Copy(descriptor, 0, memory, descriptor.Length);
            SecurityAttributes attributes = new SecurityAttributes();
            attributes.Length = Marshal.SizeOf(typeof(SecurityAttributes));
            attributes.Descriptor = memory;
            if (!CreateDirectoryW(path, ref attributes))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        } finally { Marshal.FreeHGlobal(memory); }
    }
    public static void CheckSingleLink(SafeFileHandle handle) {
        Info value;
        if (!GetFileInformationByHandle(handle, out value) || value.Links != 1 || (value.Attributes & 1024) != 0)
            throw new InvalidOperationException("Installer file identity is unsafe.");
    }
    public static void CheckDacl(byte[] descriptor) {
        var security = new System.Security.AccessControl.RawSecurityDescriptor(descriptor, 0);
        if (security.DiscretionaryAcl == null)
            throw new InvalidOperationException("Installer path has an unrestricted DACL.");
    }
}
'@
}

# Bundled tool dependencies nest deeper than the 260-character path limit that
# Win32 applies to ordinary paths. Address every installer path as an extended
# path so the same code reaches short and deep files alike.
function Get-TalosFullPath([string]$Path) {
    if ($Path.StartsWith('\\?\')) { return $Path }
    $full = [IO.Path]::GetFullPath($Path)
    # Extended paths spell a share as \\?\UNC\server. Installer paths are local,
    # so leave the remote form alone and let the caller's check reject it.
    if ($full.StartsWith('\\')) { return $full }
    return '\\?\' + $full
}

# Numbered steps in plain ASCII; Windows PowerShell 5.1 consoles render no
# Unicode symbols. A console redraws the current line with a spinner and the
# elapsed time; redirected output gets a heartbeat line at most every 10s. The
# Node helper continues the open step on the same line.
$TalosInteractive = -not [Console]::IsOutputRedirected
$TalosStepTotal = 4
# Functions write the step through $script:, so the top level must use the
# same variable even when the installer runs inside a script block.
$script:TalosStep = $null

function Format-TalosDuration([long]$Milliseconds) {
    $seconds = [long][Math]::Max(0, [Math]::Round($Milliseconds / 1000))
    if ($seconds -lt 60) { return "${seconds}s" }
    return '{0}m {1}s' -f [Math]::Floor($seconds / 60), ($seconds % 60)
}

function Write-TalosStepLine([string]$Suffix, [bool]$Final) {
    $line = '[{0}/{1}] {2}... {3}' -f $TalosStep.Number, $TalosStepTotal, $TalosStep.Label, $Suffix
    [Console]::Write("`r" + $line + (' ' * [Math]::Max(0, $TalosStep.Drawn - $line.Length)))
    if ($Final) { [Console]::WriteLine(); $TalosStep.Drawn = 0 } else { $TalosStep.Drawn = $line.Length }
}

# Long loops call this often; it redraws at most four times a second.
function Update-TalosStep([string]$Detail = '', [switch]$Force) {
    if (-not $TalosStep) { return }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $interval = 10000
    if ($TalosInteractive) { $interval = 250 }
    if (-not $Force -and $now - $TalosStep.LastDraw -lt $interval) { return }
    $TalosStep.LastDraw = $now
    $elapsed = Format-TalosDuration ($now - $TalosStep.StartedAt)
    if ($TalosInteractive) {
        $frame = '|/-\'[$TalosStep.Frame % 4]
        $TalosStep.Frame += 1
        if ($Detail) { $Detail += ' ' }
        Write-TalosStepLine ($Detail + $frame + ' ' + $elapsed) $false
    } elseif (-not $Force) {
        if (-not $Detail) { $Detail = 'still working' }
        [Console]::WriteLine(('      {0} ({1})' -f $Detail, $elapsed))
    }
}

function Stop-TalosStep([string]$Result = 'OK') {
    if (-not $TalosStep) { return }
    $elapsed = Format-TalosDuration ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $TalosStep.StartedAt)
    if ($TalosInteractive) { Write-TalosStepLine ($Result + ' (' + $elapsed + ')') $true }
    else { [Console]::WriteLine(('[{0}/{1}] {2}... {3} ({4})' -f $TalosStep.Number, $TalosStepTotal, $TalosStep.Label, $Result, $elapsed)) }
    $script:TalosStep = $null
}

function Start-TalosStep([int]$Number, [string]$Label) {
    Stop-TalosStep
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $script:TalosStep = @{ Number = $Number; Label = $Label; StartedAt = $now; LastDraw = $now; Drawn = 0; Frame = 0 }
    if (-not $TalosInteractive) { [Console]::WriteLine(('[{0}/{1}] {2}...' -f $Number, $TalosStepTotal, $Label)) }
    Update-TalosStep -Force
}

function Write-TalosNote([string]$Text) {
    if ($TalosInteractive -and $TalosStep) {
        [Console]::Write("`r" + (' ' * $TalosStep.Drawn) + "`r")
        $TalosStep.Drawn = 0
    }
    [Console]::WriteLine($Text)
    if ($TalosInteractive) { Update-TalosStep -Force }
}

# Directories whose whole ancestor chain passed in this run. A bundle has dozens
# of files under the same ancestors; walking each chain again per file took
# seconds. Only a fully successful walk is remembered.
$TalosTrustedDirectories = @{}

# The Codex Windows sandbox grants its sandbox users group and the capability
# SIDs recorded in its default home write access to its writable roots, which
# can include AppData. Talos accepts that a sandboxed Codex command may replace
# such an ancestor; product paths themselves stay private to the user.
function Get-TalosCodexSandboxSids {
    $sids = @()
    try { $sids += (New-Object Security.Principal.NTAccount('CodexSandboxUsers')).Translate([Security.Principal.SecurityIdentifier]).Value } catch {}
    $capFile = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex\cap_sid'
    try { $caps = [IO.File]::ReadAllText($capFile) | ConvertFrom-Json } catch { return $sids }
    $values = @([string]$caps.workspace)
    foreach ($roots in @($caps.workspace_by_cwd, $caps.writable_root_by_path)) {
        if ($roots) { $values += @($roots.PSObject.Properties | ForEach-Object { [string]$_.Value }) }
    }
    # Keep only the S-1-5-21 form with four 32-bit values that Codex generates,
    # so a corrupted file cannot make a well-known SID such as Everyone trusted.
    # A last value below 1000 is a well-known domain RID such as Domain Users.
    foreach ($value in $values) {
        $parts = $value -split '-'
        if ($parts.Count -ne 8 -or ($parts[0..3] -join '-') -cne 'S-1-5-21') { continue }
        $valid = $true
        foreach ($part in $parts[4..7]) { $number = [uint32]0; if (-not [uint32]::TryParse($part, [Globalization.NumberStyles]::None, [Globalization.CultureInfo]::InvariantCulture, [ref]$number)) { $valid = $false } }
        if ($valid -and $number -ge 1000) { $sids += $value }
    }
    return $sids
}
$TalosCodexSandboxSids = @(Get-TalosCodexSandboxSids)

function Assert-TalosAncestors([string]$Path) {
    $cursor = Get-TalosFullPath $Path
    $walked = @()
    while ($cursor) {
        if ($TalosTrustedDirectories.ContainsKey($cursor)) { break }
        $ancestor = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
        if ($ancestor.PSIsContainer) { $walked += $cursor }
        if (($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Install path contains a reparse point: $cursor" }
        $ancestorAcl = Get-Acl -LiteralPath $cursor
        [TalosInstallerFileIdentity]::CheckDacl($ancestorAcl.GetSecurityDescriptorBinaryForm())
        $trustedOwners = @($TalosSid.Value, 'S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
        if ($ancestorAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trustedOwners) { throw "Install ancestor has an untrusted owner: $cursor" }
        foreach ($access in $ancestorAcl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
            # Creating a sibling under C:\ is harmless. Replacing a protected
            # child or its DACL is not. Ignore inherit-only permissions.
            $inheritOnly = ($access.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0
            $replaceRights = 0x00010000 -bor 0x00040000 -bor 0x00080000 -bor 0x40 -bor 0x10000000
            # DELETE on the volume root cannot replace that root. Its children
            # remain protected by the FILE_DELETE_CHILD and ACL-change checks.
            if ($cursor -eq [IO.Path]::GetPathRoot($cursor)) { $replaceRights = $replaceRights -band (-bnot 0x00010000) }
            if (-not $inheritOnly -and $access.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
                $access.IdentityReference.Value -notin $trustedOwners -and $access.IdentityReference.Value -notin $TalosCodexSandboxSids -and
                ([int]$access.FileSystemRights -band $replaceRights) -ne 0) {
                throw "Install ancestor can be replaced by another user ($($access.IdentityReference.Value)): $cursor"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    foreach ($directory in $walked) { $TalosTrustedDirectories[$directory] = $true }
}

function Assert-TalosPath([string]$Path, [bool]$Directory) {
    $full = Get-TalosFullPath $Path
    $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if ($item.PSIsContainer -ne $Directory) { throw "Install path has the wrong kind: $full" }
    Assert-TalosAncestors $full
    $acl = Get-Acl -LiteralPath $full
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $TalosSid.Value) { throw "Install path has a different owner: $full" }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $rule.IdentityReference.Value -notin @($TalosSid.Value, 'S-1-5-18', 'S-1-5-32-544')) {
            throw "Install path is not private to the current user: $full"
        }
    }
    if (-not $Directory) {
        $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try { [TalosInstallerFileIdentity]::CheckSingleLink($stream.SafeFileHandle) }
        finally { $stream.Dispose() }
    }
}

function New-TalosDirectory([string]$Path) {
    $full = Get-TalosFullPath $Path
    if (Test-Path -LiteralPath $full) { Assert-TalosPath $full $true; return }
    $parent = [IO.Path]::GetDirectoryName($full)
    if (-not (Test-Path -LiteralPath $parent)) { New-TalosDirectory $parent }
    Assert-TalosAncestors $parent
    # New product directories inherit no access from a potentially shared parent.
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner($TalosSid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($TalosSid, (New-Object Security.Principal.SecurityIdentifier('S-1-5-18')))) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    # Apply the private DACL at creation, before another user can open the path.
    [TalosInstallerFileIdentity]::CreatePrivateDirectory($full, $acl.GetSecurityDescriptorBinaryForm())
    Assert-TalosPath $full $true
}

function Get-TalosChecksum([string]$Path) {
    Assert-TalosPath $Path $false
    return (Get-FileHash -LiteralPath (Get-TalosFullPath $Path) -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-TalosPrivateText([string]$Path) {
    Assert-TalosPath $Path $false
    return [IO.File]::ReadAllText((Get-TalosFullPath $Path), $TalosUtf8)
}

function Assert-TalosArchitecture([string]$Path) {
    Assert-TalosPath $Path $false
    $stream = [IO.File]::OpenRead((Get-TalosFullPath $Path))
    $reader = New-Object IO.BinaryReader($stream)
    try {
        if ($stream.Length -lt 64 -or $reader.ReadUInt16() -ne 0x5A4D) { throw 'Release executable has no valid PE header.' }
        $stream.Position = 60
        $offset = $reader.ReadUInt32()
        if ($offset -lt 64 -or $offset + 6 -gt $stream.Length) { throw 'Release executable has no valid PE header.' }
        $stream.Position = $offset
        if ($reader.ReadUInt32() -ne 0x00004550) { throw 'Release executable has no valid PE header.' }
        $machine = $reader.ReadUInt16()
        $expected = 0x8664
        if ($TalosTarget -eq 'win32-arm64') { $expected = 0xAA64 }
        if ($machine -ne $expected) { throw 'Release executable architecture does not match this Windows system.' }
    } finally { $reader.Dispose(); $stream.Dispose() }
}

function Assert-TalosSource([string]$Source) {
    if ([IO.Path]::IsPathRooted($Source) -and $Source -notmatch '^[A-Za-z]+://') { return }
    $uri = [Uri]$Source
    if (-not $uri.IsAbsoluteUri -or $uri.UserInfo -or $uri.Fragment) { throw 'Download URL is invalid.' }
    if ($uri.IsFile) {
        if ($uri.IsUnc) { throw 'Remote file URLs are not supported.' }
        return
    }
    if ($uri.Scheme -ne 'https' -and $uri.Scheme -ne 'http') {
        throw 'Download URL must use HTTPS or HTTP.'
    }
}

function Receive-TalosFile([string]$Source, [string]$Destination) {
    Assert-TalosSource $Source
    if ([IO.Path]::IsPathRooted($Source) -and $Source -notmatch '^[A-Za-z]+://') {
        [IO.File]::Copy($Source, $Destination, $false)
        return
    }
    $uri = [Uri]$Source
    if ($uri.IsFile) { [IO.File]::Copy($uri.LocalPath, $Destination, $false); return }
    # Handle redirects ourselves so HTTPS cannot silently downgrade to HTTP.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    for ($redirects = 0; $redirects -le 5; $redirects++) {
        Assert-TalosSource $uri.AbsoluteUri
        $request = [Net.HttpWebRequest]::Create($uri)
        $request.AllowAutoRedirect = $false
        $request.Timeout = 120000
        $response = $request.GetResponse()
        try {
            if ([int]$response.StatusCode -in @(301, 302, 303, 307, 308)) {
                if (-not $response.Headers['Location']) { throw 'Download redirect has no destination.' }
                $uri = New-Object Uri($uri, $response.Headers['Location'])
                continue
            }
            if ([int]$response.StatusCode -ne 200) { throw "Download failed: $($response.StatusCode)" }
            $output = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $inputStream = $response.GetResponseStream()
            $length = $response.ContentLength
            $invariant = [Globalization.CultureInfo]::InvariantCulture
            try {
                $buffer = New-Object byte[] 65536
                $total = 0L
                $redraw = [Diagnostics.Stopwatch]::StartNew()
                while (($count = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $total += $count
                    if ($total -gt 268435456) { throw 'Release download exceeds the size limit.' }
                    $output.Write($buffer, 0, $count)
                    if ($redraw.ElapsedMilliseconds -ge 250) {
                        $redraw.Restart()
                        if ($length -gt 0) {
                            $filled = [int][Math]::Min(20, [Math]::Floor($total * 20 / $length))
                            Update-TalosStep ([string]::Format($invariant, '[{0}{1}] {2}% {3:F1}/{4:F1} MB',
                                ('#' * $filled), ('-' * (20 - $filled)), [int][Math]::Floor($total * 100 / $length), ($total / 1MB), ($length / 1MB)))
                        } else { Update-TalosStep ([string]::Format($invariant, '{0:F1} MB', ($total / 1MB))) }
                    }
                }
            } finally { $output.Dispose(); $inputStream.Dispose() }
            return
        } finally { $response.Dispose() }
    }
    throw 'Download redirect limit exceeded.'
}

function Read-TalosExact($Stream, [byte[]]$Buffer, [int]$Count) {
    $offset = 0
    while ($offset -lt $Count) {
        $read = $Stream.Read($Buffer, $offset, $Count - $offset)
        if ($read -eq 0) { throw 'Release archive is truncated.' }
        $offset += $read
    }
}

# Writes only the runtime this script and the helper start. The helper extracts
# and verifies the whole package again, so writing thousands of tool files here
# only doubled the work. Every header is still validated.
# The dependency and application archives unpack into one bundle; each call
# writes only the named files the bootstrap needs from its archive.
function Expand-TalosBootstrap([string]$Archive, [string]$Destination, [string[]]$Required) {
    New-TalosDirectory $Destination
    $required = $Required
    $found = @{}
    $sourceStream = [IO.File]::OpenRead($Archive)
    $gzip = New-Object IO.Compression.GZipStream($sourceStream, [IO.Compression.CompressionMode]::Decompress)
    try {
        $header = New-Object byte[] 512
        $buffer = New-Object byte[] 65536
        $total = 0L
        $redraw = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            Read-TalosExact $gzip $header 512
            $sum = 0
            foreach ($byte in $header) { $sum += $byte }
            if ($sum -eq 0) { break }
            $name = [Text.Encoding]::UTF8.GetString($header, 0, 100).Split([char]0)[0]
            $prefix = [Text.Encoding]::ASCII.GetString($header, 345, 155).Split([char]0)[0]
            if ($prefix) { $name = $prefix + '/' + $name }
            $sizeText = [Text.Encoding]::ASCII.GetString($header, 124, 12).Trim([char]0, [char]32)
            $checksumText = [Text.Encoding]::ASCII.GetString($header, 148, 8).Trim([char]0, [char]32)
            $headerChecksum = 0
            for ($index = 0; $index -lt 512; $index++) {
                if ($index -ge 148 -and $index -lt 156) { $headerChecksum += 32 }
                else { $headerChecksum += $header[$index] }
            }
            $cliAsset = $name -cmatch '^cli/(talos-computer\.exe|teamhub|teamhub\.cmd|teamhub\.exe|teamhub\.js|teamhub-assets\.json|teamhub-commands\.json|teamhub-chunks/[A-Za-z0-9_-]+\.js)$'
            $toolAsset = $name.StartsWith('cli/computer-tools/') -and (Test-TalosToolPath $name)
            if (($name -notin $required -and $name -notin $TalosManagedToolEntries -and -not $cliAsset -and -not $toolAsset) -or $found.ContainsKey($name) -or ($prefix -and -not $toolAsset) -or
                $header[156] -notin @(0, 48) -or $sizeText -notmatch '^[0-7]+$' -or
                $checksumText -notmatch '^[0-7]+$' -or [Convert]::ToInt64($checksumText, 8) -ne $headerChecksum) {
                throw 'Release archive contains an invalid or unexpected entry.'
            }
            $size = [Convert]::ToInt64($sizeText, 8)
            $total += $size
            if ($size -lt 0 -or ($size -eq 0 -and -not $toolAsset) -or $total -gt 536870912) { throw 'Release archive size is invalid.' }
            $output = $null
            if ($name -in $required) {
                $file = Join-Path $Destination $name
                New-TalosDirectory ([IO.Path]::GetDirectoryName($file))
                $output = [IO.File]::Open((Get-TalosFullPath $file), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            }
            try {
                $remaining = $size
                while ($remaining -gt 0) {
                    $count = [int][Math]::Min($remaining, $buffer.Length)
                    Read-TalosExact $gzip $buffer $count
                    if ($output) { $output.Write($buffer, 0, $count) }
                    $remaining -= $count
                }
            } finally { if ($output) { $output.Dispose() } }
            $padding = [int]((512 - ($size % 512)) % 512)
            if ($padding -gt 0) { Read-TalosExact $gzip $buffer $padding }
            if ($output) { Assert-TalosPath $file $false }
            $found[$name] = $true
            if ($redraw.ElapsedMilliseconds -ge 250) { $redraw.Restart(); Update-TalosStep }
        }
        foreach ($name in $required) { if (-not $found.ContainsKey($name)) { throw "Release archive is missing $name" } }
        while (($count = $gzip.Read($buffer, 0, $buffer.Length)) -gt 0) {
            for ($index = 0; $index -lt $count; $index++) { if ($buffer[$index] -ne 0) { throw 'Release archive has trailing content.' } }
        }
    } finally { $gzip.Dispose(); $sourceStream.Dispose() }
}

function Test-TalosToolPath([string]$Name) {
    if (-not $Name -or $Name -match '[\\:\x00-\x1f]') { return $false }
    foreach ($part in $Name.Split('/')) { if (-not $part -or $part -eq '.' -or $part -eq '..') { return $false } }
    return $true
}

function Get-TalosComputerToolEntries([string]$Bundle) {
    $root = Join-Path $Bundle 'cli/computer-tools'
    if (-not (Test-Path -LiteralPath (Get-TalosFullPath $root))) { return @() }
    Assert-TalosPath $root $true
    $manifest = (Read-TalosPrivateText (Join-Path $root 'manifest.json')) | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or -not $manifest.assets -or
        $manifest.playwright_entry -cne 'node_modules/@playwright/mcp/cli.js' -or $manifest.pi_extension -cne 'extensions/pi-mcp.mjs' -or
        $manifest.cua_entry -cne 'cua/cua-driver.exe' -or $manifest.artifact_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'Invalid Computer tool manifest.' }
    $seen = @{}
    $names = @('manifest.json')
    foreach ($asset in $manifest.assets) {
        if ($asset.path -isnot [string] -or -not (Test-TalosToolPath $asset.path) -or $seen.ContainsKey($asset.path)) { throw 'Invalid Computer tool asset path.' }
        $seen[$asset.path] = $true
        $names += $asset.path
    }
    if (-not $seen.ContainsKey($manifest.playwright_entry) -or -not $seen.ContainsKey($manifest.pi_extension) -or -not $seen.ContainsKey($manifest.cua_entry)) { throw 'Computer tool entrypoint is missing.' }
    # Match JavaScript ordinal ordering in the installed bundle checksum.
    [Array]::Sort($names, [StringComparer]::Ordinal)
    return @($names | ForEach-Object { 'cli/computer-tools/' + $_ })
}

function Get-TalosBundleEntries([string]$Bundle) {
    $entries = @('cli/talos-computer.exe', 'daemon/talos-computer-daemon.exe', 'runtime/bin/node.exe', 'runtime/LICENSE')
    $tools = @(Get-TalosComputerToolEntries $Bundle)
    $manifestFile = Join-Path $Bundle 'cli/teamhub-assets.json'
    if (-not (Test-Path -LiteralPath (Get-TalosFullPath $manifestFile))) { return @($entries) + @($tools) }
    $entries += @($TalosManagedToolEntries | Where-Object { Test-Path -LiteralPath (Get-TalosFullPath (Join-Path $Bundle $_)) })
    $manifest = (Read-TalosPrivateText $manifestFile) | ConvertFrom-Json
    if ($manifest.schema_version -ne 1 -or $manifest.entrypoint -cne 'teamhub.js' -or
        -not $manifest.assets -or $manifest.artifact_sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Release CLI asset manifest is invalid.'
    }
    $nativeEntry = 'cli/teamhub.exe'
    if (-not (Test-Path -LiteralPath (Get-TalosFullPath (Join-Path $Bundle $nativeEntry)))) { $nativeEntry = 'cli/teamhub.cmd' }
    $entries += @('cli/teamhub', $nativeEntry, 'cli/teamhub-assets.json')
    # Bundles before Computer 0.30.11 lack the command manifest.
    if (Test-Path -LiteralPath (Get-TalosFullPath (Join-Path $Bundle 'cli/teamhub-commands.json'))) { $entries += 'cli/teamhub-commands.json' }
    $seen = @{}
    $hasEntry = $false
    $hasChunk = $false
    foreach ($asset in $manifest.assets) {
        if ($asset.path -isnot [string] -or $asset.path -cnotmatch '^(teamhub\.js|teamhub-chunks/[A-Za-z0-9_-]+\.js)$' -or
            $seen.ContainsKey($asset.path)) { throw 'Release CLI asset manifest is invalid.' }
        $seen[$asset.path] = $true
        if ($asset.path -ceq 'teamhub.js') { $hasEntry = $true } else { $hasChunk = $true }
        $entries += 'cli/' + $asset.path
    }
    if (-not $hasEntry -or -not $hasChunk) { throw 'Release CLI asset manifest is incomplete.' }
    return @($entries) + @($tools)
}

function Get-TalosInstalledBundle([string]$Root, [string]$RecoveryBundle = '', [string]$RecoveryChecksum = '') {
    $bundle = $RecoveryBundle
    if (-not $bundle) {
        $pointer = Join-Path $Root 'active-bundle'
        if (-not (Test-Path -LiteralPath $pointer)) { return $null }
        $name = (Read-TalosPrivateText $pointer).Trim()
        if ($name -notmatch '^lib-[0-9a-f]{16}-[0-9a-f-]+$') { throw 'Installed bundle pointer is invalid.' }
        $bundle = Join-Path $Root $name
    }
    Assert-TalosPath $bundle $true
    $sha = [Security.Cryptography.SHA256]::Create()
    $buffer = New-Object byte[] 65536
    $redraw = [Diagnostics.Stopwatch]::StartNew()
    try {
        foreach ($entry in (Get-TalosBundleEntries $bundle)) {
            if ($redraw.ElapsedMilliseconds -ge 250) { $redraw.Restart(); Update-TalosStep }
            $file = Join-Path $bundle $entry
            if (-not $entry.StartsWith('cli/computer-tools/')) { Assert-TalosPath $file $false }
            $prefix = $TalosUtf8.GetBytes($entry + [char]0)
            $null = $sha.TransformBlock($prefix, 0, $prefix.Length, $prefix, 0)
            $stream = [IO.File]::OpenRead((Get-TalosFullPath $file))
            try {
                while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) { $null = $sha.TransformBlock($buffer, 0, $count, $buffer, 0) }
            } finally { $stream.Dispose() }
        }
        $null = $sha.TransformFinalBlock((New-Object byte[] 0), 0, 0)
        $actual = ([BitConverter]::ToString($sha.Hash)).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
    $expected = $RecoveryChecksum
    if (-not $expected) { $expected = (Read-TalosPrivateText (Join-Path $Root 'installed-bundle-checksum')).Trim() }
    if ($expected -notmatch '^[0-9a-f]{64}$' -or $actual -cne $expected) { throw 'Installed bundle integrity verification failed.' }
    return $bundle
}

# The name a Web origin installs under, derived as install-talos-computer.sh
# does: the host's first label without "-talos", "local" for loopback,
# or the dashed address for an IP, plus any non-default port.
function Get-TalosEnvironmentName([string]$Origin) {
    $uri = [Uri]$Origin
    $hostName = $uri.Host.ToLowerInvariant()
    if ($hostName -in @('localhost', '127.0.0.1', '[::1]') -or $hostName.EndsWith('.localhost')) { $name = 'local' }
    elseif ($hostName.StartsWith('[')) { $name = 'ip-' + ($hostName.Trim('[', ']') -replace '[:.]', '-') }
    elseif ($hostName -match '^[0-9.]+$') { $name = 'ip-' + ($hostName -replace '\.', '-') }
    else {
        $name = $hostName.Split('.')[0]
        if ($name -ne 'talos' -and $name.EndsWith('-talos')) { $name = $name.Substring(0, $name.Length - '-talos'.Length) }
    }
    if (-not $uri.IsDefaultPort) { $name = $name + '-' + $uri.Port }
    return $name
}

$TalosDownloadBase = $env:TALOS_COMPUTER_DOWNLOAD_BASE_URL
if (-not $TalosDownloadBase) { $TalosDownloadBase = $TalosRelease.downloadBaseUrl }
$TalosWebOrigin = $env:TALOS_WEB_URL
if (-not $TalosWebOrigin) { $TalosWebOrigin = $TalosRelease.webOrigin }
# Each Web origin is its own environment with its own install root and its own
# command, talos-computer-<environment>, so Computers of several deployments
# can share one Windows account. talos-computer runs the environment when it
# is the only one installed and lists them otherwise.
$TalosCommandName = 'talos-computer-' + (Get-TalosEnvironmentName $TalosWebOrigin)
if (-not $env:LOCALAPPDATA -and (-not $env:TALOS_COMPUTER_DATA_DIR -or -not $env:TALOS_COMPUTER_BIN_DIR)) { throw 'LOCALAPPDATA is required.' }
$TalosBinDirectory = $env:TALOS_COMPUTER_BIN_DIR
if (-not $TalosBinDirectory) { $TalosBinDirectory = Join-Path $env:LOCALAPPDATA 'Talos\bin' }
if (-not [IO.Path]::IsPathRooted($TalosBinDirectory) -or $TalosBinDirectory.StartsWith('\\')) { throw 'Install bin directory must be an absolute local path.' }
$TalosBinDirectory = [IO.Path]::GetFullPath($TalosBinDirectory)
$TalosDataRoot = $env:TALOS_COMPUTER_DATA_DIR
# Reinstalling an environment upgrades it where its launcher points.
$TalosLauncherConfig = Join-Path $TalosBinDirectory ($TalosCommandName + '.launcher.json')
if (-not $TalosDataRoot -and (Test-Path -LiteralPath $TalosLauncherConfig -PathType Leaf)) {
    $TalosDataRoot = ([IO.File]::ReadAllText($TalosLauncherConfig) | ConvertFrom-Json).dataRoot
}
if (-not $TalosDataRoot) { $TalosDataRoot = Join-Path $env:LOCALAPPDATA ('Talos\Computers\' + (Get-TalosEnvironmentName $TalosWebOrigin)) }
if (-not [IO.Path]::IsPathRooted($TalosDataRoot) -or $TalosDataRoot.StartsWith('\\')) { throw 'Install root must be an absolute local path.' }
$TalosDataRoot = [IO.Path]::GetFullPath($TalosDataRoot)
if ($TalosDataRoot -eq [IO.Path]::GetPathRoot($TalosDataRoot) -or $TalosDataRoot -eq $env:USERPROFILE) { throw 'Install destination is unsafe.' }
$TalosTargetRelease = $TalosRelease.targets.$TalosTarget
if (-not $TalosTargetRelease) { throw "This deployment ships no Local Computer for $TalosTarget." }
foreach ($kind in @('deps', 'app')) {
    $archive = $TalosTargetRelease.$kind
    if (-not $archive -or $archive.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $archive.file -cne ($kind + '-' + $TalosTarget + '-' + $archive.sha256 + '.tar.gz')) {
        throw 'Installer release metadata is invalid.'
    }
}
New-TalosDirectory $TalosDataRoot
New-TalosDirectory $TalosBinDirectory
$TalosLockPath = Join-Path $TalosDataRoot '.install.lock'
if (Test-Path -LiteralPath $TalosLockPath) { Assert-TalosPath $TalosLockPath $false }
try { $TalosLock = [IO.File]::Open($TalosLockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
catch { throw 'Another Local Computer installation or uninstallation is active.' }
$TalosTemporary = Join-Path $TalosDataRoot ('.bootstrap-' + [Guid]::NewGuid().ToString())
$TalosProcess = $null
if ($TalosOperation -eq 'uninstall') { $TalosStepTotal = 3 }
try {
    Start-TalosStep 1 'Checking the installed version'
    New-TalosDirectory $TalosTemporary
    $TalosBootstrapRoot = $null
    $TalosBootstrapFailure = $null
    try { $TalosBootstrapRoot = Get-TalosInstalledBundle $TalosDataRoot }
    catch { $TalosBootstrapFailure = $_ }
    $journalFile = Join-Path $TalosDataRoot '.uninstall-transaction.json'
    if (-not $TalosBootstrapRoot -and (Test-Path -LiteralPath $journalFile)) {
        $journal = (Read-TalosPrivateText $journalFile) | ConvertFrom-Json
        $record = $journal.bundles | Select-Object -First 1
        if ($journal.schemaVersion -ne 1 -or $journal.snapshot -notmatch '^\.uninstall-[0-9a-f-]+$' -or
            -not $record -or $record.name -notmatch '^lib-[0-9a-f]{16}-[0-9a-f-]+$' -or $record.checksum -notmatch '^[0-9a-f]{64}$') { throw 'Uninstall recovery metadata is invalid.' }
        $recoveryBundle = Join-Path (Join-Path $TalosDataRoot $journal.snapshot) $record.name
        $TalosBootstrapRoot = Get-TalosInstalledBundle $TalosDataRoot $recoveryBundle $record.checksum
    }
    if (-not $TalosBootstrapRoot -and $TalosBootstrapFailure) {
        if ($TalosOperation -eq 'uninstall') { throw $TalosBootstrapFailure }
        Write-TalosNote 'Installed program failed integrity verification; downloading a verified package.'
    }
    $TalosBootstrapArchives = $null
    if ($TalosBootstrapRoot) {
        # The helper must not execute a Node/daemon that uninstall will delete.
        # It runs only these two programs from this copy.
        $verified = $TalosBootstrapRoot
        $TalosBootstrapRoot = Join-Path $TalosTemporary 'bundle'
        New-TalosDirectory $TalosBootstrapRoot
        foreach ($entry in @('runtime/bin/node.exe', 'daemon/talos-computer-daemon.exe')) {
            $original = Join-Path $verified $entry
            $copy = Join-Path $TalosBootstrapRoot $entry
            New-TalosDirectory ([IO.Path]::GetDirectoryName($copy))
            [IO.File]::Copy((Get-TalosFullPath $original), (Get-TalosFullPath $copy), $false)
            if ((Get-TalosChecksum $copy) -cne (Get-TalosChecksum $original)) { throw 'Bootstrap copy integrity verification failed.' }
        }
    }
    if (-not $TalosBootstrapRoot) {
        if ($TalosOperation -eq 'uninstall') {
            if ((Test-Path -LiteralPath (Join-Path $TalosBinDirectory ($TalosCommandName + '.exe'))) -or
                (Get-ChildItem -LiteralPath $TalosDataRoot -Force | Where-Object { $_.Name -match '^lib-' })) { throw 'Installed program is missing; files were preserved.' }
            if (-not $TalosPurge) { Stop-TalosStep 'not installed'; Write-TalosNote 'Local Computer is not installed.'; return }
        }
        # Uninstall downloads only the runtime it needs to stop owned processes.
        if ($TalosOperation -eq 'install') {
            if ($TalosBootstrapFailure) { Stop-TalosStep 'needs repair' } else { Stop-TalosStep 'not installed' }
            Start-TalosStep 2 'Downloading the package'
        }
        $TalosBootstrapArchives = @{}
        foreach ($kind in @('deps', 'app')) {
            $archive = Join-Path $TalosTemporary $TalosTargetRelease.$kind.file
            Receive-TalosFile ($TalosDownloadBase.TrimEnd('/') + '/files/' + $TalosTargetRelease.$kind.file) $archive
            $TalosBootstrapArchives[$kind] = $archive
        }
        if ($TalosOperation -eq 'install') { Start-TalosStep 3 'Verifying the package' }
        foreach ($kind in @('deps', 'app')) {
            if ((Get-TalosChecksum $TalosBootstrapArchives[$kind]) -cne $TalosTargetRelease.$kind.sha256) { throw 'Installer runtime bootstrap checksum verification failed.' }
        }
        $TalosBootstrapRoot = Join-Path $TalosTemporary 'bundle'
        Expand-TalosBootstrap $TalosBootstrapArchives['deps'] $TalosBootstrapRoot @('runtime/bin/node.exe', 'runtime/LICENSE')
        Expand-TalosBootstrap $TalosBootstrapArchives['app'] $TalosBootstrapRoot @('daemon/talos-computer-daemon.exe')
    }
    $TalosNode = Join-Path $TalosBootstrapRoot 'runtime\bin\node.exe'
    Assert-TalosArchitecture $TalosNode
    Assert-TalosArchitecture (Join-Path $TalosBootstrapRoot 'daemon\talos-computer-daemon.exe')
    $TalosNodeVersion = & $TalosNode --version
    if ($LASTEXITCODE -ne 0 -or $TalosNodeVersion -cne 'v26.5.0') { throw 'Bundled Node version is invalid.' }
    $helperFile = Join-Path $TalosTemporary 'installer.mjs'
    [IO.File]::WriteAllBytes($helperFile, [Convert]::FromBase64String($TalosHelperBase64))
    Assert-TalosPath $helperFile $false
    $settings = @{
        operation = $TalosOperation; purge = $TalosPurge; target = $TalosTarget
        dataRoot = $TalosDataRoot; binDirectory = $TalosBinDirectory; commandName = $TalosCommandName; configFile = $env:TALOS_COMPUTER_CONFIG
        downloadBaseUrl = $TalosDownloadBase; webOrigin = $TalosWebOrigin; release = $TalosTargetRelease
        bootstrapRoot = $TalosBootstrapRoot; bootstrapArchives = $TalosBootstrapArchives
        progress = @{ startedAt = $TalosStartedAt; step = $script:TalosStep.Number; label = $script:TalosStep.Label; stepStartedAt = $script:TalosStep.StartedAt }
    }
    # The helper continues the open step and reports its own result.
    $script:TalosStep = $null
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $TalosNode
    $startInfo.Arguments = '"' + $helperFile + '"'
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardInput = $true
    $TalosProcess = [Diagnostics.Process]::Start($startInfo)
    $inputBytes = $TalosUtf8.GetBytes(($settings | ConvertTo-Json -Depth 10 -Compress))
    $TalosProcess.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)
    $TalosProcess.StandardInput.Close()
    $TalosProcess.WaitForExit()
    if ($TalosProcess.ExitCode -ne 0) { throw 'Local Computer installation action failed. See the diagnostic above.' }
    if ($TalosOperation -eq 'install') {
        $TalosLauncher = Join-Path $TalosBinDirectory ($TalosCommandName + '.exe')
        $TalosCommand = $TalosCommandName
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (($userPath -split ';') -notcontains $TalosBinDirectory) {
            [Environment]::SetEnvironmentVariable('Path', ($TalosBinDirectory + ';' + $userPath).TrimEnd(';'), 'User')
        }
        if (($env:Path -split ';') -notcontains $TalosBinDirectory) { $env:Path = $TalosBinDirectory + ';' + $env:Path }
        # Running the launcher surfaces an application control block before the user meets it.
        try { & $TalosLauncher version | Out-Null; $launchFailed = $LASTEXITCODE -ne 0 }
        catch { Write-Host $_.Exception.Message; $launchFailed = $true }
        if ($launchFailed) {
            Write-Warning "$TalosCommandName.exe could not start. If Windows reported an application control policy, Smart App Control or an App Control policy blocked this unsigned build."
        }
        $configFile = $env:TALOS_COMPUTER_CONFIG
        if (-not $configFile) { $configFile = Join-Path $TalosDataRoot 'config.json' }
        # The next step follows the real state: log in, start, or inspect.
        $nextCommand = 'login'
        if (-not $launchFailed -and (Test-Path -LiteralPath $configFile)) {
            $nextCommand = 'daemon status'
            try {
                # Windows PowerShell 5.1 turns redirected native stderr into errors under Stop.
                $output = & { $ErrorActionPreference = 'Continue'; & $TalosLauncher daemon status --json 2>$null }
                $report = ($output | Out-String) | ConvertFrom-Json
                if ($report.config.configured -eq $false) { $nextCommand = 'login' }
                elseif ($report.daemon.state -and $report.daemon.state -ne 'running') { $nextCommand = 'daemon start' }
            } catch { }
        }
        Write-Host ''
        Write-Host 'Next:'
        Write-Host "  $TalosCommand $nextCommand"
        Write-Host ''
        Write-Host 'Help:'
        Write-Host "  $TalosCommand --help"
        $environments = @(Get-ChildItem -LiteralPath $TalosBinDirectory -Filter 'talos-computer-*.launcher.json' | ForEach-Object {
                $command = $_.Name.Substring(0, $_.Name.Length - '.launcher.json'.Length)
                $origin = ''
                try { $origin = ([IO.File]::ReadAllText((Join-Path ([IO.File]::ReadAllText($_.FullName) | ConvertFrom-Json).dataRoot 'web-origin'))).Trim() } catch { }
                [pscustomobject]@{ Command = $command; Origin = $origin }
            })
        $dispatcherManaged = Test-Path -LiteralPath (Join-Path $TalosBinDirectory 'talos-computer.exe')
        Write-Host ''
        if ($environments.Count -gt 1) {
            Write-Host "This computer has Talos Computer for $($environments.Count) environments, each with its own command:"
            foreach ($environment in $environments) {
                $note = ''
                if ($environment.Command -eq $TalosCommandName) { $note = '  (this installation)' }
                Write-Host ('  {0,-34} {1}{2}' -f $environment.Command, $environment.Origin, $note)
            }
            if ($dispatcherManaged) { Write-Host 'talos-computer alone lists them instead of choosing one.' }
        } elseif ($dispatcherManaged) {
            Write-Host 'While this is the only environment on this computer, talos-computer runs it too.'
        }
    }
} catch {
    Stop-TalosStep 'FAILED'
    throw
} finally {
    # Do not release the installer lock while its child is still mutating files.
    if ($TalosProcess) {
        if (-not $TalosProcess.HasExited) { $TalosProcess.WaitForExit() }
        $TalosProcess.Dispose()
    }
    if (Test-Path -LiteralPath (Get-TalosFullPath $TalosTemporary)) { Remove-Item -LiteralPath (Get-TalosFullPath $TalosTemporary) -Recurse -Force }
    $TalosLock.Dispose()
}
