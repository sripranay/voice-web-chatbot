# app.py — ONE interface: mic input + auto voice + Pause/Resume/Stop
# Env: chatvoice
# pip install streamlit ddgs trafilatura beautifulsoup4 lxml pyspellchecker sounddevice soundfile SpeechRecognition pyttsx3 pygame requests

import re, math, urllib.parse, uuid, os, tempfile, requests
from bs4 import BeautifulSoup
import trafilatura
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

# Spell-check (safe import so cloud/wrong pkg won't crash)
try:
    from spellchecker import SpellChecker   # provided by pyspellchecker
except Exception:
    class SpellChecker:
        def __init__(self, *a, **k): pass
        def unknown(self, words): return set()
        def correction(self, w): return w

import sounddevice as sd, soundfile as sf
import speech_recognition as sr
import pyttsx3, streamlit as st

# Optional playback engine (pause/resume/stop on PC speakers)
HAVE_PYGAME = True
try:
    import pygame
except Exception:
    HAVE_PYGAME = False

# ---------- Page ----------
st.set_page_config(page_title="Voice Web Chatbot", page_icon="🎙️")
st.title("🎙️ Voice Web Chatbot")

# ---------- Config ----------
SAMPLE_RATE       = 16000
BAD_DOMAINS       = ("pinterest.", "quora.", "reddit.", "youtube.", "facebook.", "x.com", "twitter.")
REQUEST_TIMEOUT   = 6
MAX_RESULTS       = 8
FETCH_TOP         = 3
TEXT_LIMIT        = 6000
USE_TRAFILATURA   = False

# ---------- Session ----------
if "chat" not in st.session_state: st.session_state.chat = []
if "mic_index" not in st.session_state: st.session_state.mic_index = None
if "audio" not in st.session_state:
    st.session_state.audio = {"file": None, "is_playing": False, "is_paused": False, "rate": 170, "volume": 1.0}
if "pygame_init" not in st.session_state: st.session_state.pygame_init = False

# ---------- Audio: Pause/Resume/Stop ----------
def ensure_pygame():
    if not HAVE_PYGAME: return False
    if not st.session_state.pygame_init:
        try:
            pygame.mixer.pre_init(frequency=22050, size=-16, channels=2, buffer=512)
            pygame.mixer.init()
            st.session_state.pygame_init = True
        except Exception:
            return False
    return True

def stop_audio():
    if HAVE_PYGAME and st.session_state.pygame_init:
        try: pygame.mixer.music.stop()
        except Exception: pass
    st.session_state.audio["is_playing"] = False
    st.session_state.audio["is_paused"] = False

def pause_audio():
    if HAVE_PYGAME and st.session_state.pygame_init and st.session_state.audio["is_playing"]:
        try:
            pygame.mixer.music.pause()
            st.session_state.audio["is_paused"] = True
        except Exception: pass

def resume_audio():
    if HAVE_PYGAME and st.session_state.pygame_init and st.session_state.audio["is_paused"]:
        try:
            pygame.mixer.music.unpause()
            st.session_state.audio["is_paused"] = False
        except Exception: pass

def play_wav_pc(path: str):
    if not ensure_pygame():
        st.warning("Pygame not available; using browser audio instead.", icon="⚠️")
        with open(path, "rb") as f: st.audio(f.read(), format="audio/wav")
        return
    stop_audio()
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
        st.session_state.audio["file"] = path
        st.session_state.audio["is_playing"] = True
        st.session_state.audio["is_paused"] = False
    except Exception:
        st.session_state.audio["is_playing"] = False
        st.session_state.audio["is_paused"] = False

# ---------- TTS to WAV ----------
def tts_to_wav(text: str) -> str:
    old = st.session_state.audio.get("file")
    if old and os.path.isfile(old):
        try: os.remove(old)
        except Exception: pass
    wav = os.path.join(tempfile.gettempdir(), f"tts_{uuid.uuid4().hex}.wav")
    e = pyttsx3.init("sapi5")
    e.setProperty("rate", st.session_state.audio["rate"])
    e.setProperty("volume", st.session_state.audio["volume"])
    # optional voice picker support
    vid = st.session_state.get("voice_id")
    if vid: e.setProperty("voice", vid)
    e.save_to_file(text, wav)
    e.runAndWait()
    try: e.stop()
    except Exception: pass
    return wav

