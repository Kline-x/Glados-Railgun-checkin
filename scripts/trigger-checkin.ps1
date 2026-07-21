# Trigger Glados checkin via workflow_dispatch
# Usage:
#   $env:GH_DISPATCH_TOKEN = "ghp_xxx"
#   .\scripts\trigger-checkin.ps1

param(
  [string]$Token = $env:GH_DISPATCH_TOKEN,
  [string]$Owner = "Kline-x",
  [string]$Repo = "Glados-Railgun-checkin",
  [string]$Ref = "master"
)

if (-not $Token) {
  Write-Error "Set GH_DISPATCH_TOKEN first, e.g. `$env:GH_DISPATCH_TOKEN = 'ghp_xxx'"
  exit 1
}

$url = "https://api.github.com/repos/$Owner/$Repo/actions/workflows/gladosCheck.yml/dispatches"
$headers = @{
  Accept = "application/vnd.github+json"
  Authorization = "Bearer $Token"
  "X-GitHub-Api-Version" = "2022-11-28"
}
$body = @{ ref = $Ref } | ConvertTo-Json

try {
  Invoke-RestMethod -Method Post -Uri $url -Headers $headers -Body $body -ContentType "application/json"
  Write-Host "OK: dispatched workflow on $Owner/$Repo@$Ref"
  Write-Host "Check: https://github.com/$Owner/$Repo/actions"
} catch {
  Write-Error $_.Exception.Message
  if ($_.ErrorDetails.Message) { Write-Error $_.ErrorDetails.Message }
  exit 1
}