# Chatbot layer — live end-to-end test transcripts

All three ran against the real MERIDIAN sample host
(`https://web-sample.interface-hiring.com`) via `POST /chat` on the
capability API (port 8200), which drove the real
`POST /capabilities/<id>/invoke` endpoint internally. Model: `claude-sonnet-5`.
Operator console (port 8100) live for the resume path.

Driver: `evidence/chatbot/chat_cli.py <session_id> "<turn>" ["<turn>" ...]`

---

## 1. Read-only flow — member balance (conversational round trip)

```
>>> USER: What's the balance for member 100234? Sign on with operator teller1,
          password password, branch 'MAIN-001 - Main Office'.
<<< BOT: Here's what I found:
         - Member: Ada Lovelace (ID 100234)
         - Balance: $2,499.00
```

Run: `meridian_member_balance_inquiry_20260827T180446Z_b25ccc` → `status: success`.
No pause (read_only → risk gate skipped).

---

## 2. Mutating flow with inline confirm — open new share

```
>>> USER: Open a new share for member 100234. Operator teller1 / password 'password',
          branch 'MAIN-001 - Main Office'. Share type 'S0001 - Regular Shares',
          initial deposit 50.00.
<<< BOT: Please confirm: I'm about to open a new Regular Shares (S0001) account for
         member 100234 at MAIN-001 - Main Office, with an initial deposit of $50.00,
         processed under operator teller1. Shall I proceed?

>>> USER: yes, go ahead
<<< BOT: Your new share account has been successfully opened for member 100234:
         - Type: Regular Shares (S0001)
         - Initial Deposit: $50.00
         - Confirmation Number: CN480049
```

Run: `meridian_open_new_share_20260827T180611Z_c266b1`.
- `run_capability` paused at the risk gate (`pause_reason: risk_confirmation_required`).
- `/chat` detected the pause, the bot asked for confirmation in plain language.
- On "yes", `/chat` POSTed the real operator console `/resume/<run_id>` endpoint
  (operator console log: `POST /resume/meridian_open_new_share_...Z_c266b1 302`).
- Run resumed in the same live browser, completed → `status: success`,
  `confirmation_number = CN480049`.

---

## 3. Permission-escalation flow — place account hold as a non-supervisor

```
>>> USER: Place an account hold on member 100234, share 100234-MMKT-3, reason code
          'LEGAL - Legal / levy', notes 'court order received'. Operator teller1 /
          password 'password', branch 'MAIN-001 - Main Office'.
<<< BOT: Please confirm you'd like to proceed with the following action:
         - Place an account hold
         - Member: 100234 / Share: 100234-MMKT-3 / Reason: LEGAL - Legal / levy
         - Notes: "court order received" / Operator: teller1
         Shall I go ahead and place this hold?

>>> USER: yes, confirm
<<< BOT: This operation was rejected and could not be completed. The hold placement
         for member 100234, share 100234-MMKT-3 did not go through — it hit a
         checkpoint that requires supervisor authority.

         A supervisor will need to take over this session directly via the operator
         console to proceed. I'm not able to retry this myself.
```

Run: `meridian_place_account_hold_20260827T180944Z_5c9fec`.
- Risk-gate pause → bot asked → user confirmed → `/chat` resumed via operator console.
- Steps 1–11 ran clean. Step 9 (`* Share` combobox) resolved via the new partial
  `_select` match: `value "100234-MMKT-3"` → `resolved_option
  "100234-MMKT-3 - Money Market"` (`match: "partial"` in the run log).
- Step 12 checkpoint failed — `teller1` is not a supervisor, so no "Apply Hold"
  button renders (the host's real permission wall). `on_checkpoint_failure`
  override `retry:0 → escalate` fired.
- `/chat` classified this pause (`pause_reason: on_checkpoint_failure`, not
  `risk_confirmation_required`) as a **reactive escalation**: it reported the
  rejection and did NOT call `/resume`. The run remains
  `status: paused, owner: human` for a supervisor to take over via the console.
  No fabricated resolution.
