# Copy the Max Payne 3 RePrescribed app folder to the PS5 over FTP, and optionally your game files
# into its install/ folder (the app installs them by itself when it opens).
#
#   .\install_to_ps5.ps1 -AppDir <the release's PPSA95454 folder> -Ps5 <the PS5's IP address>
#                        [-Files <disc image>, <title update>, <DLC package>, ...]
#                        [-Port 2121] [-Dest /data/homebrew]
#
# Needs an FTP server running on the PS5 (etaHEN's, or any other) and curl.exe (Windows 10 and later
# have it).
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$Ps5,
    [string[]]$Files = @(),
    [int]$Port = 2121,
    [string]$Dest = "/data/homebrew",
    [string]$TitleId = "PPSA95454"
)
$ErrorActionPreference = "Stop"
$base = "ftp://${Ps5}:${Port}${Dest}/${TitleId}"

function Send-File([string]$From, [string]$To) {
    $url = "$base/$To" -replace '(?<!:)//', '/'
    Write-Host ("  {0} ({1:N0} MB)" -f $To, ((Get-Item $From).Length / 1MB))
    & curl.exe -s -S --ftp-create-dirs -T $From $url
    if ($LASTEXITCODE -ne 0) { throw "upload failed: $From" }
}

function Send-Tree([string]$From, [string]$To) {
    $root = (Resolve-Path $From).Path.TrimEnd('\')
    foreach ($f in Get-ChildItem $root -Recurse -File) {
        Send-File $f.FullName ("$To" + $f.FullName.Substring($root.Length + 1).Replace('\', '/'))
    }
}

& curl.exe -s --max-time 8 "ftp://${Ps5}:${Port}/" > $null
if ($LASTEXITCODE -ne 0) { throw "The PS5's FTP server at ${Ps5}:${Port} doesn't answer. Is the console on with an FTP server running?" }

Write-Host "==> The app folder -> $Dest/$TitleId/"
Send-Tree $AppDir ""

foreach ($f in $Files) {
    $item = Get-Item $f
    Write-Host "==> $($item.Name) -> install/"
    if ($item.PSIsContainer) { Send-Tree $item.FullName "install/$($item.Name)/" }
    else { Send-File $item.FullName "install/$($item.Name)" }
}

Write-Host "==> Done: open Max Payne 3 RePrescribed on the PS5; it installs what's in install/."
