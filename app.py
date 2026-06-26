#========INTERFACE STREAMLIT (la vue)==========

import streamlit as st  # Interface web Streamlit
import json  # Lecture / écriture JSON (alertes)
import os  # Gestion fichiers système
from datetime import datetime, timedelta  # Gestion des heures
from collections import deque
import time
import requests
import streamlit.components.v1 as components  # Pour injecter le récepteur postMessage dans la page principale

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ARCHIVE_RETENTION_DAYS = 30 

# IDENTIFIANTS ADMIN (À PROTÉGER EN PRODUCTION)

ADMIN_USER = "admin"  # Identifiant de connexion
ADMIN_PASSWORD = "admin"  # Mot de passe (à changer en prod)

import secrets
import hashlib

if "server_sessions" not in st.session_state:
    st.session_state.server_sessions = {}

SESSION_COOKIE_NAME = "leclerc_session"
SESSION_MAX_AGE     = 28800  # 8h max

def _get_token_from_cookie() -> str | None:
    try:
        cookie_header = st.context.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(SESSION_COOKIE_NAME + "="):
                return part[len(SESSION_COOKIE_NAME) + 1:]
    except Exception:
        pass
    return None

def _set_session_cookie(token: str):
    st.markdown(
        f"""<script>
        document.cookie = "{SESSION_COOKIE_NAME}={token}; path=/; SameSite=Strict";
        </script>""",
        unsafe_allow_html=True
    )

def _delete_session_cookie():
    st.markdown(
        f"""<script>
        document.cookie = "{SESSION_COOKIE_NAME}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Strict";
        </script>""",
        unsafe_allow_html=True
    )

def is_authenticated() -> bool:
    # ── Priorité 1 : session_state (cycle immédiat après login) ──
    # Évite le problème de timing où le cookie JS n'est pas encore
    # posé quand Streamlit re-exécute la page après st.rerun()
    if st.session_state.get("_auth_token"):
        token      = st.session_state["_auth_token"]
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        sessions   = st.session_state.server_sessions
        if token_hash in sessions:
            if time.time() - sessions[token_hash] <= SESSION_MAX_AGE:
                return True
        # Token invalide → on nettoie
        st.session_state.pop("_auth_token", None)

    # ── Priorité 2 : cookie HTTP (rechargements de page, F5) ──
    token = _get_token_from_cookie()
    if not token:
        return False
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    sessions   = st.session_state.server_sessions
    if token_hash not in sessions:
        return False
    if time.time() - sessions[token_hash] > SESSION_MAX_AGE:
        del sessions[token_hash]
        return False
    # On met en cache dans session_state pour les cycles suivants
    st.session_state["_auth_token"] = token
    return True

def create_session() -> str:
    token      = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    st.session_state.server_sessions[token_hash] = time.time()
    # Cache immédiat dans session_state
    st.session_state["_auth_token"] = token
    return token

def destroy_session():
    token = st.session_state.get("_auth_token") or _get_token_from_cookie()
    if token:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        st.session_state.server_sessions.pop(token_hash, None)
    st.session_state.pop("_auth_token", None)
    _delete_session_cookie()

def load_stats():
    if not os.path.exists("stats.jsonl"):
        return []
    stats = []
    with open("stats.jsonl", "r") as f:
        for line in f:
            try:
                stats.append(json.loads(line.strip()))
            except:
                pass
    return stats

# CONFIGURATION PAGE STREAMLIT

st.set_page_config(
    page_title="E.Leclerc - Sécurité IA",  # Titre onglet navigateur
    layout="wide",  # Layout large
    page_icon="🛡️"  # Icône
)

# STYLE CSS GLOBAL
# On ajoute les styles de l'overlay plein écran ici, dans la page PRINCIPALE (pas dans une iframe).
# C'est crucial : l'overlay doit vivre dans le document parent pour couvrir TOUTE la page,
# sidebar Streamlit incluse.
st.markdown("""
<style>
    .stApp { background-color: white !important; color: #0066b2 !important; }

    [data-testid="stWidgetLabel"] p {
        color: black !important;
        font-weight: bold !important;
        font-size: 1.05rem !important;
    }

    [data-testid="stSidebar"] {
        background-color: #0066b2 !important;
    }

    [data-testid="stSidebar"] * {
        color: white !important;
    }

    .header {
        background-color: #0066b2;
        color: white;
        padding: 18px;
        border-bottom: 6px solid #ff6000;
        text-align: center;
        border-radius: 0 0 12px 12px;
        margin-bottom: 20px;
    }

    .card {
        background-color: #f8f9fa;
        border-left: 6px solid red;
        padding: 12px;
        border-radius: 10px;
        margin-bottom: 10px;
        color: #333
    }
    
    div[data-testid="stButton"] > button {
        background-color: white !important;
        color: #0066b2 !important;
        border: 2px solid #0066b2 !important;
        border-radius: 8px !important;
        font-weight: bold !important;
        transition: all 0.2s ease-in-out !important;
    }

    div[data-testid="stButton"] > button:hover,
    div[data-testid="stButton"] > button:hover * {
        background-color: #0066b2 !important;
        color: white !important;
        transform: scale(1.02) !important;
    }
    
    div[data-testid="stDownloadButton"] > button {
        background-color: white !important;
        color: #0066b2 !important;
        border: 2px solid #0066b2 !important;
        border-radius: 8px !important;
        font-weight: bold !important;
        transition: all 0.2s ease-in-out !important;
    }

    div[data-testid="stDownloadButton"] > button:hover,
    div[data-testid="stDownloadButton"] > button:hover * {
        background-color: #0066b2 !important;
        color: white !important;
        transform: scale(1.02) !important;
    }

    [data-testid="stSidebar"] div[data-testid="stButton"] > button {
        background-color: #0066b2 !important;
        color: white !important;
        border: 2px solid white !important;
    }

    [data-testid="stSidebar"] div[data-testid="stButton"] > button:hover,
    [data-testid="stSidebar"] div[data-testid="stButton"] > button:hover * {
        background-color: white !important;
        color: #0066b2 !important;
    }

    div[data-testid="stExpander"] > details > summary {
        background-color: #ff6000 !important;
        color: #0066b2 !important;
        border-radius: 10px !important;
        padding: 10px 15px !important;
        font-weight: bold !important;
        transition: all 0.2s ease-in-out !important;
    }

    div[data-testid="stExpander"] > details > summary * {
        color: #0066b2 !important;
    }

    div[data-testid="stExpander"] > details > summary:hover {
        background-color: #0066b2 !important;
    }

    div[data-testid="stExpander"] > details > summary:hover * {
        color: white !important;
    }
    
    div[data-testid="stButton"] > button:disabled {
        background-color: white !important;
        color: #0066b2 !important;
        border: 2px solid #0066b2 !important;
        opacity: 1 !important;
        cursor: default !important;
    }

    /* Bouton VP actif (classé VP = disabled) → vert */
    div[data-testid="stButton"] > button:disabled:has(p:contains("◀")) {
        background-color: #1a7a1a !important;
        color: white !important;
        border: 2px solid #1a7a1a !important;
    }

    /* Bouton FP inactif (non classé) → style normal blanc/bleu */
    div[data-testid="stButton"] > button p {
        color: inherit !important;
        font-weight: bold !important;
    }
    [data-testid="stMetricValue"] {
        color: #0066b2 !important;
        font-size: 1.8rem !important;
    }
    [data-testid="stMetricLabel"] {
        color: #333 !important;
        font-weight: normal !important;
        font-size: 0.88rem !important;
    }
    [data-testid="stMetricDelta"] {
        font-size: 0.82rem !important;
    }
    [data-testid="stSidebar"] [data-testid="stMetricValue"] {
        color: white !important;
    }
    [data-testid="stSidebar"] [data-testid="stMetricLabel"] {
        color: rgba(255,255,255,0.85) !important;
    }
    [data-testid="stSidebar"] [data-testid="stMetricDelta"] {
        color: rgba(255,255,255,0.7) !important;
    }

</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------
# PONT JAVASCRIPT PARENT (invisible, height=0)
# Ce bloc tourne dans une iframe enfant mais injecte l'overlay
# dans window.parent.document (la vraie page Streamlit).
# Il écoute aussi les messages postMessage venant des iframes
# de caméras pour ouvrir l'overlay plein écran.
# ---------------------------------------------------------
components.html("""
<script>
// Liste complète des caméras (reçue via postMessage au premier clic ⛶)
let ALL_CAMS = [];
// Index de la caméra affichée (-1 = overlay fermé)
let currentIdx = -1;

// ---------------------------------------------------------
// ensureOverlay()
// Crée l'élément #fs-overlay dans le document PARENT une seule fois.
// On cible window.parent.document car components.html() tourne dans
// une iframe enfant mais a besoin d'injecter dans la page principale.
// ---------------------------------------------------------
function ensureOverlay() {
    const parentDoc = window.parent.document;
    if (parentDoc.getElementById('fs-overlay')) return; // déjà créé → on sort

    const div = parentDoc.createElement('div');
    div.id = 'fs-overlay';

    // Style inline de l'overlay : position fixed pour couvrir toute la page parent
    div.style.cssText = `
        display: none;
        position: fixed;
        top: 0; left: 0;
        width: 100vw; height: 100vh;
        background: rgba(0,0,0,0.97);
        z-index: 99999;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    `;

    div.innerHTML = `
        <img id="fs-img" src="" alt="flux caméra" style="max-width:95%;max-height:82vh;object-fit:contain;border:3px solid #ff6000;border-radius:6px;">
        <div class="fs-nav" style="display:flex;align-items:center;gap:16px;margin-top:14px;">
            <button id="cap" style="background:rgba(255,255,255,0.15);color:white;border:2px solid white;border-radius:8px;padding:8px 20px;font-size:1.1rem;cursor:pointer;font-weight:bold;">📸 Capture</button>
            <button id="fs-prev" style="background:rgba(255,255,255,0.15);color:white;border:2px solid white;border-radius:8px;padding:8px 20px;font-size:1.1rem;cursor:pointer;font-weight:bold;">◀ Précédent</button>
            <span id="fs-cam-name" style="color:#ff6000;font-size:1rem;font-weight:bold;min-width:200px;text-align:center;"></span>
            <button id="fs-next" style="background:rgba(255,255,255,0.15);color:white;border:2px solid white;border-radius:8px;padding:8px 20px;font-size:1.1rem;cursor:pointer;font-weight:bold;">Suivant ▶</button>
            <button id="fs-close" style="background:rgba(255,255,255,0.15);color:white;border:2px solid white;border-radius:8px;padding:8px 20px;font-size:1.1rem;cursor:pointer;font-weight:bold;">✕ Fermer</button>
        </div>
    `;
    // L'overlay est caché par défaut ; la classe 'active' l'affiche
    parentDoc.body.appendChild(div);

    // Boutons de navigation dans l'overlay
    // FIX BUG 2 : takeSnapshot() utilisait `parentDoc` non défini dans son scope.
    // On passe maintenant `parentDoc` en paramètre explicite pour éviter
    // toute ambiguïté de portée entre les différentes iframes.
    parentDoc.getElementById('cap').onclick    = () => takeSnapshot(parentDoc);
    parentDoc.getElementById('fs-close').onclick = closeFS;
    parentDoc.getElementById('fs-prev').onclick  = () => navigate(-1);
    parentDoc.getElementById('fs-next').onclick  = () => navigate(+1);

    // Active l'affichage flex quand la classe 'active' est ajoutée
    const style = parentDoc.createElement('style');
    style.textContent = '#fs-overlay.active { display: flex !important; }';
    parentDoc.head.appendChild(style);

    // Clic sur le fond noir (hors image) → ferme l'overlay
    div.onclick = (e) => { if (e.target === div) closeFS(); };

    // Navigation clavier : ←/→ changent de caméra, Echap ferme l'overlay
    parentDoc.addEventListener('keydown', function(e) {
        if (currentIdx === -1) return;
        if (e.key === 'Escape')     closeFS();
        if (e.key === 'ArrowLeft')  navigate(-1);
        if (e.key === 'ArrowRight') navigate(+1);
        if (e.key === 'c' || e.key === 'C') takeSnapshot(parentDoc);
    });
}

// ---------------------------------------------------------
// takeSnapshot(parentDoc)
// FIX BUG 2 : parentDoc est maintenant passé en paramètre.
// L'ancienne version référençait `parentDoc` depuis le scope
// de ensureOverlay() ce qui causait une ReferenceError au clic.
// ---------------------------------------------------------
function takeSnapshot(parentDoc) {
    // Sécurité : ne rien faire si aucune caméra n'est affichée
    if (currentIdx === -1) return;

    // Récupération de la caméra courante via l'index global
    const cam = ALL_CAMS[currentIdx];
    const btn = parentDoc.getElementById('cap');
    const originalText = btn.textContent;

    // Feedback visuel immédiat pendant la requête
    btn.textContent = "⏳ Capture...";
    btn.style.borderColor = "#ff6000";

    // Appel vers le serveur IA pour déclencher la capture
    fetch("/snapshot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cam_id: cam.id })
    })
    .then(response => {
        if (response.ok) {
            btn.textContent = "✅ OK";
            btn.style.color = "#28a745";
        } else {
            btn.textContent = "❌ Erreur";
            btn.style.color = "#ff0000";
        }
    })
    .catch(() => {
        // Erreur réseau (serveur injoignable)
        btn.textContent = "⚠️ Réseau";
    })
    .finally(() => {
        // Retour à l'état initial après 1.5 seconde
        setTimeout(() => {
            btn.textContent = originalText;
            btn.style.color = "white";
            btn.style.borderColor = "white";
        }, 1500);
    });
}

