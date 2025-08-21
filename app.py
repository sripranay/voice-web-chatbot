# app.py — Gradio Space: simple mic + web answer + voice reply (Stop button)

import os, re, math, urllib.parse, uuid
from io import BytesIO

import requests
from bs4 import BeautifulSoup
import trafilatura
from duckduckgo_search import DDGS
import speech_recognition as sr
from gtts import gTTS
import gradio as gr

SENTS_OUT = 3
BAD_DOMAINS = ("pinterest.", "quora.", "reddit.", "youtube.", "facebook.", "x.com", "twitter.")
STOP = set("""a an the of for in on at to is are was were be and or by with from as about into over after
before between than then this that those these who what where when which why how""".split())

# ---------- helpers ----------
def clean_question(q: str) -> str:
    q = q.strip()
    q = re.sub(r"^\s*(who|what|where|when|why|which|tell me about)\s+(is|are|was|were|the)?\s*",
               "", q, flags=re.I)
    return q.rstrip("?.! ").strip()

def keywords(t: str):
    return [w for w in re.findall(r"[a-zA-Z]+", t.lower()) if w not in STOP]

def extract_readable(url: str) -> str:
    # try Trafilatura first
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            extracted = trafilatura.extract(
                downloaded,
                include_comments=False, include_links=False, favor_recall=False)
            if extracted and len(extracted.split()) > 40:
                return extracted
    except Exception:
        pass
    # fallback: bs4
    try:
        html = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"}).text
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script","style","noscript"]): tag.extract()
        meta = soup.find("meta", attrs={"name":"description"}) or soup.find("meta", property="og:description")
        meta_text = (meta.get("content","") if meta else "")
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        main = " ".join(paras[:6])
        return " ".join((meta_text + " " + main).split())
    except Exception:
        return ""

def rank_and_summarize(text: str, qwords, k=SENTS_OUT) -> str:
    if not text: return ""
    text = " ".join(text.split())[:8000]
    sents = re.split(r"(?<=[.!?])\s+", text)
    scored = []
    for i, s in enumerate(sents):
        words = keywords(s)
        overlap = sum(1 for w in words if w in qwords)
        if 6 <= len(s.split()) <= 40:
            scored.append((overlap, i, s))
    if not scored:
        return " ".join(sents[:k])
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = sorted(scored[:max(2,k)], key=lambda x: x[1])
    return " ".join(s for _,_,s in chosen)

def web_answer(user_query: str) -> str:
    if not user_query.strip():
        return "I didn't catch that. Please try again."
    q1 = clean_question(user_query)
    qwords = keywords(q1)

    results = []
    with DDGS() as ddg:
        results.extend(ddg.text(q1, max_results=8, safesearch="moderate", region="in-en"))
        try:
            suggs = list(ddg.suggestions(q1))
            if suggs:
                phrase = suggs[0].get("phrase","")
                if phrase and phrase.lower() != q1.lower():
                    results.extend(ddg.text(phrase, max_results=8, safesearch="moderate", region="in-en"))
        except Exception:
            pass

    seen, candidates = set(), []
    for r in results:
        url = r.get("href") or r.get("url") or ""
        title = r.get("title") or ""
        if not url or url in seen: continue
        if any(bad in url for bad in BAD_DOMAINS): continue
        seen.add(url)

        text = extract_readable(url)
        if not text: continue

        t = (title + " " + text).lower()
        hits = sum(t.count(w) for w in set(qwords))
        score = hits / math.sqrt(len(t)/1500 + 1)
        candidates.append((score, title, url, text))

    if not candidates:
        return "Sorry, I couldn't find a good answer on the web."

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, title, url, text = candidates[0]
    ans = rank_and_summarize(text, qwords, k=SENTS_OUT)
    host = urllib.parse.urlparse(url).netloc
    return f"{ans} (Source: {host})"

def transcribe(audio_path: str) -> str:
    r = sr.Recognizer()
    with sr.AudioFile(audio_path) as src:
        data = r.record(src)
    try:
        return r.recognize_google(data)
    except sr.UnknownValueError:
        return ""
    except sr.RequestError:
        return ""

def tts_mp3(text: str) -> str | None:
    try:
        mp3_path = f"/tmp/tts_{uuid.uuid4().hex}.mp3"
        gTTS(text).save(mp3_path)
        return mp3_path
    except Exception:
        return None

# ---------- Gradio pipeline ----------
def ask(mic_audio, typed_text, history):
    # decide question
    q = ""
    if mic_audio:
        q = transcribe(mic_audio)
    if not q and typed_text:
        q = typed_text.strip()
    if not q:
        return history, None, typed_text

    ans = web_answer(q)
    history = (history or []) + [(q, ans)]

    # voice reply
    mp3 = tts_mp3(ans)
    return history, mp3, ""  # clear textbox

# ---------- UI ----------
with gr.Blocks(theme=gr.themes.Soft(), css="""
#title {text-align:center}
""") as demo:
    gr.Markdown("<h1 id='title'>🎙️ Voice Web Chatbot</h1>")
    with gr.Row():
        mic = gr.Microphone(label="Record your question", type="filepath")
        txt = gr.Textbox(label="...or type your question", placeholder="Ask anything…", lines=1)
    with gr.Row():
        ask_btn = gr.Button("🧠 Transcribe & Answer", variant="primary")
        stop_btn = gr.Button("⏹ Stop audio", variant="secondary")
        clear_btn = gr.Button("Clear chat")

    chat = gr.Chatbot(label="Chat History", height=300)
    voice = gr.Audio(label="Voice answer", autoplay=True, interactive=False)

    # wiring
    ask_btn.click(ask, [mic, txt, chat], [chat, voice, txt])

    # Stop audio with a tiny JS snippet
    stop_btn.click(fn=None, inputs=None, outputs=None,
                   js="() => { const a = document.querySelector('audio'); if (a){a.pause(); a.currentTime=0;} }")

    clear_btn.click(lambda: ([], None, ""), None, [chat, voice, txt])

demo.launch()
