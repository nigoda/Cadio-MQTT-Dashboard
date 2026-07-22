# 🔄 Automation Engine — Complete Flow Diagram

## High-Level Overview

```mermaid
flowchart TD
    OFF["🔴 OFF"] -->|User turns ON| IDLE["IDLE"]
    IDLE --> INIT["🔧 INIT_SET\nSet switches to safe state"]
    INIT --> INIT_V["INIT_VERIFY\nVerify each switch responded"]
    INIT_V -->|All verified| WAIT["⏳ WAIT_CONDITION\nWaiting for gates to pass"]
    INIT_V -->|Timeout| RETRY{"Retry?\n< 3 attempts"}
    RETRY -->|Yes| INIT
    RETRY -->|No| ERR["🛑 ERROR"]

    WAIT --> GATE{"📅 Scheduler ✅?\n🔀 Action Condition ✅?\n🔄 Max Cycles OK?"}
    GATE -->|All pass| ACTION_SET["▶️ ACTION_SET\nSend switch command"]
    GATE -->|Any fails| WAIT

    ACTION_SET --> ACTION_V["ACTION_VERIFY\nVerify switch responded"]
    ACTION_V -->|Verified| ACTION_RUN["⏱️ ACTION_RUN\nTimer counting down"]
    ACTION_V -->|Timeout| RETRY2{"Retry?"}
    RETRY2 -->|Yes| ACTION_SET
    RETRY2 -->|No| ERR

    ACTION_RUN --> PAUSE_CHECK{"Still OK?"}
    PAUSE_CHECK -->|Condition FALSE| PAUSED_C["⏸️ PAUSED_CONDITION"]
    PAUSE_CHECK -->|Schedule FALSE| PAUSED_S["⏸️ PAUSED_SCHEDULE"]
    PAUSE_CHECK -->|Switch drifted| DRIFT["⚠️ DRIFT_VERIFY\nCorrect & re-verify"]
    PAUSE_CHECK -->|Timer done| NEXT{"More actions?"}

    PAUSED_C -->|Condition TRUE| ACTION_RUN
    PAUSED_S -->|Schedule TRUE| ACTION_RUN
    DRIFT -->|Corrected| ACTION_RUN
    DRIFT -->|Failed| ERR

    NEXT -->|Yes| OVERLAP["OVERLAP_NEXT_SET\nStart next action"]
    OVERLAP --> BUFFER["⏳ BUFFER\nWait between actions"]
    BUFFER --> REVERT["ACTION_REVERT\nTurn off previous action"]
    REVERT --> ACTION_SET

    NEXT -->|No, can loop| LOOP["🔄 Re-initialize\nthen loop to Action 1"]
    LOOP --> INIT
    NEXT -->|No, done| REVERT_FINAL["ACTION_REVERT\nRevert last action"]
    REVERT_FINAL --> WAIT

    ERR -->|User resets| IDLE

    style OFF fill:#ef4444,color:#fff
    style IDLE fill:#64748b,color:#fff
    style INIT fill:#f59e0b,color:#000
    style INIT_V fill:#f59e0b,color:#000
    style WAIT fill:#6366f1,color:#fff
    style ACTION_SET fill:#22c55e,color:#000
    style ACTION_V fill:#22c55e,color:#000
    style ACTION_RUN fill:#10b981,color:#fff
    style PAUSED_C fill:#f97316,color:#fff
    style PAUSED_S fill:#f97316,color:#fff
    style DRIFT fill:#eab308,color:#000
    style BUFFER fill:#8b5cf6,color:#fff
    style REVERT fill:#8b5cf6,color:#fff
    style REVERT_FINAL fill:#8b5cf6,color:#fff
    style ERR fill:#dc2626,color:#fff
    style LOOP fill:#06b6d4,color:#000
    style OVERLAP fill:#8b5cf6,color:#fff
```

---

## 📅 Scheduler Gate — Detail

The scheduler is checked at **WAIT_CONDITION** and during **ACTION_RUN**. It evaluates 3 sub-gates:

```mermaid
flowchart LR
    S["📅 check_schedule"] --> D{"Day OK?\nMon/Tue/etc"}
    D -->|No| FALSE["❌ FALSE"]
    D -->|Yes| T{"Time OK?\n06:00-08:00"}
    T -->|No| FALSE
    T -->|Yes| C{"Conditions OK?\nSensor = value\nAND/OR logic"}
    C -->|No| FALSE
    C -->|Yes| TRUE["✅ TRUE"]

    style TRUE fill:#22c55e,color:#000
    style FALSE fill:#ef4444,color:#fff
```

