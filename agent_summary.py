"""
agent_summary.py — Resumo diario e digest semanal via Telegram

v3.0 — Display redesenhado:
  - Cards individuais por email (sem agrupamentos artificiais)
  - Sender, assunto, resumo completo, acao e prazo visiveis em cada card
  - Prazos ordenados cronologicamente (futuros vs expirados separados)
  - AI escreve apenas 1 frase de analise estrategica (custo minimo de tokens)
  - Secoes construidas deterministicamente pelo Python

Cron diario:  30 7 * * *   python3 ~/email-agent/agent_summary.py daily
Cron semanal: 0 18 * * 5   python3 ~/email-agent/agent_summary.py weekly

Flags opcionais:
  --test   Imprime o resumo no terminal sem enviar ao Telegram
  --keep   Nao arquiva o log apos o resumo diario
"""

import json
import os
import re
import sys
import logging
import anthropic
from datetime import datetime, timedelta
from collections import defaultdict

from config import (
    AGENT_PROFILE, ANTHROPIC_API_KEY, LOGS_DIR,
    FOLDERS, FOLDER_LABELS,
)
from telegram_notify import send_chunked

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

CLASSIFIED_LOG = os.path.join(LOGS_DIR, "classified_today.jsonl")
ARCHIVE_DIR    = os.path.join(LOGS_DIR, "archive")

os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOGS_DIR, "summary.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# System prompt cacheado — AI so escreve a analise estrategica (1-2 frases)
SUMMARY_SYSTEM = [
    {
        "type": "text",
        "text": (
            AGENT_PROFILE + "\n"
            "Voce e um assistente de analise de email. Escreva analises concisas e diretas em portugues. "
            "Responda SOMENTE com o texto solicitado, sem texto adicional, sem markdown."
        ),
        "cache_control": {"type": "ephemeral"},
    }
]


# ─── LEITURA DO LOG ───────────────────────────────────────────────────────────