# ---------- NLP helpers ----------
STOP = set("a an the of for in on at to is are was were be and or by with from as about into over after before between than then this that those these who what where when which why how".split())
def clean_question(q: str) -> str:
    q = q.strip()
    q = re.sub(r"^\s*(who|what|where|when|why|which|tell me about)\s+(is|are|was|were|the)?\s*", "", q, flags=re.I)
    return q.rstrip("?.! ").strip()
speller = SpellChecker()
def soft_spellfix(q: str) -> str:
    words = q.split()
    unknown = {w.lower() for w in words if len(w) > 6 and w.isalpha() and w.lower() not in STOP}
    try:
        unknown = speller.unknown(unknown)
    except Exception:
        unknown = set()
    out = []
    for w in words:
        wl = w.lower()
        if wl in unknown:
            cand = speller.correction(wl)
            if cand and abs(len(cand)-len(wl)) <= 2:
                w = cand
        out.append(w)
    return " ".join(out)
def keywords(t: str):
    return [w for w in re.findall(r"[a-zA-Z]+", t.lower()) if w not in STOP]

# ---------- Fetch & summarize ----------
def extract_readable(url: str) -> str:
    if USE_TRAFILATURA:
        try:
            dl = trafilatura.fetch_url(url)
            if dl:
                ex = trafilatura.extract(dl, include_comments=False, include_links=False, favor_recall=False)
                if ex and len(ex.split()) > 40:
                    return " ".join(ex.split())[:TEXT_LIMIT]
        except Exception:
            pass
    try:
        html = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script","style","noscript"]): tag.extract()
        meta = soup.find("meta", attrs={"name":"description"}) or soup.find("meta", property="og:description")
        meta_text = (meta.get("content","") if meta else "")
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        main = " ".join(paras[:6])
        return " ".join((meta_text + " " + main).split())[:TEXT_LIMIT]
    except Exception:
        return ""

def summarize(text: str, qwords, k: int) -> str:
    if not text: return ""
    sents = re.split(r"(?<=[.!?])\s+", text)
    scored = []
    for i, s in enumerate(sents):
        words = [w for w in re.findall(r"[a-zA-Z]+", s.lower()) if w not in STOP]
        overlap = sum(1 for w in words if w in qwords)
        if 6 <= len(s.split()) <= 40:
            scored.append((overlap, i, s))
    if not scored: return " ".join(sents[:k]).strip()
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = sorted(scored[:max(2,k)], key=lambda x: x[1])
    return " ".join(s for _,_,s in chosen).strip()

def web_answer(user_query: str, sentences=3):
    if not user_query.strip(): return "I didn't catch that. Please try again.", []
    q0 = clean_question(user_query)
    q1 = soft_spellfix(q0)
    qwords = set(keywords(q1))
    results = []
    with DDGS() as ddg:
        results.extend(ddg.text(q1, max_results=MAX_RESULTS, safesearch="moderate", region="in-en"))
        try:
            suggs = list(ddg.suggestions(q1))
            if suggs:
                phrase = suggs[0].get("phrase","")
                if phrase and phrase.lower() != q1.lower():
                    results.extend(ddg.text(phrase, max_results=MAX_RESULTS, safesearch="moderate", region="in-en"))
        except Exception:
            pass
    prelim, seen = [], set()
    for r in results:
        url = r.get("href") or r.get("url") or ""
        title = r.get("title") or ""
        body = r.get("body") or r.get("description") or ""
        if not url or url in seen: continue
        if any(bad in url for bad in BAD_DOMAINS): continue
        seen.add(url)
        text = (title + " " + (body or "")).lower()
        score = sum(text.count(w) for w in qwords) / (1 + len(text)/1500)
        prelim.append((score, title, url))
    if not prelim: return "Sorry, I couldn't find a good answer on the web.", []
    prelim.sort(key=lambda x: x[0], reverse=True)
    to_fetch = prelim[:FETCH_TOP]
    candidates, sources = [], []
    for _, title, url in to_fetch:
        txt = extract_readable(url)
        if not txt: continue
        t = (title + " " + txt).lower()
        hits = sum(t.count(w) for w in qwords)
        score = hits / (1 + len(t)/1500)
        candidates.append((score, title, url, txt))
        sources.append((title or url, url))
    if not candidates: return "Sorry, I couldn't get a clear answer from the pages I found.", sources
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]
    ans = summarize(best[3], qwords, k=sentences)
    host = urllib.parse.urlparse(best[2]).netloc
    return (f"{ans} (Source: {host})" if ans else "Sorry, I couldn't get a clear answer."), sources[:3]

