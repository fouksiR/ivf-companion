# Phenotyping Audit — Signal Collection Inventory

**Date:** 2026-04-01
**Auditor:** Claude (automated)
**Scope:** `index.html` (frontend), `app.py` (backend), `firebase_db.py`, `signal_integration.py`

---

## 1. Frontend Signal Collection Table

### 1.1 PassiveCollector Class (`index.html` lines 975-1114)

Instantiated after onboarding. Flush interval: **60 seconds**. Console: `[Phenotyping] Init {sessionId}`.

#### Typing Dynamics

| Signal | What It Measures | Event / Function | Data Format |
|--------|-----------------|------------------|-------------|
| `keystrokes[]` | Inter-keystroke intervals (ms) | `_hKD` keydown handler | Array of intervals < 5000ms |
| `deletions` | Backspace/Delete key count | `_hKD` keydown handler | Integer count |
| `totalCharsTyped` | Total key presses | `_hKD` keydown handler | Integer count |
| `compositionStarts[]` | When user starts typing a message | `_hFI` focusin on chat-input | Timestamp array |
| `compositionEnds[]` | When user stops typing | `_hFO` focusout | Timestamp array |
| `pasteEvents` | Paste actions (count + text length) | `_hPaste` paste handler | Count + metadata |

#### Touch / Pressure

| Signal | What It Measures | Event / Function | Data Format |
|--------|-----------------|------------------|-------------|
| `totalTouches` | Raw touch count | `_hTS` touchstart | Integer count |
| `tapIntervals[]` | Time between consecutive taps | `_hTS` touchstart | Array of ms values |
| `doubleTaps` | Taps < 300ms apart | `_hTS` touchstart | Integer count |
| `touchPressures[]` | Force of touch (0-1) | `_hTS` touchstart | Array of force values |
| `touchVelocities[]` | Finger movement speed | `_hTM` touchmove | Array of px/ms values |
| `longPresses` | Touches held > 500ms | `_hTE` touchend | Integer count |

#### Scroll Behavior

| Signal | What It Measures | Event / Function | Data Format |
|--------|-----------------|------------------|-------------|
| `scrollVelocities[]` | Scroll speed | `_hScr` scroll handler | Array of px/ms values |
| `scrollDirectionChanges` | Up/down reversals | `_hScr` scroll handler | Integer count |
| `scrollDepths{}` | Max scroll per panel | `_hScr` scroll handler | Object: panel → 0-1 ratio |
| `totalScrollEvents` | Raw scroll event count | `_hScr` scroll handler | Integer count |

#### Session & Circadian

| Signal | What It Measures | Capture Point | Data Format |
|--------|-----------------|---------------|-------------|
| `sessionStartTime` | Session start | Constructor | ISO 8601 timestamp |
| `sessionStartHour` | Hour of day (0-23) | Constructor | Integer |
| `dayOfWeek` | Day (0=Sun, 6=Sat) | Constructor | Integer |
| `isLateNight` | Session between 00:00-05:00 | Constructor | Boolean |
| `appBackgrounds` | Times app goes to background | `_hVis` visibilitychange | Integer count |
| `orientationChanges` | Device rotation count | `_hOr` orientationchange | Integer count |

#### Device / Environment

| Signal | What It Measures | Capture Point | Data Format |
|--------|-----------------|---------------|-------------|
| `deviceMotionSamples[]` | Acceleration magnitude | `_hDM` devicemotion (sampled 1:20) | Array, capped at 300 |
| `batteryLevel` | Battery % (if < 15% + late night) | `_probeAPIs()` | Float 0-1 |
| `batteryCharging` | Charging state | `_probeAPIs()` | Boolean |
| `connectionType` | Network type (4g/3g/etc) | `_probeAPIs()` | String |

#### Linguistic Markers (captured per message via `onMessageSent`)

