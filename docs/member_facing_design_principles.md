# Member-Facing Design Principles — ArchWell Health

**Audience:** Contact center, digital, integration, and ops teams building anything a **member** sees, hears, or interacts with (IVR, voice bot, portal, SMS, agent scripts tied to self-service).

**Status:** Working draft — reference for COO review (9/1/2026) and post-review build work.

---

## Who we serve

ArchWell Health serves **Medicare Advantage seniors exclusively**. Our members are not a segment of the population — they **are** the population for every outward-facing decision.

Assume:

- Phone is the primary channel; not everyone is comfortable with smartphones or apps.
- Hearing, vision, and cognitive load vary widely — design for the member who needs the **most** support, not the least.
- Trust is built through **clear, patient, human tone** — automation must feel helpful, not clever or rushed.

**Default test:** Would this work for my own parent on a bad hearing day, without frustration?

---

## Where we are coming from

Historically, ArchWell has been **strongly person-to-person**: members talk to staff, and that matches our care model.

Recent **staffing pressure** means we cannot treat “every call to a person” as the only acceptable path forever — but we also must **not** replace warmth with a brittle phone tree.

**Goal:** Reduce **unnecessary** volume to the front desk and Patient Services queues while keeping **dignity, clarity, and an easy path to a person** when self-service is not enough.

Automation is a **relief valve**, not a substitute for care.

---

## What we automate (and what we do not)

### Good candidates (careful, read-only first)

| Intent | Example | Why it fits |
|--------|---------|-------------|
| **Appointment lookup** | “When is my next visit?” | High volume, low clinical risk if verified |
| **Hours / location** | “Where is my clinic?” | Factual, no PHI if generic |
| **Callback / queue position** | “We’ll call you back” | Reduces abandon, no chart access |
| **Routing** | “Billing vs medical records vs scheduling” | Gets member to the right queue faster |

### Not first-wave (human or licensed workflow)

- Clinical advice, symptoms, medication changes  
- Billing disputes, coverage exceptions, authorization fights  
- Anything that changes the chart (schedule change, cancel, demographics) until verify + audit + compliance sign-off  
- Surprises — never read sensitive results cold on IVR  

**Rule:** Start **read-only** and **narrow**. Expand only after verify, logging, and ops sign-off.

---

## Voice and IVR — senior-first defaults

1. **Listen, then press one button** — prefer DTMF multiple-choice over “enter your number” or open-ended speech.
2. **Short prompts** — one idea per prompt; pause between options.
3. **Always offer repeat** — e.g. “Press 9 to hear that again.”
4. **Plain language** — “visit” not “encounter”; “clinic” not “site of service”; avoid acronyms.
5. **Slow, stable pacing** — TTS speed and agent script cadence tuned for seniors, not consumer IVR norms.
6. **No blame** — “I didn’t catch that” not “invalid entry”; failure routes to a person or retry, never a dead end.
7. **Universal agent escape (Press 0)** — see dedicated section below; non-negotiable for any phone tree or bot flow.

---

## Press 0 — universal agent escape

**Hard requirement:** At **any point** in any phone tree, IVR, or automated flow, pressing **0** immediately routes the member to a live agent (or the appropriate human queue). No exceptions.

This applies when the member:

- Does not want automation and called expecting a person  
- Gets confused, lost, or misses the prompt  
- Fails verification and does not want to retry  
- Simply prefers human help — for any reason  

**Design rules:**

| Rule | Detail |
|------|--------|
| **Always active** | 0 works during prompts, during silence, mid-menu, during verify, and while waiting on middleware — not only at the main menu |
| **Always advertised** | Open every flow with: “You can press 0 at any time to speak with a member of our team.” Remind after long prompts or failed input |
| **No penalty** | Never play guilt copy (“you didn’t complete self-service”); warm handoff: “Connecting you now.” |
| **Context travels** | Transfer carries ANI, menu path, verify attempt status, and intent so the agent does not start from zero |
| **Same queue logic** | Route to the right Patient Services / front-desk queue for that entry point; 0 is not a dead-end or external number |
| **Not reused** | Do not assign 0 to other functions (cancel, repeat, etc.) — **0 = agent only** |

