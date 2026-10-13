"""
classifier.py — Classificacao inteligente de emails via Claude API
v2.1 — Prompt Caching ativado: economia de ~58% no custo de tokens

─── COMO O CACHING FUNCIONA ──────────────────────────────────────────────────

  Sem cache (antes):
    Cada chamada envia: AGENT_PROFILE (566 tokens) + PROMPT_FIXO (1023 tokens)
    + corpo do email (~250 tokens) = ~1839 tokens de INPUT por email.

  Com cache (agora):
    1a chamada do lote: envia tudo normalmente e popula o cache no servidor.
    Chamadas seguintes: AGENT_PROFILE e PROMPT_FIXO sao lidos do cache
    a $0.30/1M (vs $3.00/1M normal). So o corpo do email e cobrado por completo.

  Cache dura 5 minutos de inatividade. Como o agente processa um lote
  de emails em sequencia rapida, o cache permanece quente durante todo o lote.
  A cada nova execucao do cron (15 min), o primeiro email repopula o cache.

  Economia por lote de 100 emails: ~58% no custo de input tokens.

  Requisito de tamanho: blocos cacheados precisam ter >= 1024 tokens.
  AGENT_PROFILE (566) + PROMPT_FIXO (1023) = 1589 tokens — dentro do limite
  quando combinados no system prompt.

─────────────────────────────────────────────────────────────────────────────
"""

import json
import re
import anthropic
from config import AGENT_PROFILE, FOLDERS, ANTHROPIC_API_KEY

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── PROMPT FIXO ──────────────────────────────────────────────────────────────
# Esta parte nunca muda entre emails — e o candidato perfeito para cache.
# Fica separada do cabecalho do email (que e variavel) para que o ponto de
# cache seja aplicado logo apos esta secao.