| Signal | What It Measures | Data Format |
|--------|-----------------|-------------|
| `messageLengths[]` | Character count per message | Array of integers |
| `word_count` | Words per message | Integer |
| `question_marks` | `?` count | Integer |
| `exclamation_marks` | `!` count | Integer |
| `caps_ratio` | Uppercase / total letters | Float 0-1 |
| `ellipsis_count` | `..` occurrences | Integer |
| `negative_word_count` | 27 depression/distress words matched | Integer |
| `uncertainty_word_count` | 14 hedging/uncertainty words matched | Integer |
| `has_emoji` | Emoji presence | Boolean |

#### Navigation & Interaction

| Signal | What It Measures | Capture Point | Data Format |
|--------|-----------------|---------------|-------------|
| `tabSwitches` | Tab navigation count | `onTabSwitch(from, to)` | Integer |
| `panelVisits{}` | Visit count per panel | `onTabSwitch` | Object: panel → count |
| `educationTaps[]` | Education topic taps | `onEducationTap(topic)` | Array of {topic, time} |
| `stageModalOpens` | Stage-change modal opens | `onStageModalOpen()` | Integer count |
| `stageChanges[]` | Treatment stage transitions | `onStageChange(old, new)` | Array of {from, to, time} |

#### Check-in Behavior (meta-signals captured alongside sliders)

| Signal | What It Measures | Capture Point | Data Format |
|--------|-----------------|---------------|-------------|
| `checkinStartTime` | When check-in tab opened | `onCheckinStart()` | Timestamp |
| `sliderInteractions[]` | Per-dimension: adjustments + time | `onSliderChange(dim, val)` | Array of {dimension, adjustments, time_spent_ms} |
| `checkinCompletionTime` | Duration to complete check-in | `onCheckinComplete(scores)` | ms |
| `checkinAbandoned` | Started but not submitted | Derived | Boolean |
| `noteFieldEngaged` | Tapped optional note field | `_hFI` focusin on ci-note | Boolean |
| `noteFieldTime` | Time spent in note field | `_hFO` focusout | ms |

#### Community Metrics (`_communityMetrics` object, lines 1120-1128)

| Signal | What It Measures | Data Format |
|--------|-----------------|-------------|
| `posts_viewed` | Community posts read | Integer count |
| `posts_read_time_ms` | Reading time | Cumulative ms |
| `reactions_given` | Hearts/reactions tapped | Integer count |
| `post_created` | User wrote a post | Boolean |
| `stage_filters_used[]` | Filtered by treatment stage | Array of stage strings |
| `time_on_circle_tab_ms` | Time on community tab | Cumulative ms |
| `post_hours[]` | Hours when posts created | Array of 0-23 |

### 1.2 Check-in System (5-dimension sliders)

**Endpoint:** POST `/checkin`

| Dimension | Range | Input Element |
|-----------|-------|---------------|
| Mood | 0-10 | Slider |
| Anxiety | 0-10 | Slider |
| Loneliness | 0-10 | Slider |
| Uncertainty | 0-10 | Slider |
| Hope | 0-10 | Slider |
| Note | Free text | Textarea (optional) |

### 1.3 Flush Payload Structure

Sent every 60s to `POST /passive-signals`:

```
{
  patient_id, session_id,
  signals: [{ signal_type, value, timestamp, session_id, metadata }],
  derived_features: { /* 30+ computed features — see Section 1.4 */ },
  session_metadata: {
    start, duration_ms, hour_of_day, is_late_night, day_of_week,
    user_agent, screen_width, screen_height, viewport_width, viewport_height,
    timezone, language
  },
  session_metrics: {
    duration_seconds, screens_visited, messages_sent, checkin_done, idle_count
  },
  weekly_behavior: [ /* last 7 days from localStorage */ ]
}
```

### 1.4 Derived Features (computed in `_derive()` method)

