# 최신 릴리스 번들을 받는다 — 인터넷 되는 PC(Windows)에서. 서버로 옮기는 것은 scp/WinSCP 로.
#
#   .\fetch-release.ps1                          최신 → .\downloads\
#   .\fetch-release.ps1 -Out D:\bundles          폴더 지정
#   .\fetch-release.ps1 -Version v0.4.3          특정 버전
#   .\fetch-release.ps1 -Apptainer               apptainer .deb 도 함께(폐쇄망 서버용)
#
# 받는 것: standardplatform-<버전>.tar.gz · .sha256 (검증까지 한다)
param(
    [string]$Out = "$PSScriptRoot\downloads",
    [string]$Version = "",
    [switch]$Apptainer
)
$ErrorActionPreference = "Stop"
$Repo = "4leaf321-nox321/StandardPlatform"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

New-Item -ItemType Directory -Force -Path $Out | Out-Null

if (-not $Version) {
    $latest = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ "User-Agent" = "fetch-release" }
    $Version = $latest.tag_name
}
Write-Host "==> 버전 $Version → $Out"

$name = "standardplatform-$Version.tar.gz"
foreach ($f in @($name, "$name.sha256")) {
    $url = "https://github.com/$Repo/releases/download/$Version/$f"
    Write-Host "    받는 중: $f"
    Invoke-WebRequest -Uri $url -OutFile (Join-Path $Out $f) -Headers @{ "User-Agent" = "fetch-release" }
}

# 검증 — 절반만 받아진 tar 는 서버에서 푸는 순간에야 드러난다.
$expected = (Get-Content (Join-Path $Out "$name.sha256")).Split(" ")[0].ToLower()
$actual = (Get-FileHash (Join-Path $Out $name) -Algorithm SHA256).Hash.ToLower()
if ($expected -ne $actual) { throw "체크섬이 다릅니다 — 다시 받으세요. ($name)" }
Write-Host "    체크섬 OK"

if ($Apptainer) {
    $ap = Invoke-RestMethod "https://api.github.com/repos/apptainer/apptainer/releases/latest" -Headers @{ "User-Agent" = "fetch-release" }
    $deb = $ap.assets | Where-Object { $_.name -match '^apptainer_[0-9.]+_amd64\.deb$' } | Select-Object -First 1
    if ($deb) {
        Write-Host "    받는 중: $($deb.name)"
        Invoke-WebRequest -Uri $deb.browser_download_url -OutFile (Join-Path $Out $deb.name) -Headers @{ "User-Agent" = "fetch-release" }
    } else { Write-Warning "apptainer .deb 를 찾지 못했습니다 — https://github.com/apptainer/apptainer/releases 에서 직접" }
}

Write-Host ""
Write-Host "[OK] $Out 에 받았습니다. 서버로 옮기기 (계정·IP 는 자기 것으로):"
Write-Host "     scp `"$Out\$name`" `"$Out\$name.sha256`" <계정>@<서버IP>:~/"
