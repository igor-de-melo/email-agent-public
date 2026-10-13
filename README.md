# email-agent

A personal email triage agent written in Python. It reads messages from Gmail
over IMAP (OAuth2), asks an Anthropic Claude model to classify each one, moves
the message to a matching folder, and sends alerts and daily/weekly summaries
through a Telegram bot.

```
                 cron / Telegram bot (bot.py)
                            |
                            v
+---------+  IMAP   +-----------------+  sender, subject, body  +---------------+
|  Gmail  | <-----> | agent_triage.py | ----------------------> | Anthropic API |
| (OAuth2)|  move   |                 | <---------------------- | classifier.py |
+---------+         +-----------------+   folder, priority,     +---------------+
                       |           |      summary, deadline
                alerts |           | logs/classified_today.jsonl
                       v           v
                 +----------+   +------------------+   summary   +----------+
                 | Telegram | <-| agent_summary.py | ----------> | Telegram |
                 +----------+   +------------------+             +----------+
```

> [!WARNING]
> **This agent acts on your real mailbox and sends email content to a third party.**
>
> - It **moves emails between folders**, including to **Trash** and **Spam**,
>   based on the model's classification. Misclassifications will happen.
> - It sends the **sender, subject, date and the beginning of the body** of each
>   processed email to the **Anthropic API**. The summary step also sends sender
>   names and subject snippets.
> - Sender, subject and an AI-written summary of flagged emails are sent to
>   **Telegram**.
>
> Try it first on a **test account or a test label** with a handful of messages,
> and review the results before pointing it at an inbox you care about.

## Requirements

- Python 3 <!-- verificar: versão mínima não está declarada no repositório -->
- A Linux/Unix-like host is assumed (the scripts use `/tmp` and the examples use cron)
- A Gmail account with IMAP access enabled
- A Google Cloud project with an OAuth client
- An Anthropic API key
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)) and your Telegram chat ID

## Installation

```bash
git clone <repository-url> /caminho/do/projeto
cd /caminho/do/projeto
python3 -m venv venv
source venv/bin/activate
```

The repository does not ship a `requirements.txt` yet. The code imports the
following third-party packages:

```bash
pip install python-dotenv anthropic python-telegram-bot requests google-auth google-auth-oauthlib
```

<!-- verificar: versões não estão fixadas; bot.py usa a API ApplicationBuilder (python-telegram-bot v20+) -->

### Google OAuth client

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a
   project and configure the OAuth consent screen (add your own account as a
   test user).
   <!-- verificar: se é preciso habilitar a Gmail API no projeto para o acesso IMAP via XOAUTH2 -->
2. Create an **OAuth client ID** of type **Desktop app** and download its JSON file.
3. Save it as:

   ```
   /caminho/do/projeto/credentials/gmail_credentials.json
   ```

4. Run the one-time authorization:

   ```bash
   python3 auth_gmail.py
   ```

   This opens a browser (or prints a URL) for each configured account, requests
   the `https://mail.google.com/` scope (full mailbox access, required for
   IMAP), saves one token file per account under `credentials/`, and creates the
   agent's folders in the mailbox if they do not exist.

## Configuration

### Environment variables

Copy `.env.example` to `.env` in the project root and fill in the values. The
scripts load it with `python-dotenv`.
<!-- verificar: o .env.example sugere ~/.env + `source`; ambos os caminhos devem funcionar, mas só um precisa ser documentado -->

| Variable | Purpose | Required |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Token of the Telegram bot used for alerts, summaries and commands | Yes |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID. Messages are sent to it, and `bot.py` only accepts commands from the user with this ID | Yes |
| `ANTHROPIC_API_KEY` | Anthropic API key used for classification and summaries | Yes |
| `GMAIL_PESSOAL_EMAIL` | Address of the first Gmail account (e.g. `maria@exemplo.com`) | Yes |
| `GMAIL_USP_EMAIL` | Address of the second (institutional) Gmail account | Yes, unless you remove that account from `ACCOUNTS` in `config.py` |
| `HOTMAIL_EMAIL` | Commented out in `.env.example`; not used by the current code | No |
| `AZURE_CLIENT_ID` | Commented out in `.env.example`; not used by the current code | No |

The two Gmail accounts are defined in `ACCOUNTS` in `config.py`. Every script
iterates over all accounts listed there; an account that fails to connect is
reported and skipped.

### User profile

The classifier's system prompt starts with a profile describing who you are and
what matters to you.

```bash
cp profile.example.txt profile.local.txt
```

Edit `profile.local.txt` and replace the `<...>` placeholders. This file is
git-ignored. If it does not exist, the agent falls back to
`profile.example.txt`.

The fixed classification rules live in `PROMPT_FIXO` in `classifier.py`. They
are written in Portuguese and tuned for an aeronautical engineering student;
adapt them to your own context.

### Folders

Folder names are defined in `FOLDERS` in `config.py`:

| Key | IMAP folder |
|---|---|
| `urgente` | `Urgente` |
| `prazos` | `Prazos` |
| `oportunidades` | `Oportunidades` |
| `academia` | `Academico` |
| `networking` | `Networking` |
| `financeiro` | `Financeiro` |
| `para_ler` | `Para Ler` |
| `referencia` | `Referencia` |
| `spam` | `[Gmail]/Spam` |
| `lixo` | `[Gmail]/Lixeira` |
| `inbox` | `INBOX` |

## Usage

### Scripts