| Feature | Calculation |
|---------|------------|
| Typing speed (mean, median, std, CV) | From `keystrokes[]` if length > 2 |
| `deletion_ratio` | deletions / totalCharsTyped |
| Message length (mean, std, trend) | From `messageLengths[]` |
| Composition time (mean, max) | From compositionStarts/Ends |
| Touch velocity (mean, std) | From `touchVelocities[]` if > 5 |
| Tap interval (mean, std) | From `tapIntervals[]` if > 3 |
| Touch pressure (mean, std) | From `touchPressures[]` if > 3 |
| Scroll velocity (mean, max, std) | From `scrollVelocities[]` if > 3 |
| Motion magnitude (mean, std, max) | From `deviceMotionSamples[]` if > 10 |
| Navigation entropy | Shannon entropy of panelVisits distribution |
| Check-in meta-stats | Total slider time, adjustments per slider, abandoned flag |
| `community_activity` | Nested object from `_communityMetrics` |

---

## 2. Backend Endpoint Table

| Endpoint | Method | Data Accepted | Storage | Persistent? |
|----------|--------|---------------|---------|-------------|
| `/passive-signals` | POST | `PassiveSignalBatch`: patient_id, signals[], derived_features{}, session_metadata{} | `passive_signals_db` (memory) + Firebase `melod_ai/passive_signals/{pid}` + phenotype snapshot | **Yes** (batch + snapshot) |
| `/checkin` | POST | `CheckInRequest`: patient_id, mood, anxiety, loneliness, uncertainty, hope, note | `checkins_db` (memory) + Firebase `melod_ai/checkins/{pid}` | **Yes** |
| `/screening` | POST | `ScreeningRequest`: patient_id, instrument (PHQ-2/PHQ-9/GAD-7), responses[] | `screenings_db` (memory) + Firebase `melod_ai/screenings/{pid}` | **Yes** |
| `/reflection/{pid}` | GET | Path param | `reflections_db` (memory) + Firebase `melod_ai/reflections/{pid}` | **Yes** |
| `/reflection/{pid}/feedback` | POST | feedback_type ("resonated" or empty) | `reflections_db` (memory only) | **No — volatile** |
| `/chat` | POST | `ChatRequest`: patient_id, message, treatment_stage, etc. | `conversations_db` (memory) + Firebase `melod_ai/conversations/{pid}` | **Yes** |
| `/clinician/patient/{pid}/phenotype-history` | GET | Query: days=30 | Firebase `melod_ai/phenotype_history/{pid}` (read) | N/A (read) |
| `/clinician/alerts` | GET | Query: limit=30 | `escalations_db` + `patient_signal_store` (memory) | Hybrid |
| `/clinician/patient/{pid}/briefing` | GET | Path param | Reads conversations, checkins, signal_store (memory) | N/A (read) |

---

## 3. Firebase Path Map

| Firebase Path | Written By | Trigger | Persistent? |
|---------------|-----------|---------|-------------|
| `melod_ai/passive_signals/{pid}` | `firebase_db.append_passive_signals()` | POST `/passive-signals` | Yes |
| `melod_ai/phenotype_history/{pid}/{ts_key}` | `firebase_db.save_phenotype_snapshot()` | POST `/passive-signals` (after construct analysis) | Yes |
| `melod_ai/checkins/{pid}` | `firebase_db.append_checkin()` | POST `/checkin` | Yes |
| `melod_ai/screenings/{pid}` | `firebase_db.append_screening()` | POST `/screening` | Yes |
| `melod_ai/escalations/{pid}` | `firebase_db.append_escalation()` | POST `/checkin`, `/screening` | Yes |
| `melod_ai/reflections/{pid}` | `firebase_db.save_reflection()` | GET `/reflection/{pid}` (on generation) | Yes |
| `melod_ai/conversations/{pid}` | `firebase_db.append_conversation()` | POST `/chat`, `/checkin` | Yes |
| `melod_ai/clinical_triggers/{pid}/{ts_key}` | `firebase_db.save_clinical_trigger()` | POST `/checkin` | Yes |
| `melod_ai/patients/{pid}` | `firebase_db.save_patient()` | POST `/onboard`, `/patient/update` | Yes |

---

## 4. Gap Analysis

### GREEN — Collecting & Persisting to Firebase

