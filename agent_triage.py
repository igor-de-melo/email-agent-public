"""
agent_triage.py — Agente de triagem: le emails novos, classifica e move para pastas
Execute a cada 15 minutos via cron: */15 * * * * python3 ~/email-agent/agent_triage.py

─── MODOS DE EXECUCAO ────────────────────────────────────────────────────────

  FILTRO DE EMAILS (segunda camada, independente da pasta):

    --unread   Apenas emails nao lidos           (padrao quando nenhum filtro e dado)
    --all      Todos os emails da pasta
    --month    Emails dos ultimos 30 dias

  PASTA (primeira camada, opcional):

    <sem pasta>   Usa INBOX como origem
    <chave>       Qualquer pasta do sistema (veja --list)

  COMBINACOES:

    python3 agent_triage.py                        INBOX  +  nao lidos  (cron)
    python3 agent_triage.py --all                  INBOX  +  todos
    python3 agent_triage.py --month                INBOX  +  ultimos 30 dias
    python3 agent_triage.py para_ler               Para Ler  +  todos
    python3 agent_triage.py para_ler --unread      Para Ler  +  nao lidos
    python3 agent_triage.py para_ler --month       Para Ler  +  ultimos 30 dias
    python3 agent_triage.py spam --all             Spam  +  todos
    python3 agent_triage.py financeiro --month     Financeiro  +  ultimos 30 dias
    python3 agent_triage.py --list                 Lista as pastas disponiveis

─────────────────────────────────────────────────────────────────────────────
"""

import imaplib
import email
import email.header
import json
import os
import re
import sys
import logging
import requests
import codecs
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from config import ACCOUNTS, FOLDERS, NATIVE_FOLDERS, FOLDER_LABELS, LOGS_DIR
from classifier import classify_email
from auth_gmail import get_imap_connection as gmail_connect
from telegram_notify import send_alert
from dotenv import load_dotenv

load_dotenv()

CANCEL_FILE = "/tmp/triage_cancel"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_CHAT_ID")

# ──────────────────────────────────────────────────────────────────
def _register_unknown_charsets():
    """Registra aliases para charsets nao-padrao que o Python nao reconhece."""
    aliases = {
        "unknown_8bit":   "latin_1",
        "unknown-8bit":   "latin_1",
        "x_unknown":      "latin_1",
        "x_user_defined": "latin_1",
    }
    def search_func(name):
        normalized = name.lower().replace("-", "_")
        if normalized in aliases:
            return codecs.lookup(aliases[normalized])
        return None
    codecs.register(search_func)

_register_unknown_charsets()

# ─── LOGGING ──────────────────────────────────────────────────────────────────
os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOGS_DIR, "triage.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%d-%m-%Y %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── FILTROS ──────────────────────────────────────────────────────────────────
FILTER_UNREAD = "unread"   # UNSEEN — apenas nao lidos
FILTER_ALL    = "all"      # ALL    — todos os emails da pasta
FILTER_MONTHS = "months"   # SINCE  — ultimos N meses (N definido em tempo de execucao)

# Flags simples (sem valor associado)
FILTER_FLAGS = {
    "--unread": FILTER_UNREAD, "-u": FILTER_UNREAD,
    "--all":    FILTER_ALL,    "-a": FILTER_ALL,
}

# Flags que aceitam valor numerico: --months N  ou  --months=N
# "--month" sem numero e um atalho fixo para 1 mes
FILTER_MONTHS_FLAGS = ("--months", "--month", "-m")


def months_label(n: int) -> str:
    """Gera o label legivel para o filtro de meses."""
    if n == 1:
        return "ultimo 1 mes (SINCE)"
    return f"ultimos {n} meses (SINCE)"


def build_search_criteria(filter_mode: str, months: int = 1) -> list:
    """
    Converte o modo de filtro em criterio de busca IMAP.

    IMAP SEARCH aceita:
      UNSEEN            — apenas nao lidos
      ALL               — todos
      SINCE DD-Mon-YYYY — a partir de uma data (inclui lidos e nao lidos)

    Para FILTER_MONTHS, calcula a data subtraindo N*30 dias de hoje.
    Retorna uma lista de argumentos para conn.uid("SEARCH", None, *criterios).
    """
    if filter_mode == FILTER_UNREAD:
        return ["UNSEEN"]

    if filter_mode == FILTER_ALL:
        return ["ALL"]

    if filter_mode == FILTER_MONTHS:
        since_date = (datetime.now() - timedelta(days=months * 30)).strftime("%d-%b-%Y")
        return ["SINCE", since_date]

    return ["UNSEEN"]  # fallback seguro


