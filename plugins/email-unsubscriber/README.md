# Gmail Trash Auto-Unsubscriber

A personal/local plugin that scans Gmail Trash once per day, finds unsubscribe candidates, and posts a dry-run report to Slack channel `C0B6Q5G9QQG`.

## Setup

1. Configure Google Workspace auth with Gmail read access:

```bash
python skills/productivity/google-workspace/scripts/setup.py --check
# If not authenticated, follow that setup script's client-secret/auth-code flow.
```

2. Enable the local plugin:

```bash
hermes plugins enable email-unsubscriber
```

3. Enable Slack and run `/sethome` in your email-unsubscriber Slack channel if desired. This feature is configured to deliver directly to `slack:C0B6Q5G9QQG`.

4. Install the daily job:

```bash
hermes email-unsubscriber setup
```

The setup command writes:

- wrapper: `$HERMES_HOME/scripts/email-unsubscriber.py`
- state: `$HERMES_HOME/email-unsubscriber/state.json`
- cron job: `Gmail Trash Auto-Unsubscriber`

## Run a dry-run scan

```bash
hermes email-unsubscriber run
```

The daily cron job posts the same report to Slack. It does not unsubscribe until you confirm.

## Unsubscribe

The Slack report includes a multi-select UI. Select one or more candidates and click **Unsubscribe selected**, or click **Unsubscribe all**.

Thread replies also work:

```text
unsubscribe <candidate-id>
unsubscribe all
```

CLI fallback:

```bash
hermes email-unsubscriber unsubscribe <candidate-id>
hermes email-unsubscriber unsubscribe all
```

Every approved unsubscribe attempt uses the browser-agent. For `unsubscribe all`, Hermes processes approved pending candidates sequentially and starts a fresh isolated browser task for each candidate.

The browser-agent tries to complete only the unsubscribe/opt-out flow. It stops and reports back if the page requires login, CAPTCHA, payment details, sensitive input, unrelated preference changes, or an ambiguous choice. Hermes only opens safe HTTPS unsubscribe URLs; non-HTTPS or unsafe links are left for manual handling.

## Ignore list

```bash
hermes email-unsubscriber ignore add sender news@example.com
hermes email-unsubscriber ignore add domain example.com
hermes email-unsubscriber ignore list
```

## Status

```bash
hermes email-unsubscriber status
```

The status shows seen messages, pending candidates, unsubscribed senders, ignored senders/domains, and the current batch.