// ---------------------------------------------------------
// loadCam(idx) — Charge le flux de la caméra idx dans #fs-img
//
// FIX NAVIGATION GLOBALE :
// On affiche maintenant le nom de la zone en plus du nom de la cam.
// Chaque cam dans ALL_CAMS possède un champ `zone` (ex: "🍾 Alcool")
// ajouté côté Python lors de la construction de all_cams.
// Format affiché : "🎥 Champagnes | 🍾 Alcool (3/13 global)"
// ---------------------------------------------------------
function loadCam(idx) {
    const parentDoc = window.parent.document;
    const cam = ALL_CAMS[idx];
    const imgEl = parentDoc.getElementById('fs-img');
    const nameEl = parentDoc.getElementById('fs-cam-name');

    // FIX FLUX : on charge le flux uniquement dans l'overlay (la grille reste vide)
    if (imgEl) imgEl.src = cam.url;

    // Affichage : "🎥 Nom cam | Zone (position/total global)"
    if (nameEl) {
        const zoneLabel = cam.zone ? ' | ' + cam.zone : '';
        nameEl.textContent = '🎥 ' + cam.name + zoneLabel + ' (' + (idx + 1) + '/' + ALL_CAMS.length + ' global)';
    }
}

// ---------------------------------------------------------
// openFS(idx) — Affiche l'overlay sur la caméra idx
// ---------------------------------------------------------
function openFS(idx) {
    currentIdx = idx;
    loadCam(idx);
    window.parent.document.getElementById('fs-overlay').classList.add('active');
}

// ---------------------------------------------------------
// navigate(dir) — Passe à la caméra suivante (+1) ou précédente (-1)
// Navigation CIRCULAIRE et GLOBALE sur ALL_CAMS (toutes zones).
// Exemple : depuis la dernière cam (13/13) → Suivant → cam 1/13.
// Depuis la cam 1/13 → Précédent → cam 13/13.
// Le label affiche toujours la zone courante pour s'y retrouver.
// ---------------------------------------------------------
function navigate(dir) {
    if (currentIdx === -1) return;
    // Modulo sur ALL_CAMS.length → navigation circulaire sans jamais bloquer
    currentIdx = (currentIdx + dir + ALL_CAMS.length) % ALL_CAMS.length;
    loadCam(currentIdx);
}

// ---------------------------------------------------------
// closeFS() — Ferme l'overlay et coupe le flux MJPEG
// Couper le src libère la connexion HTTP au serveur caméra.
// La grille reste inchangée (ses img ont src="" de toute façon).
// ---------------------------------------------------------
function closeFS() {
    const parentDoc = window.parent.document;
    const overlay = parentDoc.getElementById('fs-overlay');
    if(overlay) overlay.classList.remove('active');
    
    // On coupe le flux pour libérer la connexion MJPEG
    const img = parentDoc.getElementById('fs-img');
    if(img) img.src = '';
    
    currentIdx = -1;
}

// ---------------------------------------------------------
// RÉCEPTEUR DES MESSAGES postMessage
//
// Ce pont reçoit plusieurs types de messages des iframes enfants :
//
//  1. { type:'openFS', idx:<n>, cams:[...] }
//     → Envoyé par les boutons ⛶ pour ouvrir l'overlay plein écran.
//       Le champ `cams` contient la liste COMPLÈTE de toutes les
//       caméras du site (pas seulement celles de la zone).
//       C'est ce qui permet la navigation libre entre toutes les zones.
//
//  2. { type:'snapshot', cam_id:'CAM_XX' }
//     → BUG 2 FIX : les iframes ne peuvent pas faire de fetch() vers
//       http://192.168.0.97 (blocage same-origin/mixed-content).
//       Elles délèguent la requête à CE pont parent qui lui a accès
//       au réseau local. Une fois la réponse reçue, on broadcast le
//       résultat { type:'snapshotResult', success:true/false } à
//       toutes les iframes via window.parent.frames[].postMessage.
//
//  3. { type:'setHeight', height:<px> }
//     → BUG 1 FIX : les iframes mesurent leur hauteur réelle après rendu
//       et demandent au pont parent d'ajuster leur taille. On cherche
//       l'iframe source dans window.parent.document et on met à jour
//       son attribut height.
// ---------------------------------------------------------
window.addEventListener('message', function(event) {
    if (!event.data) return;

    // --- Ouverture plein écran ---
    // On reçoit idx (index dans ALL_CAMS) + la liste complète cams[].
    // On stocke la liste complète dans ALL_CAMS du pont pour que
    // navigate() puisse parcourir TOUTES les caméras sans restriction.
    if (event.data.type === 'openFS') {
        if (event.data.cams && event.data.cams.length > 0) {
            ALL_CAMS = event.data.cams;  // ← liste COMPLÈTE, toutes zones + champ zone
        }
        openFS(event.data.idx);
        return;
    }

    // --- BUG 2 FIX : Snapshot délégué par une iframe ---
    // L'iframe ne peut pas faire le fetch() directement (blocage CORS/mixed-content).
    // On reçoit ici la demande et on fait le fetch() depuis ce contexte parent.
    if (event.data.type === 'snapshot') {
        fetch("/snapshot", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cam_id: event.data.cam_id })
        })
        .then(function(response) {
            // On broadcast le résultat à toutes les iframes enfants de la page
            // pour que celle qui a fait la demande mette à jour son bouton
            broadcastToIframes({ type: 'snapshotResult', success: response.ok });
        })
        .catch(function() {
            // Erreur réseau : on prévient quand même les iframes
            broadcastToIframes({ type: 'snapshotResult', success: false });
        });
        return;
    }
});

// ---------------------------------------------------------
// broadcastToIframes(msg)
// Envoie un postMessage à toutes les iframes de la page parent.
// Utilisé pour renvoyer le résultat du snapshot à l'iframe demandeuse.
// ---------------------------------------------------------
function broadcastToIframes(msg) {
    const parentDoc = window.parent.document;
    const iframes = parentDoc.querySelectorAll('iframe');
    iframes.forEach(function(iframe) {
        try {
            iframe.contentWindow.postMessage(msg, '*');
        } catch(e) {}  // certaines iframes cross-origin peuvent refuser
    });
}
// Écoute l'injection de ALL_CAMS depuis Python (via un message 'initCams')
window.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'initCams' && e.data.cams) {
        ALL_CAMS = e.data.cams;
    }
});


// ══════════════════════════════════════════════════
// AUDIO CLIENT — Sons synthétisés dans le navigateur
// Reproduit exactement les sons pygame du serveur
// via Web Audio API. L'AudioContext est débloqué au
// premier clic de l'utilisateur (politique autoplay).
// ══════════════════════════════════════════════════
try {
    const parentDoc = window.parent.document;
    if (!parentDoc._playTonesInjected) {
        parentDoc._playTonesInjected = true;

        // Contexte audio dans le parent
        let _audioCtxParent = null;
        function _ensureAudioParent() {
            if (!_audioCtxParent) {
                _audioCtxParent = new (window.parent.AudioContext || window.parent.webkitAudioContext)();
            }
            if (_audioCtxParent.state === 'suspended') _audioCtxParent.resume();
            return _audioCtxParent;
        }

        // Débloque au premier geste dans le parent
        ['click','pointerdown','keydown','touchstart'].forEach(function(evt) {
            parentDoc.addEventListener(evt, function() {
                if (!_audioCtxParent) {
                    _audioCtxParent = new (window.parent.AudioContext || window.parent.webkitAudioContext)();
                }
                if (_audioCtxParent.state === 'suspended') {
                    _audioCtxParent.resume().then(function() {
                        console.log('[AUDIO] AudioContext débloqué, état:', _audioCtxParent.state);
                    });
                }
            }, { once: false, passive: true });
        });

        const _soundCooldown = { alert: 0, suspicion: 0 };
        const _soundCooldownMs = { alert: 500, suspicion: 2000 };

        parentDoc._playClientSound = function(kind) {
            const now = Date.now();
            if (now - _soundCooldown[kind] < _soundCooldownMs[kind]) return;
            _soundCooldown[kind] = now;
            const ctx = _ensureAudioParent();
            if (!ctx) return;
            const tones = kind === 'alert' ? [
                { freq: 1046, dur: 0.12, start: 0.00 },
                { freq: 880,  dur: 0.12, start: 0.16 },
                { freq: 698,  dur: 0.25, start: 0.32 },
            ] : [
                { freq: 1800, dur: 0.012, start: 0.000 },
                { freq: 2200, dur: 0.080, start: 0.020 },
                { freq: 1800, dur: 0.040, start: 0.115 },
            ];
            const vol = kind === 'alert' ? 0.9 : 0.55;
            function doPlay() {
                tones.forEach(function(t) {
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.frequency.value = t.freq;
                    const s = ctx.currentTime + t.start;
                    gain.gain.setValueAtTime(0, s);
                    gain.gain.linearRampToValueAtTime(vol, s + 0.005);
                    gain.gain.linearRampToValueAtTime(0, s + t.dur - 0.01);
                    osc.start(s);
                    osc.stop(s + t.dur + 0.02);
                });
            }
            console.log("[AUDIO] état ctx au moment du son:", ctx.state, "| kind:", kind);
            if (ctx.state === 'running') { doPlay(); }
            else { ctx.resume().then(doPlay).catch(function(){}); }
        };
    }
} catch(e) {}