# ─── RESOLUCAO DE PASTA ───────────────────────────────────────────────────────

def resolve_folder(arg: str):
    """
    Resolve o argumento de linha de comando para (chave, nome_imap).

    Aceita chave exata, nome IMAP, ou prefixo unico.
    Retorna (chave, nome_imap) ou None se nao encontrado.
    """
    arg_lower = arg.strip().lower()

    if arg_lower in FOLDERS:
        return arg_lower, FOLDERS[arg_lower]

    for key, imap_name in FOLDERS.items():
        if imap_name.lower() == arg_lower:
            return key, imap_name

    matches = [(k, v) for k, v in FOLDERS.items() if k.startswith(arg_lower)]
    if len(matches) == 1:
        return matches[0]

    matches = [(k, v) for k, v in FOLDERS.items() if v.lower().startswith(arg_lower)]
    if len(matches) == 1:
        return matches[0]

    return None


def parse_args(argv: list):
    """
    Interpreta sys.argv e retorna (folder_key, filter_mode, months).

    Sintaxes aceitas para o filtro de meses:
      --months 3      valor como argumento separado
      --months=3      valor com sinal de igual
      --month         atalho fixo para 1 mes
      -m 3            forma curta com valor separado

    Regras de default:
      Filtro ausente + pasta ausente  → UNREAD  (cron padrao)
      Filtro ausente + pasta presente → ALL     (reprocessamento padrao)
    """
    filter_mode = None
    months      = 1
    folder_key  = None
    skip_next   = False

    i = 0
    while i < len(argv):
        arg = argv[i]

        if skip_next:
            skip_next = False
            i += 1
            continue

        # ── Flags de meses ──────────────────────────────────────────────────
        if arg.startswith(tuple(FILTER_MONTHS_FLAGS)):

            # Forma --months=N
            if "=" in arg:
                _, val = arg.split("=", 1)
                try:
                    months = int(val)
                    if months < 1:
                        raise ValueError
                except ValueError:
                    print(f"Erro: valor invalido '{val}' para --months. Use um inteiro >= 1.")
                    sys.exit(1)

            # Forma --month (atalho sem numero — sempre 1 mes)
            elif arg in ("--month", "-m") and (i + 1 >= len(argv) or argv[i + 1].startswith("-") or not argv[i + 1].isdigit()):
                months = 1

            # Forma --months N ou -m N (valor no proximo argumento)
            else:
                if i + 1 < len(argv) and argv[i + 1].lstrip("-").isdigit():
                    i += 1
                    try:
                        months = int(argv[i])
                        if months < 1:
                            raise ValueError
                    except ValueError:
                        print(f"Erro: valor invalido '{argv[i]}' para --months. Use um inteiro >= 1.")
                        sys.exit(1)
                else:
                    months = 1  # --months sem numero → 1 mes

            filter_mode = FILTER_MONTHS

        # ── Flags simples (--unread, --all) ─────────────────────────────────
        elif arg in FILTER_FLAGS:
            filter_mode = FILTER_FLAGS[arg]

        # ── Flags desconhecidas ──────────────────────────────────────────────
        elif arg.startswith("-") and arg not in ("--list", "-l", "list"):
            print(f"Aviso: flag desconhecida '{arg}' ignorada.")

        # ── Argumento de pasta ───────────────────────────────────────────────
        else:
            resolved = resolve_folder(arg)
            if resolved is None:
                print(f"\nErro: pasta '{arg}' nao reconhecida.")
                print("Use  python3 agent_triage.py --list  para ver as pastas disponiveis.\n")
                sys.exit(1)
            folder_key = resolved[0]

        i += 1

    # Defaults de filtro
    if filter_mode is None:
        filter_mode = FILTER_ALL if folder_key is not None else FILTER_UNREAD

    return folder_key, filter_mode, months


