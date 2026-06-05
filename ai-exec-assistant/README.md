# AI Executive Assistant

Outlook triage with **draft-then-approve** replies, plus a **deterministic daily Opportunity Score** for Eagle Eye Innovations. Claude Code builds it; **n8n Cloud runs it** 24/7. See [`CLAUDE.md`](./CLAUDE.md) for architecture and [`n8n-spec/`](./n8n-spec/) for the workflow build specs.

## Quick start

```bash
cp .env.example .env      # fill in real values (replace every [PLACEHOLDER])
npm install
npm run auth              # one-time Microsoft Graph delegated sign-in (local dev)
npm run score             # run the Opportunity Score pipeline against the live APIs
```

## Cost & spend alerts

Verified monthly estimate (per the master prompt):

| Item | Cost |
|---|---|
| n8n Cloud Pro (~10K executions/mo) | ~$50/mo |
| Claude API (Sonnet drafting + Haiku classification) | ~$15–60/mo, volume-dependent |
| Microsoft 365 | already owned |
| Companies House / Contracts Finder / Find a Tender | £0 (free, OGL) |
| Teams via Power Automate | included in M365 |
| OpenTable links | $0 |
| **Total** | **~$65–110/mo** |

**Setting up "notify me every $100" of token spend:** this is configured at the platform level, not in this code. In the **Anthropic Console → Billing → Usage limits / spend alerts**, set a spend threshold and email alert. To keep API cost down within this app: model IDs are centralized in [`src/config/models.json`](./src/config/models.json) (classification runs on the cheaper Haiku tier), the score runs once daily, and the inbox poll is rate-limited by its Schedule Trigger — so execution volume (and therefore cost) is bounded by the n8n schedules, not by ad-hoc calls.