// ══════════════════════════════════════════════════
// SSE — connexion unique, mises à jour temps réel
// ══════════════════════════════════════════════════
(function() {
    const evtSource = new EventSource("https://192.168.0.97:5000/stream");
    const doc = window.parent.document;

    evtSource.onmessage = function(e) {
        try {
            const data = JSON.parse(e.data);
            if (data.type === "heartbeat") return;

            // Son client : déclenché par le serveur via SSE, joué dans le navigateur
            if (data.type === "sound") {
                console.log("[SSE-SON] reçu:", data.kind, "| fonction dispo:", typeof window.parent.document._playClientSound);
                try { window.parent.document._playClientSound(data.kind); } catch(e) { console.error("[SSE-SON] erreur:", e); }
                return;
            }

            if (data.type === 'suspicion_update' || data.type === 'suspicion_clear') {
                return; // affichage géré côté Python via /suspicions, on garde juste le son
            }

            // Mise à jour compteur alertes en attente
            if (data.type === "init" || data.type === "new_alert") {
                const metrics = doc.querySelectorAll('[data-testid="stMetricValue"]');
                metrics.forEach(function(m) {
                    const label = m.closest('[data-testid="stMetric"]')
                                   ?.querySelector('[data-testid="stMetricLabel"]')
                                   ?.innerText || "";
                    if (label.includes("Alerte")) {
                        if (data.type === "new_alert") {
                            const current = parseInt(m.innerText) || 0;
                            m.innerText = current + 1;
                            m.style.color = "#FF0000";
                            setTimeout(function() { m.style.color = ""; }, 3000);
                        } else if (data.pending_alerts !== undefined) {
                            m.innerText = data.pending_alerts;
                        }
                    }
                });

                // Clignotement titre onglet
                if (data.type === "new_alert") {
                    const original = doc.title;
                    let blink = 0;
                    const iv = setInterval(function() {
                        doc.title = blink % 2 === 0 ? "🚨 NOUVELLE ALERTE !" : original;
                        blink++;
                        if (blink > 8) { clearInterval(iv); doc.title = original; }
                    }, 600);
                }
            }

            // Bannière flash sur alerte critique
            if (data.type === "log" && data.entry && data.entry.level === "ALERT") {
                const banner = doc.createElement("div");
                banner.style.cssText = [
                    "position:fixed", "top:20px", "right:20px",
                    "background:#FF0000", "color:white",
                    "padding:12px 20px", "border-radius:10px",
                    "font-weight:bold", "font-size:1rem",
                    "z-index:99999", "box-shadow:0 4px 15px rgba(0,0,0,0.4)"
                ].join(";");
                banner.innerText = "🚨 " + data.entry.cam + " — " + data.entry.msg;
                doc.body.appendChild(banner);
                setTimeout(function() { banner.remove(); }, 6000);
            }
        } catch(err) {}
    };

    evtSource.onerror = function() {};
})();