def list_folders():
    """Imprime todas as pastas disponiveis."""
    print("\nPastas disponiveis:\n")
    print(f"  {'CHAVE':<16} {'PASTA IMAP':<28} {'LABEL'}")
    print(f"  {'-'*16} {'-'*28} {'-'*20}")
    for key, imap_name in FOLDERS.items():
        print(f"  {key:<16} {imap_name:<28} {FOLDER_LABELS.get(key, '')}")
    print()
    print("Filtros:  --unread  --all  --months N  (ex: --months 3)")
    print("Atalho:   --month  =  --months 1")
    print("Exemplo:  python3 agent_triage.py spam --months 6\n")


# ─── IMAP HELPERS ─────────────────────────────────────────────────────────────

def decode_header_value(value: str) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def get_body(msg) -> str:
    """Extrai o corpo de texto do email (prefere text/plain).
    Trata charsets invalidos como unknown-8bit declarados no cabecalho."""

    # Charsets invalidos/nao-padrao que o Python nao reconhece como codecs.
    # Mapeados para o encoding mais proximo que consegue decodificar sem perda.
    CHARSET_FALLBACKS = {
        "unknown-8bit":    "latin-1",
        "x-unknown":       "latin-1",
        "x-user-defined":  "latin-1",
        "us-ascii":        "ascii",
    }

    def safe_decode(payload: bytes, charset: str) -> str:
        charset = (charset or "utf-8").lower().strip()
        charset = CHARSET_FALLBACKS.get(charset, charset)
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            # Charset ainda desconhecido — tenta utf-8, depois latin-1
            try:
                return payload.decode("utf-8", errors="replace")
            except Exception:
                return payload.decode("latin-1", errors="replace")

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype       = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in disposition:
                body = safe_decode(part.get_payload(decode=True), part.get_content_charset())
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    raw_html = safe_decode(part.get_payload(decode=True), part.get_content_charset())
                    body = re.sub(r"<[^>]+>", " ", raw_html)
                    body = re.sub(r"\s+", " ", body).strip()
                    break
    else:
        body = safe_decode(msg.get_payload(decode=True), msg.get_content_charset())

    return body[:2000]


def ensure_folders(conn: imaplib.IMAP4_SSL):
    result, folder_list = conn.list()
    existing = set()
    for f in folder_list:
        if f:
            decoded = f.decode("utf-8", errors="replace")
            parts = decoded.split('"/\"')
            if len(parts) > 1:
                existing.add(parts[-1].strip().strip('"'))
    for key, folder_name in FOLDERS.items():
        if key in NATIVE_FOLDERS:
            continue
        if folder_name not in existing:
            conn.create(folder_name)
            log.info(f"Pasta criada: {folder_name}")
            print(f"  Pasta criada: {folder_name}")


def move_email(conn: imaplib.IMAP4_SSL, uid: str, target_folder: str):
    # O protocolo IMAP exige aspas em nomes de pasta com espacos ou
    # caracteres especiais (RFC 3501). Sem aspas, "Para Ler" chega ao servidor
    # como dois tokens separados e retorna BAD [Could not parse command].
    quoted = f'"{target_folder}"' if " " in target_folder else target_folder
    conn.uid("COPY", uid, quoted)
    conn.uid("STORE", uid, "+FLAGS", "\\Deleted")
    conn.expunge()


# ─── PROCESSAMENTO ────────────────────────────────────────────────────────────
def telegram_progresso(atual: int, total: int):
    """Envia notificacao de progresso a cada 5 emails, com botao de cancelar."""
    if atual % 5 == 0:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_USER_ID,
            "text": f"⏳ Triagem em andamento: {atual}/{total} emails verificados...",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "🛑 Cancelar triagem", "callback_data": "cancelar_triagem"}
                ]]
            }
        })
        