# ---------- Capture mic, ASR ----------
def record_seconds(seconds: int, mic_index=None) -> str:
    if mic_index is not None:
        sd.default.device = (mic_index, None)
    st.info(f"Recording {seconds}s… Speak now.")
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    sf.write("temp.wav", audio, SAMPLE_RATE)
    with open("temp.wav", "rb") as f:
        st.audio(f.read(), format="audio/wav")
    r = sr.Recognizer()
    with sr.AudioFile("temp.wav") as src:
        data = r.record(src)
    try:
        text = r.recognize_google(data)
        st.write(f"**You said:** {text}")
        return text
    except sr.UnknownValueError:
        st.warning("Couldn't understand the audio."); return ""
    except sr.RequestError as e:
        st.error(f"API/network error: {e}"); return ""

# ---------- Sidebar ----------
with st.sidebar:
    st.header("Settings")
    # mic picker
    try:
        devices = sd.query_devices()
        inputs = [(i, d["name"]) for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]
        labels = [f"[{i}] {name}" for i, name in inputs] or ["(no input devices found)"]
        choice = st.selectbox("Microphone", options=labels, index=0 if labels else None)
        if inputs:
            st.session_state.mic_index = inputs[labels.index(choice)][0]
    except Exception:
        st.caption("Could not list microphones.")

    auto_speak = st.checkbox("🔊 Auto speak answer (PC speakers)", value=True)
    seconds    = st.slider("🎤 Record length (sec)", 3, 10, 5)
    sent_len   = st.slider("📝 Answer length (sentences)", 2, 6, 3)
    st.session_state.audio["rate"]   = st.slider("🔧 Speech rate", 140, 200, st.session_state.audio["rate"])
    st.session_state.audio["volume"] = st.slider("🔧 Speech volume", 0.5, 1.0, st.session_state.audio["volume"])

    # optional voice picker
    if "voice_id" not in st.session_state:
        try:
            _e = pyttsx3.init("sapi5")
            voices = _e.getProperty("voices")
            st.session_state.voice_choices = [(v.name, v.id) for v in voices]
        except Exception:
            st.session_state.voice_choices = []
    if st.session_state.voice_choices:
        names = [n for n,_ in st.session_state.voice_choices]
        pick  = st.selectbox("🔊 TTS voice", names, index=0)
        st.session_state.voice_id = dict(st.session_state.voice_choices)[pick]

    st.markdown("### Speech controls")
    c1, c2, c3 = st.columns(3)
    c1.button("⏸️ Pause", on_click=pause_audio,  disabled=not (HAVE_PYGAME and st.session_state.audio["is_playing"] and not st.session_state.audio["is_paused"]))
    c2.button("▶️ Resume", on_click=resume_audio, disabled=not (HAVE_PYGAME and st.session_state.audio["is_paused"]))
    c3.button("⛔ Stop",   on_click=stop_audio,   disabled=not (HAVE_PYGAME and st.session_state.audio["is_playing"]))

    if st.button("🗑️ Clear chat"):
        stop_audio()
        st.session_state.chat = []
        st.success("Chat cleared.")

# ---------- Ask flow ----------
def ask_and_answer(query_text: str):
    if not query_text.strip():
        st.warning("Please type or record a question."); return
    with st.spinner("Searching the web…"):
        ans, sources = web_answer(query_text, sentences=sent_len)
    st.success(ans)
    if auto_speak:
        wav = tts_to_wav(ans)
        play_wav_pc(wav)
    st.session_state.chat.append((query_text, ans, sources))

# ---------- Main UI ----------
q = st.text_input("Type your question (or leave empty and click **Record & Ask**):", "")
b1, b2 = st.columns(2)
if b1.button("🎤 Record & Ask"):
    rec = record_seconds(seconds, st.session_state.mic_index)
    if rec.strip(): ask_and_answer(rec)
if b2.button("Ask"):
    ask_and_answer(q)

st.subheader("Chat History")
if not st.session_state.chat:
    st.caption("No messages yet.")
else:
    for (qq, aa, ss) in reversed(st.session_state.chat):
        st.markdown(f"**You:** {qq}")
        st.markdown(f"**Bot:** {aa}")
        with st.expander("Sources"):
            for (title, url) in (ss or []):
                host = urllib.parse.urlparse(url).netloc or "source"
                st.markdown(f"- [{title or url}]({url}) — _{host}_")
        st.markdown("---")