// Initialisation immédiate : crée l'overlay dès le chargement
// pour que le listener clavier soit prêt avant même le premier clic
ensureOverlay();
</script>
""", height=0)  # height=0 → iframe invisible, uniquement un pont JavaScript


# CHARGEMENT DES ALERTES

def load_alerts():
    if not os.path.exists("alerts.jsonl"):
        return []

    try:
        alerts = []
        with open("alerts.jsonl", "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        alerts.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # Ignore les lignes corrompues

        for a in alerts:
            if "cam" not in a:
                a["cam"] = "CAM_INCONNUE"

        return alerts

    except:
        return []



# CHARGEMENT DES SUSPICIONS DEPUIS FLASK

def load_suspicions():
    """
    Récupère les suspicions actives depuis le serveur Flask (detect_obj.py).
    Les suspicions sont en mémoire RAM côté serveur, pas dans le fichier JSONL.
    Retourne un dict { cam_id → {time, score, type} } ou {} si serveur injoignable.
    """
    try:
        response = requests.get("https://192.168.0.97:5000/suspicions", timeout=2, verify=False)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass  # Serveur éteint ou inaccessible → on ignore silencieusement
    return {}


# SUPPRESSION D'ALERTE
def delete_alert(index_to_remove, video_path, raw_path=None):
    # Suppression des fichiers vidéo
    for path in [video_path, raw_path]:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass

    # Suppression de la ligne dans le JSONL
    alerts = load_alerts()
    if 0 <= index_to_remove < len(alerts):
        alert_supprimee = alerts.pop(index_to_remove)
        with open("alerts.jsonl", "w") as f:
            for alert in alerts:
                f.write(json.dumps(alert, ensure_ascii=False) + "\n")

        # Incrément compteur FP (append rapide, pas de réécriture)
        with open("stats.jsonl", "a") as f:
            f.write(json.dumps({
                "type": "FP",
                "cam":  alert_supprimee.get("cam", "?"),
                "vol_type": alert_supprimee.get("type", "?"),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
            }, ensure_ascii=False) + "\n")

    st.rerun(scope="fragment")

def _archive_alert(index_to_archive, video_path, raw_path):
    """
    VP confirmé : déplace les vidéos dans alert_clips/archives/
    et marque l'alerte label=VP dans le JSONL.
    L'alerte disparaît de l'interface (filtrée à l'affichage sur label).
    """
    import shutil

    dest_dir_ia  = os.path.join("alert_clips", "archives")
    dest_dir_raw = os.path.join("alert_clips", "archives", "raw")
    os.makedirs(dest_dir_ia,  exist_ok=True)
    os.makedirs(dest_dir_raw, exist_ok=True)

    def _move(src, dest_dir):
        if not src or not os.path.exists(src):
            return src
        if os.path.dirname(os.path.abspath(src)) == os.path.abspath(dest_dir):
            return src
        dest = os.path.join(dest_dir, os.path.basename(src))
        try:
            shutil.move(src, dest)
            return dest
        except Exception:
            return src

    new_video_path = _move(video_path, dest_dir_ia)
    new_raw_path   = _move(raw_path,   dest_dir_raw)

    alerts = load_alerts()
    if 0 <= index_to_archive < len(alerts):
        alerts[index_to_archive]["label"]      = "VP"
        alerts[index_to_archive]["video_clip"] = new_video_path
        alerts[index_to_archive]["video_raw"]  = new_raw_path

        with open("alerts.jsonl", "w") as f:
            for a in alerts:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")

    st.rerun()


# PAGE LOGIN
def login_page():
    st.markdown('<div class="header"><h2>🔐 Accès Sécurisé</h2></div>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        user     = st.text_input("Identifiant")
        password = st.text_input("Mot de passe", type="password")
        if st.button("Connexion", use_container_width=True):
            if user == ADMIN_USER and password == ADMIN_PASSWORD:
                token = create_session()   # stocke dans server_sessions + session_state
                _set_session_cookie(token) # pose le cookie JS (asynchrone)
                st.success("Connexion réussie")
                st.rerun()                 # is_authenticated() trouve le token en session_state ✓
            else:
                st.error("Identifiants incorrects")
    st.stop()


# GATE D'ACCÈS GLOBAL (IMPORTANT)

if not is_authenticated():  # Si pas connecté
    login_page()  # Affiche login

# 1. On stocke les suspicions dans session_state
if "current_suspicions" not in st.session_state:
    st.session_state.current_suspicions = {}

# 2. On fetch les nouvelles suspicions
new_suspicions = load_suspicions()

# 3. On met à jour SEULEMENT si elles ont changé
if new_suspicions != st.session_state.current_suspicions:
    st.session_state.current_suspicions = new_suspicions
    # PAS de st.rerun() ici — le cycle courant continue normalement

# 4. On utilise session_state partout au lieu de `suspicions` direct
suspicions = st.session_state.current_suspicions



# HEADER PRINCIPAL APP
st.markdown(
    '<div class="header"><h1>🛡️ Leclairvoyant - Surveillance IA - E.Leclerc Olivet</h1></div>',
    unsafe_allow_html=True
)

# CHARGEMENT DONNÉES

alerts = load_alerts()  # Liste des alertes

@st.fragment
def gestion_suspicions_fragment():

    # ── Auto-refresh léger toutes les 5s (fragment uniquement, pas la page entière) ──
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=5000, limit=None, key="susp_autorefresh")

    fresh_alerts = load_alerts()
    all_alerts = load_alerts()
    alerts_count = sum(1 for a in all_alerts if a.get("label") not in ("VP", "FP"))
    st.metric("Alertes en attente", alerts_count)

    # --- 2. RÉCUPÉRATION DES DONNÉES ---
    # On appelle ta fonction (assure-toi qu'elle retourne bien le dict des suspicions)
    suspicions = load_suspicions() 
    
    # On récupère les ignorées depuis le state
    if "ignored_suspicions" not in st.session_state:
        st.session_state.ignored_suspicions = set()
    
    # Filtrage des visibles
    suspicions_visibles = {k: v for k, v in suspicions.items() if k not in st.session_state.ignored_suspicions}

    # --- 3. TON CODE D'AFFICHAGE (Le bloc que tu m'as donné) ---
    if suspicions:
        nb_total = len(suspicions)
        
        # SIDEBAR
        st.markdown("---")
        st.markdown(f"""
            <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,0.15);padding:8px 12px;border-radius:8px;margin-bottom:6px;">
                <span style="color:white;font-weight:bold;font-size:0.95rem;">👁️​ Suspicions</span>
                <span style="background:#FF0000;color:white;border-radius:999px;padding:2px 10px;font-size:0.85rem;font-weight:bold;">{nb_total}</span>
            </div>
        """, unsafe_allow_html=True)

        # BANNIÈRES PRINCIPALES
        if suspicions_visibles:
            st.markdown("""<style>@keyframes pulse-red {0%,100% {box-shadow: 0 0 0 0 rgba(204,0,0,0.5);} 50% {box-shadow: 0 0 0 8px rgba(204,0,0,0);}}</style>""", unsafe_allow_html=True)
            
            flat_cams = []
            for z, clist in {
                "🍾 Alcool": [
                    {"id": "CAM_21", "name": "🥃​ Rayon alcool fort", "url": "https://192.168.0.97:5000/video/CAM_21"},
                    {"id": "CAM_22", "name": "🍷 Vins", "url": "https://192.168.0.97:5000/video/CAM_22"},
                    {"id": "CAM_23", "name": "🥂 Champagnes", "url": "https://192.168.0.97:5000/video/CAM_23"},
                ],
                "🌍 Espace culturel": [
                    {"id": "CAM_45", "name": "👀​ Vue Globale", "url": "https://192.168.0.97:5000/video/CAM_45"},
                    {"id": "CAM_46", "name": "📠​ Electronique/divertissement", "url": "https://192.168.0.97:5000/video/CAM_46"},
                    {"id": "CAM_47", "name": "🎧​ Audio", "url": "https://192.168.0.97:5000/video/CAM_47"},
                    {"id": "CAM_49", "name": "🎮​ Jeux Vidéos", "url": "https://192.168.0.97:5000/video/CAM_49"},
                ],
                "🏪 Galerie": [
                    {"id": "CAM_07", "name": "Fleuriste", "url": "https://192.168.0.97:5000/video"},
                    {"id": "CAM_08", "name": "Bijoux", "url": "https://192.168.0.97:5000/video"},
                    {"id": "CAM_09", "name": "Adopt", "url": "https://192.168.0.97:5000/video"},
                ],
                "🚪 Zones sécurisées": [
                    {"id": "CAM_10", "name": "Sortie secours", "url": "https://192.168.0.97:5000/video"},
                    {"id": "CAM_11", "name": "Réserve", "url": "https://192.168.0.97:5000/video"},
                    {"id": "CAM_12", "name": "Personnel", "url": "https://192.168.0.97:5000/video"},
                ]
            }.items():
                for c in clist:
                    cc = dict(c); cc["zone"] = z; flat_cams.append(cc)
            flat_cams_json = json.dumps(flat_cams)

            for cam_id, data in suspicions_visibles.items():
                score_pct = int(data.get("score", 0) * 100)
                bg_color = "#CC0000" if score_pct >= 75 else "#CC6600"
                idx = next((i for i, c in enumerate(flat_cams) if c["id"] == cam_id), 0)

                col_banner, col_btn = st.columns([5, 1])
                with col_banner:
                    st.markdown(f"""
                        <div style="background:{bg_color};border-radius:10px;padding:12px 16px;animation: pulse-red 1.5s ease-in-out infinite;margin-bottom:4px;">
                            <div style="color:white;font-size:1rem;font-weight:bold;">⚠️ Suspicion — {cam_id}</div>
                            <div style="color:white;font-size:0.88rem;margin-top:4px;">Suspect {data.get('type','?')} — {score_pct}% — 🕒 {data.get('time','?')}</div>
                        </div>
                    """, unsafe_allow_html=True)
                with col_btn:
                    if st.button("✕", key=f"ignore_{cam_id}"):
                        st.session_state.ignored_suspicions.add(cam_id)
                        st.rerun(scope="fragment")
    else:
        st.markdown("---")
        st.markdown("<div style='color:white;font-size:0.85rem;opacity:0.7;'>✅ Aucune suspicion active</div>", unsafe_allow_html=True)

    # ── Bouton Son ── 
    st.markdown("---")
    try:
        sound_resp = requests.get("https://192.168.0.97:5000/sound/status", timeout=1, verify=False)
        sound_on = sound_resp.json().get("enabled", True)
    except Exception:
        sound_on = None

    if sound_on is None:
        st.markdown("<div style='color:white;font-size:0.85rem;opacity:0.5;'>🔇 Son indisponible</div>", unsafe_allow_html=True)
    else:
        label = "🔊 Son ON" if sound_on else "🔇 Son OFF"
        st.markdown(f"<div style='color:white;font-size:0.85rem;margin-bottom:6px;'>{label}</div>", unsafe_allow_html=True)
        if st.button("Basculer le son", key="toggle_sound_btn", use_container_width=True):
            try:
                requests.post("https://192.168.0.97:5000/sound/toggle", timeout=1, verify=False)
                # Force la relecture depuis Flask au prochain cycle
                st.session_state.sound_status_ts = 0  # ← expire le cache immédiatement
                st.rerun(scope="fragment")
            except Exception:
                st.warning("Serveur injoignable")

with st.sidebar:
    gestion_suspicions_fragment()

@st.fragment
def alertes_fragment():

    alerts = load_alerts()

    col_r, col_i = st.columns([1, 5])
    with col_r:
        if st.button("🔄 Rafraîchir", key="manual_refresh"):
            st.rerun(scope="fragment")
    with col_i:
        st.caption("Rafraîchissez manuellement pour voir les nouvelles alertes.")
    
    stats       = load_stats()
    all_alerts  = load_alerts()

    fp_total = sum(1 for s in stats if s.get("type") == "FP")
    fp_corps = sum(1 for s in stats if s.get("type") == "FP" and s.get("vol_type") == "CORPS")
    fp_sac   = sum(1 for s in stats if s.get("type") == "FP" and s.get("vol_type") == "SAC")

    vp_total = sum(1 for a in all_alerts if a.get("label") == "VP")
    vp_corps = sum(1 for a in all_alerts if a.get("label") == "VP" and a.get("type") == "CORPS")
    vp_sac   = sum(1 for a in all_alerts if a.get("label") == "VP" and a.get("type") == "SAC")

    total_classes  = vp_total + fp_total
    precision_pct  = int(vp_total / total_classes * 100) if total_classes > 0 else 0
    delta_prec     = f"{precision_pct - 50:+d}% vs hasard" if total_classes > 0 else None

    st.markdown("#### 📊 Statistiques de détection")

    col_vp1, col_vp2, col_vp3 = st.columns(3)
    col_vp1.metric("✅ VP total (vols confirmés)", vp_total)
    col_vp2.metric("✅ VP CORPS", vp_corps)
    col_vp3.metric("✅ VP SAC", vp_sac)

    col_fp1, col_fp2, col_fp3, col_prec = st.columns(4)
    col_fp1.metric("❌ FP total (fausses alertes)", fp_total, delta=f"-{fp_total}" if fp_total else None, delta_color="inverse")
    col_fp2.metric("❌ FP CORPS", fp_corps)
    col_fp3.metric("❌ FP SAC", fp_sac)
    col_prec.metric("🎯 Précision du système", f"{precision_pct}%", delta=delta_prec)

    st.markdown("---")

    if not alerts:  # Si aucune alerte
        st.info("Aucune alerte")  # Message info
        return

    cams_available = sorted(list(set(a.get("cam", "CAM_INCONNUE") for a in alerts)))
    cams_available.insert(0, "Toutes")

    # Filtres UI
    col_type, col_cam, col_date, col_time = st.columns([2, 2, 2, 2])
    
    with col_type:
        type_filter = st.selectbox("Type", ["Tous", "SAC", "CORPS"], key="alert_type")
    with col_cam:
        cam_filter = st.selectbox("Caméra", cams_available, key="alert_cam")
    with col_date:
        alert_date_filter = st.date_input("📅 Jour", value=None, key="alert_date")
    with col_time:
        alert_time_mode = st.selectbox(
            "🕒 Période",
            ["Tout", "Dernière heure", "Plage horaire personnalisée"],
            key="alert_time_mode"
        )
    # Plage horaire personnalisée
    alert_heure_debut = alert_heure_fin = None
    if alert_time_mode == "Plage horaire personnalisée":
        col_h1, col_h2 = st.columns(2)
        with col_h1:
            alert_heure_debut = st.time_input("De", value=None, key="alert_h_debut")
        with col_h2:
            alert_heure_fin = st.time_input("À", value=None, key="alert_h_fin")

    now = datetime.now()  # Heure actuelle

    filtered = []  # Liste filtrée

    # FILTRAGE ALERTES

    for alert in alerts:

        if type_filter != "Tous" and alert.get("type") != type_filter:
            continue  # Skip si type différent

        if cam_filter != "Toutes" and alert.get("cam") != cam_filter:
            continue

        # Filtre par date
        if alert_date_filter is not None:
            if alert.get("date", "") != alert_date_filter.strftime("%Y-%m-%d"):
                continue

        # Filtre temporel
        if alert_time_mode == "Dernière heure":
            try:
                date_str = alert.get("date", now.strftime("%Y-%m-%d"))
                dt = datetime.strptime(f"{date_str} {alert['time']}", "%Y-%m-%d %H:%M:%S")
                if now - dt > timedelta(hours=1):
                    continue
            except:
                pass

        elif alert_time_mode == "Plage horaire personnalisée" and alert_heure_debut and alert_heure_fin:
            try:
                date_str = alert.get("date", now.strftime("%Y-%m-%d"))
                dt = datetime.strptime(f"{date_str} {alert['time']}", "%Y-%m-%d %H:%M:%S")
                if not (alert_heure_debut <= dt.time() <= alert_heure_fin):
                    continue
            except:
                pass
        if alert.get("label") in ("VP", "FP"):
            continue
        filtered.append(alert)

    st.write(f"**{len(filtered)} alertes**")  # compteur

    # AFFICHAGE ALERTES
    for i, alert in enumerate(reversed(filtered)):
        original_index = alerts.index(alert)
        score_percent = int(alert.get('score', 0) * 100)
        
        # Choix des couleurs et du texte de statut selon les critères
        if score_percent < 60:
            main_color, status_text = "#FFD700", "DOUTE IA" # Jaune
        elif score_percent < 85:
            main_color, status_text = "#FF8C00", "CERTITUDE MOYENNE" # Orange
        else:
            main_color, status_text = "#FF0000", "CERTITUDE HAUTE" # Rouge

        # EXTRACTION DE LA DATE
        vid_clip = alert.get("video_clip", "")
        vid_raw = alert.get("video_raw", "")
        
        alert_date_str = "Date inconnue"
        if "date" in alert:
            alert_date_str = alert["date"]
        elif vid_clip and os.path.exists(vid_clip):
            timestamp_creation = os.path.getmtime(vid_clip)
            alert_date_str = datetime.fromtimestamp(timestamp_creation).strftime("%d/%m/%Y")

        st.markdown('<div style="margin-bottom: 25px;">', unsafe_allow_html=True)

        st.markdown(f"""
            <div style="
                background-color: {main_color};
                color: white; 
                padding: 10px 15px; 
                border-radius: 10px 10px 0 0; 
                font-size: 1.1rem; 
                font-weight: bold; 
                display: flex; 
                justify-content: space-between; 
                align-items: center;
            ">
                <span>⚠️ ALERTE VOL {alert.get('type')}</span>
                <span style="background:rgba(255,255,255,0.2);padding:2px 10px;border-radius:12px;font-size:0.95rem;">📷 {alert.get('cam', '?')}</span>
                <div style="color:white; font-size:1.1rem; padding: 2px 10px;">📅 {alert_date_str} - 🕒 {alert.get("time")}</div>
                <span style="background: rgba(255,255,255,0.3); padding: 2px 8px; border-radius: 20px;">
                    {status_text} | {score_percent}%
                </span>
            </div>
        """, unsafe_allow_html=True)

        with st.container():
            st.markdown(f"""
                <div style="
                    border: 6px solid {main_color}; 
                    border-top: none; 
                    border-radius: 0 0 10px 10px; 
                    background-color: #fcfcfc;
                    box-shadow: 0 4px 10px rgba(0,0,0,0.1);
                ">
            """, unsafe_allow_html=True)

            col_video, col_actions = st.columns([3, 1])
            
            # GESTION DU TOGGLE IA / RAW DANS LE SESSION_STATE
            toggle_key = f"toggle_raw_{i}"
            if toggle_key not in st.session_state:
                st.session_state[toggle_key] = False # Par défaut: Vue IA

            is_raw_view = st.session_state[toggle_key]
            
            active_video_path = vid_raw if (is_raw_view and vid_raw and os.path.exists(vid_raw)) else vid_clip

            with col_video:
                if active_video_path and os.path.exists(active_video_path):
                    st.video(active_video_path)
                else:
                    st.warning("Flux vidéo indisponible sur le disque")

            with col_actions:
                st.markdown('<div style="margin-top: 0px;"></div>', unsafe_allow_html=True)

                # Bouton toggle vue IA / vue naturelle
                btn_text = "📹 Voir la vue naturelle" if not is_raw_view else "🧠 Voir la vue intelligente"
                if st.button(btn_text, key=f"btn_toggle_{i}", use_container_width=True):
                    st.session_state[toggle_key] = not is_raw_view
                    st.rerun()

                st.markdown("**Décision :**")
                col_vp, col_fp = st.columns(2)

                with col_vp:
                    if st.button(
                        "✅ VP — Archiver",
                        key=f"vp_{i}",
                        use_container_width=True,
                        help="Scénario cohérent et/ou simple erreur de détection — archive la vidéo et retire l'alerte"):
                        _archive_alert(original_index, vid_clip, vid_raw)

                with col_fp:
                    if st.button(
                        "❌ FP — Supprimer",
                        key=f"fp_{i}",
                        use_container_width=True,
                        help="Fausse alerte, le modèle a fait une erreur de logique— supprime définitivement"):
                        delete_alert(original_index, vid_clip, vid_raw)

                # Téléchargement (inchangé)
                if active_video_path and os.path.exists(active_video_path):
                    with open(active_video_path, "rb") as f:
                        file_suffix = "RAW" if is_raw_view else "IA"
                        st.download_button(
                            "📥 Télécharger",
                            f,
                            file_name=f"alert_{file_suffix}_{alert['time'].replace(':', '')}.mp4",
                            key=f"dl_{i}",
                            use_container_width=True
                        )
                st.markdown('<hr style="margin:6px 0;border-color:#ddd;">', unsafe_allow_html=True)
                st.markdown('<p style="margin:4px 0 2px 0;font-weight:bold;">📋 Ce qu\'a vu l\'IA :</p>', unsafe_allow_html=True)

                LOGS_FILE = "logs.jsonl"
                alert_cam_id   = alert.get("cam", "")
                alert_time_str = alert.get("time", "")
                alert_date_log = alert.get("date", "")
                associated_logs = []

                if alert_time_str and alert_date_log and os.path.exists(LOGS_FILE):
                    try:
                        alert_dt     = datetime.strptime(f"{alert_date_log} {alert_time_str}", "%Y-%m-%d %H:%M:%S")
                        window_start = alert_dt - timedelta(seconds=20)  # couvre le pré-buffer
                        window_end   = alert_dt + timedelta(seconds=12)  # couvre le post-alerte

                        with open(LOGS_FILE, "r") as lf:
                            for line in lf:
                                try:
                                    log = json.loads(line.strip())
                                    if log.get("cam") != alert_cam_id:
                                        continue
                                    if log.get("level") not in ("ALERT", "INFO", "DEBUG"):  # on cache DEBUG/ERROR pour l'agent
                                        continue
                                    if log.get("date", "") != alert_date_log:
                                        continue
                                    log_dt = datetime.strptime(
                                        f"{log.get('date','')} {log.get('ts','')}",
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                                    if window_start <= log_dt <= window_end:
                                        associated_logs.append(log)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                if associated_logs:
                    level_style = {
                        "ALERT": ("🚨", "#FF0000", "white"),
                        "INFO":  ("ℹ️",  "#0066b2", "white"),
                        "DEBUG": ("🔍", "#444444", "#cccccc"),
                    }
                    rows = ""
                    for log in associated_logs:
                        icon, bg, fg = level_style.get(log.get("level", "INFO"), ("ℹ️", "#888", "white"))
                        ts  = log.get("ts", "")
                        msg = (log.get("msg", "")
                            .replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;"))
                        rows += (
                            f'<div style="border-bottom:1px solid #eee;padding:6px 8px;">'
                            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">'
                            f'<span style="background:{bg};color:{fg};border-radius:4px;'
                            f'padding:1px 6px;font-size:0.72rem;font-weight:bold;">{icon} {log.get("level")}</span>'
                            f'<span style="color:#999;font-size:0.72rem;">{ts}</span></div>'
                            f'<div style="color:#222;font-size:0.78rem;line-height:1.35;">{msg}</div>'
                            f'</div>'
                        )
                    html_logs = (
                        '<div style="max-height:280px;overflow-y:auto;border:2px solid #0066b2;'
                        'border-radius:8px;background:white;">' + rows + '</div>'
                    )
                    st.markdown(html_logs, unsafe_allow_html=True)
                else:
                    st.caption("Aucun log IA sur cette période.")

# SIDEBAR MENU

st.sidebar.title("📊 Menu")  # Titre sidebar

# ==============================================================
# AFFICHAGE DES SUSPICIONS
#
# CHANGEMENT : chaque suspicion est ignorable INDIVIDUELLEMENT.
# On stocke dans st.session_state un set des cam_id ignorées.
# Une suspicion ignorée disparaît jusqu'au prochain refresh (5s).
# Si une NOUVELLE suspicion apparaît sur une cam non ignorée,
# elle s'affiche immédiatement dans la bannière.
# ==============================================================


page = st.sidebar.radio("MENU", ["📺 LIVE", "🚨 ALERTES", "🗂️ ARCHIVES VP", "📋 LOGS", "📘 GUIDE D'AMÉLIORATION"])  # Navigation

# DÉCONNEXION
if st.sidebar.button("🚪 Déconnexion"):
    destroy_session()
    st.rerun()


def render_camera_zone(zone_name: str, cams: list, all_cams: list):
    """
    Rend UNE ZONE ENTIÈRE de caméras en grille 2 colonnes dans un seul components.html().

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  CHANGEMENTS PAR RAPPORT AU CODE ORIGINAL                           ║
    ║                                                                      ║
    ║  FIX FLUX (limite Firefox 6 connexions simultanées) :               ║
    ║  Les <img> de la GRILLE ont src="" au départ → aucun flux chargé.   ║
    ║  Le flux ne démarre que quand on ouvre le plein écran (loadCam).    ║
    ║  Résultat : une seule connexion MJPEG active à la fois → CAM_49     ║
    ║  et toutes les autres sont accessibles via les flèches.             ║
    ║                                                                      ║
    ║  FIX NAVIGATION CIRCULAIRE GLOBALE :                                ║
    ║  Navigation ←/→ parcourt ALL_CAMS (toutes zones) de façon          ║
    ║  circulaire. Le label affiche la zone + position globale :          ║
    ║  "🎥 Champagnes | 🍾 Alcool (3/13 global)"                         ║
    ║  Chaque cam dans ALL_CAMS porte un champ `zone` (ajouté Python).   ║
    ║                                                                      ║
    ║  CE QUI N'A PAS CHANGÉ :                                            ║
    ║  - La grille 2 colonnes CSS reste identique                         ║
    ║  - Les boutons Python Capture/Download restent identiques           ║
    ║  - La logique d'overlay, fullscreen, snapshot reste identique       ║
    ║  - Les commentaires sont conservés et enrichis                      ║
    ╚══════════════════════════════════════════════════════════════════════╝

    Paramètres:
      zone_name : nom de la zone (ex: "Alcool") — non utilisé dans le HTML
                  car le titre est géré par st.expander côté Python
      cams      : liste de TOUTES les caméras de cette zone
      all_cams  : liste COMPLÈTE de toutes les caméras du site (toutes zones)
                  → chaque cam doit avoir un champ `zone` pour l'affichage
                  → transmise via postMessage au pont parent pour la navigation
                  globale ←/→ en plein écran
    """

    # Sérialisation JSON pour injection dans le JS inline.
    # cams_json     = toutes les caméras de CETTE zone (pour construire la grille)
    # all_cams_json = toutes les caméras du SITE avec champ `zone` (pour la nav globale)
    cams_json     = json.dumps(cams)
    all_cams_json = json.dumps(all_cams)

    # ---------------------------------------------------------
    # Calcul de la hauteur de l'iframe :
    # On calcule en fonction du nombre TOTAL de caméras de la zone.
    # layout=wide Streamlit → zone ≈ 1200px. 2 colonnes → chaque col ≈ 595px.
    # Image 16/9 dans 595px → ~335px. + header(36) + border(8) + caption(24)
    # + capture_btn(44) + gap(10) ≈ 457px/ligne. On prend 470px + 30px marge basse.
    # scrolling=True en filet de sécurité sur petits écrans.
    # ---------------------------------------------------------
    rows        = (len(cams) + 1) // 2   # ex: 3 cams → 2 lignes, 4 cams → 2 lignes
    grid_height = rows * 470 + 30        # 470px / ligne + 30px marge basse

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}

/* body sans overflow caché : on laisse le contenu définir sa taille réelle */
body {{
    font-family: sans-serif;
    background: white;
    overflow: hidden;   /* pas de scrollbar dans l'iframe */
}}

/* ---- GRILLE 2 COLONNES ---- */
/* grid-template-columns: 1fr 1fr → 2 caméras côte à côte par ligne.
   Les lignes supplémentaires (3e, 4e cam…) s'ajoutent automatiquement
   grâce à grid-auto-rows: auto qui prend la hauteur naturelle du contenu. */
.cam-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-auto-rows: auto;   /* chaque ligne prend sa hauteur naturelle */
    gap: 10px;
    padding: 4px 4px 8px 4px;
}}

/* ---- CARTE CAMÉRA ---- */
.cam-card {{ border-radius: 10px; overflow: hidden; }}

/* Header bleu avec nom en orange */
.cam-header {{
    background-color: #0066b2;
    color: #ff6000;
    padding: 5px 10px;
    border-radius: 10px 10px 0 0;
    font-weight: bold;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.9rem;
}}
.cam-header span {{ color: #ff6000; }}

/* Bouton plein écran ⛶ */
.fs-btn {{
    background: none;
    border: none;
    color: white;
    cursor: pointer;
    font-size: 1.2rem;
    padding: 2px 5px;
    border-radius: 4px;
    line-height: 1;
    transition: background 0.15s;
}}
.fs-btn:hover {{ background: rgba(255,255,255,0.25); }}

/* Flux caméra — border bleue + fond noir */
/* FIX FLUX : on affiche un placeholder gris foncé quand src est vide.
   L'utilisateur voit que la cam existe mais le flux n'est pas chargé.
   Un clic sur ⛶ charge le flux uniquement dans l'overlay plein écran. */
.cam-body {{
    border: 4px solid #0066b2;
    border-top: none;
    border-radius: 0 0 10px 10px;
    overflow: hidden;
    background-color: #1a1a2e;   /* fond sombre indiquant que le flux est en veille */
    position: relative;
}}
.cam-body img {{
    width: 100%;
    display: block;
    aspect-ratio: 16/9;    /* ratio fixe : la hauteur est déduite de la largeur */
    object-fit: contain;
}}

/* Texte placeholder affiché quand la cam est en veille (flux non chargé) */
.cam-placeholder {{
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: #ff6000;
    font-size: 0.85rem;
    text-align: center;
    pointer-events: none;   /* le clic passe à travers vers l'image */
    opacity: 0.8;
}}

/* Caption sous la carte */
.cam-caption {{
    font-size: 0.75rem;
    color: #888;
    padding: 2px 2px 6px 2px;
}}

/* ==============================================================
   OVERLAY PLEIN ÉCRAN (dans cette iframe)
   Quand requestFullscreen() est actif, le navigateur agrandit
   l'iframe en 100% écran → position:fixed couvre tout l'écran ✓
   ============================================================== */
#fs-overlay {{
    display: none;
    position: fixed;
    top: 0; left: 0;
    width: 100vw; height: 100vh;
    background: rgba(0,0,0,0.97);
    z-index: 99999;
    flex-direction: column;
    justify-content: center;
    align-items: center;
}}
#fs-overlay.active {{ display: flex; }}

#fs-img {{
    max-width: 95%;
    max-height: 82vh;
    object-fit: contain;
    border: 3px solid #ff6000;
    border-radius: 6px;
}}

.fs-nav {{
    display: flex;
    align-items: center;
    gap: 16px;
    margin-top: 14px;
}}

.fs-nav button {{
    background: rgba(255,255,255,0.15);
    color: white;
    border: 2px solid white;
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 1.1rem;
    cursor: pointer;
    font-weight: bold;
    transition: background 0.2s;
}}
.fs-nav button:hover {{ background: rgba(255,255,255,0.35); }}

#fs-cam-name {{
    color: #ff6000;
    font-size: 1rem;
    font-weight: bold;
    min-width: 280px;   /* agrandi pour afficher zone + position */
    text-align: center;
}}
</style>
</head>
<body>

<!-- GRILLE DE CAMÉRAS (le titre de zone est géré par st.expander Python) -->
<div class="cam-grid" id="cam-grid"></div>

<!-- OVERLAY PLEIN ÉCRAN (local à cette iframe, activé via requestFullscreen) -->
<div id="fs-overlay">
    <img id="fs-img" src="" alt="flux caméra plein écran">
    <div class="fs-nav">
        <button id="cap">📸 Capture</button>
        <button id="fs-prev">◀ Précédent</button>
        <span id="fs-cam-name"></span>
        <button id="fs-next">Suivant ▶</button>
        <button id="fs-close">✕ Fermer</button>
    </div>
</div>

<script>
// ==============================================================
// DONNÉES DE CAMÉRAS
//
// CAMS_ZONE  = caméras de CETTE zone uniquement (pour construire la grille HTML)
// ALL_CAMS   = toutes les caméras du site, toutes zones confondues
//              Chaque cam porte un champ `zone` pour l'affichage du label.
//              Ex: {{ id:"CAM_23", name:"Champagnes", url:"...", zone:"🍾 Alcool" }}
// ==============================================================
const CAMS_ZONE = {cams_json};    // Caméras de cette zone (pour la grille)
const ALL_CAMS  = {all_cams_json}; // Toutes les caméras du site (pour la nav plein écran)

// Index courant dans ALL_CAMS (-1 = overlay fermé)
let currentIdx = -1;

// ----------------------------------------------------------
// Construction dynamique de la grille de caméras
//
// FIX FLUX : Les <img> sont créées avec src="" (pas de flux chargé).
// On affiche un placeholder texte pour indiquer que la cam est en veille.
// Le flux ne démarre que lors de l'ouverture plein écran via openFS().
// Cela respecte la limite de 6 connexions simultanées de Firefox.
// ----------------------------------------------------------
const grid = document.getElementById('cam-grid');

CAMS_ZONE.forEach(function(cam) {{
    // On retrouve l'index global de cette caméra dans ALL_CAMS.
    // C'est cet index qui sera transmis à openFS() pour que navigate()
    // sache exactement où on se situe dans la liste complète du site.
    const globalIdx = ALL_CAMS.findIndex(function(c) {{ return c.id === cam.id; }});

    const card = document.createElement('div');
    card.className = 'cam-card';

    // FIX FLUX : src="" → pas de connexion MJPEG dans la grille.
    // Le placeholder indique à l'utilisateur comment voir le flux.
    card.innerHTML =
        '<div class="cam-header">' +
            '<span>🎥 ' + cam.name + '</span>' +
            '<button class="fs-btn" title="Ouvrir en plein écran pour voir le flux">⛶</button>' +
        '</div>' +
        '<div class="cam-body">' +
            '<img id="img_' + cam.id + '" src="" alt="' + cam.name + '">' +
            '<div class="cam-placeholder">▶ Cliquer sur ⛶ pour voir le flux</div>' +
        '</div>' +
        '<div class="cam-caption">ID: ' + cam.id + ' | Cliquer ⛶ pour activer le flux</div>';

    // Listener sur ⛶ — attaché APRÈS insertion dans le DOM.
    // On capture globalIdx par closure pour que chaque bouton ouvre
    // la bonne caméra dans le contexte global (pas juste dans la zone).
    card.querySelector('.fs-btn').addEventListener('click', (function(idx) {{
        return function() {{ openFS(idx); }};
    }})(globalIdx));

    grid.appendChild(card);
}});

// ----------------------------------------------------------
// BUG 1 FIX — Ajustement dynamique de la hauteur de l'iframe
// On attend que le DOM soit rendu (requestAnimationFrame) puis
// on mesure la hauteur réelle du contenu (scrollHeight).
// On envoie cette valeur au document parent via postMessage
// avec le type 'setHeight' : le pont parent ajuste l'iframe.
// On ajoute 20px de marge basse pour éviter toute coupure.
// ----------------------------------------------------------
function reportHeight() {{
    const h = document.body.scrollHeight + 20;
    window.parent.postMessage({{ type: 'setHeight', height: h }}, '*');
}}
// Premier appel après le premier paint
requestAnimationFrame(function() {{
    setTimeout(reportHeight, 200);  // 200ms : laisse le temps aux images de s'initialiser
}});

// ----------------------------------------------------------
// FONCTIONS PLEIN ÉCRAN
// ----------------------------------------------------------

// loadCam(idx) : charge le flux de ALL_CAMS[idx] dans l'overlay
//
// FIX NAVIGATION GLOBALE + AFFICHAGE ZONE :
// Le label affiche désormais : "🎥 Nom cam | Zone (X/13 global)"
// Le champ `zone` est injecté côté Python dans all_cams.
// La position est toujours globale (sur ALL_CAMS) pour indiquer
// clairement où on se trouve parmi toutes les caméras du site.
function loadCam(idx) {{
    const cam = ALL_CAMS[idx];

    // FIX FLUX : on charge le flux UNIQUEMENT dans l'overlay, jamais dans la grille
    document.getElementById('fs-img').src = cam.url;

    // Label : "🎥 Nom | Zone (position/total global)"
    // Le champ `zone` est ajouté par Python lors de la construction de all_cams.
    const zoneLabel = cam.zone ? ' | ' + cam.zone : '';
    document.getElementById('fs-cam-name').textContent =
        '🎥 ' + cam.name + zoneLabel + ' (' + (idx + 1) + '/' + ALL_CAMS.length + ' global)';
}}

// openFS(idx) : ouvre l'overlay plein écran sur la caméra d'index global idx
function openFS(idx) {{
    currentIdx = idx;
    loadCam(idx);
    document.getElementById('fs-overlay').classList.add('active');

    // Vrai plein écran navigateur via requestFullscreen()
    // DOIT être appelé dans un handler d'événement utilisateur (clic) ✓
    const el = document.documentElement;
    if (el.requestFullscreen) {{
        el.requestFullscreen().catch(function() {{}});
    }} else if (el.webkitRequestFullscreen) {{
        el.webkitRequestFullscreen();  // Safari
    }} else if (el.mozRequestFullScreen) {{
        el.mozRequestFullScreen();     // Firefox ancien
    }} else if (el.msRequestFullscreen) {{
        el.msRequestFullscreen();      // IE/Edge ancien
    }}
}}

// navigate(dir) : passe à la caméra suivante (+1) ou précédente (-1)
//
// FIX NAVIGATION CIRCULAIRE GLOBALE :
// - ALL_CAMS contient toutes les cams du site (toutes zones confondues)
// - Navigation CIRCULAIRE : depuis la dernière cam → Suivant → 1ère cam
//   grâce au modulo : (idx + dir + total) % total
// - Le label affiche la zone en temps réel via loadCam()
// Exemple : 13 cams, on est à idx=12 (dernière)
//   navigate(+1) → (12 + 1 + 13) % 13 = 0 → retour à la 1ère cam ✓
//   navigate(-1) → (0 - 1 + 13) % 13 = 12 → retour à la dernière ✓
function navigate(dir) {{
    if (currentIdx === -1) return;
    // Modulo sur ALL_CAMS.length → navigation circulaire sans jamais bloquer
    currentIdx = (currentIdx + dir + ALL_CAMS.length) % ALL_CAMS.length;
    loadCam(currentIdx);
}}

// closeFS() : ferme l'overlay + quitte le plein écran navigateur
//
// FIX FLUX : on coupe uniquement le flux de l'overlay (img#fs-img).
// La grille reste inchangée : ses img ont toujours src="" donc
// aucune connexion MJPEG n'est ouverte ni à fermer côté grille.
function closeFS() {{
    document.getElementById('fs-overlay').classList.remove('active');
    document.getElementById('fs-img').src = '';  // libère la connexion HTTP MJPEG
    currentIdx = -1;

    // Quitte le plein écran navigateur (API standard + variantes navigateurs)
    if (document.exitFullscreen) {{
        document.exitFullscreen().catch(function() {{}});
    }} else if (document.webkitExitFullscreen) {{
        document.webkitExitFullscreen();
    }} else if (document.mozCancelFullScreen) {{
        document.mozCancelFullScreen();
    }} else if (document.msExitFullscreen) {{
        document.msExitFullscreen();
    }}
}}

// ----------------------------------------------------------
// BUG 2 FIX — Bouton Capture plein écran
//
// PROBLÈME PRÉCÉDENT :
//   fetch("/snapshot") depuis cette iframe
//   était bloqué par la politique same-origin du navigateur.
//
// SOLUTION :
//   On fait le fetch() DIRECTEMENT depuis l'iframe avec un timeout
//   de 3 secondes. Cela fonctionne en HTTP local (même réseau LAN).
//   Si le serveur Flask est injoignable, on affiche "❌ Hors ligne".
// ----------------------------------------------------------
function takeSnapshot() {{
    if (currentIdx === -1) return;  // Sécurité : overlay doit être ouvert

    const cam = ALL_CAMS[currentIdx];
    const btn = document.getElementById('cap');
    const originalText = btn.textContent;

    // Feedback visuel immédiat
    btn.textContent = "⏳ Capture...";
    btn.style.borderColor = "#ff6000";
    btn.disabled = true;

    // AbortController pour le timeout de 3 secondes
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    // POST vers /snapshot avec l'identifiant de la caméra courante
    fetch("/snapshot", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ cam_id: cam.id }}),
        signal: controller.signal
    }})
    .then(function(response) {{
        clearTimeout(timeoutId);
        if (response.ok) {{
            btn.textContent = "✅ OK";
            btn.style.color = "#28a745";
        }} else {{
            btn.textContent = "❌ Erreur serveur";
            btn.style.color = "#ff0000";
        }}
    }})
    .catch(function(err) {{
        clearTimeout(timeoutId);
        btn.textContent = "❌ Hors ligne";
        btn.style.color = "#ff0000";
    }})
    .finally(function() {{
        setTimeout(function() {{
            btn.textContent = originalText;
            btn.style.color = "white";
            btn.style.borderColor = "white";
            btn.disabled = false;
        }}, 2000);
    }});
}}

// ----------------------------------------------------------
// Navigation clavier ←/→ dans l'overlay
// ----------------------------------------------------------
document.addEventListener('keydown', function(e) {{
    if (currentIdx === -1) return;
    if (e.key === 'ArrowLeft')  {{ e.preventDefault(); navigate(-1); }}
    if (e.key === 'ArrowRight') {{ e.preventDefault(); navigate(+1); }}
    if (e.key === 'c' || e.key === 'C') takeSnapshot();
}});

// Quand le navigateur quitte le plein écran (Echap ou bouton natif)
// → ferme l'overlay proprement pour rester en état cohérent
document.addEventListener('fullscreenchange', function() {{
    if (!document.fullscreenElement && currentIdx !== -1) {{
        document.getElementById('fs-overlay').classList.remove('active');
        document.getElementById('fs-img').src = '';
        currentIdx = -1;
    }}
}});
// Variante webkit pour Safari
document.addEventListener('webkitfullscreenchange', function() {{
    if (!document.webkitFullscreenElement && currentIdx !== -1) {{
        document.getElementById('fs-overlay').classList.remove('active');
        document.getElementById('fs-img').src = '';
        currentIdx = -1;
    }}
}});

// Clic sur le fond noir de l'overlay → ferme + quitte le plein écran
document.getElementById('fs-overlay').addEventListener('click', function(e) {{
    if (e.target === this) closeFS();
}});

// Boutons de navigation de l'overlay
document.getElementById('fs-close').addEventListener('click', closeFS);
document.getElementById('fs-prev').addEventListener('click', function() {{ navigate(-1); }});
document.getElementById('fs-next').addEventListener('click', function() {{ navigate(+1); }});
document.getElementById('cap').addEventListener('click', takeSnapshot);
</script>

</body>
</html>"""

    components.html(html, height=grid_height, scrolling=True)


