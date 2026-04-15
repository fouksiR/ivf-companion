def _render_chat_page(token: str, patient_name: str) -> str:
    safe_name = (patient_name or "").replace("<", "&lt;").replace(">", "&gt;")
    tok = token.replace('"', '&quot;')
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1">
  <title>Pre-visit questionnaire</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500&family=Outfit:wght@200;300;400;500&display=swap" rel="stylesheet">
  <style>
    :root{--white:#ffffff;--text:rgba(255,255,255,0.98);--text-soft:rgba(255,255,255,0.88);--text-muted:rgba(255,255,255,0.62);--line:rgba(255,255,255,0.45);--pill:999px;--radius:28px;--sans:"Outfit",-apple-system,BlinkMacSystemFont,sans-serif;--serif:"Cormorant Garamond","Times New Roman",serif}
    *{box-sizing:border-box;-webkit-tap-highlight-color:transparent;margin:0;padding:0}
    html,body{min-height:100%}
    body{font-family:var(--sans);color:var(--text);display:flex;justify-content:center;
      background:linear-gradient(180deg,rgba(136,162,178,0.55) 0%,rgba(136,162,178,0.38) 35%,rgba(136,162,178,0.18) 60%,rgba(136,162,178,0.30) 100%),
      url("https://images.unsplash.com/photo-1517483000871-1dbf64a6e1c6?auto=format&fit=crop&w=3000&q=90") center 60%/cover no-repeat fixed}
    .app{width:100%;max-width:430px;min-height:100vh;position:relative}
    .shell{position:relative;z-index:2;min-height:100vh;padding:24px 22px 28px;display:flex;flex-direction:column}
    .screen{display:none;flex:1;animation:fade .4s ease}
    .screen.active{display:flex}
    @keyframes fade{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
    .hero{width:100%;min-height:calc(100vh - 60px);display:flex;flex-direction:column;justify-content:space-between;padding-top:4vh;padding-bottom:3vh}
    .hero-center{text-align:center;padding:0 10px;margin:auto 0}
    .rule{width:100%;height:1px;background:var(--line);margin:18px 0}
    .hero-title{margin:0 auto;max-width:20ch;font-family:var(--sans);font-size:1.15rem;line-height:1.4;font-weight:500;color:var(--white);text-shadow:0 2px 12px rgba(50,70,90,0.40)}
    .hero-copy{max-width:34ch;margin:0 auto;font-size:.93rem;line-height:1.85;color:var(--text-soft);font-weight:300;text-shadow:0 1px 8px rgba(50,70,90,0.35)}
    .meta{margin-top:14px;font-size:.76rem;letter-spacing:.12em;color:var(--text-muted);text-transform:uppercase;text-align:center}
    .cta-row{display:flex;justify-content:center;margin-top:22px}
    .button{appearance:none;border:1px solid rgba(255,255,255,0.50);background:rgba(255,255,255,0.15);color:var(--white);font-family:var(--sans);padding:14px 24px;border-radius:var(--pill);cursor:pointer;font-size:.76rem;text-transform:uppercase;letter-spacing:.26em;font-weight:300;transition:background .2s,border-color .2s,transform .16s;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
    .button:hover,.button:focus-visible{outline:none;background:rgba(255,255,255,0.25);border-color:rgba(255,255,255,0.72);transform:translateY(-1px)}
    .button[disabled]{opacity:.36;cursor:default;transform:none}
    .panel-wrap{display:flex;align-items:center;justify-content:center;flex:1}
    .panel{width:100%;border-radius:var(--radius);border:1px solid rgba(255,255,255,0.24);background:linear-gradient(180deg,rgba(120,150,170,0.28),rgba(140,170,190,0.16));box-shadow:0 24px 48px rgba(40,70,100,0.12);padding:26px 18px 22px;backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px)}
    .bodycopy{max-width:31ch;margin:0 auto;text-align:center;color:var(--text-soft);font-size:.95rem;line-height:1.85;font-weight:300}
    .q-scroll{flex:1;display:flex;flex-direction:column;justify-content:center;padding:0 6px;background:radial-gradient(ellipse at center,rgba(60,85,110,0.35) 0%,transparent 70%);border-radius:20px}
    .q-counter{text-align:center;font-size:.7rem;text-transform:uppercase;letter-spacing:.3em;color:var(--text-muted);margin-bottom:10px}
    .progress-wrap{height:2.5px;border-radius:2px;background:rgba(255,255,255,0.18);overflow:hidden;margin-bottom:32px}
    .progress-bar{height:100%;border-radius:2px;background:rgba(255,255,255,0.60);transition:width .35s ease;width:0%}
    .q-statement-single{text-align:center;font-size:1.1rem;line-height:1.75;color:var(--white);font-weight:400;margin:0 auto 34px;max-width:26ch;text-shadow:0 2px 14px rgba(30,50,70,0.55),0 0 30px rgba(60,90,120,0.25);animation:fade .35s ease}
    .scale-legend{display:flex;justify-content:space-between;padding:0 2px;margin:0 0 8px}
    .scale-legend span{font-size:.6rem;text-transform:uppercase;letter-spacing:.14em;color:var(--text-muted);font-weight:300}
    .rating-row{display:flex;justify-content:space-between;padding:0 2px}
    .rating-btn{appearance:none;border:none;background:none;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:6px 10px;flex:1;transition:transform .15s}
    .rating-btn:hover{transform:scale(1.12)}
    .dot{width:42px;height:42px;border-radius:50%;border:1.5px solid rgba(255,255,255,0.38);background:rgba(255,255,255,0.10);transition:background .18s,border-color .18s,box-shadow .18s,transform .15s;display:flex;align-items:center;justify-content:center;font-size:.78rem;font-weight:400;color:var(--text-muted)}
    .rating-btn.selected .dot{background:rgba(255,255,255,0.34);border-color:rgba(255,255,255,0.78);box-shadow:0 0 16px rgba(255,255,255,0.16);color:var(--white);font-weight:500;transform:scale(1.08)}
    .sending{text-align:center;color:var(--text-soft);font-size:.9rem;margin-top:20px}
    @media(max-width:390px){.shell{padding-left:16px;padding-right:16px}.hero-title{font-size:1.05rem}.q-statement-single{font-size:.96rem}.dot{width:36px;height:36px;font-size:.7rem}.rating-btn{padding:6px 6px}}
  </style>
</head>
<body>
<main class="app"><div class="shell">
  <section class="screen active" id="screenHome">
    <div class="hero">
      <div></div>
      <div class="hero-center">
        <div class="rule"></div>
        <h1 class="hero-title">Hi """ + safe_name + """</h1>
        <div class="rule"></div>
        <p class="hero-copy">These short questions help us understand how you like to receive information, make decisions, and feel supported.</p>
        <p class="hero-copy" style="margin-top:14px">This allows Dr. Fouks to tailor your care from the very first minute.</p>
        <div class="meta">~ 1 minute</div>
        <div class="cta-row"><button class="button" id="enterBtn">Begin &rarr;</button></div>
      </div>
      <div></div>
    </div>
  </section>
  <section class="screen" id="screenQ">
    <div class="q-scroll">
      <div class="q-counter" id="qCounter"></div>
      <div class="progress-wrap"><div class="progress-bar" id="progressBar"></div></div>
      <p class="q-statement-single" id="qText"></p>
      <div class="scale-legend"><span>Strongly disagree</span><span>Strongly agree</span></div>
      <div class="rating-row" id="ratingRow"></div>
    </div>
  </section>
  <section class="screen" id="screenDone">
    <div class="panel-wrap">
      <div class="panel">
        <div class="rule"></div>
        <p class="bodycopy" style="margin-bottom:10px">Your responses will help Dr. Fouks tailor your consultation.</p>
        <p class="bodycopy" style="font-family:var(--serif);font-size:1.6rem;margin-bottom:18px;color:var(--white)">See you very soon</p>
        <div class="rule"></div>
        <div id="statusMsg" class="sending"></div>
      </div>
    </div>
  </section>
</div></main>
<script>
var TOKEN = \"""" + tok + """\";
var QUESTIONS = [
  "I feel confident managing my health and making decisions about my care.",
  "I prefer my doctor to clearly guide me on what to do rather than leaving decisions open.",
  "I often feel worried or overwhelmed when thinking about my health or treatment.",
  "I usually research or think through my options before seeing a doctor.",
  "I sometimes question or double-check medical advice before following it.",
  "I find it easy to follow medical plans and instructions consistently.",
  "I prefer to take time to think before committing to a treatment plan."
];
var answers = new Array(7).fill(null);
var currentQ = 0;
var screens = {home:document.getElementById("screenHome"),q:document.getElementById("screenQ"),done:document.getElementById("screenDone")};
var qText = document.getElementById("qText");
var qCounter = document.getElementById("qCounter");
var progressBar = document.getElementById("progressBar");
var ratingRow = document.getElementById("ratingRow");
var statusMsg = document.getElementById("statusMsg");
function showScreen(id){Object.values(screens).forEach(function(el){el.classList.remove("active")});screens[id].classList.add("active");window.scrollTo({top:0})}
function scoreProfile(v){var q1=v[0],q2=v[1],q3=v[2],q4=v[3],q5=v[4],q6=v[5],q7=v[6];var p=[{type:"Driver",score:q1+q4+q6-q2-q3,copy:"High activation and autonomy. Often best with concise options, efficient discussion, and shared decision-making."},{type:"Support-Seeker",score:q2+q3+(6-q1),copy:"May value reassurance, clear structure, and steady guidance. Usually benefits from warmth, clarity, and predictable next steps."},{type:"Avoider",score:(6-q1)+(6-q6)+q7+q3,copy:"May feel overloaded or hesitant. Often best supported with simplified plans, reduced friction, and one clear step at a time."},{type:"Skeptic",score:q5+q4+Math.max(0,q7-2),copy:"Often prefers evidence, transparency, and time to evaluate options. Usually responds well to rationale, data, and room for questions."}];p.sort(function(a,b){return b.score-a.score});return p}
function renderQuestion(){qCounter.textContent=(currentQ+1)+" of "+QUESTIONS.length;progressBar.style.width=Math.round(currentQ/QUESTIONS.length*100)+"%";qText.style.animation="none";qText.offsetHeight;qText.style.animation="fade .35s ease";qText.textContent=QUESTIONS[currentQ];ratingRow.innerHTML="";for(var v=1;v<=5;v++){(function(val){var btn=document.createElement("button");btn.type="button";btn.className="rating-btn";btn.setAttribute("aria-label","Rate "+val);var dot=document.createElement("span");dot.className="dot";dot.textContent=val;btn.appendChild(dot);if(answers[currentQ]===val)btn.classList.add("selected");btn.addEventListener("click",function(){answers[currentQ]=val;ratingRow.querySelectorAll(".rating-btn").forEach(function(b){b.classList.remove("selected")});btn.classList.add("selected");setTimeout(function(){if(currentQ<QUESTIONS.length-1){currentQ++;renderQuestion()}else{progressBar.style.width="100%";submitResults()}},250)});ratingRow.appendChild(btn)})(v)}}
async function submitResults(){showScreen("done");statusMsg.textContent="Sending your responses...";var allScores=scoreProfile(answers);var primary=allScores[0];var payload={answers:answers,profile_type:primary.type,profile_score:primary.score,profile_copy:primary.copy,all_scores:allScores.map(function(p){return{type:p.type,score:p.score}})};try{var r=await fetch("/api/questionnaire/"+TOKEN,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});if(r.ok){statusMsg.textContent=""}else{var d=await r.json();statusMsg.textContent="Something went wrong: "+(d.detail||r.status)}}catch(e){statusMsg.textContent="Network error. Your responses may not have been saved."}}
document.getElementById("enterBtn").addEventListener("click",function(){currentQ=0;renderQuestion();showScreen("q")});
</script>
</body>
</html>"""