def load_classified(hours: int = 24) -> list:
    """
    Carrega emails processados nas ultimas X horas.
    Filtra por 'processed_at'; fallback para 'date' do email.
    """
    if not os.path.exists(CLASSIFIED_LOG):
        return []

    cutoff  = datetime.now() - timedelta(hours=hours)
    entries = []

    with open(CLASSIFIED_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("processed_at", "")

                if ts_str:
                    # processed_at presente: filtra pela hora real de processamento
                    try:
                        ts = datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
                        if ts >= cutoff:
                            entries.append(entry)
                    except Exception:
                        entries.append(entry)  # formato inesperado: inclui por seguranca
                else:
                    # processed_at ausente (entradas antigas sem o campo):
                    # inclui sempre — nao ha como saber quando foi processado
                    entries.append(entry)
            except json.JSONDecodeError:
                continue

    return entries


def load_week_entries() -> list:
    """Carrega logs dos ultimos 7 dias (arquivados + hoje)."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    entries = []

    for i in range(7):
        date_str = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        log_file = os.path.join(ARCHIVE_DIR, f"classified_{date_str}.jsonl")
        if os.path.exists(log_file):
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        entries.append(json.loads(line.strip()))
                    except Exception:
                        continue

    if os.path.exists(CLASSIFIED_LOG):
        with open(CLASSIFIED_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except Exception:
                    continue

    return entries


def archive_log():
    """Move o log do dia para o historico."""
    if not os.path.exists(CLASSIFIED_LOG):
        return
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    dest = os.path.join(ARCHIVE_DIR, f"classified_{datetime.now().strftime('%Y-%m-%d')}.jsonl")
    os.rename(CLASSIFIED_LOG, dest)
    print(f"  Log arquivado: {dest}")
    log.info(f"Log arquivado em {dest}")


# ─── HELPERS DE DISPLAY ───────────────────────────────────────────────────────

def sender_name(raw: str) -> str:
    """
    Extrai o nome legivel do remetente.
      "Embraer" <email@embraer.com>  ->  Embraer
      Prof. Maria <maria@exemplo.com>       ->  Prof. Maria
      email@exemplo.com                   ->  email@exemplo.com
    """
    raw = (raw or "").strip().replace("\r", "").replace("\n", " ")
    m = re.match(r'^"?([^"<\n]+?)"?\s*<[^>]+>$', raw)
    if m:
        return m.group(1).strip().strip('"').strip("'")[:55]
    if "<" in raw:
        return raw.split("<")[0].strip().strip('"')[:55]
    return raw[:55]


def fl(imap_name: str) -> str:
    """Converte nome IMAP para label com emoji."""
    key = next((k for k, v in FOLDERS.items() if v == imap_name), None)
    return FOLDER_LABELS.get(key, imap_name)


def parse_prazo(prazo_str: str):
    """Converte DD/MM/AAAA para datetime; retorna None se invalido."""
    if not prazo_str:
        return None
    try:
        return datetime.strptime(prazo_str.strip(), "%d/%m/%Y")
    except Exception:
        return None


ACAO_ICONS = {
    "responder":  "✉️ responder",
    "inscrever":  "📝 inscrever",
    "follow-up":  "🔔 follow-up",
    "denunciar":  "🚫 denunciar",
    # arquivar e ignorar: nao exibe (nao polui o card)
}


def entry_card(e: dict, number: int = 0, show_folder: bool = False) -> str:
    """
    Formata um email como card legivel para Telegram.

    Exemplo de saida:
      *2.* FAPESP . USP . P4
      📌 [FAPESP] Solicitacao de Assinatura Eletronica
      Aditivo de processo aguardando assinatura no SAGe. Prazo de 60 dias a partir de 02/09.
      ✉️ responder · 📅 12/06/2024
    """
    num    = f"*{number}.* " if number else "- "
    name   = sender_name(e.get("sender", ""))
    acct   = e.get("account", "")
    subj   = (e.get("subject") or "(sem assunto)").strip()[:80]
    resumo = (e.get("resumo") or "").strip()
    prazo  = e.get("prazo") or ""
    acao   = ACAO_ICONS.get(e.get("acao", ""), "")
    prio   = e.get("prioridade", 1)
    folder = fl(e.get("folder", "")) if show_folder else ""

    # Data de recebimento formatada
    raw_date = (e.get("date") or "").strip()
    try:
        dt = datetime.strptime(raw_date[:16], "%Y-%m-%d %H:%M")
        recv_date = dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        recv_date = raw_date[:16] if raw_date else ""

    # Linha 1: remetente · conta · pasta? · prioridade? · data recebida
    h_parts = [f"*{name}*", acct]
    if folder:
        h_parts.append(folder)
    if prio >= 4:
        h_parts.append(f"P{prio}")
    if recv_date:
        h_parts.append(f"_{recv_date}_")
    header = num + " · ".join(p for p in h_parts if p)

    # Linha 2: assunto em italico
    subj_line = f"   📌 _{subj}_"

    # Linha em branco + resumo (mostra se tem conteudo util e e distinto do assunto)
    resumo_show = resumo[:160]
    resumo_line = (
        f"\n   {resumo_show}"
        if resumo_show and len(resumo_show) > 20
           and resumo_show.lower()[:25] not in subj.lower()
        else ""
    )

    # Linha final: acao + prazo
    meta = [x for x in [acao, f"📅 {prazo}" if prazo else ""] if x]
    meta_line = f"   {' · '.join(meta)}" if meta else ""

    lines = [header, subj_line]
    if resumo_line:
        lines.append(resumo_line)
    if meta_line:
        lines.append(meta_line)

    return "\n".join(lines)


def build_section(emoji: str, title: str, items: list,
                  max_show: int = 6, empty_msg: str = "Nenhum") -> str:
    """
    Monta uma secao com cabecalho, cards individuais e indicador de overflow.
    Se len(items) > max_show: mostra os primeiros e adiciona "...e mais N".
    """
    SEP = "─" * 25

    if not items:
        return f"{SEP}\n{emoji} *{title}* (0)\n{empty_msg}"

    show  = items[:max_show]
    extra = len(items) - max_show

    header = f"{SEP}\n{emoji} *{title}* ({len(items)})\n"
    cards  = "\n\n".join(entry_card(e, i + 1) for i, e in enumerate(show))
    suffix = f"\n_...e mais {extra} na pasta_" if extra > 0 else ""

    return header + cards + suffix


def volume_block(entries: list) -> str:
    """Bloco de volume por pasta com barra visual."""
    vol = defaultdict(int)
    for e in entries:
        vol[e.get("folder", "INBOX")] += 1

    lines = []
    for pasta, n in sorted(vol.items(), key=lambda x: -x[1]):
        lines.append(f"  {fl(pasta)}: {n}")
    return "\n".join(lines)


# ─── CHAMADA A API ────────────────────────────────────────────────────────────

def call_api(prompt: str, max_tokens: int = 100) -> str:
    """Chamada a API com system prompt cacheado."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=SUMMARY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = response.usage
    if hasattr(usage, "cache_read_input_tokens") and usage.cache_read_input_tokens:
        log.info(
            f"Cache hit={usage.cache_read_input_tokens}t "
            f"miss={usage.cache_creation_input_tokens or 0}t "
            f"in={usage.input_tokens}t out={usage.output_tokens}t"
        )
    return response.content[0].text.strip()


def ai_analysis_daily(entries: list, urgentes: list,
                      prazos_futuros: list, oportunidades: list) -> str:
    """
    Pede ao AI 1 frase de analise estrategica do dia.
    Custo minimo: AI recebe apenas numeros e nomes, nao emails completos.
    """
    top_urg  = "; ".join(
        f"{sender_name(e['sender'])} ({(e.get('subject') or '')[:30]})"
        for e in urgentes[:2]
    )
    top_opor = "; ".join(sender_name(e["sender"]) for e in oportunidades[:3])
    top_praz = "; ".join(
        f"{e.get('prazo')} ({(e.get('subject') or '')[:25]})"
        for e in prazos_futuros[:3] if e.get("prazo")
    )

    prompt = (
        f"Hoje: {len(entries)} emails processados.\n"
        f"Urgentes ({len(urgentes)}): {top_urg or 'nenhum'}\n"
        f"Oportunidades ({len(oportunidades)}): {top_opor or 'nenhuma'}\n"
        f"Prazos proximos: {top_praz or 'nenhum'}\n\n"
        "Escreva 1 frase direta (max 25 palavras) sobre o que e mais importante agir hoje."
    )
    try:
        return call_api(prompt, max_tokens=80)
    except Exception as exc:
        log.warning(f"ai_analysis_daily falhou: {exc}")
        return ""


def ai_analysis_weekly(entries: list, oportunidades: list,
                       networking: list, tecnicos: list, spam_det: list) -> str:
    """Analise estrategica da semana em 2 frases."""
    oport_names = "; ".join(sender_name(e["sender"]) for e in oportunidades[:5])

    prompt = (
        f"Semana: {len(entries)} emails totais.\n"
        f"Oportunidades ({len(oportunidades)}): {oport_names or 'nenhuma'}\n"
        f"Networking: {len(networking)} | Tecnico: {len(tecnicos)} | Spam: {len(spam_det)}\n\n"
        "Escreva 2 frases sobre os padroes da semana e o que exige follow-up. Max 50 palavras."
    )
    try:
        return call_api(prompt, max_tokens=120)
    except Exception as exc:
        log.warning(f"ai_analysis_weekly falhou: {exc}")
        return ""


# ─── RESUMO DIARIO ────────────────────────────────────────────────────────────

def build_daily_summary(entries: list) -> str:
    """
    Constroi o resumo diario com cards individuais por email.

    Arquitetura hibrida:
      Python  -> segmenta, ordena e formata cada email (sender, assunto, resumo, acao, prazo)
      AI      -> escreve 1 frase de analise estrategica no topo (max 80 tokens, custo minimo)

    Prazos: separados entre futuros (ordenados por data) e expirados.
    Academico: so exibe emails com P >= 3 para nao poluir.
    Overflow: secoes com mais de N emails mostram "...e mais N na pasta".
    """
    today = datetime.now().strftime("%d/%m/%Y")

    if not entries:
        return f"📬 *Resumo do dia — {today}*\n\nNenhum email processado nas ultimas 24h."

    # ── Segmentar ────────────────────────────────────────────────────────────
    urgentes      = [e for e in entries if e.get("folder") == "Urgente"]
    oportunidades = [e for e in entries if e.get("folder") == "Oportunidades"]
    networking    = [e for e in entries if e.get("folder") == "Networking"]
    financeiro    = [e for e in entries if e.get("folder") == "Financeiro"]
    academico     = [e for e in entries if e.get("folder") == "Academico"
                     and e.get("prioridade", 1) >= 3]

    # Prazos com data explicita, ordenados cronologicamente
    now = datetime.now()
    prazos_com_data = [e for e in entries if e.get("prazo")]
    prazos_com_data.sort(key=lambda x: parse_prazo(x["prazo"]) or datetime.max)
    prazos_futuros   = [e for e in prazos_com_data
                        if (parse_prazo(e["prazo"]) or now) >= now]
    prazos_expirados = [e for e in prazos_com_data
                        if (parse_prazo(e["prazo"]) or now) < now]

    # Contas
    acc = defaultdict(int)
    for e in entries:
        acc[e.get("account", "?")] += 1
    acc_line = " | ".join(f"{a}: {n}" for a, n in sorted(acc.items()))

    # ── Analise AI (1 frase curta) ───────────────────────────────────────────
    analise = ai_analysis_daily(entries, urgentes, prazos_futuros, oportunidades)

    # ── Montar output ────────────────────────────────────────────────────────
    SEP = "─" * 25
    parts = [f"🌞 *Resumo do dia — {today}*"]

    if analise:
        parts.append(f"💡 _{analise}_")

    parts.append(build_section("🔴", "URGENTE — AÇÃO IMEDIATA",
                               urgentes, max_show=5))
    parts.append(build_section("⏰", "PRAZOS PRÓXIMOS",
                               prazos_futuros, max_show=8))

    if prazos_expirados:
        parts.append(build_section("🗓️", "PRAZOS EXPIRADOS (verificar)",
                                   prazos_expirados, max_show=3))

    parts.append(build_section("🚀", "OPORTUNIDADES",
                               oportunidades, max_show=6))
    parts.append(build_section("🤝", "NETWORKING",
                               networking, max_show=4))
    parts.append(build_section("💸", "FINANCEIRO",
                               financeiro, max_show=4))

    if academico:
        parts.append(build_section("🎓", "ACADEMICO (P≥3)",
                                   academico, max_show=4))

    parts.append(
        f"{SEP}\n📊 *VOLUME*\n{volume_block(entries)}\n"
        f"*Total: {len(entries)}* · {acc_line}"
    )

    return "\n\n".join(parts)


# ─── DIGEST SEMANAL ───────────────────────────────────────────────────────────

def build_weekly_digest(entries: list) -> str:
    """Digest semanal com cards individuais. AI escreve 2 frases de analise."""
    week_num = datetime.now().isocalendar()[1]

    if not entries:
        return (f"📡 *Digest Semanal — Semana {week_num}*\n\n"
                "Nenhum email registrado esta semana.")

    oportunidades = [e for e in entries if e.get("folder") == "Oportunidades"]
    networking    = [e for e in entries if e.get("folder") == "Networking"]
    tecnicos      = [e for e in entries if e.get("folder") == "Para Ler"]
    financeiro    = [e for e in entries if e.get("folder") == "Financeiro"]
    spam_det      = [e for e in entries if e.get("folder") == "[Gmail]/Spam"]

    prazos = [e for e in entries if e.get("prazo")]
    prazos.sort(key=lambda x: parse_prazo(x["prazo"]) or datetime.max)
    prazos_futuros = [e for e in prazos
                      if (parse_prazo(e["prazo"]) or datetime.now()) >= datetime.now()]

    analise = ai_analysis_weekly(entries, oportunidades, networking, tecnicos, spam_det)

    acc = defaultdict(int)
    for e in entries:
        acc[e.get("account", "?")] += 1
    acc_line = " | ".join(f"{a}: {n}" for a, n in sorted(acc.items()))

    SEP = "─" * 25
    parts = [f"📡 *Digest Semanal — Semana {week_num}*"]

    if analise:
        parts.append(f"💡 _{analise}_")

    parts.append(build_section("🚀", "OPORTUNIDADES DA SEMANA",
                               oportunidades, max_show=8))
    parts.append(build_section("🤝", "NETWORKING",
                               networking, max_show=5))
    parts.append(build_section("📚", "CONTEUDO TECNICO RELEVANTE",
                               tecnicos, max_show=5))
    parts.append(build_section("💸", "FINANCEIRO",
                               financeiro, max_show=5))
    parts.append(build_section("⏰", "PRAZOS DA SEMANA",
                               prazos_futuros, max_show=6))

    if spam_det:
        parts.append(build_section("🚫", "SPAM DETECTADO",
                                   spam_det, max_show=3))

    parts.append(
        f"{SEP}\n📈 *ATIVIDADE DA SEMANA*\n{volume_block(entries)}\n"
        f"*Total: {len(entries)}* · {acc_line}\n"
        f"Opor: {len(oportunidades)} | Net: {len(networking)} | "
        f"Tecnico: {len(tecnicos)} | Spam: {len(spam_det)}"
    )

    return "\n\n".join(parts)


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def run_daily(test: bool = False, keep: bool = False):
    print(f"\nResumo diario — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    entries = load_classified(hours=24)
    print(f"  {len(entries)} emails nas ultimas 24h.")
    log.info(f"Daily: {len(entries)} entradas.")

    summary = build_daily_summary(entries)

    print("\n" + "─" * 50)
    print(summary)
    print("─" * 50 + "\n")

    if not test:
        ok = send_chunked(summary)
        print("Enviado ao Telegram." if ok else "Falha no envio.")
        log.info(f"Telegram daily: {'ok' if ok else 'falha'}")
        if not keep:
            archive_log()
    else:
        print("[--test] Nao enviado ao Telegram. Log mantido.")


def run_weekly(test: bool = False):
    print(f"\nDigest semanal — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    entries = load_week_entries()
    print(f"  {len(entries)} emails esta semana.")
    log.info(f"Weekly: {len(entries)} entradas.")

    digest = build_weekly_digest(entries)

    print("\n" + "─" * 50)
    print(digest)
    print("─" * 50 + "\n")

    if not test:
        ok = send_chunked(digest)
        print("Enviado ao Telegram." if ok else "Falha no envio.")
        log.info(f"Telegram weekly: {'ok' if ok else 'falha'}")
    else:
        print("[--test] Nao enviado ao Telegram.")


if __name__ == "__main__":
    args = set(sys.argv[1:])
    mode = "weekly" if "weekly" in args else "daily"
    test = "--test" in args
    keep = "--keep" in args

    if mode == "weekly":
        run_weekly(test=test)
    else:
        run_daily(test=test, keep=keep)
