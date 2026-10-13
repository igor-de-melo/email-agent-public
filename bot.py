import subprocess
import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID  = int(os.getenv("TELEGRAM_CHAT_ID"))
PROJECT_DIR      = "/home/ubuntu/Projects/email-agent"
PYTHON           = f"{PROJECT_DIR}/venv/bin/python3"
CANCEL_FILE      = "/tmp/triage_cancel"

# ─── AUTORIZACAO ──────────────────────────────────────────────────────────────

def autorizado(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID

# ─── COMANDOS PRINCIPAIS ──────────────────────────────────────────────────────

async def triar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triagem padrão: INBOX + não lidos."""
    if not autorizado(update):
        return
    await update.message.reply_text("⏳ Iniciando triagem (INBOX · não lidos)...")
    result = subprocess.run(
        [PYTHON, f"{PROJECT_DIR}/agent_triage.py"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        await update.message.reply_text("✅ Triagem concluída!")
    else:
        await update.message.reply_text(f"❌ Erro na triagem:\n{result.stderr[:500]}")


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gera o resumo diário."""
    if not autorizado(update):
        return
    await update.message.reply_text("⏳ Gerando resumo diário...")
    result = subprocess.run(
        [PYTHON, f"{PROJECT_DIR}/agent_summary.py"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        await update.message.reply_text("✅ Resumo enviado!")
    else:
        await update.message.reply_text(f"❌ Erro no resumo:\n{result.stderr[:500]}")


async def triar_e_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triagem + resumo em sequência."""
    if not autorizado(update):
        return
    await update.message.reply_text("⏳ Rodando triagem + resumo...")
    t = subprocess.run(
        [PYTHON, f"{PROJECT_DIR}/agent_triage.py"],
        capture_output=True, text=True
    )
    if t.returncode != 0:
        await update.message.reply_text(f"❌ Erro na triagem:\n{t.stderr[:500]}")
        return
    d = subprocess.run(
        [PYTHON, f"{PROJECT_DIR}/agent_summary.py"],
        capture_output=True, text=True
    )
    if d.returncode == 0:
        await update.message.reply_text("✅ Triagem + resumo concluídos!")
    else:
        await update.message.reply_text(f"❌ Erro no resumo:\n{d.stderr[:500]}")


# ─── TRIAGEM POR PASTA ────────────────────────────────────────────────────────

async def triar_pasta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triagem em uma pasta específica.

    Uso:
      /triar_pasta spam
      /triar_pasta para_ler
      /triar_pasta financeiro --months 3

    Sem argumento → exibe teclado inline com as pastas disponíveis.
    """
    if not autorizado(update):
        return

    args = context.args

    if not args:
        keyboard = [
            [InlineKeyboardButton("📥 Inbox (todos)",  callback_data="pasta:inbox:--all")],
            [InlineKeyboardButton("📖 Para Ler",       callback_data="pasta:para_ler:--all")],
            [InlineKeyboardButton("💼 Oportunidades",  callback_data="pasta:oportunidades:--all")],
            [InlineKeyboardButton("🤝 Networking",     callback_data="pasta:networking:--all")],
            [InlineKeyboardButton("💰 Financeiro",     callback_data="pasta:financeiro:--all")],
            [InlineKeyboardButton("🔧 Técnico",        callback_data="pasta:tecnico:--all")],
            [InlineKeyboardButton("🗑️ Spam",           callback_data="pasta:spam:--all")],
            [InlineKeyboardButton("📁 Outros",         callback_data="pasta:outros:--all")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "📂 Selecione a pasta para triar:\n"
            "_(ou use `/triar_pasta <chave>` diretamente)_",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    pasta = args[0]
    flags_extras = args[1:]  # ex: ["--months", "3"]
    cmd = [PYTHON, f"{PROJECT_DIR}/agent_triage.py", pasta] + flags_extras

    await update.message.reply_text(f"⏳ Triando pasta `{pasta}`...", parse_mode="Markdown")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        await update.message.reply_text(f"✅ Triagem de `{pasta}` concluída!", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Erro:\n{result.stderr[:500]}")


async def pasta_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback dos botões inline de pasta."""
    query = update.callback_query
    if query.from_user.id != ALLOWED_USER_ID:
        return
    await query.answer()

    # callback_data formato: "pasta:<chave>:<flag>"
    _, pasta, flag = query.data.split(":")
    cmd = [PYTHON, f"{PROJECT_DIR}/agent_triage.py", pasta, flag]

    await query.edit_message_text(f"⏳ Triando pasta `{pasta}`...", parse_mode="Markdown")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        await query.edit_message_text(f"✅ Triagem de `{pasta}` concluída!", parse_mode="Markdown")
    else:
        await query.edit_message_text(f"❌ Erro:\n{result.stderr[:500]}")


# ─── TRIAGEM POR MESES ────────────────────────────────────────────────────────

async def triar_meses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triagem dos últimos N meses na INBOX.

    Uso:
      /triar_meses 3    → últimos 3 meses
      /triar_meses      → exibe teclado inline com opções

    Para combinar com pasta, use /triar_pasta diretamente:
      /triar_pasta financeiro --months 3
    """
    if not autorizado(update):
        return

    args = context.args

    if not args:
        keyboard = [
            [
                InlineKeyboardButton("1 mês",   callback_data="meses:1"),
                InlineKeyboardButton("2 meses",  callback_data="meses:2"),
                InlineKeyboardButton("3 meses",  callback_data="meses:3"),
            ],
            [
                InlineKeyboardButton("6 meses",  callback_data="meses:6"),
                InlineKeyboardButton("12 meses", callback_data="meses:12"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🗓️ Quantos meses deseja reprocessar?",
            reply_markup=reply_markup
        )
        return

    try:
        n = int(args[0])
        if n < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Use um número inteiro >= 1. Ex: `/triar_meses 3`",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"⏳ Triando últimos {n} meses na INBOX...")
    result = subprocess.run(
        [PYTHON, f"{PROJECT_DIR}/agent_triage.py", "--months", str(n)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        await update.message.reply_text(f"✅ Triagem dos últimos {n} meses concluída!")
    else:
        await update.message.reply_text(f"❌ Erro:\n{result.stderr[:500]}")


async def meses_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback dos botões inline de meses."""
    query = update.callback_query
    if query.from_user.id != ALLOWED_USER_ID:
        return
    await query.answer()

    n = query.data.split(":")[1]
    await query.edit_message_text(f"⏳ Triando últimos {n} meses na INBOX...")
    result = subprocess.run(
        [PYTHON, f"{PROJECT_DIR}/agent_triage.py", "--months", n],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        await query.edit_message_text(f"✅ Triagem dos últimos {n} meses concluída!")
    else:
        await query.edit_message_text(f"❌ Erro:\n{result.stderr[:500]}")


# ─── CANCELAMENTO ─────────────────────────────────────────────────────────────

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia sinal de cancelamento para a triagem em andamento."""
    if not autorizado(update):
        return
    with open(CANCEL_FILE, "w") as f:
        f.write("cancel")
    await update.message.reply_text(
        "🛑 Sinal enviado. A triagem será interrompida no próximo email."
    )


async def cancelar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback do botão inline 🛑 Cancelar triagem."""
    query = update.callback_query
    if query.from_user.id != ALLOWED_USER_ID:
        return
    await query.answer()
    with open(CANCEL_FILE, "w") as f:
        f.write("cancel")
    await query.edit_message_text(
        "🛑 Sinal enviado. A triagem será interrompida no próximo email."
    )


# ─── AJUDA ────────────────────────────────────────────────────────────────────

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todos os comandos disponíveis."""
    if not autorizado(update):
        return
    texto = (
        "🤖 *Comandos disponíveis*\n\n"
        "*Triagem*\n"
        "/triar — INBOX · não lidos \\(padrão cron\\)\n"
        "/triar\\_pasta — escolhe pasta via menu ou argumento\n"
        "  ex: `/triar_pasta spam`\n"
        "  ex: `/triar_pasta financeiro \\-\\-months 3`\n"
        "/triar\\_meses — últimos N meses via menu ou argumento\n"
        "  ex: `/triar_meses 6`\n\n"
        "*Resumo*\n"
        "/daily — gera resumo diário\n"
        "/triar\\_e\\_daily — triagem \\+ resumo em sequência\n\n"
        "*Controle*\n"
        "/cancelar — interrompe triagem em andamento\n"
        "/ajuda — esta mensagem"
    )
    await update.message.reply_text(texto, parse_mode="MarkdownV2")


# ─── INICIALIZAÇÃO ────────────────────────────────────────────────────────────

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("triar",         triar))
app.add_handler(CommandHandler("daily",         daily))
app.add_handler(CommandHandler("triar_e_daily", triar_e_daily))
app.add_handler(CommandHandler("triar_pasta",   triar_pasta))
app.add_handler(CommandHandler("triar_meses",   triar_meses))
app.add_handler(CommandHandler("cancelar",      cancelar))
app.add_handler(CommandHandler("ajuda",         ajuda))

app.add_handler(CallbackQueryHandler(cancelar_callback, pattern="^cancelar_triagem$"))
app.add_handler(CallbackQueryHandler(pasta_callback,    pattern="^pasta:"))
app.add_handler(CallbackQueryHandler(meses_callback,    pattern="^meses:"))

app.run_polling()
