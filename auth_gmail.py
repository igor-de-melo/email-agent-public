"""
auth_gmail.py — Autenticação OAuth2 para contas Gmail
Suporta múltiplas contas com tokens separados.
"""

import os
import imaplib
import smtplib
import base64
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from config import GMAIL_CREDENTIALS, ACCOUNTS

# Escopos necessários: leitura, escrita e envio de email
SCOPES = [
    "https://mail.google.com/",
]


def get_credentials(account_key: str) -> Credentials:
    """
    Obtém (ou renova) credenciais OAuth2 para uma conta Gmail.
    Na primeira execução abre o navegador para autorização.
    Nas execuções seguintes usa o token salvo.
    """
    account = ACCOUNTS[account_key]
    token_file = account["token_file"]
    creds = None

    # Carrega token existente se disponível
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    # Se não há token válido, autentica ou renova
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print(f"[{account['label']}] Renovando token...")
            creds.refresh(Request())
        else:
            print(f"\n[{account['label']}] Primeira autenticação.")
            print(f"Faça login com a conta: {account['email']}")
            print("O navegador será aberto. Se estiver na VM sem interface,")
            print("copie a URL e abra no seu navegador principal.\n")

            flow = InstalledAppFlow.from_client_secrets_file(
                GMAIL_CREDENTIALS, SCOPES
            )
            # run_console permite autenticar via URL quando não há browser na VM
            creds = flow.run_local_server(
                port=0,
                open_browser=True,
                authorization_prompt_message="Acesse: {url}",
                success_message="Autenticação concluída. Pode fechar esta aba."
            )

        # Salva o token para uso futuro
        os.makedirs(os.path.dirname(token_file), exist_ok=True)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
        print(f"[{account['label']}] Token salvo em {token_file}")

    return creds


def get_imap_connection(account_key: str) -> imaplib.IMAP4_SSL:
    """
    Retorna uma conexão IMAP autenticada via OAuth2 para a conta Gmail.
    """
    account = ACCOUNTS[account_key]
    creds = get_credentials(account_key)

    # Monta a string de autenticação XOAUTH2
    auth_string = f"user={account['email']}\x01auth=Bearer {creds.token}\x01\x01"

    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    conn.authenticate("XOAUTH2", lambda x: auth_string.encode())

    print(f"[{account['label']}] Conectado ao IMAP.")
    return conn


def get_smtp_connection(account_key: str) -> smtplib.SMTP:
    """
    Retorna uma conexão SMTP autenticada via OAuth2 para envio.
    """
    account = ACCOUNTS[account_key]
    creds = get_credentials(account_key)

    auth_string = f"user={account['email']}\x01auth=Bearer {creds.token}\x01\x01"

    conn = smtplib.SMTP("smtp.gmail.com", 587)
    conn.ehlo()
    conn.starttls()
    conn.docmd("AUTH", "XOAUTH2 " + __import__("base64").b64encode(
        auth_string.encode()).decode())

    print(f"[{account['label']}] Conectado ao SMTP.")
    return conn


def ensure_folders_exist(imap_conn: imaplib.IMAP4_SSL, folders: dict):
    """
    Cria as pastas IMAP se ainda não existirem.
    - Pula pastas nativas (INBOX, Lixeira) — já existem no servidor
    - Nomes com espaços ou emojis são colocados entre aspas (protocolo IMAP)
    - Usa UTF-8 para suporte a emojis (Gmail suporta nativamente)
    """
    from config import NATIVE_FOLDERS

    raw_list = imap_conn.list()[1]
    existing = []
    for f in raw_list:
        if f:
            try:
                decoded = f.decode("utf-8")
            except UnicodeDecodeError:
                decoded = f.decode("ascii", errors="replace")
            parts = decoded.split('"/"')
            name = parts[-1].strip().strip('"') if len(parts) > 1 else decoded.split()[-1].strip('"')
            existing.append(name)

    for key, folder_name in folders.items():
        # Pula pastas nativas — já existem no servidor
        if key in NATIVE_FOLDERS:
            print(f"  Pasta nativa (ignorada): {folder_name}")
            continue

        # Aspas obrigatórias para nomes com espaços ou emojis
        imap_name = f'"{folder_name}"'

        if folder_name not in existing:
            imap_conn.create(imap_name)
            print(f"  Pasta criada: {folder_name}")
        else:
            print(f"  Pasta OK: {folder_name}")


if __name__ == "__main__":
    """
    Roda autenticação inicial para todas as contas Gmail.
    Execute uma vez na VM: python auth_gmail.py
    """
    from config import FOLDERS

    gmail_accounts = [k for k, v in ACCOUNTS.items() if v["type"] == "gmail"]

    for account_key in gmail_accounts:
        print(f"\n{'='*50}")
        print(f"Autenticando: {ACCOUNTS[account_key]['label']}")
        print(f"{'='*50}")
        try:
            conn = get_imap_connection(account_key)
            print("Verificando/criando pastas...")
            ensure_folders_exist(conn, FOLDERS)
            conn.logout()
            print(f"✓ {ACCOUNTS[account_key]['label']} configurado com sucesso.")
        except Exception as e:
            print(f"✗ Erro: {e}")