# 📺 PAGE LIVE (MULTI CAMÉRAS PRO)

if page == "📺 LIVE":
    st.markdown(
        '<div class="header"><h1>🎥 Surveillance en direct</h1></div>',
        unsafe_allow_html=True
    )
    col_refresh, col_info = st.columns([1, 4])
    with col_refresh:
        if st.button("🔄 Actualiser"):
            st.rerun()
    with col_info:
        st.info("Note : Les flux s'activent uniquement en mode plein écran (⛶) pour rester sous la limite navigateur.")

    # 📍 DÉFINITION DES CAMÉRAS PAR ZONES
    cameras = {
        "🍾 Alcool": [
            {"id": "CAM_21", "name": "🥃​ Rayon alcool fort", "url": "https://192.168.0.97:5000/video/CAM_21"},
            {"id": "CAM_22", "name": "🍷 Vins", "url": "https://192.168.0.97:5000/video/CAM_22"},
            {"id": "CAM_23", "name": "🥂 Champagnes", "url": "https://192.168.0.97:5000/video/CAM_23"},
        ],
        "🌍 Espace culturel": [
            {"id": "CAM_45", "name": "👀​ Vue Globale", "url": "https://192.168.0.97:5000/video/CAM_45"},
            {"id": "CAM_46", "name": "📠​ Electronique/divertissement", "url": "https://192.168.0.97:5000/video/CAM_46"},
            {"id": "CAM_47", "name": "🎧​ Audio", "url": "https://192.168.0.97:5000/video/CAM_47"},
            {"id": "CAM_49", "name": "🎮​ Jeux Vidéos", "url": "https://192.168.0.97:5000/video/CAM_49"},
        ],
        "🏪 Galerie": [
            {"id": "CAM_07", "name": "Fleuriste", "url": "https://192.168.0.97:5000/video"},
            {"id": "CAM_08", "name": "Bijoux", "url": "https://192.168.0.97:5000/video"},
            {"id": "CAM_09", "name": "Adopt", "url": "https://192.168.0.97:5000/video"},
        ],
        "🚪 Zones sécurisées": [
            {"id": "CAM_10", "name": "Sortie secours", "url": "https://192.168.0.97:5000/video"},
            {"id": "CAM_11", "name": "Réserve", "url": "https://192.168.0.97:5000/video"},
            {"id": "CAM_12", "name": "Personnel", "url": "https://192.168.0.97:5000/video"},
        ]
    }

    # ---------------------------------------------------------
    # Construction de all_cams avec le champ `zone` injecté.
    #
    # FIX NAVIGATION GLOBALE : on ajoute le nom de la zone à chaque cam
    # pour que le JS puisse l'afficher dans le label plein écran.
    # Format : "🎥 Champagnes | 🍾 Alcool (3/13 global)"
    # On ne modifie pas le dict original (on crée une copie avec zone).
    # ---------------------------------------------------------
    all_cams = []
    for zone_name, zone_cams in cameras.items():
        for cam in zone_cams:
            cam_with_zone = dict(cam)          # copie pour ne pas polluer le dict original
            cam_with_zone["zone"] = zone_name  # on ajoute le nom de zone
            all_cams.append(cam_with_zone)

    all_cams_json = json.dumps(all_cams)
    components.html(f"""
    <script>
    window.parent.postMessage({{ type: 'initCams', cams: {all_cams_json} }}, '*');
    </script>
    """, height=0)

    for zone, cams in cameras.items():
        with st.expander(f"📍 {zone}", expanded=True):

            # ----------------------------------------------------------
            # render_camera_zone() est appelée UNE SEULE FOIS par zone
            # avec toutes les cams de la zone → une seule iframe par zone.
            # La grille 2 colonnes est gérée par CSS (pas changée).
            # ----------------------------------------------------------
            render_camera_zone(zone, cams, all_cams)

            # ----------------------------------------------------------
            # BOUTONS CAPTURE PYTHON — affichés PAR PAIRE sous chaque rangée.
            # Ces boutons n'ont pas changé par rapport au code original.
            # ----------------------------------------------------------