---

## ⚡ Schedule Enforcement — Background

Runs **independently** on every engine tick (regardless of state):

```mermaid
flowchart TD
    TICK["Engine Tick"] --> CHECK{"check_schedule = ?"}
    CHECK -->|TRUE| DURING["✅ Enforce 'During Schedule'\nForce switches to configured state"]
    CHECK -->|FALSE| OUTSIDE["🚫 Enforce 'Outside Schedule'\nForce switches to configured state"]
    DURING --> VERIFY{"Switch correct?"}
    OUTSIDE --> VERIFY
    VERIFY -->|Yes| DONE["✔️ OK"]
    VERIFY -->|No| SEND["Send MQTT command\n+ retry up to 3x"]
    SEND -->|Verified| DONE
    SEND -->|Failed 3x| ERR["🛑 ERROR"]

    style DURING fill:#22c55e,color:#000
    style OUTSIDE fill:#ef4444,color:#fff
    style ERR fill:#dc2626,color:#fff
```

---

## 🔀 Action Condition — During ACTION_RUN

While an action is running, if the action condition becomes false:

```mermaid
flowchart LR
    RUN["⏱️ ACTION_RUN"] --> CHK{"Action Condition\nstill TRUE?"}
    CHK -->|Yes| RUN
    CHK -->|No| PAUSE["⏸️ PAUSED_CONDITION\nTimer frozen"]
    PAUSE --> CHK2{"Condition\nback to TRUE?"}
    CHK2 -->|Yes| RESUME["▶️ ACTION_RUN\nTimer resumes"]
    CHK2 -->|No| PAUSE

    style RUN fill:#10b981,color:#fff
    style PAUSE fill:#f97316,color:#fff
    style RESUME fill:#10b981,color:#fff
```

---

## 📊 Complete State List

| State | Description |
|:------|:------------|
| `IDLE` | Off, waiting to be turned on |
| `INIT_SET` | Sending initialization switch commands |
| `INIT_VERIFY_INDIVIDUAL` | Verifying each init switch one by one |
| `INIT_VERIFY_ALL` | Final bulk verification of all init switches |
| `WAIT_CONDITION` | Waiting for Scheduler ✅ AND Action Condition ✅ |
| `ACTION_SET` | Sending action switch command |
| `ACTION_VERIFY` | Verifying action switch responded |
| `ACTION_RUN` | ⏱️ Timer running for current action |
| `ACTION_DRIFT_VERIFY` | Switch drifted during run, correcting |
| `OVERLAP_NEXT_SET` | Setting next action while current still active |
| `OVERLAP_NEXT_VERIFY` | Verifying next action switch |
| `BUFFER` | Wait period between actions |
| `BUFFER_DRIFT_VERIFY` | Switch drifted during buffer, correcting |
| `ACTION_REVERT` | Reverting (turning off) previous action |
| `ACTION_VERIFY_REVERT` | Verifying revert succeeded |
| `PAUSED_CONDITION` | ⏸️ Paused — action condition went false |
| `PAUSED_SCHEDULE` | ⏸️ Paused — outside schedule window |
| `PAUSED_USER` | ⏸️ Paused by user manually |
| `PAUSED_ENFORCE` | ⏸️ Pausing to enforce schedule switch |
| `ERROR_SET` | Setting error-state switches |
| `ERROR_VERIFY` | Verifying error-state switches |
| `ERROR` | 🛑 Stuck in error — needs manual reset |
| `COMPLETED` | Cycle completed, transitioning back |

---

## 🔁 Cycle Looping Logic

After the last action finishes:

```mermaid
flowchart TD
    DONE["Last action timer done"] --> CYCLES{"Max cycles\nreached?"}
    CYCLES -->|Yes| STOP["Re-init → Revert → WAIT_CONDITION\nPaused until tomorrow"]
    CYCLES -->|No| SCHED{"Schedule\nstill active?"}
    SCHED -->|Yes & multi-action| LOOP["🔄 Re-init → Loop to Action 1\nCycle count +1"]
    SCHED -->|No| REVERT["Revert → WAIT_CONDITION"]

    style LOOP fill:#06b6d4,color:#000
    style STOP fill:#ef4444,color:#fff
    style REVERT fill:#8b5cf6,color:#fff
```
