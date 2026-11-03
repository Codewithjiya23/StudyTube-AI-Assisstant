# StudyTube AI ✨

**Turn YouTube videos into your personal learning experience.**

Paste a YouTube link, pick a difficulty and language (English or Urdu), and StudyTube AI builds a full study kit from the video's transcript using Google Gemini.

## Features

- Video summary, key points and definitions
- Detailed study notes and Q&A
- 10+ multiple-choice questions (with answers and explanations)
- Interactive quiz with score and per-question feedback
- Flashcards (flip, next, previous)
- "Ask AI" chat that answers only from the video transcript
- Quick revision sheet
- Downloadable PDF notes (English and Urdu)
- Learning Performance Report (score, strengths, weaknesses, improvement tips, optional AI feedback)

The AI is instructed to use **only the transcript** and never invent video content. Very long transcripts are truncated and you are told how much was analysed.

## Files

```
app.py            # the Streamlit app
requirements.txt  # Python dependencies
packages.txt      # system packages (fonts) installed by Streamlit Cloud - needed for Urdu PDFs
README.md         # this file
```

## Run locally

1. Install Python 3.10 or newer.
2. (Optional) create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   ```
3. Install dependencies: `pip install -r requirements.txt`
4. Get a free Gemini API key at <https://aistudio.google.com/apikey>.
5. Create `.streamlit/secrets.toml` next to `app.py`:
   ```toml
   GEMINI_API_KEY = "your-api-key-here"
   # Optional - normally leave this out, the app picks a working Gemini model automatically:
   # GEMINI_MODEL = "gemini-3.6-flash"
   ```
6. Start the app: `streamlit run app.py`

> ⚠️ Never commit `.streamlit/secrets.toml`. Add it to your `.gitignore`.

On Linux, install a font with full Urdu support for Urdu PDFs: `sudo apt install fonts-noto-core`. Windows and macOS usually work without extra steps.

## Deploy: GitHub + Streamlit Community Cloud

1. Create a GitHub repository (e.g. `studytube-ai`) and push **all four files**:
   ```bash
   git init
   git add app.py requirements.txt packages.txt README.md
   git commit -m "StudyTube AI"
   git branch -M main
   git remote add origin https://github.com/<your-username>/studytube-ai.git
   git push -u origin main
   ```
2. Go to <https://share.streamlit.io>, sign in with GitHub and click **Create app**.
3. Choose your repository, branch `main`, main file `app.py`.
4. Open **Advanced settings → Secrets** and paste:
   ```toml
   GEMINI_API_KEY = "your-api-key-here"
   ```
5. Click **Deploy**.

`packages.txt` must sit in the repository root next to `requirements.txt`; Streamlit Cloud installs every package listed there with `apt`. If you change it after deploying, reboot the app (*Manage app → ⋮ → Reboot*).

## Urdu PDF: how it works

Urdu needs three things, and the app checks them for you:

1. **`arabic-reshaper` + `python-bidi`** (in `requirements.txt`) join the letters and put the text right-to-left.
2. **An Arabic-script font.** `packages.txt` installs `fonts-noto-core` (includes *Noto Naskh Arabic*) and `fonts-dejavu-core`.
3. **A font that has every Urdu letter shape.** The app inspects each installed font and picks the best one in this order: Noto Naskh Arabic → Amiri → Noto Sans Arabic → Scheherazade → DejaVu Sans. English words inside Urdu text use a separate Latin font.

Notes:
- DejaVu Sans lacks two common Urdu shapes (ہ and ے). If it is the only font available, the app uses look-alike letters and **shows a warning** in the PDF tab. Noto Naskh Arabic has all shapes.
- As a last resort the app tries to download Noto Naskh Arabic at runtime (needs internet access on the server).
- If no suitable font can be found, the PDF tab explains what is missing instead of failing silently.

## When YouTube blocks transcript requests

YouTube often blocks requests coming from cloud servers (including Streamlit Cloud). The app never crashes in that case. It shows a clear message and offers these options:

1. **Paste or upload the transcript** by choosing **Paste Transcript Manually** at the top of the app (works everywhere). On YouTube: open the video → **⋯ More → Show transcript** → copy the text. `.txt`, `.srt` and `.vtt` files are accepted; timestamps are removed automatically.
2. **Use a proxy** (optional, most reliable for a public app). Add one of these to your secrets:
   ```toml
   PROXY_URL = "http://username:password@proxy-host:port"
   # or a Webshare residential proxy:
   # WEBSHARE_USER = "..."
   # WEBSHARE_PASS = "..."
   ```
3. **Run the app locally** (home internet connections are rarely blocked).

## Troubleshooting

| Problem | What to do |
|---|---|
| "Subtitles are disabled" / "No transcript" | The video has no captions. Use the paste/upload fallback with a transcript from another source. |
| "YouTube is blocking transcript requests" | See the section above. |
| "No available Gemini model was found" / "model not found" | Google retires old models (e.g. `gemini-2.5-flash` shut down in Oct 2026). Remove `GEMINI_MODEL` from Secrets so the app auto-selects, or set it to a current name from <https://ai.google.dev/gemini-api/docs/models>. |
| "Gemini rejected the request" | Check `GEMINI_API_KEY` in Secrets. |
| "Quota / rate limit reached" | Wait a minute, or use a key with higher limits. |
| Urdu letters show as `?` in the PDF | Make sure `packages.txt` is in the repo root, then reboot the app. |
| Odd words in notes | Auto-generated captions can contain errors; verify important facts. |

## Tech stack

Python · Streamlit · Google Gen AI SDK (`google-genai`) · `youtube-transcript-api` · ReportLab · arabic-reshaper · python-bidi

Made with 💜 for curious learners.