# PAGE ALERTES

elif page == "🚨 ALERTES":
    st.markdown(
        '<div class="header"><h1>🚨 Historique des alertes</h1></div>',
        unsafe_allow_html=True
    )
    alertes_fragment()


elif page == "🗂️ ARCHIVES VP":
    st.markdown(
        '<div class="header"><h1>🗂️ Archives — Vols Confirmés (VP)</h1></div>',
        unsafe_allow_html=True
    )

    st.info(f"🔒 RGPD : Les clips archivés sont automatiquement supprimés après {ARCHIVE_RETENTION_DAYS} jours.")

    @st.fragment
    def archives_fragment():
        col_r, _ = st.columns([1, 5])
        with col_r:
            if st.button("🔄 Rafraîchir", key="refresh_archives"):
                st.rerun(scope="fragment")

        all_alerts = load_alerts()
        archives = [a for a in all_alerts if a.get("label") == "VP"]

        if not archives:
            st.success("✅ Aucune archive VP pour le moment.")
            return

        # Filtres
        col_cam, col_type, col_date = st.columns([2, 2, 2])
        cams_arch = sorted(set(a.get("cam", "?") for a in archives))
        cams_arch.insert(0, "Toutes")

        with col_cam:
            cam_f = st.selectbox("📷 Caméra", cams_arch, key="arch_cam")
        with col_type:
            type_f = st.selectbox("Type", ["Tous", "CORPS", "SAC"], key="arch_type")
        with col_date:
            date_f = st.date_input("📅 Jour", value=None, key="arch_date")

        filtered = []
        for a in archives:
            if cam_f != "Toutes" and a.get("cam") != cam_f:
                continue
            if type_f != "Tous" and a.get("type") != type_f:
                continue
            if date_f and a.get("date", "") != date_f.strftime("%Y-%m-%d"):
                continue
            filtered.append(a)

        st.write(f"**{len(filtered)} archive(s) VP**")

        for i, alert in enumerate(reversed(filtered)):
            original_index = all_alerts.index(alert)
            score_pct = int(alert.get("score", 0) * 100)
            vid_clip  = alert.get("video_clip", "")
            vid_raw   = alert.get("video_raw",  "")
            date_str  = alert.get("date", "Date inconnue")

            st.markdown(f"""
                <div style="background:#1a7a1a;color:white;padding:10px 15px;
                border-radius:10px 10px 0 0;font-weight:bold;
                display:flex;justify-content:space-between;align-items:center;">
                    <span>✅ VOL CONFIRMÉ — {alert.get('type','?')}</span>
                    <span style="background:rgba(255,255,255,0.2);padding:2px 10px;border-radius:12px;">
                        📷 {alert.get('cam','?')}
                    </span>
                    <span>📅 {date_str} — 🕒 {alert.get('time','?')}</span>
                    <span style="background:rgba(255,255,255,0.2);padding:2px 10px;border-radius:12px;">
                        Confiance : {score_pct}%
                    </span>
                </div>
            """, unsafe_allow_html=True)

            with st.container():
                col_vid, col_act = st.columns([3, 1])

                toggle_key = f"arch_toggle_{i}"
                if toggle_key not in st.session_state:
                    st.session_state[toggle_key] = False
                is_raw = st.session_state[toggle_key]
                active_path = vid_raw if (is_raw and vid_raw and os.path.exists(vid_raw)) else vid_clip

                with col_vid:
                    if active_path and os.path.exists(active_path):
                        st.video(active_path)
                    else:
                        st.warning("⚠️ Clip supprimé (purge RGPD ou fichier manquant)")

                with col_act:
                    btn_text = "📹 Vue naturelle" if not is_raw else "🧠 Vue intelligente"
                    if st.button(btn_text, key=f"arch_tog_{i}", use_container_width=True):
                        st.session_state[toggle_key] = not is_raw
                        st.rerun()

                    if active_path and os.path.exists(active_path):
                        with open(active_path, "rb") as f:
                            suffix = "RAW" if is_raw else "IA"
                            st.download_button(
                                "📥 Télécharger",
                                f,
                                file_name=f"VP_{suffix}_{alert.get('time','').replace(':','')}.mp4",
                                key=f"arch_dl_{i}",
                                use_container_width=True
                            )

                    st.markdown("---")
                    if st.button("🗑️ Supprimer définitivement",
                                 key=f"arch_del_{i}",
                                 use_container_width=True,
                                 help="Supprime le clip et l'entrée de l'historique"):
                        delete_alert(original_index, vid_clip, vid_raw)

            st.markdown("<div style='margin-bottom:20px'></div>", unsafe_allow_html=True)

    archives_fragment()