| Script | What it does |
|---|---|
| `auth_gmail.py` | One-time OAuth authorization for each account; creates missing folders |
| `agent_triage.py` | Fetches emails, classifies them, moves them, logs the result and sends Telegram alerts |
| `agent_summary.py` | Builds the daily summary or weekly digest from the triage log and sends it to Telegram |
| `bot.py` | Telegram bot (long polling) that runs the triage and summary scripts on command |
| `mark_unread.py` | Marks read emails as unread again; pure IMAP, no AI calls |
| `classifier.py` | Classification logic. Run directly, it classifies three built-in sample emails (this calls the API) |
| `telegram_notify.py` | Telegram send helpers. Run directly, it sends a test message |
| `config.py` | Central configuration: accounts, folders, paths, profile loading |

#### `agent_triage.py`

```bash
python3 agent_triage.py                      # INBOX, unread only (default)
python3 agent_triage.py --all                # INBOX, all emails
python3 agent_triage.py --month              # INBOX, last 30 days
python3 agent_triage.py --months 3           # INBOX, last 3 months (N x 30 days)
python3 agent_triage.py financeiro --month   # a specific folder, last 30 days
python3 agent_triage.py spam --all           # a specific folder, all emails
python3 agent_triage.py --list               # list available folder keys
```

Filters: `--unread` / `-u`, `--all` / `-a`, `--months N` / `--months=N` / `-m N`,
`--month`. With a folder and no filter, the default is all emails in that
folder.

For each email, the model returns a folder, a priority (1–5), a short summary,
an optional deadline, a suggested action and whether to alert. The email is
moved if the target folder differs from the source. Messages are fetched
without being marked as read. During a run, a progress message with a cancel
button is sent to Telegram every 5 emails, and a completion message is sent per
account.

#### `agent_summary.py`

```bash
python3 agent_summary.py daily            # summary of the last 24h, then archives the day's log
python3 agent_summary.py weekly           # digest of the last 7 days
python3 agent_summary.py daily --test     # print only, do not send to Telegram
python3 agent_summary.py daily --keep     # send, but do not archive the log
```

`daily` is the default when no mode is given.

#### `mark_unread.py`

```bash
python3 mark_unread.py          # all folders listed in config.py
python3 mark_unread.py inbox    # INBOX only
```

### Cron example

```cron
# Triage of unread INBOX emails every 15 minutes
*/15 * * * *  /caminho/do/projeto/venv/bin/python3 /caminho/do/projeto/agent_triage.py

# Daily summary at 08:00
0 8 * * *     /caminho/do/projeto/venv/bin/python3 /caminho/do/projeto/agent_summary.py daily

# Weekly digest on Fridays at 18:00
0 18 * * 5    /caminho/do/projeto/venv/bin/python3 /caminho/do/projeto/agent_summary.py weekly
```

### Telegram bot

```bash
python3 bot.py
```

Before running, edit the `PROJECT_DIR` constant at the top of `bot.py` so it
points to your checkout (e.g. `/caminho/do/projeto`). The bot runs the other
scripts with the interpreter at `PROJECT_DIR/venv/bin/python3`.

Commands are ignored unless they come from the user whose ID equals
`TELEGRAM_CHAT_ID`.

| Command | Action |
|---|---|
| `/triar` | Triage of unread INBOX emails |
| `/triar_pasta` | Shows an inline menu of folders to triage |
| `/triar_pasta <key> [flags]` | Triage of a specific folder, e.g. `/triar_pasta financeiro --months 3` |
| `/triar_meses` | Shows an inline menu (1, 2, 3, 6, 12 months) |
| `/triar_meses <N>` | Triage of the last N months of the INBOX |
| `/daily` | Generates and sends the daily summary |
| `/triar_e_daily` | Triage followed by the daily summary |
| `/cancelar` | Signals a running triage to stop |
| `/ajuda` | Lists the commands |

## Security and privacy

**Kept out of Git** (see `.gitignore`):

- `.env` — API keys, bot token, email addresses
- `credentials/` — the OAuth client file and the per-account token files, which
  grant full access to the mailbox
- `profile.local.txt` — your personal profile
- `logs/`, `*.log`, `*.jsonl`

**Logs contain email data.** The agent writes to `logs/` inside the project
directory:

- `logs/triage.log` — includes email subjects
- `logs/summary.log`
- `logs/classified_today.jsonl` — sender, subject, AI summary, deadline and
  target folder of every processed email
- `logs/archive/classified_YYYY-MM-DD.jsonl` — previous days

There is no automatic retention or cleanup of these files. Protect the project
directory accordingly and delete old logs yourself.

**Data sent to third parties:** email content goes to the Anthropic API and
alert/summary text goes to Telegram, as described in the warning above.

**Reporting a vulnerability:** please open an issue in this repository.

## Known limitations

- Folder names are hardcoded and depend on the Gmail account language (the
  Trash folder is `[Gmail]/Lixeira`, as in Portuguese-language accounts).
- Only Gmail is supported. The Hotmail/Outlook integration is not implemented;
  the related configuration is commented out.
- The account list, folder list and classification rules are configured by
  editing the source (`config.py`, `classifier.py`), and the rules are tailored
  to one specific user profile.
- `bot.py` requires editing a hardcoded project path and assumes a Unix-like
  host.
- The bot's inline folder menu is not fully in sync with the folder list in
  `config.py`. <!-- verificar -->
- Triage using a folder whose name contains spaces as the source may fail.
  <!-- verificar -->
- Emails that remain in the INBOX after triage stay unread and are classified
  again on the next default run.
- `CLEANUP_RULES` and `FOLLOWUP_RULES` in `config.py` are declared but not used
  by any script.
- Classification is done by a language model reading untrusted email content;
  treat its decisions as suggestions and review Trash and Spam periodically.
- There are no automated tests, pinned dependencies or packaging yet.

## License

Licença: a definir
