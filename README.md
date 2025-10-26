# subsflow

**subsflow** is an end‑to‑end command‑line tool for cleaning, preparing, translating, and rebuilding subtitles.  
It was designed for workflows where you:

1. Extract **auto‑generated YouTube subtitles** (e.g., using `yt‑dlp`),
2. Clean and deduplicate them for readability,
3. Convert them into a simple **TSV format** for LLM translation,
4. Rejoin the translations into a proper `.srt` file with timestamps intact.

---

## ✨ Features

- **Clean auto‑generated captions** — removes rolling‑window repetition and keeps timestamps unchanged.  
- **Prepare clean text for LLM translation** — output `.tsv` or plain text without timestamps.  
- **Rejoin translations** — merges translated lines back into timestamped `.srt` files.  
- **Validate & auto‑fix** TSV files before rejoining (detects extra tabs, malformed lines).  
- Supports both **2‑column** (`id + text`) and **3‑column** (`id + original + translation`) TSV formats.  
- Works on very large subtitle files (tens of thousands of captions).  
- 100 % offline and open‑source – safe for use with API‑translated content.

---

## 🚀 Installation

Clone this repository and make the script executable:

```bash
git clone https://github.com/<your‑username>/subsflow.git
cd subsflow
chmod +x subsflow.py
```

(Optionally, install globally:)

```bash
pip install .
# then you can run `subsflow` directly
```

---

## Usage Overview

### 1️⃣ Clean auto‑generated YouTube captions

Removes overlapping text caused by rolling‑window updates.

```bash
python3 subsflow.py clean original.srt original_clean.srt
```

→ Outputs a clean, readable SRT ready for translation.

---

### 2️⃣ Prepare subtitles for LLM translation

Converts the cleaned SRT into TSV format (`id + text`) so each caption fits on one line.

```bash
python3 subsflow.py prep original_clean.srt for_translation.tsv
```

Produces:

```
1   Hello everyone!
2   Welcome back to the channel.
3   ♪
```

(`\t` denotes a tab.)

Use this file as your LLM input — ask your model to preserve IDs and TSV structure.

---

### 3️⃣ Rejoin translations with their original timestamps

After translating to French (or another language) and saving your file as `French_translation.tsv`:

```bash
python3 subsflow.py rejoin original_clean.srt French_translation.tsv final_French.srt
```

✅ Creates `final_French.srt` — perfectly time‑aligned, with your translated text.

By default the program:
- Checks that every TSV line has the same ID and count as the SRT,
- Auto‑fixes rows that have extra tabs (if you add `--tsv-auto-fix`),
- Preserves timestamps and numbering.

---

### 4️⃣ Validate a TSV (optional)

Check a TSV before rejoining:

```bash
python3 subsflow.py validate-tsv French_translation.tsv --expect-columns 3
```

It reports line counts, column counts, and invalid IDs.

---

## ⚙️ Command Summary

| Command | Purpose |
|----------|----------|
| `clean` | Clean and deduplicate raw YouTube auto‑subs |
| `prep` | Strip timestamps and export TSV for translation |
| `rejoin` | Merge translated text back with timestamps |
| `validate-tsv` | Validate or inspect TSV structure |

---

## Typical Workflow Example

### 1️⃣ Download captions with `yt‑dlp`

There are two common ways to fetch subtitles from YouTube before cleaning ⤵︎  

**Option A — prefer uploaded (human‑written) subs:**
```bash
yt-dlp --write-auto-subs --sub-lang en -skip-download --convert-subs srt <VIDEO_URL>
```
↳ Downloads available subtitles (manual if present, auto otherwise).

**Option B — auto‑subs only, no video:**
```bash
yt-dlp --write-auto-subs --no-write-subs --sub-lang en --skip-download --convert-subs srt <VIDEO_URL>
```
↳ Downloads **only the auto‑generated English captions**, no video file.

---

### 2️⃣ Use `subsflow`

```bash
# 1. Clean auto-captions (optional)
python3 subsflow.py clean original.srt original_clean.srt

# 2. Prepare for translation
python3 subsflow.py prep original_clean.srt for_translation.tsv

# 3. Translate with AI (GPT, Claude, Gemini, etc.)
# → Make sure IDs stay unchanged and TSV structure stays intact.

# 4. Rejoin timestamps and translations
python3 subsflow.py rejoin original_clean.srt French_translation.tsv final_French.srt

# 5. Validate (optional)
python3 subsflow.py validate-tsv French_translation.tsv --expect-columns 3
```

---

## 🛠️ Options Cheat Sheet

| Flag | Meaning |
|------|----------|
| `--numbering` | `original` to keep IDs, `renumber` to rewrite 1…N |
| `--id-source` | Use original or sequential IDs in `prep` |
| `--tsv-columns` | 2 = ID + text (typical), 3 = add empty translation column |
| `--align` | In `rejoin`, align translations by `id` or `seq` order |
| `--strict / --no-strict` | Enforce or skip strict ID/count matching |
| `--tsv-auto-fix` | Automatically merge extra tab columns during `rejoin` |
| `--tsv-fixed-out` | Save a corrected TSV copy before rejoining |

---

## 🧠 Design Goals

- **Deterministic:** inputs → clean outputs with no lost captions.  
- **Transparent:** every caption number traceable end‑to‑end.  
- **Compatible:** produces normal `.srt` files playable anywhere.  
- **Extensible:** easy to add new subcommands (`split`, `merge-gaps`, etc.).

---

## 💬 Example Output

```srt
217
00:09:44,250 --> 00:09:47,000
Bonjour à tous et bienvenue à nouveau !

218
00:09:47,200 --> 00:09:49,500
Aujourd’hui, nous allons examiner quelque chose d’intéressant.
```

---

## 🪪 License

MIT License — free for personal and commercial use.  
Contributions welcome!

---

## 🙌 Acknowledgments

Built as part of the **YouTube Subtitle Cleaner & Translator Workflow** project —  
an open pipeline to turn messy captions into professional multilingual subtitles.

---

### Author
**Raphaël Duperret**  

---
