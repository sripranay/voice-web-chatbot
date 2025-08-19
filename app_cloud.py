# app_cloud.py — Streamlit Voice Web Chatbot (cloud-safe: plays audio in browser)
# Works on Streamlit Community Cloud. Voice OUT via gTTS (browser player). Input = text box.
# pip install -r requirements_cloud.txt

import re, math, urllib.parse, io
import requests
from bs4 import BeautifulSoup
import trafilatura
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS
# Try to use pyspellchecker (module name: spellchecker). If a wrong package is present, fall back gracefully.
try:
    from spellchecker import SpellChecker  # provided by pyspellchecker
except Exception:
    class SpellChecker:                    # no-op fallback so the app still runs
        def __init__(self, *a, **k): pass
        def unknown(self, words): return set()
        def correction(self, w): return w

from gtts import gTTS
import streamlit as st

st.set_page_config(page_title="Voice Web Chatbot (Cloud)", page_icon="🌐")
st.title("🌐 Voice Web Chatbot — Cloud")

BAD_DOMAINS     = ("pinterest.", "quora.", "reddit.", "youtube.", "facebook.", "x.com", "twitter.")
REQUEST_TIMEOUT = 6
MAX_RESULTS     = 8
FETCH_TOP       = 3
TEXT_LIMIT      = 6000
USE_TRAFILATURA = False

STOP = set("a an the of for in on at to is are was were be and or by with from as about into over after before between than then this that those these who what where when which why how".split())

def clean_question(q: str) -> str:
    q = q.strip()
    q = re.sub(r"^\s*(who|what|where|when|why|which|tell me about)\s+(is|are|was|were|the)?\s*", "", q, flags=re.I)
    return q.rstrip("?.! ").strip()

speller = SpellChecker()
def soft_spellfix(q: str) -> str:
    words = q.split()
    unknown = {w.lower() for w in words if len(w) > 6 and w.isalpha() and w.lower() not in STOP}
    unknown = speller.unknown(unknown)
    out = []
    for w in words:
        wl = w.lower()
        if wl in unknown:
            cand = speller.correction(wl)
            if cand and abs(len(cand) - len(wl)) <= 2:
                w = cand
        out.append(w)
    return " ".join(out)

def keywords(t: str):
    return [w for w in re.findall(r"[a-zA-Z]+", t.lower()) if w not in STOP]

def extract_readable(url: str) -> str:
    if USE_TRAFILATURA:
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                extracted = trafilatura.extract(downloaded, include_comments=False, include_links=False, favor_recall=False)
                if extracted and len(extracted.split()) > 40:
                    return " ".join(extracted.split())[:TEXT_LIMIT]
        except Exception:
            pass
    try:
        html = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}).text
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]): tag.extract()
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        meta_text = (meta.get("content", "") if meta else "")
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
    if not scored:
        return " ".join(sents[:k]).strip()
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen = sorted(scored[:max(2, k)], key=lambda x: x[1])
    return " ".join(s for _, _, s in chosen).strip()

def web_answer(user_query: str, sentences=3):
    if not user_query.strip():
        return "I didn't catch that. Please try again.", []
    q0 = clean_question(user_query)
    q1 = soft_spellfix(q0)
    qwords = set(keywords(q1))

    results = []
    with DDGS() as ddg:
        results.extend(ddg.text(q1, max_results=MAX_RESULTS, safesearch="moderate", region="in-en"))
        try:
            suggs = list(ddg.suggestions(q1))
            if suggs:
                phrase = suggs[0].get("phrase", "")
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
        text = (title + " " + body).lower()
        score = sum(text.count(w) for w in qwords) / (1 + len(text)/1500)
        prelim.append((score, title, url))

    if not prelim:
        return "Sorry, I couldn't find a good answer on the web.", []

    prelim.sort(key=lambda x: x[0], reverse=True)
    to_fetch = prelim[:3]

    candidates, sources = [], []
    for _, title, url in to_fetch:
        txt = extract_readable(url)
        if not txt: continue
        t = (title + " " + txt).lower()
        hits = sum(t.count(w) for w in qwords)
        score = hits / (1 + len(t)/1500)
        candidates.append((score, title, url, txt))
        sources.append((title or url, url))

    if not candidates:
        return "Sorry, I couldn't get a clear answer from the pages I found.", sources

    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0]
    ans = summarize(best[3], qwords, k=sentences)
    host = urllib.parse.urlparse(best[2]).netloc
    if ans:
        ans = f"{ans} (Source: {host})"
    else:
        ans = "Sorry, I couldn't get a clear answer."
    return ans, sources[:3]

def speak_in_browser(text: str, lang: str = "en"):
    mp3 = io.BytesIO()
    gTTS(text=text, lang=lang).write_to_fp(mp3)
    mp3.seek(0)
    st.audio(mp3.read(), format="audio/mp3")

if "chat" not in st.session_state:
    st.session_state.chat = []

sent_len   = st.sidebar.slider("📝 Answer length (sentences)", 2, 6, 3)
auto_voice = st.sidebar.checkbox("🔊 Auto play voice", value=True)

q = st.text_input("Type your question:", "")
if st.button("Ask") and q.strip():
    with st.spinner("Searching the web…"):
        ans, sources = web_answer(q, sentences=sent_len)
    st.success(ans)
    if auto_voice:
        speak_in_browser(ans)
    st.session_state.chat.append((q, ans, sources))

st.subheader("Chat History")
if not st.session_state.chat:
    st.caption("No messages yet.")
else:
    for qq, aa, ss in reversed(st.session_state.chat):
        st.markdown(f"**You:** {qq}")
        st.markdown(f"**Bot:** {aa}")
        with st.expander("Sources"):
            for (title, url) in ss:
                host = urllib.parse.urlparse(url).netloc
                st.markdown(f"- [{title}]({url}) — _{host}_")
        st.markdown("---")
