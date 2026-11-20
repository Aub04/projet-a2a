#!/usr/bin/env bash
# Campagne complète en une commande sous Linux ou macOS : ./campagne.sh [--runs 1] [--max-hours 8]
# Installe uv et Ollama s'ils manquent, démarre Ollama, télécharge le modèle, installe les dépendances,
# lance les tests puis la campagne (reprise automatique si on relance).
set -euo pipefail
cd "$(dirname "$0")"
info() { printf '\033[36m[campagne] %s\033[0m\n' "$*"; }
fail() { printf '\033[31m[campagne] ERREUR : %s\033[0m\n' "$*"; exit 1; }
export PATH="$HOME/.local/bin:$PATH" PYTHONUTF8=1

if ! command -v uv >/dev/null; then
  info "uv absent : installation (https://astral.sh/uv)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  command -v uv >/dev/null || fail "installation de uv impossible"
fi

if ! command -v ollama >/dev/null; then
  if [ "$(uname)" = "Linux" ]; then
    info "Ollama absent : installation (script officiel, demande le mot de passe sudo)..."
    curl -fsSL https://ollama.com/install.sh | sh
  else
    fail "Ollama introuvable. Installez-le depuis https://ollama.com/download puis relancez ./campagne.sh"
  fi
fi

ollama_ok() { curl -fs http://localhost:11434/api/version >/dev/null 2>&1; }
if ! ollama_ok; then
  info "Démarrage du serveur Ollama..."
  nohup ollama serve >/tmp/ollama_campagne.log 2>&1 &
  for _ in $(seq 30); do ollama_ok && break; sleep 2; done
fi
ollama_ok || fail "Ollama ne répond pas sur http://localhost:11434"

MODEL=$(sed -nE 's/^[[:space:]]*model:[[:space:]]*"?([^"[:space:]#]+).*/\1/p' config.yaml | head -1)
info "Modèle $MODEL (téléchargement de 5 Go la première fois)..."
ollama pull "$MODEL"

info "Installation des dépendances Python (uv sync)..."
uv sync --frozen
info "Tests automatiques..."
uv run pytest -q || fail "les tests ne passent pas, la campagne n'est pas lancée"

info "Lancement de la campagne. Laisser le terminal ouvert."
if command -v systemd-inhibit >/dev/null; then
  exec systemd-inhibit --what=idle:sleep --why="campagne A2A" uv run python -m a2a_team.eval.campagne "$@"
elif command -v caffeinate >/dev/null; then
  exec caffeinate -i uv run python -m a2a_team.eval.campagne "$@"
else
  exec uv run python -m a2a_team.eval.campagne "$@"
fi