| Signal Domain | What's Working |
|---------------|---------------|
| **Passive signal batches** | 60s flush → `/passive-signals` → Firebase `passive_signals/{pid}` |
| **Phenotype snapshots** | Construct analysis → Firebase `phenotype_history/{pid}/{ts}` (escalation level, 7 constructs, derived features, check-in scores) |
| **Check-in scores** | 5 dimensions + note → Firebase `checkins/{pid}` |
| **Screening instruments** | PHQ-9 / GAD-7 scored → Firebase `screenings/{pid}` |
| **Escalation events** | RED/AMBER/GREEN → Firebase `escalations/{pid}` |
| **Clinical triggers** | 6 rules evaluated per check-in → Firebase `clinical_triggers/{pid}` |
| **Reflections** | AI-generated summaries → Firebase `reflections/{pid}` |
| **Conversations** | Full chat history → Firebase `conversations/{pid}` |
| **Typing dynamics** | Keystroke intervals, deletion ratio, composition time — included in derived_features within phenotype snapshot |
| **Touch patterns** | Velocity, pressure, long presses, double taps — included in derived_features |
| **Scroll behavior** | Velocity, direction changes, depth — included in derived_features |
| **Circadian signals** | Session hour, is_late_night, day_of_week — included in session_metadata |
| **Linguistic markers** | Negative words, uncertainty words, caps ratio, emoji, message length trends — included in derived_features |
| **Community activity** | Posts viewed, reactions, read time, post hours — included in derived_features |

### YELLOW — Collecting But Volatile (In-Memory Only)

| Data | Location | Risk |
|------|----------|------|
| **`patient_signal_store`** (signal_integration.py) | In-memory dict: signal_history[], check_in_history[], current_assessment, escalation_level, baseline calibration | **CRITICAL.** All baseline calibration lost on Cloud Run restart. A 50-session patient reverts to population norms. Longitudinal construct detection degraded. |
| **Reflection feedback** | `reflections_db` feedback field | Hearts/reactions on reflections NOT synced to Firebase. Analytics on which reflections resonate are impossible across restarts. |
| **Raw signal granularity** | `passive_signals_db` in-memory | Firebase batch writes are capped at 50 signals per write. Individual signal-level granularity may be lost between flushes if instance crashes. |
| **Alert queue** | `alert_queue` in signal_integration.py | Global list of RED/AMBER alerts (capped at 100) is in-memory only. Clinician dismissals are transient. Alerts resurface on restart. |

### RED — Not Yet Collecting / Not Implemented

| Feature | Status | Notes |
|---------|--------|-------|
| **Baseline persistence** | Not implemented | `patient_signal_store` (personal means, std devs, signal_history) has zero Firebase sync. After restart, all patients start from population norms regardless of history. |
| **Multi-instance state sync** | Not implemented | Cloud Run instances don't share in-memory state. POST to instance A, GET from instance B → stale signal context. Firebase is source of truth but real-time signal assessment (constructs, escalation) has a cold-start gap. |
| **Community flags in chat prompt** | Not implemented | Community behavior flags (SEEKING_CONNECTION, LATE_NIGHT_COMMUNITY, ANTICIPATORY_BROWSING) are computed but only injected into clinician views, NOT into the `/chat` system prompt. |
| **Urgent signal flush on RED** | Not implemented | No priority flush when RED escalation is detected. Phenotype snapshot is saved, but raw signal batch waits for the normal 60s cycle. |
| **Reflection generation baseline check** | Not implemented | Reflection endpoint doesn't check `baseline_established` flag. Reflections generated before 7 sessions use population norms (less personalized). |
| **Session recording across restarts** | Not implemented | `session_count` in `patient_signal_store` resets to 0 on restart. No Firebase counter for total sessions. |

---

## 5. Recommended Priority List

### P0 — Critical (Data Loss Risk)

1. **Persist `patient_signal_store` baseline to Firebase**
   Save `signal_history[-50]`, `check_in_history[-20]`, `session_count`, and `baseline_established` flag to `melod_ai/signal_baselines/{pid}`. Reload on cold start. Without this, every Cloud Run restart wipes longitudinal calibration.

