"""StudyTube AI ✨ - turn YouTube videos into a personal learning experience.

Stack: Streamlit + Google Gemini (google-genai SDK) + youtube-transcript-api + ReportLab.
The Gemini API key is read from Streamlit Secrets (GEMINI_API_KEY) - never hard-coded.
"""

import glob
import html
import io
import json
import os
import random
import re
import tempfile
import time
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape as xml_escape

import streamlit as st
from google import genai
from google.genai import errors, types
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

try:  # needed only for Urdu text inside the PDF
    import arabic_reshaper
    try:
        from bidi.algorithm import get_display
    except ImportError:
        from bidi import get_display
    SHAPING_OK = True
except ImportError:
    SHAPING_OK = False

st.set_page_config(page_title="StudyTube AI ✨", page_icon="✨", layout="wide")

MAX_CHARS = 180_000  # transcript characters sent to Gemini (longer videos are truncated)
MIN_CHARS = 200      # shorter transcripts are not useful for study material

DIFFICULTY = {
    "Beginner": "Use simple words and short sentences, explain ideas step by step, and ask basic recall questions.",
    "Intermediate": "Use a balanced level of detail, include some application questions and moderately tricky options.",
    "Advanced": "Use precise technical language, go deep into details, ask analytical questions with plausible distractors.",
}

LABELS = {
    "English": ["Summary", "Key Points", "Definitions", "Detailed Notes", "Questions & Answers",
                "Quick Revision Sheet", "Multiple Choice Questions", "Answer", "Flashcards", "Explanation"],
    "Urdu": ["خلاصہ", "اہم نکات", "تعریفات", "تفصیلی نوٹس", "سوالات و جوابات",
             "فوری دہرائی شیٹ", "کثیر الانتخابی سوالات", "جواب", "فلیش کارڈز", "وضاحت"],
}

# ---- PDF font discovery ------------------------------------------------------
FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
             "/Library/Fonts", "/System/Library/Fonts", "C:/Windows/Fonts", tempfile.gettempdir()]
# Preferred Arabic-script fonts (lowercase file-name prefixes, best first)
AR_PREF = ["notonaskharabic-regular", "notonaskharabic[", "notonaskharabic", "amiri-regular", "amiri",
           "notosansarabic-regular", "notosansarabic[", "scheherazade", "dejavusans.", "tahoma", "arial."]
LAT_PREF = ["dejavusans.", "notosans-regular", "liberationsans-regular", "freesans.", "arial."]
FONT_URLS = [  # last-resort download (needs internet access on the server)
    "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoNaskhArabic/hinted/ttf/NotoNaskhArabic-Regular.ttf",
    "https://github.com/google/fonts/raw/main/ofl/notonaskharabic/NotoNaskhArabic%5Bwght%5D.ttf",
]
# Arabic presentation forms that Urdu text needs (pe, tte, che, dal, rre, jeh, gaf, noon ghunna, heh, yeh barree...)
NEED_FORMS = [c for a, b in [(0xFB56, 0xFB59), (0xFB66, 0xFB69), (0xFB7A, 0xFB7D), (0xFB88, 0xFB8D),
                             (0xFB92, 0xFB95), (0xFB9E, 0xFB9F), (0xFBA6, 0xFBAF), (0xFBFC, 0xFBFF),
                             (0xFE8D, 0xFEFC)] for c in range(a, b + 1)]