def process_account(account_key: str, folder_key: str = None, filter_mode: str = FILTER_UNREAD, months: int = 1):
    """
    Processa emails de uma conta com pasta e filtro definidos.

    folder_key=None   → INBOX
    folder_key="xyz"  → pasta xyz

    filter_mode  → FILTER_UNREAD | FILTER_ALL | FILTER_MONTHS
    months       → usado apenas quando filter_mode == FILTER_MONTHS
    """
    account      = ACCOUNTS[account_key]
    label        = account["label"]
    imap_source  = FOLDERS[folder_key] if folder_key else "INBOX"

    if filter_mode == FILTER_MONTHS:
        filter_label = months_label(months)
    elif filter_mode == FILTER_ALL:
        filter_label = "todos (ALL)"
    else:
        filter_label = "nao lidos (UNSEEN)"

    folder_display = FOLDER_LABELS.get(folder_key, imap_source) if folder_key else "📥 Inbox"
    mode_label   = f"{folder_display}  |  {filter_label}"

    log.info(f"Iniciando [{label}] {mode_label}")
    print(f"\n[{label}] Conectando...  ({mode_label})")

    try:
        if account["type"] == "gmail":
            conn = gmail_connect(account_key)
        else:
            from auth_outlook import get_imap_connection as outlook_connect
            conn = outlook_connect(account_key)
    except Exception as e:
        log.error(f"[{label}] Falha na conexao: {e}")
        print(f"[{label}] Erro de conexao: {e}")
        return

    ensure_folders(conn)

    select_result, _ = conn.select(imap_source)
    if select_result != "OK":
        print(f"[{label}] Pasta '{imap_source}' nao encontrada ou inacessivel.")
        log.error(f"[{label}] Falha ao selecionar '{imap_source}'.")
        conn.logout()
        return

    criterios = build_search_criteria(filter_mode, months=months)
    result, data = conn.uid("SEARCH", None, *criterios)

    if result != "OK" or not data[0]:
        print(f"[{label}] Nenhum email encontrado  ({filter_label} em '{imap_source}').")
        conn.logout()
        return

    uids  = data[0].split()
    total = len(uids)
    print(f"[{label}] {total} email(s) encontrado(s).")
    log.info(f"[{label}] {total} emails ({' '.join(criterios)} em {imap_source}).")

    processed = moved = alerted = 0

    for idx, uid in enumerate(uids, start=1):
        telegram_progresso(idx, total)
        
        # Checa cancelamento
        if os.path.exists(CANCEL_FILE):
            os.remove(CANCEL_FILE)
            msg_cancelado = (
                f"🛑 *Triagem cancelada pelo usuário*\n"
                f"*Conta:* {label}\n"
                f"*Progresso:* {processed}/{total} emails processados"
            )
            send_alert(msg_cancelado)
            log.warning(f"[{label}] Triagem cancelada pelo usuario em {idx}/{total}.")
            conn.logout()
            return
    
        try:
            result, msg_data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if result != "OK":
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            sender   = decode_header_value(msg.get("From", ""))
            subject  = decode_header_value(msg.get("Subject", "(sem assunto)"))
            date_raw = msg.get("Date", "")
            try:
                date = parsedate_to_datetime(date_raw).strftime("%d/%m/%Y %H:%M")
            except Exception:
                date = date_raw

            body = get_body(msg)

            print(f"  <{idx}/{total}> Classificando: {subject[:65]}")

            classification = classify_email(
                sender=sender,
                subject=subject,
                body=body,
                date=date,
                account_label=label,
            )

            target_folder  = classification["pasta"]
            prioridade     = classification["prioridade"]
            resumo         = classification["resumo"]
            prazo          = classification.get("prazo")
            alerta         = classification.get("alerta_telegram", False)
            dest_key       = next((k for k, v in FOLDERS.items() if v == target_folder), "inbox")
            folder_label   = FOLDER_LABELS.get(dest_key, target_folder)

            # Indicador de movimento
            if target_folder != imap_source:
                arrow = f"  ↪ '{imap_source}'  →  {folder_label}"
            else:
                arrow = "  (sem mudanca de pasta)"

            print(f"  <{idx}/{total}> → {folder_label} (P{prioridade})")
            if folder_key is not None or filter_mode != FILTER_UNREAD:
                print(f"  {arrow}")

            # Move se destino != origem
            if target_folder != imap_source:
                dest = "INBOX" if target_folder == "INBOX" else target_folder
                move_email(conn, uid, dest)
                moved += 1

            log.info(
                f"[{label}] <{idx}/{total}> '{subject[:60]}' "
                f"'{imap_source}' -> '{target_folder}' P{prioridade} prazo={prazo}"
            )

            _log_classified(account_key, {
                "uid": uid.decode(), "sender": sender, "subject": subject,
                "date": date, "source": imap_source, "folder": target_folder,
                "prioridade": prioridade, "resumo": resumo, "prazo": prazo,
                "acao": classification.get("acao_sugerida"), "account": label,
                "filter": filter_mode,
                "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })

            if alerta:
                emoji    = "⚠️" if prioridade == 5 else "📫"
                msg_text = (
                    "══════════════════════\n"
                    f"{emoji} *Alerta — {folder_label}* (P{prioridade})\n"
                    f"*Conta:* {label}\n"
                    f"*De:* {sender[:60]}\n"
                    f"*Recebido em:* {date}\n\n"
                    f"*Assunto:* {subject[:80]}\n\n"
                    f"{resumo}"
                )
                if prazo:
                    msg_text += f"\n*Prazo:* {prazo}"
                try:
                    send_alert(msg_text)
                    alerted += 1
                except Exception as e:
                    log.warning(f"Telegram falhou: {e}")

            processed += 1

        except Exception as e:
            log.error(f"[{label}] Erro uid {uid}: {e}")
            print(f"  <{idx}/{total}> Erro: {e}")

    conn.logout()
    
    msg_conclusao = (
        f"✅ *Triagem concluída — {label}*\n"
        f"📊 {processed}/{total} processados"
        + (f" · {moved} movidos" if moved else "")
        + (f" · {alerted} alertas" if alerted else "")
    )
    send_alert(msg_conclusao)
    print(
        f"\n[{label}] Concluido: {processed}/{total} processados"
        + (f", {moved} movidos" if moved else "")
        + f", {alerted} alertas."
    )
    log.info(f"[{label}] Concluido: {processed}/{total}, movidos={moved}, alertas={alerted}.")


def _log_classified(account_key: str, entry: dict):
    log_file = os.path.join(LOGS_DIR, "classified_today.jsonl")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── PONTO DE ENTRADA ─────────────────────────────────────────────────────────

def run(folder_key: str = None, filter_mode: str = FILTER_UNREAD, months: int = 1):
    folder_display = FOLDER_LABELS.get(folder_key, "📥 Inbox") if folder_key else "📥 Inbox"
    filter_label   = months_label(months) if filter_mode == FILTER_MONTHS else (
                     "todos (ALL)" if filter_mode == FILTER_ALL else "nao lidos (UNSEEN)")

    print(f"\n{'='*55}")
    print(f"  Pasta:  {folder_display}")
    print(f"  Filtro: {filter_label}")
    print(f"  Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    for account_key in ACCOUNTS:
        process_account(account_key, folder_key=folder_key, filter_mode=filter_mode, months=months)

    print(f"\n{'='*55}")
    print(f"  Concluido: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*55}")


if __name__ == "__main__":
    args = sys.argv[1:]

    # ── --list → mostra pastas e encerra ────────────────────────────────────
    if any(a in ("--list", "-l", "list") for a in args):
        list_folders()
        sys.exit(0)

    # ── Sem argumentos → triagem normal (cron) ──────────────────────────────
    if not args:
        run(folder_key=None, filter_mode=FILTER_UNREAD)
        sys.exit(0)

    # ── Parse combinado de pasta + filtro ───────────────────────────────────
    folder_key, filter_mode, months = parse_args(args)

    # Confirma o que vai rodar
    folder_display = FOLDER_LABELS.get(folder_key, "📥 Inbox") if folder_key else "📥 Inbox"
    filter_label   = months_label(months) if filter_mode == FILTER_MONTHS else (
                     "todos (ALL)" if filter_mode == FILTER_ALL else "nao lidos (UNSEEN)")
    print(f"  Pasta  : {folder_display}  ({FOLDERS.get(folder_key, 'INBOX')})")
    print(f"  Filtro : {filter_label}")

    run(folder_key=folder_key, filter_mode=filter_mode, months=months)
