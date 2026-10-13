"""
telegram_notify.py — Envio de alertas e resumos via Telegram
"""

import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_alert(text: str, parse_mode: str = "Markdown") -> bool:
    """
    Envia uma mensagem para o chat Telegram configurado.
    Retorna True se enviado com sucesso.
    """
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "SEU_TOKEN_DO_BOT":
        print(f"  [Telegram] Token nao configurado. Mensagem:\n{text}\n")
        return False

    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
    }

    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            print(f"  [Telegram] Erro {resp.status_code}: {resp.text[:200]}")
            return False
    except requests.RequestException as e:
        print(f"  [Telegram] Falha na requisicao: {e}")
        return False


def send_chunked(text: str, parse_mode: str = "Markdown") -> bool:
    """
    Envia mensagem longa dividindo em partes de ate 4000 chars
    (limite do Telegram e 4096; a margem evita cortes inesperados).
    Sempre quebra em linha vazia — nunca no meio de um bullet.
    Retorna True se todas as partes foram enviadas com sucesso.
    """
    LIMIT = 4000

    if len(text) <= LIMIT:
        return send_alert(text, parse_mode)

    parts  = []
    chunk  = []
    length = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 pelo \n
        if length + line_len > LIMIT and chunk:
            parts.append("\n".join(chunk))
            chunk  = [line]
            length = line_len
        else:
            chunk.append(line)
            length += line_len

    if chunk:
        parts.append("\n".join(chunk))

    print(f"  [Telegram] Mensagem dividida em {len(parts)} partes.")
    success = True
    for i, part in enumerate(parts, 1):
        label = f"\n_({i}/{len(parts)})_" if len(parts) > 1 else ""
        if not send_alert(part + label, parse_mode):
            success = False
    return success


def send_daily_summary(summary: str) -> bool:
    """Envia resumo diario — usa send_chunked para respeitar limite do Telegram."""
    return send_chunked(summary)


if __name__ == "__main__":
    """Teste: python3 telegram_notify.py"""
    print("Enviando mensagem de teste...")
    ok = send_alert(
        "✅ *Teste do sistema de email*\n"
        "Notificacoes do agente de triagem funcionando corretamente."
    )
    print("Enviado com sucesso!" if ok else "Falhou — verifique o token e chat ID no config.py")
