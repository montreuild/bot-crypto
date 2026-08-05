# Démarrage Docker du Crypto Bot (Windows PowerShell).
#
# Usage :
#   .\scripts\docker-up.ps1              # API paper (défaut)
#   .\scripts\docker-up.ps1 -Full        # API + frontend
#   .\scripts\docker-up.ps1 -Test        # pytest dans un conteneur
#   .\scripts\docker-up.ps1 -Prod        # stack prod (compose.prod)
#   .\scripts\docker-up.ps1 -Down        # arrête les services
#   .\scripts\docker-up.ps1 -NoBuild     # sans rebuild

[CmdletBinding()]
param(
    [switch]$Full,
    [switch]$Test,
    [switch]$Prod,
    [switch]$Down,
    [switch]$NoBuild,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Extra
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker introuvable. Installez Docker Desktop."
    }
    docker compose version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose v2 requis (docker compose …)."
    }
}

function Ensure-EnvFile {
    if (Test-Path .env) { return }
    $key = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
    try {
        $key = python -c "import secrets; print(secrets.token_urlsafe(32))" 2>$null
    } catch { }

    if (Test-Path .env.example) {
        (Get-Content .env.example -Raw) -replace "change-me-generate-a-secret", $key |
            Set-Content -Path .env -Encoding utf8 -NoNewline
    } else {
        @"
ENV=dev
WEB_API_KEY=$key
ALLOW_INSECURE_WEB=0
FRONTEND_URL=http://localhost:3000
API_PORT=8000
WEB_PORT=3000
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
"@ | Set-Content -Path .env -Encoding utf8
    }
    Write-Host "✓ .env créé avec une WEB_API_KEY aléatoire"
}

Assert-Docker
Ensure-EnvFile
New-Item -ItemType Directory -Force -Path data, logs, models | Out-Null

$build = @()
if (-not $NoBuild) { $build = @("--build") }

$files = @("-f", "docker-compose.yml")
if ($Prod) {
    $files += @("-f", "docker-compose.prod.yml")
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*WEB_API_KEY=(.+)$') {
            $script:webKey = $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    if (-not $webKey -or $webKey -eq "change-me-generate-a-secret") {
        throw "WEB_API_KEY manquante ou placeholder — refuse le mode prod."
    }
    if (-not $env:API_HOST_BIND) { $env:API_HOST_BIND = "127.0.0.1" }
    $env:ENV = "prod"
    $env:ALLOW_INSECURE_WEB = "0"
}

if ($Down) {
    & docker compose @files down
    exit $LASTEXITCODE
}

if ($Test) {
    Write-Host "→ Tests (profile test)…"
    & docker compose @files --profile test run --rm @build test @Extra
    exit $LASTEXITCODE
}

$bind = if ($env:API_HOST_BIND) { $env:API_HOST_BIND } else { "0.0.0.0" }
$portApi = if ($env:API_PORT) { $env:API_PORT } else { "8000" }
$portWeb = if ($env:WEB_PORT) { $env:WEB_PORT } else { "3000" }

if ($Full) {
    if ($Prod) {
        Write-Host "→ Production : API + frontend Next (ENV=prod, OpenAPI off)…"
    } else {
        Write-Host "→ Stack complète API + web…"
    }
    & docker compose @files --profile full up -d @build
    Write-Host ""
    Write-Host "  API  → http://${bind}:${portApi}/health"
    Write-Host "  Web  → http://${bind}:${portWeb}  (build Next standalone)"
    if ($Prod) {
        Write-Host "  ENV  → prod  ·  /api/docs désactivé (SEC-006)"
        Write-Host "  Proxy public → nginx sur l'hôte (deploy/nginx.conf, cf. DEPLOY.md)"
    }
    exit $LASTEXITCODE
}

if ($Prod) {
    Write-Host "→ Production : API seule (ENV=prod, OpenAPI off)…"
    Write-Host "  Astuce : ajoutez -Full pour build + démarrer le frontend Next."
} else {
    Write-Host "→ API paper…"
}
& docker compose @files up -d @build api
Write-Host ""
Write-Host "  API  → http://${bind}:${portApi}/health"
if ($Prod) {
    Write-Host "  ENV  → prod  ·  /api/docs désactivé (SEC-006)"
    Write-Host "  Proxy → nginx (deploy/nginx.conf) — DEPLOY.md"
}
Write-Host "  Logs → docker compose logs -f api"
Write-Host "  Stop → .\scripts\docker-up.ps1 -Down"
