$ErrorActionPreference = "Continue"

$scriptPath = $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptPath)) {
  $scriptDir = (Get-Location).Path
} else {
  $scriptDir = Split-Path -Parent $scriptPath
}

Set-Location $scriptDir

$exe = Join-Path $scriptDir "enclave_probe.exe"
$probeVersion = "v9"
$output = Join-Path $scriptDir "probe-output-$probeVersion.txt"
$errorFile = Join-Path $scriptDir "probe-error-$probeVersion.txt"

Remove-Item -Force -ErrorAction SilentlyContinue $output
Remove-Item -Force -ErrorAction SilentlyContinue $errorFile

"scriptDir=$scriptDir" | Out-File -FilePath $output -Encoding ascii
"exe=$exe" | Out-File -FilePath $output -Encoding ascii -Append
"probeVersion=$probeVersion" | Out-File -FilePath $output -Encoding ascii -Append

if (!(Test-Path $exe)) {
  "ERROR: enclave_probe.exe not found" | Out-File -FilePath $output -Encoding ascii -Append
} else {
  foreach ($mode in @("safe", "setup", "sgx", "tls")) {
    Write-Host "Running enclave_probe.exe $mode..."
    "---- mode $mode ----" | Out-File -FilePath $output -Encoding ascii -Append
    $cmd = "`"$exe`" $mode 1>> `"$output`" 2>> `"$errorFile`""
    cmd.exe /c $cmd
    "exitCode.$mode=$LASTEXITCODE" | Out-File -FilePath $output -Encoding ascii -Append
  }
}

if (Test-Path $errorFile) {
  $errSize = (Get-Item $errorFile).Length
  if ($errSize -gt 0) {
    "---- stderr ----" | Out-File -FilePath $output -Encoding ascii -Append
    Get-Content $errorFile | Out-File -FilePath $output -Encoding ascii -Append
  }
}

if (!(Test-Path $output)) {
  "ERROR: failed to create probe-output.txt" | Out-File -FilePath $output -Encoding ascii
}

Write-Host "Saved output to $output"

$targets = @(
  "http://10.0.2.2:8765/upload?version=$probeVersion",
  "http://113.54.253.14:8765/upload?version=$probeVersion"
)

foreach ($target in $targets) {
  try {
    Write-Host "Uploading output to $target ..."
    Invoke-WebRequest -Uri $target -Method POST -InFile $output -UseBasicParsing | Out-Null
    Write-Host "Upload succeeded: $target"
    exit 0
  } catch {
    Write-Host "Upload failed: $target"
  }
}

Write-Host "Upload failed for all targets. Please run: Get-Content .\probe-output.txt"
exit 1