2. **Persist session count to Firebase**
   Atomic counter at `melod_ai/patients/{pid}/session_count`. Increment on each `/passive-signals` POST. Prevents the 7-session baseline threshold from resetting.

### P1 — High (Analytics Gap)

3. **Sync reflection feedback to Firebase**
   On POST `/reflection/{pid}/feedback`, write to `melod_ai/reflections/{pid}/{key}/feedback`. Enables measuring which reflection types resonate.

4. **Persist alert dismissals**
   Write clinician alert actions (dismiss, resolve) to Firebase so they survive restarts and multi-instance routing.

### P2 — Medium (Feature Enhancement)

5. **Inject community flags into `/chat` system prompt**
   Add SEEKING_CONNECTION, LATE_NIGHT_COMMUNITY, ANTICIPATORY_BROWSING context to the chat prompt. Currently only visible to clinicians.

6. **Add urgent flush on RED escalation**
   When construct analysis yields RED, trigger an immediate `/passive-signals` flush from the frontend (instead of waiting for the 60s cycle).

7. **Check baseline_established before reflection generation**
   Gate personalized reflections on 7+ sessions to avoid premature insights based on population norms.

### P3 — Low (Robustness)

8. **Multi-instance state warming**
   On cold start, load last phenotype snapshot from Firebase to warm `patient_signal_store` for active patients. Reduces the window where signal context is empty after instance scaling.

9. **Increase Firebase signal batch cap**
   Current 50-signal cap per batch write may lose granularity during high-activity sessions. Consider writing full batches or using a streaming approach.

---

## Appendix: 7 Construct Detectors (signal_integration.py)

| Construct | Signal Source | Threshold | Escalation |
|-----------|-------------|-----------|------------|
| **Psychomotor Retardation** | `typing.mean_iki_ms` (slowed typing) | z > 1.5 vs personal baseline | AMBER |
| **Psychomotor Agitation** | `typing.deletion_ratio` (high correction) | z > 1.5 | AMBER |
| **Sleep Disturbance** | `circadian.hour` in [00-05] | 2+ late-night sessions in 7 days | AMBER |
| **Social Withdrawal** | `session_gap_days` + `engagement_decline_z` | Gap >= 3 days OR z < -1.5 | AMBER |
| **Rumination** | `typing.composition_time_ms` (long drafting) | z > 1.5 | AMBER |
| **Anxiety Escalation** | Check-in anxiety trend + `uncertainty_word_ratio` | Trend >= 2 pts over 3 checkins OR z > 1.5 | AMBER |
| **Hopelessness** | Check-in hope trend + `negative_word_ratio` | Trend <= -2 pts over 3 checkins OR z > 1.5 | AMBER |

**Escalation aggregation:**
- **RED:** 3+ active constructs OR max_z >= 2.5 OR (mood <= 2 AND hope <= 2) OR anxiety >= 9
- **AMBER:** 2+ active constructs OR max_z >= 2.0
- **GREEN:** Otherwise

---

## Appendix: 6 Clinical Trigger Rules (app.py)

| Rule | Condition | Support Widget | Clinician Flag |
|------|-----------|---------------|----------------|
| PRE_PROCEDURE_ANXIETY | Procedure within 48h + anxiety >= 5 | `pre_procedure_checklist` | Yes |
| POST_RESULT_VULNERABILITY | Result within 72h + mood < 4 | `post_result_care` | Yes |
| STIMULATION_FATIGUE | Stimulation stage + 8+ injections + declining mood | `stim_fatigue_support` | Yes |
| TWW_SPIRAL | TWW stage + anxiety >= 6 or mood < 5 | `tww_survival_kit` | Yes |
| DISENGAGEMENT_WARNING | 3+ days silent + latest mood < 5 | `gentle_nudge` | Yes |
| MEDICATION_CONFUSION | 2+ med-related messages in 3 days | `medication_education` | Yes |
