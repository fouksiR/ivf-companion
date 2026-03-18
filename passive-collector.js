/**
 * ════════════════════════════════════════════════════════════════════
 * MELOD-AI — PASSIVE DIGITAL PHENOTYPING COLLECTOR
 * ════════════════════════════════════════════════════════════════════
 *
 * Silent behavioural instrumentation layer for IVF patient monitoring.
 * Collects multi-modal passive signals from mobile web browser and
 * maps them to clinical distress constructs.
 *
 * CLINICAL SIGNAL TAXONOMY:
 *
 *   PSYCHOMOTOR RETARDATION → slower typing, longer pauses, reduced
 *     touch velocity, decreased device motion amplitude
 *   PSYCHOMOTOR AGITATION → rapid erratic scrolling, high device motion
 *     variance, excessive deletion/retyping, restless navigation
 *   SLEEP DISTURBANCE → late-night sessions (00:00-05:00), irregular
 *     circadian usage patterns, declining session regularity
 *   SOCIAL WITHDRAWAL → declining message length, fewer sessions,
 *     skipped check-ins, shorter engagement, reduced education browsing
 *   RUMINATION → revisiting same content, long composition with heavy
 *     deletion, late-night sessions, repeated topic searches
 *   ANHEDONIA → reduced total engagement, shorter sessions, less
 *     exploration, declining interaction diversity
 *   ANXIETY → increased check frequency, rapid slider adjustments,
 *     high scroll velocity spikes, frequent app-switching
 *   HOPELESSNESS → declining engagement trajectory, abrupt session
 *     termination, reduced future-oriented content consumption
 *
 * Dr Yuval Fouks — March 2026
 */

class PassiveCollector {

    constructor(config = {}) {
        this.patientId = config.patientId || null;
        this.apiBase = config.apiBase || '';
        this.flushInterval = config.flushInterval || 60000;  // flush every 60s
        this.debug = config.debug || false;

        // ── Signal buffer ──
        this.buffer = [];
        this.sessionId = this._uuid();
        this.sessionStart = Date.now();

        // ── Running metrics (computed per session, flushed as derived features) ──
        this.metrics = {
            // Session
            sessionStartTime: new Date().toISOString(),
            sessionStartHour: new Date().getHours(),
            totalTouches: 0,
            totalScrollEvents: 0,
            tabSwitches: 0,
            appBackgrounds: 0,

            // Typing dynamics
            keystrokes: [],           // timestamps for inter-key interval
            deletions: 0,
            totalCharsTyped: 0,
            compositionStarts: [],    // timestamp when user starts typing in a field
            compositionEnds: [],      // timestamp when they stop/send
            messagesSent: 0,
            messageLengths: [],
            pasteEvents: 0,

            // Touch dynamics
            touchVelocities: [],      // px/ms for each touch-move
            touchPressures: [],       // if available (Force Touch / pressure)
            tapIntervals: [],         // time between consecutive taps
            longPresses: 0,           // taps > 500ms (hesitation signal)
            doubleTaps: 0,

            // Scroll dynamics
            scrollVelocities: [],     // px/ms
            scrollDirectionChanges: 0,
            scrollDepths: {},         // per panel: max scroll depth ratio

            // Check-in specific
            sliderInteractions: [],   // {dimension, adjustments, timeSpent, finalValue}
            checkinStartTime: null,
            checkinCompletionTime: null,
            checkinAbandoned: false,
            noteFieldEngaged: false,
            noteFieldTime: 0,

            // Navigation
            panelVisits: {},          // {chat: 5, checkin: 2, journey: 1}
            educationTaps: [],        // which topics clicked, timestamps
            stageModalOpens: 0,
            stageChanges: [],

            // Device & environment
            screenOrientation: null,
            orientationChanges: 0,
            batteryLevel: null,
            batteryCharging: null,
            connectionType: null,
            deviceMotionSamples: [],  // magnitude of acceleration

            // Circadian / temporal
            hourOfDay: new Date().getHours(),
            dayOfWeek: new Date().getDay(),
            isLateNight: false,       // 00:00 - 05:00
            minutesSinceMidnight: new Date().getHours() * 60 + new Date().getMinutes(),
        };

        // ── Internal state ──
        this._lastTouchTime = 0;
        this._lastScrollY = 0;
        this._lastScrollTime = 0;
        this._lastKeystrokeTime = 0;
        this._currentSlider = null;
        this._sliderStartTime = 0;
        this._sliderAdjustments = 0;
        this._touchStartTime = 0;
        this._composing = false;
        this._composeStartTime = 0;
        this._composeField = null;
        this._flushTimer = null;
        this._motionSampleCount = 0;
        this._visibilityChanges = [];

        // Init
        this._checkCircadian();
        this._bindAll();
        this._startFlushCycle();
        this._probeDeviceAPIs();

        if (this.debug) console.log('[Melod Phenotyping] Collector initialised', this.sessionId);
    }

