"""
config.py — Configurações centrais do sistema de gestão de email

Variaveis de ambiente necessarias (defina no .env ou no ~/.bashrc):
  TELEGRAM_BOT_TOKEN   — token do bot
  TELEGRAM_CHAT_ID     — seu chat ID pessoal no Telegram
  ANTHROPIC_API_KEY    — chave da API Anthropic
  GMAIL_PESSOAL_EMAIL  — seu email Gmail pessoal
  GMAIL_USP_EMAIL      — seu email institucional USP

Como carregar: source ~/.env  (ou adicione ao ~/.bashrc para carregar automatico)
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── CAMINHOS ────────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_DIR = os.path.join(BASE_DIR, "credentials")
LOGS_DIR        = os.path.join(BASE_DIR, "logs")

GMAIL_CREDENTIALS = os.path.join(CREDENTIALS_DIR, "gmail_credentials.json")

# ─── CONTAS ───────────────────────────────────────────────────────────────────
ACCOUNTS = {
    "gmail_pessoal": {
        "type":       "gmail",
        "email":      os.environ.get("GMAIL_PESSOAL_EMAIL", ""),
        "token_file": os.path.join(CREDENTIALS_DIR, "gmail_pessoal_token.json"),
        "label":      "Gmail",
    },
    "gmail_usp": {
        "type":       "gmail",
        "email":      os.environ.get("GMAIL_USP_EMAIL", ""),
        "token_file": os.path.join(CREDENTIALS_DIR, "gmail_usp_token.json"),
        "label":      "USP",
    },
    # Hotmail desabilitado temporariamente — pendente configuracao Azure
    # "hotmail": {
    #     "type":       "outlook",
    #     "email":      os.environ.get("HOTMAIL_EMAIL", ""),
    #     "token_file": os.path.join(CREDENTIALS_DIR, "outlook_token.json"),
    #     "label":      "Hotmail",
    #     "client_id":  os.environ.get("AZURE_CLIENT_ID", ""),
    #     "tenant_id":  "consumers",
    # },
}

# ─── PASTAS IMAP ──────────────────────────────────────────────────────────────
FOLDERS = {
    "urgente":       "Urgente",
    "prazos":        "Prazos",
    "oportunidades": "Oportunidades",
    "academia":      "Academico",
    "networking":    "Networking",
    "financeiro":    "Financeiro",
    "para_ler":      "Para Ler",
    "referencia":    "Referencia",
    "spam":          "[Gmail]/Spam",
    "lixo":          "[Gmail]/Lixeira",
    "inbox":         "INBOX",
}

NATIVE_FOLDERS = {"inbox", "lixo", "spam"}

FOLDER_LABELS = {
    "urgente":       "🔴 Urgente",
    "prazos":        "📅 Prazos",
    "oportunidades": "🚀 Oportunidades",
    "academia":      "🎓 Academico",
    "networking":    "🤝 Networking",
    "financeiro":    "💸 Financeiro",
    "para_ler":      "📖 Para Ler",
    "referencia":    "🗂️ Referencia",
    "spam":          "🚫 Spam",
    "lixo":          "🗑️ Lixeira",
    "inbox":         "📥 Inbox",
}

# ─── TELEGRAM ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ─── ANTHROPIC ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ─── REGRAS DE LIMPEZA ────────────────────────────────────────────────────────
CLEANUP_RULES = {
    "archive_after_days":              30,
    "delete_trash_after_days":         30,
    "delete_spam_after_days":          15,
    "unsubscribe_suggest_after_weeks":  3,
}

# ─── REGRAS DE FOLLOW-UP ──────────────────────────────────────────────────────
FOLLOWUP_RULES = {
    "networking_no_reply_days":  5,
    "candidatura_no_reply_days": 5,
}

# ─── PERFIL DO AGENTE ─────────────────────────────────────────────────────────
def _load_profile():
    from pathlib import Path
    base = Path(__file__).parent
    for nome in ("profile.local.txt", "profile.example.txt"):
        p = base / nome
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("Crie profile.local.txt a partir de profile.example.txt")

AGENT_PROFILE = _load_profile()
