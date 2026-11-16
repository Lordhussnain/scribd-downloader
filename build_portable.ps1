# Builds an x64 Windows package. The default includes a matched Chrome pair.
# Requires Rust MSVC, VS C++ build tools, and Internet access on the build PC.
param(
    [ValidateSet('Full', 'DriverOnly', 'System')]
    [string]$Mode = 'Full'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Set-Location $PSScriptRoot

$dist = Join-Path $PSScriptRoot 'dist'
$staging = Join-Path $dist ('.portable-' + [Guid]::NewGuid().ToString('N'))
$folderName = switch ($Mode) {
    'Full' { 'ScribdDownloader-Portable' }
    'DriverOnly' { 'ScribdDownloader-DriverOnly' }
    'System' { 'ScribdDownloader-System' }
}
$portable = Join-Path $dist $folderName
$oldFlags = [Environment]::GetEnvironmentVariable('RUSTFLAGS', 'Process')
try {
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Write-Host 'Building Rust application with the static MSVC runtime...'
    $env:RUSTFLAGS = "$oldFlags -C target-feature=+crt-static"
    & cargo build --release --target x86_64-pc-windows-msvc --manifest-path rust-gui\Cargo.toml
    if ($LASTEXITCODE -ne 0) { throw "Cargo build failed (exit code $LASTEXITCODE)." }
    $exe = Join-Path $PSScriptRoot 'rust-gui\target\x86_64-pc-windows-msvc\release\scribd-rust-gui.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Built executable not found: $exe" }
    Copy-Item -LiteralPath $exe -Destination (Join-Path $staging 'scribd-rust-gui.exe')

    if ($Mode -eq 'System') {
        $description = 'Chrome and ChromeDriver are not included. Install Chrome and a compatible ChromeDriver on PATH (or set CHROMEDRIVER to its full path).'
    } else {
        Write-Host 'Finding the latest stable Chrome for Testing release...'
        $metadataUrl = 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json'
        $metadata = Invoke-RestMethod -Uri $metadataUrl
        $release = $metadata.channels.Stable
        $version = [string]$release.version
        if ($version -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw 'Invalid version in Chrome for Testing metadata.' }
        $driver = @($release.downloads.chromedriver | Where-Object { $_.platform -eq 'win64' })
        if ($driver.Count -ne 1) { throw 'Could not find the win64 ChromeDriver release.' }
        $components = @(@{ Name='chromedriver'; Url=[string]$driver[0].url })
        if ($Mode -eq 'Full') {
            $chrome = @($release.downloads.chrome | Where-Object { $_.platform -eq 'win64' })
            if ($chrome.Count -ne 1) { throw 'Could not find the matching win64 Chrome release.' }
            $components += @{ Name='chrome'; Url=[string]$chrome[0].url }
        }
        $runtime = Join-Path $staging 'runtime'
        New-Item -ItemType Directory -Path $runtime | Out-Null
        foreach ($component in $components) {
            # Only accept URLs for this exact official release and platform.
            $expected = "https://storage.googleapis.com/chrome-for-testing-public/$version/win64/$($component.Name)-win64.zip"
            if ($component.Url -cne $expected) { throw "Unexpected download URL for $($component.Name)." }
            $zip = Join-Path $staging "$($component.Name).zip"
            Write-Host "Downloading $($component.Name) $version (win64)..."
            Invoke-WebRequest -Uri $component.Url -OutFile $zip -UseBasicParsing
            Expand-Archive -LiteralPath $zip -DestinationPath $runtime
            Remove-Item -LiteralPath $zip -Force
        }
        $driverExe = Join-Path $runtime 'chromedriver-win64\chromedriver.exe'
        if (-not (Test-Path -LiteralPath $driverExe -PathType Leaf)) { throw 'Downloaded ChromeDriver is missing.' }
        $driverVersion = & $driverExe --version
        if ($LASTEXITCODE -ne 0 -or $driverVersion -notmatch [regex]::Escape($version)) { throw "ChromeDriver version mismatch: $driverVersion" }
        if ($Mode -eq 'Full') {
            $chromeExe = Join-Path $runtime 'chrome-win64\chrome.exe'
            if (-not (Test-Path -LiteralPath $chromeExe -PathType Leaf)) { throw 'Downloaded Chrome is missing.' }
            $chromeVersion = (Get-Item -LiteralPath $chromeExe).VersionInfo.ProductVersion
            if ($chromeVersion -notmatch [regex]::Escape($version)) { throw "Chrome version mismatch: $chromeVersion" }
            'full' | Set-Content -LiteralPath (Join-Path $runtime 'package-mode.txt') -Encoding ASCII
            $description = "Bundled Chrome for Testing and ChromeDriver version: $version. The browser does not update automatically; rebuild regularly for security updates."
        } else {
            'driver-only' | Set-Content -LiteralPath (Join-Path $runtime 'package-mode.txt') -Encoding ASCII
            $description = "Bundled ChromeDriver version: $version. Chrome is not included; install a compatible Chrome version."
        }
    }
    @"
Scribd Downloader portable package ($Mode)
$description
Copy this entire folder to the target Windows 10/11 x64 PC.
Run scribd-rust-gui.exe. Internet is required to open Scribd documents.
"@ | Set-Content -LiteralPath (Join-Path $staging 'PACKAGE-INFO.txt') -Encoding UTF8

    # Only remove the old output after a complete replacement is ready.
    if (Test-Path -LiteralPath $portable) { Remove-Item -LiteralPath $portable -Recurse -Force }
    Move-Item -LiteralPath $staging -Destination $portable
    Write-Host "`nPortable package: $portable"
    Write-Host 'Copy the entire folder to the target PC.'
} catch {
    Write-Error $_
    exit 1
} finally {
    $env:RUSTFLAGS = $oldFlags
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
