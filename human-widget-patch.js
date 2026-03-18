/*
 * MELOD-AI HUMAN ESCALATION WIDGET
 * ================================
 * 
 * HOW TO INTEGRATE INTO index.html:
 * 
 * 1. Copy the <style> block below into your existing <style> section
 * 2. Copy the HTML widget div just before </body>
 * 3. Copy the JavaScript into your existing <script> section
 * 4. In your existing check-in submission handler, call:
 *      checkAndShowHumanWidget(response)
 *    where `response` is the JSON from POST /checkin
 * 5. In your existing chat message handler, after receiving AI response, call:
 *      checkEscalationFromChat(responseData)
 * 
 * The widget will:
 * - Show a gentle "Talk to someone" button when RED escalation is detected
 * - Let the patient write a brief message about what they need
 * - POST to /escalate/human endpoint
 * - Show confirmation
 * - The clinician dashboard picks this up instantly via polling
 */

// ══════════════════════════════════════════════════════════
// PASTE THIS CSS INTO YOUR <style> SECTION:
// ══════════════════════════════════════════════════════════
/*

.human-widget-container {
    position: fixed;
    bottom: 80px;
    right: 20px;
    z-index: 500;
    display: none;
}

.human-widget-container.visible {
    display: block;
    animation: hwSlideUp 0.5s ease-out;
}

@keyframes hwSlideUp {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
}

.hw-trigger {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 20px;
    background: white;
    border: 1px solid #eae8e4;
    border-radius: 24px;
    box-shadow: 0 2px 12px rgba(42,39,34,0.1);
    cursor: pointer;
    transition: all 0.3s ease;
    font-family: 'Montserrat', sans-serif;
    font-size: 14px;
    font-weight: 500;
    color: #3e3a34;
}

.hw-trigger:hover {
    box-shadow: 0 4px 20px rgba(42,39,34,0.15);
    transform: translateY(-1px);
}

.hw-trigger-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #c44040;
    animation: hwDotPulse 2s ease-in-out infinite;
}

@keyframes hwDotPulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.6; transform: scale(1.3); }
}

.hw-panel {
    display: none;
    position: absolute;
    bottom: 56px;
    right: 0;
    width: 320px;
    background: white;
    border: 1px solid #eae8e4;
    border-radius: 12px;
    box-shadow: 0 4px 24px rgba(42,39,34,0.12);
    padding: 24px;
    animation: hwFadeIn 0.3s ease-out;
}

.hw-panel.open { display: block; }

@keyframes hwFadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}

.hw-panel-title {
    font-family: 'Cormorant Garamond', serif;
    font-size: 18px;
    font-weight: 400;
    color: #2a2722;
    margin-bottom: 8px;
}

.hw-panel-subtitle {
    font-size: 13px;
    color: #7a7268;
    line-height: 1.5;
    margin-bottom: 16px;
}

.hw-textarea {
    width: 100%;
    min-height: 80px;
    padding: 12px;
    border: 1px solid #d4d0ca;
    border-radius: 8px;
    font-family: 'Montserrat', sans-serif;
    font-size: 13px;
    color: #2a2722;
    resize: vertical;
    line-height: 1.5;
}

.hw-textarea:focus {
    outline: none;
    border-color: #9c9486;
}

.hw-textarea::placeholder {
    color: #b8b2a8;
}

.hw-actions {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 12px;
}

.hw-btn-send {
    font-family: 'Montserrat', sans-serif;
    font-size: 13px;
    font-weight: 500;
    color: white;
    background: #2a2722;
    border: none;
    padding: 10px 24px;
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.2s;
}

.hw-btn-send:hover { background: #3e3a34; }
.hw-btn-send:disabled { background: #b8b2a8; cursor: not-allowed; }

.hw-btn-cancel {
    font-family: 'Montserrat', sans-serif;
    font-size: 12px;
    color: #9c9486;
    background: none;
    border: none;
    cursor: pointer;
    padding: 8px;
}

.hw-btn-cancel:hover { color: #7a7268; }

.hw-confirmation {
    display: none;
    text-align: center;
    padding: 12px 0;
}

.hw-confirmation.show { display: block; }

.hw-confirmation-icon {
    font-size: 32px;
    margin-bottom: 8px;
    color: #4a7c59;
}

.hw-confirmation-text {
    font-family: 'Cormorant Garamond', serif;
    font-size: 16px;
    color: #2a2722;
    margin-bottom: 6px;
}

.hw-confirmation-sub {
    font-size: 12px;
    color: #7a7268;
    line-height: 1.5;
}

.hw-note {
    font-size: 11px;
    color: #b8b2a8;
    text-align: center;
    margin-top: 12px;
    line-height: 1.4;
}

*/


