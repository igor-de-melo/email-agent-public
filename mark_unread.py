"""
mark_unread.py — Recoloca o status "nao lido" nos emails classificados pela IA
Nao usa a API LLM. Apenas operacoes IMAP puras.

Modos:
  python3 mark_unread.py           — marca como nao lido tudo nas pastas do sistema
  python3 mark_unread.py inbox     — marca como nao lido tudo que esta no INBOX
"""

import imaplib
import os
import sys
from datetime import datetime

from config import ACCOUNTS, FOLDERS, NATIVE_FOLDERS, LOGS_DIR
from auth_gmail import get_imap_connection as gmail_connect


def select_folder(conn: imaplib.IMAP4_SSL, folder_name: str) -> bool:
    """
    Seleciona uma pasta IMAP. Tenta com aspas e sem aspas.
    Retorna True se conseguiu selecionar.
    """
    # Tenta com aspas primeiro (necessario para pastas com espacos e paths especiais)
    result, data = conn.select(f'"{folder_name}"')
    if result == "OK":
        return True
    # Tenta sem aspas
    result, data = conn.select(folder_name)
    if result == "OK":
        return True

    print(f"  Nao foi possivel abrir: {folder_name} — {data}")
    return False


def mark_folder_unread(conn: imaplib.IMAP4_SSL, folder_name: str) -> int:
    """
    Marca todos os emails lidos de uma pasta como nao lidos.
    Retorna a quantidade de emails afetados.
    """
    if not select_folder(conn, folder_name):
        return 0

    result, data = conn.uid("SEARCH", None, "SEEN")
    if result != "OK" or not data[0]:
        print(f"  {folder_name}: nenhum email lido encontrado.")
        return 0

    uids = data[0].split()
    if not uids:
        return 0

    uid_list = b",".join(uids)
    conn.uid("STORE", uid_list, "-FLAGS", "\\Seen")
    print(f"  {folder_name}: {len(uids)} email(s) marcado(s) como nao lido.")
    return len(uids)


def run_all(account_key: str):
    """Marca como nao lido tudo nas pastas do sistema."""
    account = ACCOUNTS[account_key]
    label   = account["label"]
    print(f"\n[{label}] Conectando...")

    try:
        conn = gmail_connect(account_key) if account["type"] == "gmail" else __import__(
            "auth_outlook").get_imap_connection(account_key)
    except Exception as e:
        print(f"[{label}] Erro de conexao: {e}")
        return

    total = 0
    for key, folder_name in FOLDERS.items():
        total += mark_folder_unread(conn, folder_name)

    conn.logout()
    print(f"[{label}] Total: {total} email(s) restaurados como nao lido.")


def run_inbox(account_key: str):
    """Marca como nao lido tudo que esta no INBOX."""
    account = ACCOUNTS[account_key]
    label   = account["label"]
    print(f"\n[{label}] Conectando...")

    try:
        conn = gmail_connect(account_key) if account["type"] == "gmail" else __import__(
            "auth_outlook").get_imap_connection(account_key)
    except Exception as e:
        print(f"[{label}] Erro de conexao: {e}")
        return

    total = mark_folder_unread(conn, "INBOX")
    conn.logout()
    print(f"[{label}] Total: {total} email(s) restaurados no INBOX.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    print(f"\n{'='*55}")
    print(f"  mark_unread — modo: {mode}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    for account_key in ACCOUNTS:
        if mode == "inbox":
            run_inbox(account_key)
        else:
            run_all(account_key)

    print(f"\nConcluido.")