PROMPT_FIXO = """
Classifique o email que sera fornecido a seguir. Responda SOMENTE com um objeto JSON valido, sem texto adicional, sem markdown.

{
  "pasta": "<nome exato de uma das pastas abaixo>",
  "prioridade": <numero inteiro de 1 a 5>,
  "resumo": "<descricao em ate 2 linhas>",
  "prazo": "<data no formato DD/MM/AAAA se houver prazo explicito, ou null>",
  "acao_sugerida": "<responder|arquivar|ignorar|follow-up|denunciar>",
  "alerta_telegram": <true ou false>
}

== PASTAS DISPONIVEIS (use o nome exato):

  -- ACAO IMEDIATA --
 * Urgente        — requer acao imediata: entrevistas, prazos iminentes,espostas de candidatura, professores sobre TCC ou notas. Qualquer email de empresa aeronautica confirmando entrevista ou solicitando documentos vai AQUI, independente do assunto.

 * Prazos         — inscricoes, deadlines, eventos futuros com data explicita e que NAO requerem acao antes de 48h.

  -- CARREIRA / ESTUDO --
 * Oportunidades  — vagas, estagios, bolsas, programas, editais, congressos e cursos relevantes para engenharia aeronautica.

 * Academico      — emails de universidade, professores, sistemas academicos (Moodle, Portal do Aluno), colegas sobre projetos/trabalhos.

 * Networking     — LinkedIn, contatos profissionais, convites de eventos, follow-ups de networking, recomendacoes.

  -- FINANCEIRO --
 * Financeiro     — boletos, faturas de cartao, extratos bancarios, notas fiscais, cobranças, confirmacoes de pagamento, recibos de transacao. Qualquer email sobre DINHEIRO ou DOCUMENTO FISCAL vai AQUI.

  -- LEITURA / ARQUIVO --
 * Para Ler       — newsletters tecnicas, artigos, digests e conteudo editorial relevante que pode ser lido depois, sem urgencia.

 * Referencia     — confirmacoes de cadastro, termos de uso, manuais, guias, comprovantes NAO-financeiros para consultar depois.

  -- DESCARTE --
 * [Gmail]/Spam   — SOMENTE emails com intenção maliciosa ou fraudulenta. Spam NAO e simplesmente um email irrelevante — isso e Lixeira. Spam e um email que tenta enganar, roubar ou abusar.

 * [Gmail]/Lixeira — emails legitimos mas indesejados: promocoes de lojas, cupons, notificacoes de apps, marketing normal de empresas conhecidas, newsletters que nao interessam.

 * INBOX          — use APENAS se absolutamente impossivel classificar.

== ESCALA DE PRIORIDADE
  5 — Critico: empresa aeronautica respondeu | prazo<48h | phishing
  4 — Alto: entrevista | prazo<7d | boleto vencendo | professor importante | boleto vencendo
  3 — Medio: oportunidade nova | networking | fatura
  2 — Baixo: conteudo util sem urgencia | confirmacoes | referencias
  1 — Informativo: newsletter, notificacao automatica, spam identificado

== ALERTA TELEGRAM: true se P>=4 ou prazo<=7d ou phishing. Senao false.

== CLASSIFICACAO DE SPAM — protocolo em 3 etapas ===

  PRINCIPIO BASE: Spam = intencao maliciosa ou fraudulenta.
  Email irrelevante, chato ou de remetente desconhecido NAO e Spam — e Lixeira.
  Em caso de duvida entre Spam e Lixeira, escolha SEMPRE Lixeira.

  ETAPA 1 — PROTECAO ABSOLUTA (avaliar ANTES de qualquer outra coisa)
  Se o assunto OU o corpo do email contiver QUALQUER item abaixo,
  o email NUNCA e Spam. Classifique na pasta correta e encerre:

    CONTEUDO TECNICO:
    • Engenharia, fisica, matematica, simulacao, CFD, CAD, FEA, MATLAB
    • Aeronautica, aeroespacial, aviacao, propulsao, aerodinamica, UAV
    • Software, programacao, embarcados, IoT, automacao, industria 4.0
    • Dados reais, machine learning, IA aplicada, manufatura, prototipo
    • Hardware, eletronica, sistemas de controle, baterias, sensores

    CONTEUDO ACADEMICO / CARREIRA:
    • Universidade, faculdade, professor, disciplina, laboratorio, TCC
    • Estagio, trainee, vaga, bolsa, edital, congresso, workshop, curso
    • Harvard, MIT, USP, UNICAMP, ITA, INPE — mesmo em email de marketing
    • Qualquer empresa aeronautica ou de engenharia como remetente

    REMETENTES CONHECIDOS (independente do conteudo):
    • Plataformas de newsletter: Substack, Mailchimp, SendGrid, HubSpot,
      ConvertKit, Beehiiv, Ghost, Revue, Campaign Monitor, Brevo
      → Sao ferramentas de envio usadas por publicacoes legitimas.
      Um dominio @substack.com ou @mailchimp.com NAO e sinal de fraude.
    • Plataformas educacionais: Coursera, edX, Udemy, LinkedIn Learning,
      Khan Academy, Alura, FIAP, Hotmart (cursos tecnicos)
    • Redes profissionais: LinkedIn, GitHub, Stack Overflow, Kaggle

  ETAPA 2 — SINAIS DE FRAUDE (so avaliar se Etapa 1 nao protegeu)
  Spam requer PELO MENOS 2 sinais SIMULTANEOS de intencao maliciosa.
  Cada sinal vale 1 ponto. Pontue antes de decidir:

    [A] +1 pt — Dominio do remetente visivelmente falsificado ou gerado:
        ex: @xn--abc.tk, @secure-verify-mail.net, @prize-claim-now.com
        NAO pontua: dominios de newsletter legitimos (mailchimp, sendgrid,
        hubspot, substack), empresas reais com dominio proprio,
        dominios .edu, .gov, .br de instituicoes conhecidas.

    [B] +1 pt — Promessa financeira irreal OU urgencia fabricada no corpo:
        "voce ganhou R$", "seu CPF foi sorteado", "heranca disponivel",
        "transfira agora", "conta bloqueada em 24h", "resgate seu premio"
        NAO pontua: desconto de loja conhecida, oferta de curso pago,
        fatura real de servico que o usuario usa.

    [C] +1 pt — Solicitacao explicita de senha, dados bancarios ou
        numero de cartao no corpo do email (nao em formulario seguro).

    [D] +1 pt — Link disfarçado (texto exibe dominio diferente do href)
        OU anexo executavel (.exe, .bat, .scr, .zip protegido por senha).

    [E] +1 pt — Email em idioma completamente aleatorio (cirílico, arabicoNo
        contexto de email nao solicitado) SEM nenhuma relacao com o perfil.

    ATENCAO — NAO sao sinais de fraude e NAO pontuam:
    • Assunto em MAIUSCULAS ou com muitos "!!!" → e marketing agressivo,
      nao fraude. Um curso real pode ter assunto "GRATUITO E ONLINE!!!".
    • Email de plataforma desconhecida sobre topico tecnico → Para Ler.
    • Email promocional de empresa que o usuario pode ter se cadastrado.
    • Newsletter sem opcao de descadastro visivel → chato, mas e Lixeira.

  ETAPA 3 — DECISAO FINAL
    Etapa 1 protegeu o email       →  pasta adequada (NUNCA Spam)
    Pontuacao Etapa 2 >= 2 pontos  →  [Gmail]/Spam
    Pontuacao Etapa 2 == 1 ponto   →  [Gmail]/Lixeira
    Pontuacao Etapa 2 == 0 pontos  →  [Gmail]/Lixeira  (ou outra pasta relevante)
    Duvida entre Spam e Lixeira    →  SEMPRE Lixeira (preferir falso negativo)
"""

# Template minimo — so o cabecalho varia por email (tokens variaveis)
EMAIL_TEMPLATE = """== EMAIL RECEBIDO ==
De:      {sender}
Para:    {account_label}
Assunto: {subject}
Data:    {date}
Corpo:
{body}"""