// ══════════════════════════════════════════════════════════
// PASTE THIS HTML JUST BEFORE </body> IN index.html:
// ══════════════════════════════════════════════════════════
/*

<!-- Human Escalation Widget -->
<div class="human-widget-container" id="humanWidget">
    <div class="hw-trigger" id="hwTrigger" onclick="toggleHumanPanel()">
        <span class="hw-trigger-dot"></span>
        <span>Talk to someone</span>
    </div>
    <div class="hw-panel" id="hwPanel">
        <div id="hwForm">
            <div class="hw-panel-title">We're here for you</div>
            <div class="hw-panel-subtitle">
                If you'd like to talk to a real person on your care team, 
                let us know and someone will reach out.
            </div>
            <textarea class="hw-textarea" id="hwMessage" 
                placeholder="Is there anything specific you'd like to talk about? (optional)"></textarea>
            <div class="hw-actions">
                <button class="hw-btn-cancel" onclick="toggleHumanPanel()">Not now</button>
                <button class="hw-btn-send" id="hwSendBtn" onclick="sendHumanEscalation()">
                    Request callback
                </button>
            </div>
            <div class="hw-note">
                Your care team will receive this right away.<br>
                If you are in crisis, please call Lifeline on 13 11 14.
            </div>
        </div>
        <div class="hw-confirmation" id="hwConfirmation">
            <div class="hw-confirmation-icon">✓</div>
            <div class="hw-confirmation-text">Request received</div>
            <div class="hw-confirmation-sub">
                Someone from your care team has been notified<br>
                and will be in touch with you soon.
            </div>
        </div>
    </div>
</div>

*/


// ══════════════════════════════════════════════════════════
// PASTE THIS JAVASCRIPT INTO YOUR <script> SECTION:
// ══════════════════════════════════════════════════════════

// ── Human Widget State ────────────────────────────────────
let humanWidgetVisible = false;
let humanPanelOpen = false;
let humanEscalationSent = false;

/**
 * Call this after receiving the check-in response.
 * The /checkin endpoint returns { show_human_widget: true } when RED.
 */
function checkAndShowHumanWidget(checkinResponse) {
    if (checkinResponse && checkinResponse.show_human_widget) {
        showHumanWidget();
    } else if (checkinResponse && checkinResponse.escalation_level === 'RED') {
        showHumanWidget();
    }
}

/**
 * Call this after receiving a chat response from the backend.
 * If the response includes escalation data, show the widget.
 */
function checkEscalationFromChat(chatResponse) {
    // The backend might include escalation_level in the response
    if (chatResponse && chatResponse.escalation_level === 'RED') {
        showHumanWidget();
    }
    // Or check if signal context indicates RED
    if (chatResponse && chatResponse.signal_assessment &&
        chatResponse.signal_assessment.escalation_level === 'RED') {
        showHumanWidget();
    }
}

function showHumanWidget() {
    if (humanEscalationSent) return; // Don't re-show after they already requested
    const widget = document.getElementById('humanWidget');
    if (widget && !humanWidgetVisible) {
        humanWidgetVisible = true;
        widget.classList.add('visible');
    }
}