**Why this matters for ArchWell:** Many members have only ever talked to a person. Offering automation without a constant, trusted escape would betray that expectation. Staffing relief only works if members who **choose** self-service do so willingly; everyone else reaches a teammate immediately.

**Implementation note (Genesys):** Architect flows should register **global 0** (or equivalent universal interrupt) on every menu and bot state, not reimplemented per screen — so new prompts cannot ship without the escape hatch.

---

## Identity verification

Members must be verified before **any** PHI (name + appointment time + provider, etc.).

**Preferred pattern:** Multiple-choice last-4 SSN (or equivalent on file):

> “If the last four digits of your Social Security number are… Press 1 for … Press 2 for … Press 3 for … Press 4 if yours are not listed.”

**Implementation rules (middleware — not Genesys):**

- Match caller to patient in eClinicalWorks; build options server-side with **cryptographic random decoys** (never another member’s digits).
- Shuffle option order every call.
- **“Not listed”** always available → agent or alternate verify.
- Limit attempts (e.g. two); then queue to staff with context.
- Genesys receives **pass/fail** and **pre-written readback text** only — no SSN, MRN, or full DOB in the cloud layer if avoidable.

Internal middleware is **internally hosted** so PHI handling stays inside our control boundary; what crosses to Genesys is minimized by design.

---

## Architecture split (Genesys vs middleware vs eCW)

| Layer | Role |
|-------|------|
| **Genesys** | Call flow, play prompts, collect DTMF, TTS readback of **script we supply**, queue to agents |
| **Internal middleware** | Patient match, verify, MCQ logic, appointment lookup, audit, rate limits |
| **eClinicalWorks** | System of record — patient, SSN last-4, schedule |

Genesys is **not** trusted with random number generation, identity logic, or raw eCW data.

---

## Agent experience still matters

Self-service should **reduce** repetitive calls, not **hide** information from staff.

When a call escalates (including **Press 0**):

- Agent sees **why** (member pressed 0, verify failed, not listed, bot timeout, confusion).
- Agent sees **what was already attempted** (without duplicating verify unnecessarily).
- Screen-pop / context from eCW when verify succeeds on the agent leg.

Members who prefer a person — or fail self-service — must never feel punished.

---

## Cultural guardrails (automation without betraying the model)

1. **Careful manner** — pilot one intent; measure; expand. No big-bang IVR replumb.
2. **Transparency** — “I’m the automated assistant; I can tell you your next appointment. For anything else, I’ll connect you to our team.”
3. **Staffing honesty** — automation supports teammates; messaging to members stays service-oriented, not “we’re understaffed.”
4. **Compliance first** — privacy, BAA boundaries, call recording, and retention reviewed before production.
5. **Member opt-out** — **Press 0 anytime** for an agent; speaking to a person is always valid and never discouraged.

---

## Success metrics (proposed)

Track whether automation **helps members and staff**, not just deflection rate:

- % of “next appointment” intents resolved without queue  
- Average handle time change on related queues  
- Verify success rate vs “not listed” / **Press 0** / agent transfer rate  
- Member complaints or repeat calls within 24h (proxy for confusion)  
- Agent feedback — did transfers arrive with useful context?

**Anti-metric:** Raw deflection at the cost of repeat calls or abandon.

---

## Phasing (recommended)

| Phase | Scope |
|-------|--------|
| **0 — Discovery** | eCW API/interface access, test patients, compliance review |
| **1 — Middleware** | Patient match + MCQ verify + next-appointment read (API only) |
| **2 — Pilot IVR** | Single intent, one queue or after-hours window, agent fallback |
| **3 — Agent assist** | Verified context / screen-pop for live calls |
| **4 — Expand** | Additional intents only with ops + compliance approval |

Hold production member-facing changes behind **explicit approval** after leadership review (e.g. post–COO 9/1).

---

## One sentence for leadership

> For our Medicare Advantage seniors, we automate **simple, verified, read-only** tasks with **listen-and-press** flows — and **Press 0 anytime** for a person — reducing front-desk load without abandoning the human relationship that defines ArchWell.

---

## Document ownership

- **Update when:** COO review outcomes, compliance rulings, or eCW integration scope changes.  
- **Used by:** GenesysCloudToolkit / contact center integration work; cite this doc in specs and COO follow-up build lists.