elif page == "📋 LOGS":

    st.markdown(
        '<div class="header"><h1>📋 Logs système en temps réel</h1></div>',
        unsafe_allow_html=True
    )

    @st.fragment
    def logs_fragment():

        col_r, col_i = st.columns([1, 5])
        with col_r:
            if st.button("🔄 Rafraîchir les logs", key="manual_refresh_logs"):
                st.rerun(scope="fragment")
        with col_i:
            st.caption("Cliquez pour charger les derniers logs.")

        # ── Filtres ────────────────────────────────────────────────────────
        col_cam, col_level, col_date, col_time, col_n = st.columns([2, 2, 2, 2, 1])

        cam_ids_log = ["ALL"] + [c["cam_id"] for c in [
            {"cam_id": "CAM_21"}, {"cam_id": "CAM_22"}, {"cam_id": "CAM_23"},
            {"cam_id": "CAM_45"}, {"cam_id": "CAM_46"}, {"cam_id": "CAM_47"},
            {"cam_id": "CAM_49"},
        ]]

        with col_cam:
            selected_cam = st.selectbox("📷 Caméra", cam_ids_log, key="log_cam")
        with col_level:
            selected_level = st.selectbox(
                "🎚️ Niveau",
                ["ALL", "ALERT", "INFO", "DEBUG", "ERROR"],
                key="log_level"
            )
        with col_time:
            filter_mode = st.selectbox(
                "🕒 Période",
                ["Tout", "Dernière heure", "Plage horaire personnalisée"],
                key="log_time_mode"
            )
        with col_n:
            max_logs = st.number_input("Nb max", min_value=10, max_value=1000,
                                        value=200, step=50, key="log_max")

        with col_date:
            date_filter = st.date_input("📅 Jour", value=None, key="log_date")


        # Plage horaire personnalisée
        heure_debut = heure_fin = None
        if filter_mode == "Plage horaire personnalisée":
            col_h1, col_h2 = st.columns(2)
            with col_h1:
                heure_debut = st.time_input("De", value=None, key="log_h_debut")
            with col_h2:
                heure_fin = st.time_input("À", value=None, key="log_h_fin")

        # ── Récupération des logs ──────────────────────────────────────────
        try:
            params = {"last": max_logs}
            if selected_cam != "ALL":
                params["cam"] = selected_cam
            if selected_level != "ALL":
                params["level"] = selected_level
            resp = requests.get("https://192.168.0.97:5000/logs", params=params, timeout=2, verify=False)
            logs = resp.json() if resp.status_code == 200 else []
        except Exception:
            logs = []
            st.warning("⚠️ Serveur de détection injoignable — logs indisponibles.")


        # ── Persistance locale des logs ───────────────────────────────────
        LOGS_FILE = "logs.jsonl"

        def save_logs_to_disk(new_logs):
            existing = set()
            if os.path.exists(LOGS_FILE):
                with open(LOGS_FILE, "r") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            existing.add((entry.get("date",""), entry.get("ts",""), entry.get("cam",""), entry.get("msg","")))
                        except:
                            pass
            with open(LOGS_FILE, "a") as f:
                for log in new_logs:
                    key = (log.get("date",""), log.get("ts",""), log.get("cam",""), log.get("msg",""))
                    if key not in existing:
                        f.write(json.dumps(log, ensure_ascii=False) + "\n")
                        existing.add(key)

        def load_all_logs():
            """Lit TOUT le fichier — le tri/limite se fait APRÈS les filtres."""
            if not os.path.exists(LOGS_FILE):
                return []
            result = []
            with open(LOGS_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            result.append(json.loads(line))
                        except Exception:
                            pass
            return result

        # 1. Sauvegarde les logs Flask (RAM) sur disque
        if logs:
            save_logs_to_disk(logs)

        # 2. Recharge TOUT le fichier
        logs = load_all_logs()

        # 3. Applique TOUS les filtres d'abord
        if selected_cam != "ALL":
            logs = [l for l in logs if l.get("cam") == selected_cam]
        if selected_level != "ALL":
            logs = [l for l in logs if l.get("level") == selected_level]
        if date_filter is not None:
            date_str = date_filter.strftime("%Y-%m-%d")
            logs = [l for l in logs if l.get("date", "") == date_str]

        # 4. SEULEMENT ICI on applique le nb max → les N derniers parmi les résultats filtrés
        logs = logs[-max_logs:]

        # ── Filtrage temporel ──────────────────────────────────────────────
        now_time = datetime.now()

        if filter_mode == "Dernière heure":
            filtered_logs = []
            for log in logs:
                try:
                    date_str = log.get("date", now_time.strftime("%Y-%m-%d"))
                    dt = datetime.strptime(f"{date_str} {log['ts']}", "%Y-%m-%d %H:%M:%S")
                    if now_time - dt <= timedelta(hours=1):
                        filtered_logs.append(log)
                except Exception:
                    filtered_logs.append(log)
            logs = filtered_logs

        elif filter_mode == "Plage horaire personnalisée" and heure_debut and heure_fin:
            filtered_logs = []
            for log in logs:
                try:
                    date_str = log.get("date", now_time.strftime("%Y-%m-%d"))
                    dt = datetime.strptime(f"{date_str} {log['ts']}", "%Y-%m-%d %H:%M:%S")
                    t_time = dt.time()
                    if heure_debut <= t_time <= heure_fin:
                        filtered_logs.append(log)
                except Exception:
                    filtered_logs.append(log)
            logs = filtered_logs

        elif filter_mode == "Plage horaire personnalisée" and heure_debut and heure_fin:
            filtered_logs = []
            for log in logs:
                try:
                    t = datetime.strptime(log["ts"], "%H:%M:%S").replace(
                        year=now_time.year, month=now_time.month, day=now_time.day
                    )
                    t_time = t.time()
                    if heure_debut <= t_time <= heure_fin:
                        filtered_logs.append(log)
                except Exception:
                    filtered_logs.append(log)
            logs = filtered_logs
            logs = logs[-max_logs:]

        # ── Résumé ────────────────────────────────────────────────────────
        nb_alert = sum(1 for l in logs if l.get("level") == "ALERT")
        nb_error = sum(1 for l in logs if l.get("level") == "ERROR")
        nb_debug = sum(1 for l in logs if l.get("level") == "DEBUG")
        nb_info  = sum(1 for l in logs if l.get("level") == "INFO")

        col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
        col_s1.metric("Total", len(logs))
        col_s2.metric("🚨 Alertes", nb_alert)
        col_s3.metric("❌ Erreurs", nb_error)
        col_s4.metric("ℹ️ Info",    nb_info)
        col_s5.metric("🔍 Debug",   nb_debug)

        st.markdown("---")

        if not logs:
            st.info("Aucun log correspondant aux filtres sélectionnés.")
            return

        # ── Couleurs par niveau ───────────────────────────────────────────
        level_styles = {
            "ALERT": {"bg": "#FF0000", "color": "white",   "icon": "🚨"},
            "ERROR": {"bg": "#CC3300", "color": "white",   "icon": "❌"},
            "INFO":  {"bg": "#0066b2", "color": "white",   "icon": "ℹ️"},
            "DEBUG": {"bg": "#444444", "color": "#aaaaaa", "icon": "🔍"},
        }

        # ── Affichage (du plus récent au plus ancien) ─────────────────────
        rows_html = ""
        for log in reversed(logs):
            level = log.get("level", "INFO")
            style = level_styles.get(level, level_styles["INFO"])
            cam   = log.get("cam", "?")
            ts    = log.get("ts", "?")
            msg   = log.get("msg", "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

            rows_html += (
                '<div style="display:grid;grid-template-columns:70px 90px 80px 1fr;'
                'border-bottom:1px solid #e8e8e8;min-height:36px;font-size:0.82rem;">'
                '<div style="background:' + style["bg"] + ';color:' + style["color"] + ';'
                'text-align:center;padding:6px 4px;font-weight:bold;">'
                + style["icon"] + ' ' + level + '</div>'
                '<div style="color:#888;padding:6px 10px;">' + ts + '</div>'
                '<div style="color:#ff6000;font-weight:bold;padding:6px 10px;">' + cam + '</div>'
                '<div style="color:#222;padding:6px 12px;">' + msg + '</div>'
                '</div>'
            )

        full_html = (
            '<div style="border:2px solid #0066b2;border-radius:10px;overflow:hidden;background:white;">'
            '<div style="display:grid;grid-template-columns:70px 90px 80px 1fr;'
            'background:#0066b2;color:white;font-weight:bold;font-size:0.8rem;">'
            '<div style="padding:8px 4px;text-align:center;">NIVEAU</div>'
            '<div style="padding:8px 10px;">HEURE</div>'
            '<div style="padding:8px 10px;">CAM</div>'
            '<div style="padding:8px 12px;">MESSAGE</div>'
            '</div>'
            '<div>' + rows_html + '</div>'
            '</div>'
        )
        components.html(full_html, height=600, scrolling=True)

        # ── Export CSV ────────────────────────────────────────────────────
        import csv, io
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["ts", "date", "cam", "level", "msg"])
        writer.writeheader()
        for log in logs:
            writer.writerow({k: log.get(k, "") for k in ["ts", "date", "cam", "level", "msg"]})
        st.download_button(
            "📥 Exporter les logs (CSV)",
            buf.getvalue().encode("utf-8"),
            file_name=f"logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            key="log_export"
        )

    logs_fragment()

elif page == "📘 GUIDE D'AMÉLIORATION":

    st.markdown(
        '<div class="header"><h1>🔄 Guide d\'amélioration continue</h1></div>',
        unsafe_allow_html=True
    )

    st.markdown("""
    <style>
    .step-card {
        background: white;
        border-radius: 14px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 6px 15px rgba(0,0,0,0.06);
        border-left: 6px solid #0066b2;
    }

    .step-title {
        font-size: 1.4rem;
        font-weight: bold;
        color: #0066b2;
        margin-bottom: 10px;
    }

    .step-sub {
        font-size: 1.05rem;
        color: #333;
        margin-bottom: 10px;
    }

    .code-box {
        background: #f4f4f4;
        padding: 10px;
        border-radius: 8px;
        font-family: monospace;
        margin-top: 8px;
        margin-bottom: 8px;
    }

    .warning {
        color: #d00000;
        font-weight: bold;
    }

    .badge {
        display:inline-block;
        background:#ff6000;
        color:white;
        padding:3px 8px;
        border-radius:8px;
        font-size:0.8rem;
        margin-left:5px;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🧪 ÉTAPE 1 : Identification et Extraction</div>'

            '<div class="step-sub">'
                '<b>Repérage :</b> On analyse les vidéos dans <code>alert_clips/</code><br>'
                '<b>Récupération :</b> <code>alert_clips/raw/</code>'
            '</div>'

            '<div class="step-sub">'
                'Extraction :'
                '<ul>'
                    '<li>1 image / seconde</li>'
                    '<li>uniquement les erreurs visibles</li>'
                '</ul>'
            '</div>'

            '<div class="step-sub">'
                'Script utilisé : <b>frame.py</b><br>'
                '<span class="warning">ATTENTION :</span> bien changer la ligne 14 par le bon chemin de la vidéo'
            '</div>'

            '<div class="code-box">python3 frame.py</div>'
        '</div>',
    unsafe_allow_html=True
    )

    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🧠 ÉTAPE 2 : Mise à jour Dataset Global (Radar)</div>'

            '<div class="step-sub">'
            'Upload des images sur <b>Roboflow Global</b>'
            '</div>'

            '<ul>'
                '<li><b>Upload :</b> Envoi ces images dans ton projet Roboflow Global </li>'
                '<li><b>Correction :</b> Corrige ou ajoute les labels (ID 3 pour la personne, et les autres pour les mains/sacs/articles)</li>'
                '<li><b>Génération :</b> Créé une Nouvelle Version sur Roboflow. Garde tes paramètres d\'augmentation (Blur, Noise, Light) pour que le modèle reste robuste</li>'
                '<li><b>Export :</b> Télécharge le nouveau data.yaml et les images et renomme le Data_global_vX avec X la version du dataset</li>'
                '<li><b>Transport :</b> Déplace le dans le dossier du projet</li>'
            '</ul>'
        '</div>',
    unsafe_allow_html=True)

    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🔧 ÉTAPE 3 : Radar Global</div>'

            '<div class="step-sub">'
            'Aucun besoin d\'amélioration actuellement<br>'
            '<b>Les personnes sont suffisamment bien détectées</b>'
            '</div>'
        '</div>', 
    unsafe_allow_html=True)

    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">✂️ ÉTAPE 4 : Préparation du Spécialiste</div>'

            '<div class="step-sub">'
            'Relancer le script de découpe sur tes nouvelles images en adaptant le script <b>decoupe.py </b>:'
            'Il faut changer les ligne 6 et 7 en ajoutant les version (ex: Data_global_vX ou bien Dataset_specialiste_vX)'
            'On lance '
            '</div>'
            '<div class="code-box">python3 decoupe.py</div>'

            '<div class="step-sub">'
            'Sur vs code, sur le dossier créé par le script de découpe on fait clic droit new file : data.yaml => ici c\'est le meme que la version du radar spécialiste antérieur (sauf si ajout ou suppression de classes) donc on copie colle.'
            '<br>'
            '<span class="warning">ATTENTION :</span> vérifier chemin ligne 1'
            '</div>'

            '<div class="step-sub">'
            'Pour tester que cela fonctionne on lance le script verif.py'
            '<br>'
            '<span class="warning">ATTENTION :</span> changer les lignes 7 et 8'
            '</div>'
            '<div class="code-box">python3 verif.py</div>'

            '<div class="step-sub">'
            'Ensuite va dans le dossier du modèle spécialiste:'
            '</div>'
            '<div class="code-box">cd Dataset_Specialiste_vX</div>'

            '<div class="step-sub">'
            'Puis on crée les dossiers pour séparer les données (valid et train):'
            '</div>'
            '<div class="code-box">mkdir -p images/train images/val labels/train labels/val</div>'

            '<div class="step-sub">'
            'Split : Lancement du script de séparation pour isoler 80% pour le train et 20% pour le valid:'
            '<br>'
            '<span class="warning">ATTENTION :</span> Faire gaffe aux lignes 6 et 7 avec le chemin'
            '</div>'
            '<div class="code-box">python3 split.py</div>'
        '</div>',
     unsafe_allow_html=True)

    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🚀 ÉTAPE 5 : Ré-entraînement du Spécialiste</div>'

            '<div class="code-box">'
            'yolo task=detect mode=train model=runs/detect/radar_specialiste_v(X-1)/weights/best.pt data=Dataset_Specialiste_vX/data.yaml epochs=200 patience=50 imgsz=640 batch=-1 mosaic=1.0 mixup=0.2 cos_lr=True close_mosaic=10 name=specialiste_final_vX'
            '</div>'

            '<div class="step-sub">'
            '<b>best.pt</b> <span class="badge">MEILLEUR</span><br>'
            'C\'est la version qui a eu les meilleurs scores de précision lors des tests de validation.'
            'Pour le Fine-Tuning. C\'est le cerveau le plus "brillant" que l\'on a produit. C\'est la base pour devenir encore meilleur.'
            '</div>'

            '<div class="step-sub">'
            '<b>last.pt</b> <span class="badge">REPRISE</span><br>'
            'C\'est l\'image exacte du modèle à la toute dernière époque de l\'entraînement.'
            'Pour la reprise après crash. Si l\'entraînement a duré 20h et que le PC a planté, on reprend le last.pt pour finir les époques restantes.'
            '</div>'
        '</div>',
    unsafe_allow_html=True)

    st.markdown(
        '<div class="step-card">'
            '<div class="step-title">🔄 ÉTAPE 6 : Mise à jour detect_obj.py</div>'

            '<div class="step-sub">'
            'Remplacer simplement dans le script detect_obj.py:'
            '<ul>'
                '<li>model_radar</li>'
                '<li>model_specialiste</li>'
            '</ul>'
            '</div>'

            '<div class="step-sub">'
            '→ vers les nouveaux <b>best.pt</b>'
            '</div>'
        '</div>', 
    unsafe_allow_html=True)


# RGPD
st.markdown("""
<div style="font-size:0.8rem;color:gray;margin-top:30px">
<b>RGPD :</b> traitement local des données vidéo pour sécurité uniquement.
Aucune reconnaissance faciale.
</div>
""", unsafe_allow_html=True)