function hideHumanWidget() {
    const widget = document.getElementById('humanWidget');
    if (widget) {
        humanWidgetVisible = false;
        widget.classList.remove('visible');
    }
}

function toggleHumanPanel() {
    const panel = document.getElementById('hwPanel');
    humanPanelOpen = !humanPanelOpen;
    if (humanPanelOpen) {
        panel.classList.add('open');
    } else {
        panel.classList.remove('open');
    }
}

async function sendHumanEscalation() {
    const btn = document.getElementById('hwSendBtn');
    const msg = document.getElementById('hwMessage');
    const form = document.getElementById('hwForm');
    const confirm = document.getElementById('hwConfirmation');
    
    btn.disabled = true;
    btn.textContent = 'Sending…';
    
    try {
        // Get patient info from your existing app state
        // Adjust these variable names to match your index.html
        const patientId = window.patientId || window.currentPatientId || 'unknown';
        const patientName = window.patientName || window.currentPatientName || '';
        
        // Get latest check-in scores if available
        let currentScores = {};
        if (window.lastCheckIn) {
            currentScores = window.lastCheckIn;
        }
        
        const payload = {
            patient_id: patientId,
            patient_name: patientName,
            reason: msg.value.trim() || 'Patient requested human support (no specific reason given)',
            current_scores: currentScores,
            urgency: 'high'
        };
        
        const response = await fetch(`${window.API_BASE || ''}/escalate/human`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (!response.ok) throw new Error('Request failed');
        
        // Show confirmation
        form.style.display = 'none';
        confirm.classList.add('show');
        humanEscalationSent = true;
        
        // Auto-close after 8 seconds
        setTimeout(() => {
            toggleHumanPanel();
            // Change trigger text
            const trigger = document.getElementById('hwTrigger');
            if (trigger) {
                trigger.innerHTML = '<span style="color: #4a7c59;">✓</span><span>Request sent</span>';
                trigger.style.cursor = 'default';
                trigger.onclick = null;
            }
        }, 8000);
        
    } catch (err) {
        console.error('Escalation failed:', err);
        btn.disabled = false;
        btn.textContent = 'Try again';
        // Even if the backend is down, show a fallback
        alert('We couldn\'t send your request right now. If you need immediate support, please call Lifeline on 13 11 14.');
    }
}


// ══════════════════════════════════════════════════════════
// WIRING INTO EXISTING CHECK-IN HANDLER:
// ══════════════════════════════════════════════════════════
// 
// Find your existing check-in submission code in index.html.
// It probably looks something like:
//
//   async function submitCheckIn() {
//       const data = { patient_id: ..., mood: ..., anxiety: ..., ... };
//       const res = await fetch(`${API_BASE}/checkin`, { ... });
//       const result = await res.json();
//       // ADD THIS LINE:
//       checkAndShowHumanWidget(result);
//   }
//
// And in your chat handler:
//
//   async function sendMessage(text) {
//       const res = await fetch(`${API_BASE}/chat`, { ... });
//       const result = await res.json();
//       // ADD THIS LINE:
//       checkEscalationFromChat(result);
//   }


// ══════════════════════════════════════════════════════════
// ALSO: UPDATE YOUR PASSIVE COLLECTOR FLUSH
// ══════════════════════════════════════════════════════════
//
// In your PassiveCollector.flush() method, update the endpoint:
//
//   async flush() {
//       const data = this.collect();  // your existing collect method
//       data.patient_id = window.patientId || 'unknown';
//       
//       try {
//           const res = await fetch(`${API_BASE}/signals`, {
//               method: 'POST',
//               headers: { 'Content-Type': 'application/json' },
//               body: JSON.stringify(data)
//           });
//           const result = await res.json();
//           
//           // Check if signals triggered RED
//           if (result.escalation_level === 'RED') {
//               showHumanWidget();
//           }
//       } catch (err) {
//           console.error('Signal flush failed:', err);
//       }
//   }
