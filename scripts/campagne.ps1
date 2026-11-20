# Lance la campagne complete sur un PC Windows (appele par campagne.cmd a la racine du depot).
# Installe uv et Ollama s'ils manquent, demarre Ollama, telecharge le modele, installe les dependances,
# lance les tests puis la campagne (reprise automatique si on relance).
# Usage : .\campagne.cmd                 (3 repetitions)
#         .\campagne.cmd -Runs 1         (1 repetition)
#         .\campagne.cmd -MaxHours 8     (s'arrete proprement apres 8 h, relancer pour reprendre)
param([int]$Runs = 0, [double]$MaxHours = 0, [switch]$SansDemo)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Info($m) { Write-Host "[campagne] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[campagne] ERREUR : $m" -ForegroundColor Red; exit 1 }

# 1. uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Info "uv absent : installation (https://astral.sh/uv)..."
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail "installation de uv impossible, voir https://docs.astral.sh/uv/" }
}

# 2. Ollama
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Info "Ollama absent : installation via winget..."
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    }
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Fail "Ollama introuvable. Installez-le depuis https://ollama.com/download puis relancez campagne.cmd"
    }
}

function Test-Ollama {
    try { Invoke-RestMethod -Uri "http://localhost:11434/api/version" -TimeoutSec 3 | Out-Null; return $true }
    catch { return $false }
}

# Reglages GPU : requetes en parallele selon la memoire video (le p2p interroge plusieurs agents a la fois),
# flash attention (calcul exact, moins de memoire). Ne s'appliquent que si ce script demarre lui-meme Ollama.
$vram = 0
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $q = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits | Select-Object -First 1
    if ($q) { $vram = [int](($q -split ",")[1].Trim()); Info "GPU detecte : $q Mo" }
} else {
    Info "ATTENTION : aucune carte NVIDIA detectee (nvidia-smi absent). Le calcul se fera sur CPU, tres lentement."
}
$parallel = if ($vram -ge 16000) { 4 } elseif ($vram -ge 11000) { 2 } else { 1 }
$env:OLLAMA_NUM_PARALLEL = "$parallel"
$env:OLLAMA_FLASH_ATTENTION = "1"
if (Test-Ollama) {
    Info "Ollama tourne deja : les reglages GPU de ce script ($parallel requetes en parallele) ne s'appliquent pas."
    Info "Pour aller plus vite : quitter Ollama (icone pres de l'horloge, Quit Ollama) puis relancer campagne.cmd."
} else {
    Info "Demarrage du serveur Ollama ($parallel requete(s) en parallele, flash attention)..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep -Seconds 2 }
}
if (-not (Test-Ollama)) { Fail "Ollama ne repond pas sur http://localhost:11434" }

# 3. Modele (nom lu dans config.yaml)
$model = (Select-String -Path "config.yaml" -Pattern '^\s*model:\s*"?([^"\s#]+)').Matches[0].Groups[1].Value
Info "Modele $model (telechargement de 5 Go la premiere fois)..."
ollama pull $model
if ($LASTEXITCODE -ne 0) { Fail "ollama pull $model a echoue" }

# 4. Dependances et tests (sans LLM, quelques secondes)
Info "Installation des dependances Python (uv sync)..."
uv sync --frozen
if ($LASTEXITCODE -ne 0) { Fail "uv sync a echoue" }
Info "Tests automatiques..."
uv run pytest -q
if ($LASTEXITCODE -ne 0) { Fail "les tests ne passent pas, la campagne n'est pas lancee" }

# 5. Campagne
$cargs = @()
if ($Runs -gt 0) { $cargs += @("--runs", "$Runs") }
if ($MaxHours -gt 0) { $cargs += @("--max-hours", "$MaxHours") }
if ($SansDemo) { $cargs += "--sans-demo" }
Info "Lancement de la campagne. Laisser la fenetre ouverte ; le PC ne se mettra pas en veille."
uv run python -m a2a_team.eval.campagne @cargs
exit $LASTEXITCODE