    // ════════════════════════════════════════════════════════════════
    // PUBLIC API
    // ════════════════════════════════════════════════════════════════

    setPatientId(id) {
        this.patientId = id;
    }

    /**
     * Record a named event with metadata.
     * Low-level — used by internal hooks and available for custom events.
     */
    record(signalType, value, metadata = {}) {
        this.buffer.push({
            signal_type: signalType,
            value: value,
            timestamp: new Date().toISOString(),
            session_id: this.sessionId,
            metadata: metadata,
        });

        if (this.debug && this.buffer.length % 50 === 0) {
            console.log(`[Phenotyping] Buffer: ${this.buffer.length} signals`);
        }
    }

    /**
     * Force-flush the buffer + derived metrics to backend.
     */
    async flush() {
        if (!this.patientId) return;
        if (this.buffer.length === 0 && !this._hasDerivedMetrics()) return;

        // Compute derived features
        const derived = this._computeDerivedFeatures();

        // Package everything
        const payload = {
            patient_id: this.patientId,
            session_id: this.sessionId,
            signals: [...this.buffer],
            derived_features: derived,
            session_metadata: {
                start: this.metrics.sessionStartTime,
                duration_ms: Date.now() - this.sessionStart,
                hour_of_day: this.metrics.sessionStartHour,
                is_late_night: this.metrics.isLateNight,
                day_of_week: this.metrics.dayOfWeek,
                user_agent: navigator.userAgent,
                screen_width: screen.width,
                screen_height: screen.height,
                viewport_width: window.innerWidth,
                viewport_height: window.innerHeight,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                language: navigator.language,
            }
        };

        // Clear buffer
        const bufferSize = this.buffer.length;
        this.buffer = [];

        try {
            const res = await fetch(`${this.apiBase}/passive-signals`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (this.debug) console.log(`[Phenotyping] Flushed ${bufferSize} signals + derived features. Status: ${res.status}`);
        } catch (e) {
            // Store failed flush locally for retry
            if (this.debug) console.warn('[Phenotyping] Flush failed, buffering locally:', e.message);
            // Re-add signals to buffer for next attempt
            this.buffer = [...payload.signals, ...this.buffer];
        }
    }

    /**
     * End the session. Flush everything and clean up.
     */
    async endSession() {
        this.record('session_end', Date.now() - this.sessionStart, {
            total_touches: this.metrics.totalTouches,
            total_messages: this.metrics.messagesSent,
            total_keystrokes: this.metrics.totalCharsTyped,
        });
        await this.flush();
        this._unbindAll();
        if (this._flushTimer) clearInterval(this._flushTimer);
    }

    // ════════════════════════════════════════════════════════════════
    // BINDING — attach to all available browser events
    // ════════════════════════════════════════════════════════════════

    _bindAll() {
        // ── Touch & Pointer ──
        this._onTouchStart = this._handleTouchStart.bind(this);
        this._onTouchMove = this._handleTouchMove.bind(this);
        this._onTouchEnd = this._handleTouchEnd.bind(this);
        document.addEventListener('touchstart', this._onTouchStart, { passive: true });
        document.addEventListener('touchmove', this._onTouchMove, { passive: true });
        document.addEventListener('touchend', this._onTouchEnd, { passive: true });

        // ── Keyboard ──
        this._onKeyDown = this._handleKeyDown.bind(this);
        this._onKeyUp = this._handleKeyUp.bind(this);
        document.addEventListener('keydown', this._onKeyDown);
        document.addEventListener('keyup', this._onKeyUp);

        // ── Scroll ──
        this._onScroll = this._handleScroll.bind(this);
        window.addEventListener('scroll', this._onScroll, { passive: true, capture: true });

        // ── Visibility (app backgrounding) ──
        this._onVisibility = this._handleVisibility.bind(this);
        document.addEventListener('visibilitychange', this._onVisibility);

        // ── Focus / Blur on input fields ──
        this._onFocusIn = this._handleFocusIn.bind(this);
        this._onFocusOut = this._handleFocusOut.bind(this);
        document.addEventListener('focusin', this._onFocusIn);
        document.addEventListener('focusout', this._onFocusOut);

        // ── Paste ──
        this._onPaste = this._handlePaste.bind(this);
        document.addEventListener('paste', this._onPaste);

        // ── Orientation ──
        this._onOrientation = this._handleOrientation.bind(this);
        window.addEventListener('orientationchange', this._onOrientation);

        // ── Device Motion (accelerometer — psychomotor signal) ──
        this._onDeviceMotion = this._handleDeviceMotion.bind(this);
        if (window.DeviceMotionEvent) {
            // iOS 13+ requires permission
            if (typeof DeviceMotionEvent.requestPermission === 'function') {
                // Will request on first interaction
                this._motionPermissionPending = true;
            } else {
                window.addEventListener('devicemotion', this._onDeviceMotion);
            }
        }

        // ── Before unload (session end) ──
        this._onBeforeUnload = () => { this.flush(); };
        window.addEventListener('beforeunload', this._onBeforeUnload);
        window.addEventListener('pagehide', this._onBeforeUnload);

        // ── Online/offline ──
        this._onOnline = () => this.record('connectivity', 'online');
        this._onOffline = () => this.record('connectivity', 'offline');
        window.addEventListener('online', this._onOnline);
        window.addEventListener('offline', this._onOffline);
    }

    _unbindAll() {
        document.removeEventListener('touchstart', this._onTouchStart);
        document.removeEventListener('touchmove', this._onTouchMove);
        document.removeEventListener('touchend', this._onTouchEnd);
        document.removeEventListener('keydown', this._onKeyDown);
        document.removeEventListener('keyup', this._onKeyUp);
        window.removeEventListener('scroll', this._onScroll, { capture: true });
        document.removeEventListener('visibilitychange', this._onVisibility);
        document.removeEventListener('focusin', this._onFocusIn);
        document.removeEventListener('focusout', this._onFocusOut);
        document.removeEventListener('paste', this._onPaste);
        window.removeEventListener('orientationchange', this._onOrientation);
        window.removeEventListener('devicemotion', this._onDeviceMotion);
        window.removeEventListener('beforeunload', this._onBeforeUnload);
        window.removeEventListener('pagehide', this._onBeforeUnload);
        window.removeEventListener('online', this._onOnline);
        window.removeEventListener('offline', this._onOffline);
    }

    // ════════════════════════════════════════════════════════════════
    // EVENT HANDLERS
    // ════════════════════════════════════════════════════════════════

    // ── TOUCH ──────────────────────────────────────────────────────

    _handleTouchStart(e) {
        const now = Date.now();
        this.metrics.totalTouches++;
        this._touchStartTime = now;

        // Tap interval (time between consecutive taps)
        if (this._lastTouchTime > 0) {
            const interval = now - this._lastTouchTime;
            this.metrics.tapIntervals.push(interval);

            // Double tap detection
            if (interval < 300) this.metrics.doubleTaps++;
        }
        this._lastTouchTime = now;

        // Touch pressure (if available)
        if (e.touches && e.touches[0]) {
            const touch = e.touches[0];
            if (touch.force && touch.force > 0) {
                this.metrics.touchPressures.push(touch.force);
            }
        }

        // Request device motion permission on first touch (iOS)
        if (this._motionPermissionPending) {
            this._motionPermissionPending = false;
            DeviceMotionEvent.requestPermission().then(state => {
                if (state === 'granted') {
                    window.addEventListener('devicemotion', this._onDeviceMotion);
                }
            }).catch(() => {});
        }

        // Track which element was touched (for navigation pattern analysis)
        const target = e.target.closest('[data-tab], .edu-item, .stage-option, .modal-stage-btn, .send-btn, .btn, .cs-change, .stage-pill');
        if (target) {
            this.record('ui_interaction', target.className.split(' ')[0], {
                element: target.tagName,
                text: (target.textContent || '').substring(0, 50).trim(),
            });
        }
    }

    _handleTouchMove(e) {
        if (!e.touches || !e.touches[0]) return;
        const touch = e.touches[0];

        // Touch velocity (movement speed — psychomotor indicator)
        if (this._lastTouchX !== undefined) {
            const dx = touch.clientX - this._lastTouchX;
            const dy = touch.clientY - this._lastTouchY;
            const dt = Date.now() - this._lastTouchMoveTime;
            if (dt > 0) {
                const velocity = Math.sqrt(dx * dx + dy * dy) / dt;
                this.metrics.touchVelocities.push(velocity);
            }
        }
        this._lastTouchX = touch.clientX;
        this._lastTouchY = touch.clientY;
        this._lastTouchMoveTime = Date.now();
    }

    _handleTouchEnd(e) {
        const duration = Date.now() - this._touchStartTime;

        // Long press detection (hesitation / indecision signal)
        if (duration > 500) {
            this.metrics.longPresses++;
            this.record('long_press', duration, {
                element: e.target.tagName,
            });
        }

        this._lastTouchX = undefined;
        this._lastTouchY = undefined;
    }

    // ── KEYBOARD ───────────────────────────────────────────────────

    _handleKeyDown(e) {
        const now = Date.now();
        this.metrics.totalCharsTyped++;

        // Inter-key interval (typing speed — psychomotor signal)
        if (this._lastKeystrokeTime > 0) {
            const iki = now - this._lastKeystrokeTime;
            // Only record reasonable intervals (< 5 seconds, otherwise it's a pause)
            if (iki < 5000) {
                this.metrics.keystrokes.push(iki);
            }
        }
        this._lastKeystrokeTime = now;

        // Deletion tracking (Backspace/Delete — rumination/perfectionism signal)
        if (e.key === 'Backspace' || e.key === 'Delete') {
            this.metrics.deletions++;
        }
    }

    _handleKeyUp(e) {
        // We primarily track on keydown for timing
    }

    // ── SCROLL ─────────────────────────────────────────────────────

    _handleScroll(e) {
        const now = Date.now();
        const scrollY = window.scrollY || document.documentElement.scrollTop;
        this.metrics.totalScrollEvents++;

        if (this._lastScrollTime > 0) {
            const dt = now - this._lastScrollTime;
            const dy = Math.abs(scrollY - this._lastScrollY);
            if (dt > 0) {
                const velocity = dy / dt;
                this.metrics.scrollVelocities.push(velocity);
            }

            // Direction change detection (erratic scrolling — agitation signal)
            const direction = scrollY > this._lastScrollY ? 'down' : 'up';
            if (this._lastScrollDir && direction !== this._lastScrollDir) {
                this.metrics.scrollDirectionChanges++;
            }
            this._lastScrollDir = direction;
        }

        this._lastScrollY = scrollY;
        this._lastScrollTime = now;

        // Scroll depth tracking per active panel
        const activePanel = document.querySelector('.tab-panel.active');
        if (activePanel) {
            const panelId = activePanel.id;
            const maxScroll = activePanel.scrollHeight - activePanel.clientHeight;
            if (maxScroll > 0) {
                const depth = activePanel.scrollTop / maxScroll;
                this.metrics.scrollDepths[panelId] = Math.max(
                    this.metrics.scrollDepths[panelId] || 0,
                    depth
                );
            }
        }
    }

    // ── VISIBILITY (app backgrounding / tab switching) ─────────────

    _handleVisibility() {
        if (document.hidden) {
            this.metrics.appBackgrounds++;
            this._visibilityChanges.push({ type: 'hidden', time: Date.now() });
            this.record('app_background', null, {
                session_duration_so_far: Date.now() - this.sessionStart,
            });
            // Flush on background (may not complete, best effort)
            this.flush();
        } else {
            this._visibilityChanges.push({ type: 'visible', time: Date.now() });
            const lastHidden = this._visibilityChanges.filter(v => v.type === 'hidden').pop();
            const awayDuration = lastHidden ? Date.now() - lastHidden.time : 0;
            this.record('app_foreground', awayDuration, {
                away_duration_ms: awayDuration,
            });
        }
    }

    // ── INPUT FOCUS (composition tracking) ─────────────────────────

    _handleFocusIn(e) {
        const target = e.target;
        if (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT') {
            this._composing = true;
            this._composeStartTime = Date.now();
            this._composeField = target.id || target.className;
            this._composeInitialLength = (target.value || '').length;

            // Check-in note field tracking
            if (target.id === 'checkin-note') {
                this.metrics.noteFieldEngaged = true;
                this._noteFieldStart = Date.now();
            }

            // Chat composition start
            if (target.id === 'chat-input') {
                this.metrics.compositionStarts.push(Date.now());
            }
        }
    }

    _handleFocusOut(e) {
        const target = e.target;
        if (this._composing && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT')) {
            const duration = Date.now() - this._composeStartTime;
            const finalLength = (target.value || '').length;
            const charsAdded = Math.max(0, finalLength - this._composeInitialLength);

            this.record('composition_end', duration, {
                field: this._composeField,
                duration_ms: duration,
                chars_added: charsAdded,
                final_length: finalLength,
                deletions_during: this.metrics.deletions, // approximate
            });

            if (target.id === 'chat-input') {
                this.metrics.compositionEnds.push(Date.now());
            }

            if (target.id === 'checkin-note' && this._noteFieldStart) {
                this.metrics.noteFieldTime += Date.now() - this._noteFieldStart;
            }

            this._composing = false;
        }
    }

    // ── PASTE ──────────────────────────────────────────────────────

    _handlePaste(e) {
        this.metrics.pasteEvents++;
        const text = (e.clipboardData || window.clipboardData)?.getData('text') || '';
        this.record('paste', text.length, {
            field: (e.target.id || e.target.className || '').substring(0, 30),
        });
    }

    // ── ORIENTATION ────────────────────────────────────────────────

    _handleOrientation() {
        this.metrics.orientationChanges++;
        this.metrics.screenOrientation = screen.orientation?.type || 'unknown';
        this.record('orientation_change', this.metrics.screenOrientation);
    }

    // ── DEVICE MOTION (accelerometer — psychomotor agitation/retardation) ──

    _handleDeviceMotion(e) {
        // Sample at reduced rate (every 20th event ≈ 2.5 Hz if device fires at 50Hz)
        this._motionSampleCount++;
        if (this._motionSampleCount % 20 !== 0) return;

        const accel = e.accelerationIncludingGravity;
        if (accel && accel.x != null) {
            // Total magnitude (gravity-inclusive)
            const magnitude = Math.sqrt(accel.x ** 2 + accel.y ** 2 + accel.z ** 2);
            // Subtract ~9.81 for gravity to get movement component
            const movement = Math.abs(magnitude - 9.81);
            this.metrics.deviceMotionSamples.push(movement);

            // Keep only last 300 samples (~2 minutes at 2.5 Hz)
            if (this.metrics.deviceMotionSamples.length > 300) {
                this.metrics.deviceMotionSamples = this.metrics.deviceMotionSamples.slice(-300);
            }
        }
    }

    // ════════════════════════════════════════════════════════════════
    // APP-SPECIFIC HOOKS (called from main app code)
    // ════════════════════════════════════════════════════════════════

    /**
     * Called when user sends a message in chat.
     */
    onMessageSent(messageText) {
        this.metrics.messagesSent++;
        this.metrics.messageLengths.push(messageText.length);

        // Composition time for this message
        const lastStart = this.metrics.compositionStarts[this.metrics.compositionStarts.length - 1];
        const compositionTime = lastStart ? Date.now() - lastStart : null;

        // Content metadata (no actual content stored — only structural features)
        const words = messageText.trim().split(/\s+/).length;
        const questionMarks = (messageText.match(/\?/g) || []).length;
        const exclamationMarks = (messageText.match(/!/g) || []).length;
        const capsRatio = messageText.replace(/[^a-zA-Z]/g, '').length > 0
            ? (messageText.match(/[A-Z]/g) || []).length / messageText.replace(/[^a-zA-Z]/g, '').length
            : 0;
        const ellipsis = (messageText.match(/\.{2,}/g) || []).length;
        const negativeWords = (messageText.toLowerCase().match(/\b(can't|won't|never|nothing|hopeless|alone|scared|terrified|hate|failed|pointless|useless|worthless|empty|numb|dead|die|hurt|pain|crying|cry|awful|terrible|horrible|devastated|broken|shattered|lost)\b/g) || []).length;
        const uncertaintyWords = (messageText.toLowerCase().match(/\b(maybe|might|possibly|uncertain|unsure|confused|don't know|not sure|i think|i guess|wonder|worried|what if)\b/g) || []).length;

        this.record('message_sent', messageText.length, {
            word_count: words,
            composition_time_ms: compositionTime,
            question_marks: questionMarks,
            exclamation_marks: exclamationMarks,
            caps_ratio: Math.round(capsRatio * 100) / 100,
            ellipsis_count: ellipsis,
            negative_word_count: negativeWords,
            uncertainty_word_count: uncertaintyWords,
            has_emoji: /[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}]/u.test(messageText),
        });
    }

    /**
     * Called when user starts a check-in.
     */
    onCheckinStart() {
        this.metrics.checkinStartTime = Date.now();
        this.metrics.checkinAbandoned = true; // set to false on completion
        this.record('checkin_start', null);
    }

    /**
     * Called when a slider is adjusted.
     */
    onSliderChange(dimension, value) {
        if (this._currentSlider !== dimension) {
            // New slider
            if (this._currentSlider) {
                this._finaliseSlider();
            }
            this._currentSlider = dimension;
            this._sliderStartTime = Date.now();
            this._sliderAdjustments = 0;
        }
        this._sliderAdjustments++;
    }

    _finaliseSlider() {
        if (!this._currentSlider) return;
        this.metrics.sliderInteractions.push({
            dimension: this._currentSlider,
            adjustments: this._sliderAdjustments,
            time_spent_ms: Date.now() - this._sliderStartTime,
        });
    }

    /**
     * Called when check-in is submitted.
     */
    onCheckinComplete(scores) {
        this._finaliseSlider();
        this._currentSlider = null;
        this.metrics.checkinAbandoned = false;
        this.metrics.checkinCompletionTime = Date.now();

        const duration = this.metrics.checkinStartTime
            ? Date.now() - this.metrics.checkinStartTime
            : null;

        this.record('checkin_complete', duration, {
            duration_ms: duration,
            scores: scores,
            slider_interactions: this.metrics.sliderInteractions.length,
            note_engaged: this.metrics.noteFieldEngaged,
            note_time_ms: this.metrics.noteFieldTime,
        });
    }

    /**
     * Called when user switches tabs.
     */
    onTabSwitch(fromTab, toTab) {
        this.metrics.tabSwitches++;
        this.metrics.panelVisits[toTab] = (this.metrics.panelVisits[toTab] || 0) + 1;
        this.record('tab_switch', null, { from: fromTab, to: toTab });
    }

    /**
     * Called when an education topic is tapped.
     */
    onEducationTap(topic) {
        this.metrics.educationTaps.push({ topic, time: Date.now() });
        this.record('education_tap', topic);
    }

    /**
     * Called when the stage modal is opened/changed.
     */
    onStageModalOpen() {
        this.metrics.stageModalOpens++;
        this.record('stage_modal_open', null);
    }

    onStageChange(oldStage, newStage) {
        this.metrics.stageChanges.push({ from: oldStage, to: newStage, time: Date.now() });
        this.record('stage_change', null, { from: oldStage, to: newStage });
    }

    /**
     * Called when Melod's response arrives (to measure read time).
     */
    onResponseReceived(responseLength) {
        this._lastResponseTime = Date.now();
        this._lastResponseLength = responseLength;
        this.record('response_received', responseLength);
    }

    // ════════════════════════════════════════════════════════════════
    // DERIVED FEATURES — computed at flush time
    // ════════════════════════════════════════════════════════════════

    _computeDerivedFeatures() {
        const f = {};

        // ── Session features ──
        f.session_duration_ms = Date.now() - this.sessionStart;
        f.session_hour = this.metrics.sessionStartHour;
        f.is_late_night = this.metrics.isLateNight;
        f.day_of_week = this.metrics.dayOfWeek;
        f.total_touches = this.metrics.totalTouches;
        f.total_messages_sent = this.metrics.messagesSent;
        f.app_backgrounds = this.metrics.appBackgrounds;
        f.tab_switches = this.metrics.tabSwitches;
        f.orientation_changes = this.metrics.orientationChanges;
        f.paste_events = this.metrics.pasteEvents;

        // ── Typing dynamics (psychomotor indicators) ──
        if (this.metrics.keystrokes.length > 2) {
            f.typing_speed_mean_ms = this._mean(this.metrics.keystrokes);
            f.typing_speed_median_ms = this._median(this.metrics.keystrokes);
            f.typing_speed_std_ms = this._std(this.metrics.keystrokes);
            f.typing_speed_cv = f.typing_speed_std_ms / (f.typing_speed_mean_ms || 1); // coefficient of variation
        }
        f.total_chars_typed = this.metrics.totalCharsTyped;
        f.total_deletions = this.metrics.deletions;
        f.deletion_ratio = this.metrics.totalCharsTyped > 0
            ? this.metrics.deletions / this.metrics.totalCharsTyped
            : 0;

        // ── Message features (social withdrawal / rumination) ──
        if (this.metrics.messageLengths.length > 0) {
            f.message_length_mean = this._mean(this.metrics.messageLengths);
            f.message_length_std = this._std(this.metrics.messageLengths);
            f.message_count = this.metrics.messageLengths.length;

            // Trend: are messages getting shorter? (linear slope)
            if (this.metrics.messageLengths.length >= 3) {
                f.message_length_trend = this._linearSlope(this.metrics.messageLengths);
            }
        }

        // ── Composition time (cognitive load / rumination) ──
        if (this.metrics.compositionStarts.length > 0 && this.metrics.compositionEnds.length > 0) {
            const compositionTimes = [];
            for (let i = 0; i < Math.min(this.metrics.compositionStarts.length, this.metrics.compositionEnds.length); i++) {
                compositionTimes.push(this.metrics.compositionEnds[i] - this.metrics.compositionStarts[i]);
            }
            if (compositionTimes.length > 0) {
                f.composition_time_mean_ms = this._mean(compositionTimes);
                f.composition_time_max_ms = Math.max(...compositionTimes);
            }
        }

        // ── Touch dynamics (psychomotor state) ──
        if (this.metrics.touchVelocities.length > 5) {
            f.touch_velocity_mean = this._mean(this.metrics.touchVelocities);
            f.touch_velocity_std = this._std(this.metrics.touchVelocities);
        }
        if (this.metrics.tapIntervals.length > 3) {
            f.tap_interval_mean_ms = this._mean(this.metrics.tapIntervals);
            f.tap_interval_std_ms = this._std(this.metrics.tapIntervals);
        }
        f.long_presses = this.metrics.longPresses;
        f.double_taps = this.metrics.doubleTaps;

        if (this.metrics.touchPressures.length > 3) {
            f.touch_pressure_mean = this._mean(this.metrics.touchPressures);
            f.touch_pressure_std = this._std(this.metrics.touchPressures);
        }

        // ── Scroll dynamics (agitation / engagement) ──
        if (this.metrics.scrollVelocities.length > 3) {
            f.scroll_velocity_mean = this._mean(this.metrics.scrollVelocities);
            f.scroll_velocity_max = Math.max(...this.metrics.scrollVelocities);
            f.scroll_velocity_std = this._std(this.metrics.scrollVelocities);
        }
        f.scroll_direction_changes = this.metrics.scrollDirectionChanges;
        f.scroll_depths = this.metrics.scrollDepths;

        // ── Device motion (psychomotor agitation / restlessness) ──
        if (this.metrics.deviceMotionSamples.length > 10) {
            f.motion_magnitude_mean = this._mean(this.metrics.deviceMotionSamples);
            f.motion_magnitude_std = this._std(this.metrics.deviceMotionSamples);
            f.motion_magnitude_max = Math.max(...this.metrics.deviceMotionSamples);
            // High variance + high mean = physical agitation
            // Low mean + low variance = stillness (could be normal or psychomotor retardation)
        }

        // ── Check-in engagement ──
        if (this.metrics.sliderInteractions.length > 0) {
            const totalSliderTime = this.metrics.sliderInteractions.reduce((s, i) => s + i.time_spent_ms, 0);
            const totalAdjustments = this.metrics.sliderInteractions.reduce((s, i) => s + i.adjustments, 0);
            f.checkin_total_slider_time_ms = totalSliderTime;
            f.checkin_total_adjustments = totalAdjustments;
            f.checkin_adjustments_per_slider = totalAdjustments / this.metrics.sliderInteractions.length;
            f.checkin_abandoned = this.metrics.checkinAbandoned;
            f.checkin_note_engaged = this.metrics.noteFieldEngaged;
            f.checkin_note_time_ms = this.metrics.noteFieldTime;

            if (this.metrics.checkinStartTime && this.metrics.checkinCompletionTime) {
                f.checkin_duration_ms = this.metrics.checkinCompletionTime - this.metrics.checkinStartTime;
            }
        }

        // ── Navigation patterns (engagement / anhedonia) ──
        f.panel_visits = this.metrics.panelVisits;
        f.education_taps_count = this.metrics.educationTaps.length;
        f.stage_modal_opens = this.metrics.stageModalOpens;

        // Interaction diversity (entropy of panel visits)
        const visits = Object.values(this.metrics.panelVisits);
        if (visits.length > 0) {
            const total = visits.reduce((a, b) => a + b, 0);
            f.navigation_entropy = -visits.reduce((h, v) => {
                const p = v / total;
                return h + (p > 0 ? p * Math.log2(p) : 0);
            }, 0);
        }

        // ── Device / environment ──
        f.battery_level = this.metrics.batteryLevel;
        f.battery_charging = this.metrics.batteryCharging;
        f.connection_type = this.metrics.connectionType;

        return f;
    }

    _hasDerivedMetrics() {
        return this.metrics.totalTouches > 0 || this.metrics.messagesSent > 0 || this.metrics.totalCharsTyped > 0;
    }

    // ════════════════════════════════════════════════════════════════
    // DEVICE API PROBES
    // ════════════════════════════════════════════════════════════════

    async _probeDeviceAPIs() {
        // Battery Status API
        if (navigator.getBattery) {
            try {
                const battery = await navigator.getBattery();
                this.metrics.batteryLevel = Math.round(battery.level * 100);
                this.metrics.batteryCharging = battery.charging;

                battery.addEventListener('levelchange', () => {
                    this.metrics.batteryLevel = Math.round(battery.level * 100);
                    // Very low battery + late night = possible distress signal
                    if (battery.level < 0.15 && this.metrics.isLateNight) {
                        this.record('low_battery_late_night', battery.level);
                    }
                });
                battery.addEventListener('chargingchange', () => {
                    this.metrics.batteryCharging = battery.charging;
                });
            } catch (e) {}
        }

        // Network Information API
        if (navigator.connection) {
            this.metrics.connectionType = navigator.connection.effectiveType;
            navigator.connection.addEventListener('change', () => {
                this.metrics.connectionType = navigator.connection.effectiveType;
                this.record('connection_change', navigator.connection.effectiveType);
            });
        }

        // Screen orientation
        if (screen.orientation) {
            this.metrics.screenOrientation = screen.orientation.type;
        }
    }

    // ════════════════════════════════════════════════════════════════
    // UTILITIES
    // ════════════════════════════════════════════════════════════════

    _checkCircadian() {
        const hour = new Date().getHours();
        this.metrics.isLateNight = hour >= 0 && hour < 5;
        this.metrics.hourOfDay = hour;
    }

    _startFlushCycle() {
        this._flushTimer = setInterval(() => this.flush(), this.flushInterval);
    }

    _uuid() {
        return 'xxxx-xxxx'.replace(/x/g, () => ((Math.random() * 16) | 0).toString(16));
    }

    _mean(arr) {
        if (!arr.length) return 0;
        return arr.reduce((a, b) => a + b, 0) / arr.length;
    }

    _median(arr) {
        if (!arr.length) return 0;
        const sorted = [...arr].sort((a, b) => a - b);
        const mid = Math.floor(sorted.length / 2);
        return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    }

    _std(arr) {
        if (arr.length < 2) return 0;
        const m = this._mean(arr);
        return Math.sqrt(arr.reduce((s, v) => s + (v - m) ** 2, 0) / (arr.length - 1));
    }

    _linearSlope(arr) {
        // Simple linear regression slope — positive = increasing, negative = decreasing
        const n = arr.length;
        if (n < 2) return 0;
        const xMean = (n - 1) / 2;
        const yMean = this._mean(arr);
        let num = 0, den = 0;
        for (let i = 0; i < n; i++) {
            num += (i - xMean) * (arr[i] - yMean);
            den += (i - xMean) ** 2;
        }
        return den > 0 ? num / den : 0;
    }
}

// Export for use
if (typeof module !== 'undefined') module.exports = PassiveCollector;
