# Native Windows entry. No Python, registry writes or profile activation required.
$ErrorActionPreference = 'Stop'
$arguments = @($args)
function Blocked([string]$Message) { throw "bootstrap blocked: $Message" }
function NoAlias([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Blocked "path alias forbidden: $Path" }
    }
}
# SHA256 is built into .NET; setup must work without PowerShell module discovery.
function FileHash([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}
# Win32 argv quoting works with PowerShell 5.1 ProcessStartInfo (no ArgumentList).
function QuoteArg([string]$Value) {
    if ($Value -notmatch '[\s"]' -and $Value.Length -gt 0) { return $Value }
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
function OwnedProcess([string]$File, [string[]]$Values, [int]$Seconds = 240, [bool]$Capture = $true) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = (($Values | ForEach-Object { QuoteArg $_ }) -join ' ')
    $info.WorkingDirectory = $root
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    # A windowless native child does not reliably inherit a redirected parent
    # console. Always own its pipes; forward bytes explicitly for common CLI.
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $info.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    foreach ($key in @($info.EnvironmentVariables.Keys)) {
        if ($key -match '^(UV_|PYTHON|PIP_)' -or $key -in @('VIRTUAL_ENV','CONDA_PREFIX','CONDA_DEFAULT_ENV','__PYVENV_LAUNCHER__')) { $info.EnvironmentVariables.Remove($key) }
    }
    $info.EnvironmentVariables['UV_PYTHON_INSTALL_DIR'] = "$runtime\python"
    $info.EnvironmentVariables['UV_CACHE_DIR'] = "$runtime\cache"
    $info.EnvironmentVariables['UV_PYTHON_BIN_DIR'] = "$runtime\bin"
    $info.EnvironmentVariables['UV_NO_CONFIG'] = '1'
    $info.EnvironmentVariables['UV_NO_ENV_FILE'] = '1'
    $info.EnvironmentVariables['UV_HTTP_TIMEOUT'] = '60'
    $info.EnvironmentVariables['UV_HTTP_RETRIES'] = '0'
    $info.EnvironmentVariables['PYTHONUTF8'] = '1'
    $info.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $deadline = $Seconds * 1000
    try {
        [void]$process.Start()
        if ($Capture) {
            $outTask = $process.StandardOutput.ReadToEndAsync()
            $errTask = $process.StandardError.ReadToEndAsync()
        } else {
            # Copy both streams concurrently before waiting to avoid full-pipe
            # deadlocks and preserve JSON/UTF-8 bytes without PowerShell encoding.
            $stdout = [Console]::OpenStandardOutput()
            $stderr = [Console]::OpenStandardError()
            $outTask = $process.StandardOutput.BaseStream.CopyToAsync($stdout)
            $errTask = $process.StandardError.BaseStream.CopyToAsync($stderr)
        }
        $remaining = [Math]::Max(1, $deadline - [int]$watch.ElapsedMilliseconds)
        if (-not $process.WaitForExit($remaining)) {
            $process.Kill()
            if (-not $process.WaitForExit(2000)) { Blocked 'runtime child did not exit after termination' }
            Blocked 'runtime command timed out'
        }
        $code = $process.ExitCode
        foreach ($task in @($outTask, $errTask)) {
            $remaining = [Math]::Max(1, $deadline - [int]$watch.ElapsedMilliseconds)
            if (-not $task.Wait($remaining)) { Blocked 'runtime output forwarding timed out' }
        }
        if ($Capture) {
            $output = $outTask.Result; $errorText = $errTask.Result
            if ($code -ne 0) { Blocked "runtime command exit ${code}: $errorText" }
            if ($errorText) {
                $bytes = [Text.Encoding]::UTF8.GetBytes($errorText)
                $errorStream = [Console]::OpenStandardError()
                $errorStream.Write($bytes, 0, $bytes.Length); $errorStream.Flush()
            }
            return $output.Trim()
        }
        $stdout.Flush(); $stderr.Flush()
        return $code
    } finally {
        if ($process.StartInfo.RedirectStandardOutput) {
            try { $process.StandardOutput.Dispose(); $process.StandardError.Dispose() } catch { }
        }
        $process.Dispose()
    }
}
try {
    if ($env:OS -ne 'Windows_NT') { Blocked 'this entry supports native Windows; POSIX uses scripts/run.sh' }
    NoAlias $PSCommandPath
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $runtime = Join-Path $root '.local\runtime'
    $lock = @{}
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $root 'scripts\bootstrap.lock'))) {
        if (-not $line -or $line.StartsWith('#')) { continue }
        $fields = $line.Split("`t"); $lock[$fields[0]] = $fields[1..($fields.Length - 1)]
    }
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $arch = switch ($arch.ToUpperInvariant()) { AMD64 {'x86_64'} ARM64 {'aarch64'} default { Blocked 'unsupported architecture' } }
    $row = $lock["windows-$arch"]
    if (-not $row) { Blocked 'unsupported Windows architecture' }
    $archive = $row[0]; $expected = $row[1]; $entry = $row[2]
    $setup = $arguments.Count -eq 1 -and $arguments[0] -eq 'setup'
    foreach ($path in @($root, "$root\.local", $runtime, "$runtime\owner", "$runtime\uv.exe", "$runtime\$archive", "$runtime\python", "$runtime\cache", "$runtime\bin", "$root\.venv")) { NoAlias $path }
    if (Test-Path -LiteralPath $runtime) {
        if (-not (Test-Path -LiteralPath "$runtime\owner") -or [IO.File]::ReadAllText("$runtime\owner").Trim() -cne $root) { Blocked 'foreign or relocated runtime; preserve it and use a fresh checkout' }
    } elseif ($setup) {
        if (Test-Path -LiteralPath "$root\.venv") { Blocked 'foreign .venv; preserve it and use a fresh checkout' }
        [void][IO.Directory]::CreateDirectory($runtime)
        [IO.File]::WriteAllText("$runtime\owner", $root + "`n", (New-Object Text.UTF8Encoding($false)))
    } else { Blocked 'environment not ready; run scripts/run.ps1 setup' }
    $temp = Join-Path $runtime ('.fetch-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($temp)
    $archivePath = Join-Path $runtime $archive
    if (-not (Test-Path -LiteralPath $archivePath)) {
        if (-not $setup) { Blocked 'pinned uv archive missing; run explicit setup' }
        $uri = "https://github.com/astral-sh/uv/releases/download/$($lock['UV_VERSION'][0])/$archive"
        $request = [Net.HttpWebRequest]::Create($uri)
        $request.Timeout = 120000; $request.ReadWriteTimeout = 60000; $request.MaximumAutomaticRedirections = 5
        $response = $request.GetResponse()
        try {
            if ($response.ResponseUri.Scheme -ne 'https') { Blocked 'insecure download redirect' }
            $inputStream = $response.GetResponseStream(); $outputStream = [IO.File]::Create("$temp\archive")
            try {
                $buffer = New-Object byte[] 65536; $total = 0; $watch = [Diagnostics.Stopwatch]::StartNew()
                while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $total += $read
                    if ($total -gt [int]$lock['MAX_ARCHIVE_BYTES'][0] -or $watch.Elapsed.TotalSeconds -gt 120) { Blocked 'download size/time limit exceeded' }
                    $outputStream.Write($buffer, 0, $read)
                }
            } finally { $outputStream.Dispose(); $inputStream.Dispose() }
        } finally { $response.Dispose() }
        if ((FileHash "$temp\archive") -ne $expected) { Blocked 'uv archive SHA256 mismatch; downloaded bytes were not executed' }
        [IO.File]::Move("$temp\archive", $archivePath)
    }
    if ((Get-Item -LiteralPath $archivePath).Length -gt [int]$lock['MAX_ARCHIVE_BYTES'][0] -or (FileHash $archivePath) -ne $expected) { Blocked 'cached uv archive size/SHA256 mismatch; preserve it and inspect before retry' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entries = @($zip.Entries | Where-Object { $_.FullName -ceq $entry })
        if ($entries.Count -ne 1 -or $entries[0].Length -gt [int]$lock['MAX_EXECUTABLE_BYTES'][0]) { Blocked 'uv archive entry absent, duplicated or too large' }
        $source = $entries[0].Open(); $destination = [IO.File]::Create("$temp\uv.exe")
        try { $source.CopyTo($destination) } finally { $destination.Dispose(); $source.Dispose() }
    } finally { $zip.Dispose() }
    if (Test-Path -LiteralPath "$runtime\uv.exe") {
        if ((FileHash "$runtime\uv.exe") -ne (FileHash "$temp\uv.exe")) { Blocked 'uv executable differs from pinned archive' }
    } elseif ($setup) { [IO.File]::Move("$temp\uv.exe", "$runtime\uv.exe") }
    else { Blocked 'uv executable missing; run explicit setup' }
    if ($setup) {
        if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) { Blocked 'Git 2.43+ required; agent should use native OS installer within existing rights: https://git-scm.com/downloads' }
        [void](OwnedProcess "$runtime\uv.exe" @('--no-config','python','install',$lock['PYTHON_REQUEST'][0],'--install-dir',"$runtime\python",'--no-bin','--no-registry'))
    }
    $python = OwnedProcess "$runtime\uv.exe" @('--no-config','python','find','--offline','--system','--managed-python','--no-project','--no-python-downloads',$lock['PYTHON_REQUEST'][0]) 30
    if (-not $python.StartsWith("$runtime\python\", [StringComparison]::OrdinalIgnoreCase)) { Blocked 'managed Python points outside the project runtime' }
    $code = OwnedProcess $python (@('-I', "$root\scripts\bootstrap.py") + $arguments) 300 $false
    exit $code
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
} finally {
    if ($temp -and (Test-Path -LiteralPath $temp)) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
