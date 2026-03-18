#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# CodeMax One-Click Setup for Linux / macOS
# Usage: curl -fsSL https://api.codemax.pro/setup.sh | bash
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

API_ENDPOINT="https://api.codemax.pro"
MCP_URL="https://api.codemax.pro"

# Colors for output
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "\n${CYAN}  ╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}  ║     CodeMax Setup for Linux/macOS      ║${NC}"
echo -e "${CYAN}  ╚══════════════════════════════════════════╝${NC}\n"

# Prompt for API Key directly
read -p "  Enter your CodeMax API key: " API_KEY < /dev/tty
if [ -z "$API_KEY" ]; then
    echo -e "${RED}  ✗ API key cannot be empty. Aborting.${NC}"
    exit 1
fi

echo ""

# ── [1/5] Check Node.js ──
echo -e "${YELLOW}  [1/5] Checking Node.js...${NC}"
if command -v node >/dev/null 2>&1; then
    NODE_VERSION=$(node --version)
    echo -e "${GREEN}  ✓ Node.js $NODE_VERSION found${NC}"
else
    echo -e "${RED}  ✗ Node.js not found. Please install Node.js (v18+) first.${NC}"
    exit 1
fi

# ── [2/5] Install codemax-mcp ──
echo -e "${YELLOW}  [2/5] Installing codemax-mcp...${NC}"
if npm install -g codemax-mcp >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓ codemax-mcp installed${NC}"
else
    echo -e "${YELLOW}  ⚠ Could not install globally, will use npx${NC}"
fi

# ── [3/5] Configure Claude Code (~/.claude/settings.json) ──
echo -e "${YELLOW}  [3/5] Configuring Claude Code...${NC}"
CLAUDE_DIR="$HOME/.claude"
mkdir -p "$CLAUDE_DIR"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"

if [ ! -f "$SETTINGS_FILE" ]; then
    echo "{}" > "$SETTINGS_FILE"
fi

# ── Clear old system env vars (prevent auth conflicts) ──
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL
unset ANTHROPIC_MODEL ANTHROPIC_SMALL_FAST_MODEL
unset ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL
unset ANTHROPIC_DEFAULT_HAIKU_MODEL CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC

# Use Node to safely update JSON files
API_KEY="$API_KEY" API_ENDPOINT="$API_ENDPOINT" SETTINGS_FILE="$SETTINGS_FILE" node -e "
const fs = require('fs');
let settings = {};
try { settings = JSON.parse(fs.readFileSync(process.env.SETTINGS_FILE, 'utf8')); } catch(e) { }
settings.env = settings.env || {};
settings.env['ANTHROPIC_AUTH_TOKEN'] = process.env.API_KEY;
settings.env['ANTHROPIC_BASE_URL'] = process.env.API_ENDPOINT;
settings.env['ANTHROPIC_MODEL'] = 'claude-opus-4-6-thinking';
settings.env['ANTHROPIC_REASONING_MODEL'] = 'Opus 4.6';
settings.env['ANTHROPIC_DEFAULT_SONNET_MODEL'] = 'Sonnet 4.5';
settings.env['ANTHROPIC_DEFAULT_OPUS_MODEL'] = 'Opus 4.6';
settings.env['ANTHROPIC_DEFAULT_HAIKU_MODEL'] = 'Haiku 4.5';
settings.env['CLAUDE_CODE_SUBAGENT_MODEL'] = 'claude-opus-4-6-thinking';
settings.env['ENABLE_EXPERIMENTAL_MCP_CLI'] = 'true';
settings.model = 'opus[1m]';
fs.writeFileSync(process.env.SETTINGS_FILE, JSON.stringify(settings, null, 2));
"
echo -e "${GREEN}  ✓ $SETTINGS_FILE configured${NC}"

# ── [4/5] Configure MCP server (~/.claude.json) ──
echo -e "${YELLOW}  [4/5] Configuring MCP server...${NC}"
CLAUDE_MCP_FILE="$HOME/.claude.json"

if [ ! -f "$CLAUDE_MCP_FILE" ]; then
    echo "{}" > "$CLAUDE_MCP_FILE"
fi

API_KEY="$API_KEY" MCP_URL="$MCP_URL" CLAUDE_MCP_FILE="$CLAUDE_MCP_FILE" node -e "
const fs = require('fs');
let config = {};
try { config = JSON.parse(fs.readFileSync(process.env.CLAUDE_MCP_FILE, 'utf8')); } catch(e) { }
config.mcpServers = config.mcpServers || {};
config.mcpServers['CodeMax'] = {
  command: 'npx',
  args: ['-y', 'codemax-mcp'],
  env: {
    CodeMax_API_KEY: process.env.API_KEY,
    CodeMax_URL: process.env.MCP_URL
  }
};
config.hasCompletedOnboarding = true;
fs.writeFileSync(process.env.CLAUDE_MCP_FILE, JSON.stringify(config, null, 2));
"
echo -e "${GREEN}  ✓ $CLAUDE_MCP_FILE configured${NC}"


# ── [5/5] Install Claude Code CLI ──
echo -e "\n${YELLOW}  [5/5] Installing Claude Code CLI...${NC}"
if ! command -v claude >/dev/null 2>&1; then
    if sudo npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Claude Code CLI installed${NC}"
    else
        echo -e "${YELLOW}  ⚠ Auto-install failed. Run: npm install -g @anthropic-ai/claude-code${NC}"
    fi
else
    echo -e "${GREEN}  ✓ Claude Code CLI already installed${NC}"
fi

# ── Setup Complete ──
echo -e "\n${GREEN}  ╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}  ║       ✓ Setup complete!                  ║${NC}"
echo -e "${GREEN}  ╚══════════════════════════════════════════╝${NC}\n"

echo -e "  What was configured:"
echo -e "    • Claude Code settings (~/.claude/settings.json)"
echo -e "    • MCP server with search & image parsing (~/.claude.json)"
echo -e "    • Claude Code CLI"
echo -e "\n  Restart your terminal/IDE to apply if necessary."
echo -e "  Type 'claude' to start using Claude Code!\n"