def classify_email(
    sender: str,
    subject: str,
    body: str,
    date: str,
    account_label: str,
) -> dict:
    """
    Classifica um email usando a Claude API com Prompt Caching.

    Estrutura da chamada:
      system  → [AGENT_PROFILE + PROMPT_FIXO]  cache_control: ephemeral
                 Cacheado apos a 1a chamada. Relido a 10% do custo nas seguintes.

      messages → [EMAIL_TEMPLATE com dados do email]
                 Variavel — nunca cacheado, cobrado normalmente.

    Retorna dict com: pasta, prioridade, resumo, prazo, acao_sugerida, alerta_telegram.
    """
    email_block = EMAIL_TEMPLATE.format(
        sender=sender,
        account_label=account_label,
        subject=subject,
        date=date,
        body=body[:1500],
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=[
            {
                "type": "text",
                "text": AGENT_PROFILE + "\n\n" + PROMPT_FIXO,
                "cache_control": {"type": "ephemeral"},
                # ^ Marca este bloco para cache. Na 1a chamada e processado
                #   normalmente e armazenado. Nas seguintes, e relido do cache
                #   a $0.30/1M em vez de $3.00/1M (90% de desconto).
            }
        ],
        messages=[
            {"role": "user", "content": email_block}
        ],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [classifier] JSON invalido, usando fallback. Raw: {raw[:200]}")
        result = {
            "pasta":           "INBOX",
            "prioridade":      2,
            "resumo":          f"Classificacao falhou — revisar manualmente. Assunto: {subject}",
            "prazo":           None,
            "acao_sugerida":   "arquivar",
            "alerta_telegram": False,
        }

    # Valida pasta retornada
    valid_folders = set(FOLDERS.values())
    if result.get("pasta") not in valid_folders:
        print(f"  [classifier] Pasta invalida '{result.get('pasta')}', usando INBOX.")
        result["pasta"] = "INBOX"

    # Spam nao gera alerta no Telegram (exceto phishing P5)
    if result.get("pasta") == "[Gmail]/Spam" and result.get("prioridade", 1) < 5:
        result["alerta_telegram"] = False

    # Log de uso de cache (visivel em modo verbose)
    usage = response.usage
    if hasattr(usage, "cache_read_input_tokens") and usage.cache_read_input_tokens:
        print(
            f"  [cache] hit={usage.cache_read_input_tokens}tok  "
            f"miss={usage.cache_creation_input_tokens or 0}tok  "
            f"email={usage.input_tokens}tok"
        )

    return result


def classify_batch(emails: list) -> list:
    """
    Classifica uma lista de emails.
    O cache e populado na 1a chamada e reaproveitado nas seguintes.
    """
    results = []
    for i, email in enumerate(emails):
        print(f"  Classificando {i+1}/{len(emails)}: {email.get('subject', '')[:60]}")
        try:
            classification = classify_email(
                sender=email.get("sender", ""),
                subject=email.get("subject", ""),
                body=email.get("body", ""),
                date=email.get("date", ""),
                account_label=email.get("account_label", ""),
            )
            email["classification"] = classification
        except Exception as e:
            print(f"  [classifier] Erro ao classificar: {e}")
            email["classification"] = {
                "pasta":           "INBOX",
                "prioridade":      2,
                "resumo":          f"Erro na classificacao: {str(e)[:100]}",
                "prazo":           None,
                "acao_sugerida":   "arquivar",
                "alerta_telegram": False,
            }
        results.append(email)
    return results


if __name__ == "__main__":
    """
    Teste com 3 emails em sequencia para visualizar o comportamento do cache.
    1o email: cache MISS (popula o cache) — custo normal
    2o e 3o:  cache HIT  (le do cache)   — 90% desconto nos tokens fixos
    """
    print("Testando classifier v2.1 (prompt caching)...\n")

    test_emails = [
        {
            "sender":        "recrutamento@airbus.com",
            "subject":       "Internship Application — Aerodynamics CFD — Next Steps",
            "body":          "Dear Igor, we would like to invite you to a technical interview "
                             "on March 20th. Please confirm your availability by March 15th.",
            "date":          "2026-03-08",
            "account_label": "Gmail Pessoal",
        },
        {
            "sender":        "noreply@bancoxyz-oferta.tk",
            "subject":       "VOCE FOI SELECIONADO!!! CLIQUE AGORA E RESGATE SEU PREMIO",
            "body":          "Parabens! Seu CPF foi sorteado. Informe seus dados bancarios.",
            "date":          "2026-03-08",
            "account_label": "Gmail Pessoal",
        },
        {
            "sender":        "financeiro@nubank.com.br",
            "subject":       "Fatura do seu cartao Nubank — vencimento 15/03",
            "body":          "Sua fatura no valor de R$ 847,50 vence em 15/03/2026.",
            "date":          "2026-03-08",
            "account_label": "Gmail Pessoal",
        },
    ]

    for i, test in enumerate(test_emails, 1):
        label = "MISS (popula cache)" if i == 1 else "HIT  (le do cache) "
        print(f"=== TESTE {i} [{label}]: {test['subject'][:55]} ===")
        result = classify_email(**test)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print()