# Look-alike glyphs used only if a font lacks the exact Urdu form (heh goal -> heh, yeh barree -> alef maksura)
SUBST = {0xFBA6: 0xFEE9, 0xFBA7: 0xFEEA, 0xFBA8: 0xFEEB, 0xFBA9: 0xFEEC, 0xFBAE: 0xFEEF, 0xFBAF: 0xFEF0}
AR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Noto+Naskh+Arabic:wght@400;600&display=swap');
:root{--lav:#b9a6f0;--lavd:#7c5cd6;--pink:#ffc8dd;--peach:#ffd8be;--cream:#fff8ef;--ink:#3b3355;}
html, body, .stApp {font-family:'Poppins','Noto Naskh Arabic',sans-serif;}
.stApp{background:linear-gradient(135deg,#fff8ef 0%,#f3ecff 45%,#ffeaf2 100%);}
#MainMenu, footer, header[data-testid="stHeader"]{visibility:hidden;height:0;}
.block-container{max-width:1100px;padding-top:1.5rem;}
.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp p,.stApp li,.stApp label,
[data-testid="stMarkdownContainer"],[data-testid="stMetricValue"],[data-testid="stMetricLabel"]{color:var(--ink);}
.hero{background:linear-gradient(120deg,#d9c8ff 0%,#ffc8dd 55%,#ffd8be 100%);border-radius:28px;
 padding:2.2rem 1.5rem;text-align:center;box-shadow:0 12px 30px rgba(124,92,214,.18);margin-bottom:1.4rem;}
.hero h1{font-size:clamp(1.9rem,5vw,3rem);margin:0;font-weight:700;color:#3b2a74 !important;}
.hero p{margin:.5rem 0 0;font-size:clamp(.95rem,2.4vw,1.15rem);color:#5a4a86 !important;}
.card{background:#fff;border-radius:20px;padding:1rem 1.3rem;margin-bottom:.8rem;line-height:1.7;
 box-shadow:0 6px 18px rgba(124,92,214,.12);border-left:6px solid var(--lav);color:var(--ink);}
.card.pink{border-left-color:#ff9ec4;} .card.peach{border-left-color:#ffb98a;}
.card b{color:#5b3fb0;}
.flash{background:linear-gradient(135deg,#fff 0%,#f3ecff 100%);border:2px dashed var(--lav);border-radius:26px;
 padding:2.2rem 1.5rem;min-height:190px;display:flex;align-items:center;justify-content:center;text-align:center;
 font-size:1.35rem;font-weight:600;color:var(--ink);box-shadow:0 10px 26px rgba(124,92,214,.15);margin:.6rem 0 1rem;}
.flash.back{background:linear-gradient(135deg,#fff3ea 0%,#ffe6ef 100%);border-color:#ffb3cf;}
.stButton>button,.stFormSubmitButton>button,.stDownloadButton>button{width:100%;border:none;border-radius:16px;
 padding:.65rem 1.2rem;font-weight:600;color:#fff !important;background:linear-gradient(120deg,#9b7be8,#ff8fba);
 box-shadow:0 6px 16px rgba(155,123,232,.35);transition:transform .15s ease,box-shadow .15s ease;}
.stButton>button p,.stFormSubmitButton>button p,.stDownloadButton>button p{color:#fff !important;}
.stButton>button:hover,.stFormSubmitButton>button:hover,.stDownloadButton>button:hover{transform:translateY(-2px);
 box-shadow:0 10px 22px rgba(155,123,232,.45);color:#fff !important;}
.stTextInput input,.stTextArea textarea,div[data-baseweb="select"]>div{background:#fff !important;color:var(--ink) !important;
 border-radius:14px !important;}
[data-testid="stMetric"]{background:#fff;border-radius:18px;padding:.8rem 1rem;box-shadow:0 6px 16px rgba(124,92,214,.12);}
button[data-baseweb="tab"]{background:rgba(255,255,255,.7);border-radius:14px 14px 0 0;padding:.5rem 1rem;margin-right:.3rem;}
button[data-baseweb="tab"][aria-selected="true"]{background:linear-gradient(120deg,#e4d8ff,#ffd9e8);}
button[data-baseweb="tab"] p{color:var(--ink) !important;font-weight:600;}
[data-testid="stExpander"]{background:#fff;border-radius:16px;box-shadow:0 4px 12px rgba(124,92,214,.1);border:none;}
@media (max-width:640px){.block-container{padding:1rem .6rem;} .card{padding:.8rem 1rem;} .flash{font-size:1.1rem;}}
</style>
"""

RTL_CSS = """
<style>
[data-testid="stTabs"] [role="tabpanel"]{direction:rtl;text-align:right;font-family:'Noto Naskh Arabic','Poppins',sans-serif;font-size:1.1rem;}
.card{border-left:none;border-right:6px solid var(--lav);} .card.pink{border-right-color:#ff9ec4;} .card.peach{border-right-color:#ffb98a;}
</style>
"""


class AppError(Exception):
    """Friendly error that is safe to show to the user.

    fallback=True means the manual-transcript option should be opened for the user.
    """

    def __init__(self, message, fallback=False):
        super().__init__(message)
        self.fallback = fallback


# ----------------------------------------------------------------------------
# Secrets, YouTube and transcript helpers
# ----------------------------------------------------------------------------
def get_api_key():
    try:
        key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        key = os.environ.get("GEMINI_API_KEY")
    return key.strip() if key else None


def get_model_name():
    try:
        return st.secrets["GEMINI_MODEL"]
    except Exception:
        return "gemini-2.5-flash"


def get_proxy_config():
    """Optional proxy (set PROXY_URL or WEBSHARE_USER/WEBSHARE_PASS in secrets) to avoid YouTube IP blocks."""
    try:
        from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig
        if "WEBSHARE_USER" in st.secrets and "WEBSHARE_PASS" in st.secrets:
            return WebshareProxyConfig(proxy_username=st.secrets["WEBSHARE_USER"],
                                       proxy_password=st.secrets["WEBSHARE_PASS"])
        if "PROXY_URL" in st.secrets:
            return GenericProxyConfig(http_url=st.secrets["PROXY_URL"], https_url=st.secrets["PROXY_URL"])
    except Exception:
        pass
    return None


@st.cache_resource(show_spinner=False)
def get_client(api_key):
    return genai.Client(api_key=api_key)


def extract_video_id(url):
    url = (url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    host = urllib.parse.urlparse(url if "//" in url else "//" + url).netloc.lower()
    if "youtube.com" not in host and "youtu.be" not in host:
        return None
    m = re.search(r"(?:v=|youtu\.be/|/embed/|/shorts/|/live/|/v/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def fetch_title(video_id):
    try:
        watch = f"https://www.youtube.com/watch?v={video_id}"
        api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(watch, safe="")
        with urllib.request.urlopen(api, timeout=6) as r:
            return json.loads(r.read().decode("utf-8")).get("title") or "YouTube Video"
    except Exception:
        return "YouTube Video"


BLOCK_HELP = (" You can still continue: choose **Paste Transcript Manually** above and paste the transcript "
              "(YouTube: ⋯ More → Show transcript) or upload a .txt/.srt/.vtt file.")


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_transcript(video_id):
    """Return the best available transcript (manual first, then auto-generated)."""
    try:
        cfg = get_proxy_config()
        api = YouTubeTranscriptApi(proxy_config=cfg) if cfg else YouTubeTranscriptApi()
        listing = api.list(video_id)
        try:
            tr = listing.find_transcript(["en", "en-US", "en-GB", "ur"])
        except NoTranscriptFound:
            tr = next(iter(listing), None)
            if tr is None:
                raise AppError("No transcript is available for this video." + BLOCK_HELP, True)
        text = " ".join(snip.text for snip in tr.fetch())
        text = re.sub(r"\[[^\]]{0,30}\]", " ", text)  # remove [Music], [Applause]...
        text = re.sub(r"\s+", " ", text).strip()
        return {"text": text, "language": tr.language, "generated": tr.is_generated}
    except AppError:
        raise
    except TranscriptsDisabled:
        raise AppError("Subtitles are disabled for this video, so there is no transcript." + BLOCK_HELP, True)
    except NoTranscriptFound:
        raise AppError("No transcript was found for this video." + BLOCK_HELP, True)
    except VideoUnavailable:
        raise AppError("This video is unavailable (private, deleted or region-locked)." + BLOCK_HELP, True)
    except CouldNotRetrieveTranscript as e:
        name = type(e).__name__
        if any(k in name for k in ("Block", "PoToken", "RequestFailed")):
            raise AppError("🚫 YouTube is blocking transcript requests from this server (common on cloud hosting)."
                           + BLOCK_HELP, True)
        if "Age" in name:
            raise AppError("This video is age-restricted, so its transcript cannot be fetched." + BLOCK_HELP, True)
        raise AppError("The transcript could not be retrieved for this video." + BLOCK_HELP, True)
    except Exception as e:
        raise AppError(f"Unexpected problem while fetching the transcript ({type(e).__name__})." + BLOCK_HELP, True)


def clean_manual_transcript(text):
    """Strip timestamps / SRT / VTT markup from pasted or uploaded transcripts."""
    out = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if (not line or line.upper().startswith(("WEBVTT", "NOTE", "KIND:", "LANGUAGE:")) or "-->" in line
                or re.fullmatch(r"\d+", line) or re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?([.,]\d+)?", line)):
            continue
        out.append(re.sub(r"<[^>]+>", "", line))
    return re.sub(r"\s+", " ", " ".join(out)).strip()


# ----------------------------------------------------------------------------
# Gemini helpers
# ----------------------------------------------------------------------------
def ask_gemini(client, prompt, system, as_json=True, max_tokens=16384):
    model = get_model_name()
    kwargs = {}
    if model.startswith("gemini-2.5-flash"):  # skip hidden "thinking" tokens: faster, fewer truncated answers
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    cfg = types.GenerateContentConfig(
        system_instruction=system, temperature=0.3, max_output_tokens=max_tokens,
        response_mime_type="application/json" if as_json else None, **kwargs)
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
            if not resp.text:
                raise AppError("Gemini returned no content (the response may have been blocked). Please try again.")
            return resp.text
        except AppError:
            raise
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code in (429, 500, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if code in (400, 401, 403):
                raise AppError("Gemini rejected the request. Check that GEMINI_API_KEY is valid and has access.")
            if code == 404:
                raise AppError("The Gemini model was not found. Set a valid GEMINI_MODEL in secrets.")
            if code == 429:
                raise AppError("Gemini quota/rate limit reached. Please wait a minute and try again.")
            raise AppError("Gemini is temporarily unavailable. Please try again shortly.")
        except Exception as e:
            raise AppError(f"Could not reach Gemini ({type(e).__name__}). Please try again.")
    raise AppError("Gemini did not respond. Please try again.")


def parse_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise AppError("The AI response was not valid JSON.")


def call_json(client, prompt, system):
    last = None
    for _ in range(2):
        try:
            return parse_json(ask_gemini(client, prompt, system))
        except AppError as e:
            last = e
    raise AppError(f"{last} Please click Generate again.")


def s(x):
    return str(x).strip() if x is not None else ""


def as_list(x):
    return x if isinstance(x, list) else []


def build_system(difficulty, language):
    lang_rule = ("Write every text value in clear Urdu script (اردو). Keep JSON keys in English."
                 if language == "Urdu" else "Write every text value in English.")
    return ("You are StudyTube AI, a careful study assistant. Use ONLY the transcript you are given. "
            "Never invent facts, names, numbers, quotes or examples that are not in the transcript. "
            "If the transcript does not cover something, leave it out. The transcript may be auto-generated "
            f"and contain small recognition errors. Difficulty level: {difficulty}. {DIFFICULTY[difficulty]} "
            f"{lang_rule}")


SCHEMA1 = """Return ONE JSON object with exactly these keys:
{"summary": "150-250 word summary of the video",
 "key_points": ["8-12 important points"],
 "definitions": [{"term": "...", "definition": "..."}],
 "detailed_notes": "Markdown study notes using ## headings, bullet points and examples taken from the video",
 "qa": [{"question": "...", "answer": "..."}],
 "revision_sheet": ["12-15 very short bullet reminders"]}
Provide 8 items in qa. Only define terms the video actually explains or uses (use an empty list if none)."""

SCHEMA2 = """Return ONE JSON object with exactly these keys:
{"mcqs": [{"question": "...", "options": ["...", "...", "...", "..."], "answer_index": 0,
           "explanation": "why this answer is correct", "topic": "2-3 word topic"}],
 "flashcards": [{"front": "term or question", "back": "short answer"}]}
Create 12 MCQs, each with exactly 4 plain options (no A/B/C/D prefixes); answer_index is the 0-based index of
the correct option. Create 12 flashcards. Every question must be answerable from the transcript."""


def clean_mcqs(items):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        opts = [re.sub(r"^[A-Da-d][\).:\-]\s+", "", s(o)) for o in as_list(it.get("options")) if s(o)]
        try:
            idx = int(it.get("answer_index"))
        except (TypeError, ValueError):
            continue
        if len(opts) != 4 or not 0 <= idx < 4 or not s(it.get("question")):
            continue
        correct = opts[idx]
        random.shuffle(opts)  # avoids the AI favouring one answer position
        out.append({"question": s(it.get("question")), "options": opts, "answer": opts.index(correct),
                    "explanation": s(it.get("explanation")), "topic": s(it.get("topic")) or "General"})
    return out


def generate_material(client, transcript, title, difficulty, language):
    system = build_system(difficulty, language)
    head = f'Video title: {title}\n\nTRANSCRIPT:\n"""\n{transcript}\n"""\n\n'
    d = call_json(client, head + SCHEMA1, system)
    material = {
        "summary": s(d.get("summary")),
        "key_points": [s(k) for k in as_list(d.get("key_points")) if s(k)],
        "definitions": [{"term": s(x.get("term")), "definition": s(x.get("definition"))}
                        for x in as_list(d.get("definitions")) if isinstance(x, dict) and s(x.get("term"))],
        "notes": s(d.get("detailed_notes")),
        "qa": [{"question": s(x.get("question")), "answer": s(x.get("answer"))}
               for x in as_list(d.get("qa")) if isinstance(x, dict) and s(x.get("question"))],
        "revision": [s(r) for r in as_list(d.get("revision_sheet")) if s(r)],
    }
    if not material["summary"] and not material["notes"]:
        raise AppError("The AI returned incomplete study material. Please try again.")
    mcqs, cards = [], []
    for _ in range(2):  # second attempt only if we got fewer than 10 valid MCQs
        d2 = call_json(client, head + SCHEMA2, system)
        mcqs = clean_mcqs(as_list(d2.get("mcqs")))
        cards = [{"front": s(c.get("front")), "back": s(c.get("back"))}
                 for c in as_list(d2.get("flashcards")) if isinstance(c, dict) and s(c.get("front")) and s(c.get("back"))]
        if len(mcqs) >= 10:
            break
    if not mcqs:
        raise AppError("Could not create valid quiz questions from this transcript.")
    return material, mcqs, cards


def ask_about_video(client, transcript, history, question, language):
    lang = "Urdu (اردو)" if language == "Urdu" else "English"
    system = ("You answer questions about one YouTube video using ONLY its transcript. If the answer is not in the "
              "transcript, say clearly that the video does not cover it. Never invent video content. "
              f"Answer in {lang}, clearly and concisely.")
    past = "\n".join(f"{'User' if r == 'user' else 'Assistant'}: {t}" for r, t in history[-6:])
    prompt = f'TRANSCRIPT:\n"""\n{transcript}\n"""\n\nPrevious conversation:\n{past}\n\nQuestion: {question}'
    return ask_gemini(client, prompt, system, as_json=False, max_tokens=4096)


def ai_feedback(client, material, mcqs, result, language):
    wrong = [f"- Q: {q['question']} | Correct: {q['options'][q['answer']]} | Topic: {q['topic']}"
             for q, ok in zip(mcqs, result["correct"]) if not ok]
    lang = "Urdu (اردو)" if language == "Urdu" else "English"
    prompt = (f"A student scored {result['score']}/{len(mcqs)} on a quiz about this video summary:\n"
              f"{material['summary']}\n\nQuestions answered wrongly:\n" + ("\n".join(wrong) or "None") +
              f"\n\nWrite 4-5 short, encouraging, personalised improvement tips in {lang}, based only on these results.")
    return ask_gemini(client, prompt, "You are a supportive study coach. Do not invent video content.",
                      as_json=False, max_tokens=2048)


# ----------------------------------------------------------------------------
# PDF generation (with Urdu / RTL support)
# ----------------------------------------------------------------------------
def font_cmap(path):
    try:
        return set(TTFont("probe", path).face.charToGlyph.keys())
    except Exception:
        return set()


def missing_forms(cmap):
    exact = [c for c in NEED_FORMS if c not in cmap]
    return len(exact), len([c for c in exact if SUBST.get(c) not in cmap])


def download_arabic_font():
    for url in FONT_URLS:
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                data = r.read()
            if len(data) < 40_000:
                continue
            path = os.path.join(tempfile.gettempdir(), "studytube_NotoNaskhArabic.ttf")
            with open(path, "wb") as f:
                f.write(data)
            if missing_forms(font_cmap(path))[0] == 0:
                return path
        except Exception:
            continue
    return None


@st.cache_resource(show_spinner=False, ttl=3600)
def get_pdf_fonts(need_arabic):
    """Find usable fonts: a Latin font and (for Urdu) an Arabic-script font with full coverage."""
    files = []
    for base in FONT_DIRS:
        for root, _, names in os.walk(base):
            files += [(n.lower(), os.path.join(root, n)) for n in names if n.lower().endswith(".ttf")]
    files.sort()

    def ordered(prefs):
        seen = []
        for pref in prefs:
            seen += [p for n, p in files if n.startswith(pref) and p not in seen]
        return seen

    lat = next(iter(ordered(LAT_PREF)), None)
    lat_bold = None
    if lat:
        for cand in (lat.replace("-Regular", "-Bold"), lat.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
                     lat.replace("arial.ttf", "arialbd.ttf")):
            if cand != lat and os.path.exists(cand):
                lat_bold = cand
                break
    info = {"lat": lat, "lat_bold": lat_bold, "lat_cmap": font_cmap(lat) if lat else set(),
            "ar": None, "ar_cmap": set()}
    if need_arabic:
        best = None
        for path in ordered(AR_PREF):
            cm = font_cmap(path)
            if not cm:
                continue
            score = (missing_forms(cm)[1], missing_forms(cm)[0])  # fewer gaps first, exact matches preferred
            if best is None or score < best[0]:
                best = (score, path, cm)
            if score == (0, 0):
                break
        if best is None or best[0] != (0, 0):  # no perfect font installed -> try to download one
            dl = download_arabic_font()
            if dl:
                best = ((0, 0), dl, font_cmap(dl))
        if best is not None:
            info["ar"], info["ar_cmap"] = best[1], best[2]
            info["ar_gaps"] = best[0]
    return info


def strip_md(t):
    return re.sub(r"[*`]+", "", t).strip()


def bidi_display(t):
    try:
        return get_display(t, base_dir="R")
    except TypeError:
        return get_display(t)


def build_pdf(meta, mat, mcqs, cards):
    """Return (pdf_bytes, warning_or_None)."""
    lang = meta["language"]
    rtl = lang == "Urdu"
    L = LABELS[lang]
    fi = get_pdf_fonts(rtl)
    warning = None

    # Latin font (always) -------------------------------------------------
    if fi["lat"]:
        pdfmetrics.registerFont(TTFont("LatFont", fi["lat"]))
        lat = "LatFont"
        if fi["lat_bold"]:
            pdfmetrics.registerFont(TTFont("LatBold", fi["lat_bold"]))
            latb = "LatBold"
        else:
            latb = "LatFont"
    else:
        lat, latb = "Helvetica", "Helvetica-Bold"

    # Arabic-script font (Urdu) ------------------------------------------
    ar_ok = False
    if rtl:
        if not SHAPING_OK:
            warning = ("Urdu PDF needs the arabic-reshaper and python-bidi packages (see requirements.txt). "
                       "Urdu letters are shown as '?' in the PDF until they are installed.")
        elif not fi["ar"]:
            warning = ("No Arabic-script font was found on this server, so Urdu text appears as '?' in the PDF. "
                       "Add a packages.txt with fonts-noto-core (see README) and redeploy.")
        else:
            pdfmetrics.registerFont(TTFont("ArFont", fi["ar"]))
            ar_ok = True
            if any(fi.get("ar_gaps", (0, 0))):
                warning = ("The installed font lacks a few Urdu letter shapes, so look-alike letters are used for "
                           "ہ and ے. Install fonts-noto-core (see README) for perfect Urdu.")

    def usable(ch):
        o = ord(ch)
        if ch.isspace() or (ar_ok and o in fi["ar_cmap"]):
            return True
        if fi["lat"]:
            return o in fi["lat_cmap"]
        try:
            ch.encode("cp1252")
            return True
        except UnicodeEncodeError:
            return False

    def sanitize(t):
        out = []
        for ch in t:
            if usable(ch):
                out.append(ch)
            elif ar_ok and SUBST.get(ord(ch)) in fi["ar_cmap"]:
                out.append(chr(SUBST[ord(ch)]))
            else:
                out.append("?")
        return "".join(out)

    def runs(text):
        out = []
        for ch in text:
            f = "ArFont" if (ar_ok and AR_RE.match(ch)) else lat
            if out and out[-1][0] == f:
                out[-1][1] += ch
            else:
                out.append([f, ch])
        return out

    def shape_word(w):
        if AR_RE.search(w):
            w = arabic_reshaper.reshape(w).replace("\u200c", "").replace("\u200d", "")
        return w

    align = 2 if rtl else 0
    ink = colors.HexColor("#3b3355")

    def mk(name, font=lat, **kw):
        return ParagraphStyle(name, fontName=font, textColor=kw.pop("color", ink), alignment=align,
                              spaceAfter=0, **kw)

    title_s = mk("t", latb, fontSize=20, leading=28, color=colors.HexColor("#6c4fb3"))
    h1 = mk("h1", latb, fontSize=14, leading=22, color=colors.HexColor("#d6538b"))
    h2 = mk("h2", latb, fontSize=11.5, leading=19, color=colors.HexColor("#6c4fb3"))
    body = mk("b", fontSize=10.5, leading=18 if rtl else 16)
    small = mk("s", fontSize=8.5, leading=13, color=colors.HexColor("#8a82a6"))
    bul = mk("bl", fontSize=10.5, leading=18 if rtl else 16, leftIndent=0 if rtl else 12,
             firstLineIndent=0 if rtl else -10)

    story = []
    max_w = (A4[0] - 4 * cm) * 0.94

    def add(text, style, bullet=False, gap=5):
        text = strip_md(text)
        if bullet:
            text = "• " + text
        if rtl and ar_ok:
            words = [sanitize(shape_word(w)) for w in text.split()]
            cur = []
            lines = []
            for w in words:
                trial = " ".join(cur + [w])
                width = sum(pdfmetrics.stringWidth(r, f, style.fontSize) for f, r in runs(trial))
                if cur and width > max_w:
                    lines.append(" ".join(cur))
                    cur = [w]
                else:
                    cur.append(w)
            if cur:
                lines.append(" ".join(cur))
            for line in lines or [""]:
                markup = "".join(f'<font name="{f}">{xml_escape(r)}</font>' for f, r in runs(bidi_display(line)))
                story.append(Paragraph(markup, style))
        else:
            story.append(Paragraph(xml_escape(sanitize(text)).replace("\n", "<br/>"), style))
        story.append(Spacer(1, gap))

    add(f"StudyTube AI - {meta['title']}", title_s, gap=3)
    add(f"{meta['url'] or 'Manual transcript'}  |  {meta['difficulty']}  |  {lang}", small, gap=12)
    add(L[0], h1)
    add(mat["summary"], body, gap=10)
    add(L[1], h1)
    for k in mat["key_points"]:
        add(k, bul, bullet=True)
    if mat["definitions"]:
        add(L[2], h1)
        for d in mat["definitions"]:
            add(f"{d['term']}: {d['definition']}", bul, bullet=True)
    add(L[3], h1)
    for line in mat["notes"].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            add(line.lstrip("# "), h2, gap=3)
        elif line.startswith(("- ", "* ", "• ")):
            add(line[2:], bul, bullet=True, gap=3)
        else:
            add(line, body, gap=4)
    add(L[4], h1)
    for i, qa in enumerate(mat["qa"], 1):
        add(f"{i}. {qa['question']}", h2, gap=2)
        add(qa["answer"], body, gap=7)
    add(L[5], h1)
    for r in mat["revision"]:
        add(r, bul, bullet=True, gap=3)
    add(L[6], h1)
    for i, q in enumerate(mcqs, 1):
        add(f"{i}. {q['question']}", h2, gap=2)
        for j, o in enumerate(q["options"]):
            add(f"{'ABCD'[j]}. {o}", body, gap=2)
        add(f"{L[7]}: {'ABCD'[q['answer']]}. {q['options'][q['answer']]}", body, gap=2)
        if q["explanation"]:
            add(f"{L[9]}: {q['explanation']}", small, gap=7)
    add(L[8], h1)
    for i, c in enumerate(cards, 1):
        add(f"{i}. {c['front']}", h2, gap=2)
        add(c["back"], body, gap=6)
    add("Generated by StudyTube AI from the video transcript using Gemini. Always verify important facts.", small)

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                      bottomMargin=2 * cm, title="StudyTube AI notes").build(story)
    return buf.getvalue(), warning


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------
def card(text, color=""):
    safe = html.escape(text).replace("\n", "<br>")
    st.markdown(f'<div class="card {color}">{safe}</div>', unsafe_allow_html=True)


MODE_URL = "🔗 YouTube URL (automatic captions)"
MODE_MANUAL = "📋 Paste Transcript Manually"


def switch_to_manual():
    st.session_state.input_mode = MODE_MANUAL


def reset_session():
    st.session_state.update(quiz_ver=st.session_state.get("quiz_ver", 0) + 1, quiz_result=None, fc_idx=0,
                            fc_show=False, chat=[], feedback=None, pdf=None, pdf_warning=None)


def fc_move(step, total):
    st.session_state.fc_idx = (st.session_state.fc_idx + step) % total
    st.session_state.fc_show = False


def fc_flip():
    st.session_state.fc_show = not st.session_state.fc_show


def retake_quiz():
    st.session_state.quiz_ver += 1
    st.session_state.quiz_result = None
    st.session_state.feedback = None


def process(client, source, url, manual_text, difficulty, language):
    """Fetch (or take) the transcript, call Gemini and store everything in session state."""
    st.session_state.error = None
    st.session_state.data = None
    failed = False
    bar = st.progress(0, text="Preparing transcript...")
    try:
        video_id = extract_video_id(url)
        if source == "youtube":
            if not video_id:
                raise AppError("Please paste a valid YouTube video link (youtube.com or youtu.be).")
            tr = fetch_transcript(video_id)
            text, tr_lang, generated = tr["text"], tr["language"], tr["generated"]
        else:
            text, tr_lang, generated = manual_text, "provided by you", False
        if len(text) < MIN_CHARS:
            raise AppError("This transcript is too short to create meaningful study material."
                           + (BLOCK_HELP if source == "youtube" else ""), source == "youtube")
        truncated = len(text) > MAX_CHARS
        pct = int(MAX_CHARS * 100 / len(text)) if truncated else 100
        text = text[:MAX_CHARS]
        bar.progress(25, text="Reading video details...")
        title = fetch_title(video_id) if video_id else "Pasted transcript"
        bar.progress(40, text="Gemini is creating your study material (about 30-60 seconds)...")
        material, mcqs, cards = generate_material(client, text, title, difficulty, language)
        bar.progress(100, text="Done!")
        reset_session()
        st.session_state.show_fallback = False
        st.session_state.data = {
            "meta": {"video_id": video_id, "url": url.strip(), "title": title, "difficulty": difficulty,
                     "language": language, "tr_lang": tr_lang, "generated": generated, "source": source,
                     "truncated": truncated, "pct": pct, "words": len(text.split())},
            "transcript": text, "material": material, "mcqs": mcqs, "cards": cards,
        }
    except AppError as e:
        st.session_state.error = str(e)
        st.session_state.show_fallback = st.session_state.show_fallback or e.fallback
        failed = True
    finally:
        bar.empty()
    if failed:
        st.rerun()  # redraw so the error and the fallback section appear above


# ----------------------------------------------------------------------------
# Main app
# ----------------------------------------------------------------------------
st.markdown(CSS, unsafe_allow_html=True)
for key, default in {"data": None, "quiz_ver": 0, "quiz_result": None, "fc_idx": 0, "fc_show": False,
                     "chat": [], "feedback": None, "pdf": None, "pdf_warning": None, "error": None,
                     "show_fallback": False, "input_mode": MODE_URL}.items():
    st.session_state.setdefault(key, default)

st.markdown('<div class="hero"><h1>StudyTube AI ✨</h1>'
            '<p>Turn YouTube videos into your personal learning experience.</p></div>', unsafe_allow_html=True)

api_key = get_api_key()
if not api_key:
    st.error("🔑 Gemini API key not found.")
    st.markdown("Add it in **Streamlit Secrets** as `GEMINI_API_KEY` (locally: `.streamlit/secrets.toml`; "
                "on Streamlit Cloud: *App settings → Secrets*). See the README for details.")
    st.stop()
client = get_client(api_key)

mode = st.radio("How do you want to provide the video?", [MODE_URL, MODE_MANUAL], key="input_mode",
                horizontal=True)
manual, upload = "", None
if mode == MODE_URL:
    url = st.text_input("🔗 YouTube video URL", placeholder="https://www.youtube.com/watch?v=...", key="url_input")
else:
    url = st.text_input("🔗 YouTube video URL (optional - only used for the title and thumbnail)",
                        placeholder="https://www.youtube.com/watch?v=...", key="url_optional")
    st.caption("On YouTube open the video, click ⋯ More → Show transcript, copy the text and paste it below. "
               "Timestamps are removed automatically.")
    manual = st.text_area("📋 Paste your transcript here", height=320, key="manual_text",
                          placeholder="Paste the full video transcript here...")
    upload = st.file_uploader("…or upload a transcript file (.txt, .srt, .vtt)", type=["txt", "srt", "vtt"])

c1, c2, c3 = st.columns([1, 1, 1])
difficulty = c1.selectbox("🎚️ Difficulty", list(DIFFICULTY))
language = c2.selectbox("🌍 Language", ["English", "Urdu"])
c3.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
go = c3.button("✨ Generate Study Material")

err_slot = st.empty()
switch_slot = st.empty()
if st.session_state.error:
    err_slot.error(f"⚠️ {st.session_state.error}")
    if st.session_state.show_fallback and mode == MODE_URL:
        with switch_slot:
            st.button("📋 Switch to Paste Transcript Manually", on_click=switch_to_manual)

if go:
    err_slot.empty()
    switch_slot.empty()
    if mode == MODE_URL:
        process(client, "youtube", url, "", difficulty, language)
    else:
        raw = upload.getvalue().decode("utf-8-sig", errors="replace") if upload else manual
        if not (raw or "").strip():
            st.session_state.error = "Please paste your transcript (or upload a transcript file) before generating."
            st.rerun()
        cleaned = clean_manual_transcript(raw)
        if not cleaned:
            st.session_state.error = ("No readable text was found. Make sure you pasted the spoken words of the "
                                      "transcript, not only timestamps.")
            st.rerun()
        process(client, "manual", url, cleaned, difficulty, language)

data = st.session_state.data
if not data:
    st.info("Choose how to provide the video, pick a level and language, then click **Generate Study Material**. "
            "Use **YouTube URL** for automatic captions, or **Paste Transcript Manually** if the video has no "
            "accessible captions.")
    st.stop()

meta, mat, mcqs, cards = data["meta"], data["material"], data["mcqs"], data["cards"]
if meta["language"] == "Urdu":
    st.markdown(RTL_CSS, unsafe_allow_html=True)

st.markdown("---")
info_col = st
if meta["video_id"]:
    t1, info_col = st.columns([1, 2])
    t1.image(f"https://img.youtube.com/vi/{meta['video_id']}/hqdefault.jpg")
with (info_col if info_col is not st else st.container()):
    st.subheader(meta["title"])
    kind = "provided manually" if meta["source"] == "manual" else (
        f"{meta['tr_lang']}, {'auto-generated' if meta['generated'] else 'manual captions'}")
    st.caption(f"Transcript: {kind} · {meta['words']:,} words analysed")
    m1, m2, m3 = st.columns(3)
    m1.metric("Level", meta["difficulty"])
    m2.metric("Language", meta["language"])
    m3.metric("MCQs", len(mcqs))
if meta["truncated"]:
    st.warning(f"This is a long transcript, so only the first ~{meta['pct']}% was analysed.")
if meta["generated"]:
    st.caption("ℹ️ The transcript is auto-generated, so names or technical terms may contain small errors.")

tabs = st.tabs(["📝 Summary", "⭐ Key Points", "📖 Notes", "❓ Q&A", "🧠 MCQs", "🎯 Quiz", "🃏 Flashcards",
                "💬 Ask AI", "⚡ Revision", "📊 Report", "📄 PDF"])

with tabs[0]:
    card(mat["summary"])

with tabs[1]:
    st.markdown("#### Important points")
    for p in mat["key_points"]:
        card(p, "pink")
    st.markdown("#### Definitions")
    if mat["definitions"]:
        for d in mat["definitions"]:
            st.markdown(f'<div class="card peach"><b>{html.escape(d["term"])}</b><br>{html.escape(d["definition"])}</div>',
                        unsafe_allow_html=True)
    else:
        st.info("The video does not clearly define specific terms.")

with tabs[2]:
    with st.container(border=True):
        st.markdown(mat["notes"])

with tabs[3]:
    for i, qa in enumerate(mat["qa"], 1):
        st.markdown(f'<div class="card"><b>Q{i}. {html.escape(qa["question"])}</b><br>{html.escape(qa["answer"])}</div>',
                    unsafe_allow_html=True)

with tabs[4]:
    st.caption(f"{len(mcqs)} multiple-choice questions. Open a question to reveal the answer.")
    for i, q in enumerate(mcqs, 1):
        card(f"Q{i}. {q['question']}\n" + "\n".join(f"{'ABCD'[j]}. {o}" for j, o in enumerate(q["options"])), "pink")
        with st.expander("Show answer"):
            st.markdown(f"**Answer: {'ABCD'[q['answer']]}. {q['options'][q['answer']]}**")
            if q["explanation"]:
                st.write(q["explanation"])

with tabs[5]:
    ver, result = st.session_state.quiz_ver, st.session_state.quiz_result
    if result is None:
        with st.form(f"quiz_form_{ver}"):
            st.markdown("#### Test yourself")
            picks = []
            for i, q in enumerate(mcqs):
                picks.append(st.radio(f"Q{i + 1}. {q['question']}", range(4), index=None, key=f"q{ver}_{i}",
                                      format_func=lambda j, q=q: f"{'ABCD'[j]}. {q['options'][j]}"))
            submitted = st.form_submit_button("✅ Submit Quiz")
        if submitted:
            correct = [p == q["answer"] for p, q in zip(picks, mcqs)]
            st.session_state.quiz_result = {"answers": picks, "correct": correct, "score": sum(correct)}
            st.rerun()
    else:
        score, total = result["score"], len(mcqs)
        pct = round(score * 100 / total)
        st.markdown(f"## 🎉 Your score: {score} / {total} ({pct}%)")
        st.progress(score / total)
        st.write("Amazing work!" if pct >= 80 else "Good effort - review the notes and try again!" if pct >= 50
                 else "Keep going - revisit the notes and flashcards, then retake the quiz.")
        for i, (q, pick, ok) in enumerate(zip(mcqs, result["answers"], result["correct"]), 1):
            with st.expander(f"{'✅' if ok else '❌'} Q{i}. {q['question']}"):
                st.write("Your answer: " + (f"{'ABCD'[pick]}. {q['options'][pick]}" if pick is not None else "Not answered"))
                st.write(f"Correct answer: {'ABCD'[q['answer']]}. {q['options'][q['answer']]}")
                if q["explanation"]:
                    st.info(q["explanation"])
        st.button("🔄 Retake Quiz", on_click=retake_quiz)

with tabs[6]:
    if not cards:
        st.info("No flashcards were generated.")
    else:
        n = len(cards)
        i = min(st.session_state.fc_idx, n - 1)
        side = "back" if st.session_state.fc_show else ""
        text = cards[i]["back"] if st.session_state.fc_show else cards[i]["front"]
        st.caption(f"Card {i + 1} of {n} · {'Answer' if side else 'Question'}")
        st.markdown(f'<div class="flash {side}">{html.escape(text)}</div>', unsafe_allow_html=True)
        b1, b2, b3 = st.columns(3)
        b1.button("⬅️ Previous", on_click=fc_move, args=(-1, n))
        b2.button("🔁 Flip card", on_click=fc_flip)
        b3.button("Next ➡️", on_click=fc_move, args=(1, n))

with tabs[7]:
    st.markdown("Ask anything about this video. Answers come only from its transcript.")
    with st.form("ask_form", clear_on_submit=True):
        question = st.text_input("Your question", placeholder="e.g. What is the main idea of the video?")
        ask = st.form_submit_button("💬 Ask")
    if ask and question.strip():
        try:
            with st.spinner("Thinking..."):
                ans = ask_about_video(client, data["transcript"], st.session_state.chat, question.strip(),
                                      meta["language"])
            st.session_state.chat += [("user", question.strip()), ("ai", ans)]
        except AppError as e:
            st.error(f"⚠️ {e}")
    for role, msg in st.session_state.chat:
        with st.chat_message("user" if role == "user" else "assistant"):
            st.markdown(msg)
    if st.session_state.chat and st.button("🧹 Clear conversation"):
        st.session_state.chat = []
        st.rerun()

with tabs[8]:
    st.markdown("#### Quick revision sheet")
    for r in mat["revision"]:
        card("⚡ " + r, "peach")

with tabs[9]:
    result = st.session_state.quiz_result
    if result is None:
        st.info("Take the quiz in the **🎯 Quiz** tab first - your Learning Performance Report will appear here.")
    else:
        total, score = len(mcqs), result["score"]
        pct = round(score * 100 / total)
        topics = {}
        for q, ok in zip(mcqs, result["correct"]):
            t = topics.setdefault(q["topic"], [0, 0])
            t[0] += int(ok)
            t[1] += 1
        strengths = [t for t, (c, n) in topics.items() if c / n >= 0.7]
        weaknesses = [t for t, (c, n) in topics.items() if c / n < 0.7]
        r1, r2, r3 = st.columns(3)
        r1.metric("Quiz score", f"{score}/{total}")
        r2.metric("Accuracy", f"{pct}%")
        r3.metric("Level", "Excellent" if pct >= 80 else "Good progress" if pct >= 60 else "Needs practice")
        st.markdown("#### 💪 Strengths")
        if strengths:
            for t in strengths:
                card(f"{t} ({topics[t][0]}/{topics[t][1]} correct)")
        else:
            card("Keep practising - strengths will appear as you improve.")
        st.markdown("#### 🎯 Weaknesses")
        if weaknesses:
            for t in weaknesses:
                card(f"{t} ({topics[t][0]}/{topics[t][1]} correct)", "pink")
        else:
            card("None - you answered every topic well!", "pink")
        st.markdown("#### 🚀 Improvement suggestions")
        tips = [f"Re-read the study notes about '{t}' and explain it in your own words." for t in weaknesses]
        if None in result["answers"]:
            tips.append("Try to answer every question - an educated guess is better than skipping.")
        tips.append("Watch the video again at a slower pace and use the flashcards daily." if pct < 60
                    else "Review the Quick Revision sheet, then retake the quiz to beat your score." if pct < 90
                    else "Great mastery! Try generating material at the next difficulty level.")
        for tip in tips:
            card(tip, "peach")
        if st.button("🤖 Get personalised AI feedback"):
            try:
                with st.spinner("Preparing feedback..."):
                    st.session_state.feedback = ai_feedback(client, mat, mcqs, result, meta["language"])
            except AppError as e:
                st.error(f"⚠️ {e}")
        if st.session_state.feedback:
            st.markdown(st.session_state.feedback)

with tabs[10]:
    st.markdown("Download all your study material as a PDF.")
    if st.button("📄 Create PDF"):
        try:
            with st.spinner("Building PDF..."):
                st.session_state.pdf, st.session_state.pdf_warning = build_pdf(meta, mat, mcqs, cards)
        except Exception as e:
            st.error(f"Could not build the PDF ({type(e).__name__}: {e})")
    if st.session_state.pdf_warning:
        st.warning(st.session_state.pdf_warning)
    if st.session_state.pdf:
        st.download_button("⬇️ Download PDF Notes", st.session_state.pdf,
                           file_name=f"studytube_{meta['video_id'] or 'notes'}.pdf", mime="application/pdf")
