# Troubleshooting

Real problems hit during the first setup, and how to fix them. If yours isn't here, copy the **last 10 lines of red
text** from the terminal or the app; errors usually name exactly what is missing.

## Installing

### `Failed building wheel for pyyaml` / `Microsoft Visual C++ 14.0 or greater is required`
**Why:** your Python is newer than the add-ons (e.g. Python 3.15). pip can't find a ready-made version (a "wheel"), so
it tries to build one from source, which needs Microsoft's C++ compiler (a multi-gigabyte install you don't want).
**Fix:** install **Python 3.12** next to your current one (both can live on one laptop), then rebuild the toolbox:
```
rmdir /s /q .venv
py -3.12 -m venv .venv
.venv\Scripts\activate
python --version          (should say 3.12.x)
pip install -r requirements.txt
```
`py` is the Windows Python launcher; `-3.12` picks that version.

### `'python' is not recognized as an internal or external command`
**Why:** Python wasn't added to PATH during install. **Fix:** run the installer again → **Modify** → tick
**Add Python to environment variables** (or use `py -3.12` instead of `python`).

### `running scripts is disabled on this system` (when activating)
**Why:** PowerShell blocks the activate script. **Fix:** use **Command Prompt**: click the folder's address bar, type
`cmd`, press Enter. (Or, in PowerShell once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.)

### Install is very slow, files "in use", random errors
**Why:** the folder is inside Google Drive / OneDrive / Dropbox, which tries to sync thousands of files in `.venv`.
**Fix:** move the folder to a normal local folder (e.g. `C:\erpsim-copilot`), delete `.venv`, redo steps 4–5.

### `No module named ...`
**Why:** the toolbox isn't active, or the add-ons weren't installed. **Fix:** check the line starts with `(.venv)`;
if not, run `.venv\Scripts\activate`, then `pip install -r requirements.txt`.

## Connecting to the game

### The app can't read the game data (401 / 403 / login errors)
Check the **OData URL** (`https://<server>/odata/<client>`, the client number comes from your instructor and changes
between practice games and tournaments), your **ERPsim user** and **password**. Try the URL in a browser: it should ask
for the same login.

### `could not convert string to float` or similar number errors
Some feeds send numbers as text (sometimes empty). The co-pilot converts them automatically since v0.1. If you still see
it, update to the latest version and send the error.

### Numbers look stale
Look at the live bar above the day heading. "new data 4 s ago" means it's current: the game only creates new data
once per game day (about 45 seconds in a tournament), so nothing can be fresher. The co-pilot checks for a new day
every 5 seconds (one tiny request) and then downloads only that day's rows.
- **Red dot, "Can't reach the game server":** Wi-Fi dropped or the server is busy. It keeps showing the last data and
  retries by itself. If it stays red, check the OData URL and your login.
- **Still looks wrong:** click **Reload everything** in the sidebar to download the whole game again.

### No sound on urgent alerts
Browsers only allow sound after you've interacted with the page: click anywhere on it once. Check that
**Sound on urgent alerts** is on in the sidebar and your laptop isn't muted. Pop-ups appear either way.

### No AI brief line
The one-line AI brief needs the Gemini key (the toggle is greyed out without it). On the free tier, if the minute's
limit is used up, that day shows the rules-based line instead ("rules" label) and the next day tries again.

## The Ask tab (AI)

### "To chat, add your free key to .env…"
No key found. Open `.env` (`notepad .env`), make sure there's a line `GEMINI_API_KEY=your-key` with **no spaces or
quotes**, save, and restart the app (Ctrl + C, then `streamlit run app.py`).

### "The free AI limit for this minute is used up"
The free Gemini tier allows only a few requests per minute and each question uses 2–4. Wait a minute. During a game,
use the tabs (no AI needed) and save chat questions for breaks.

### AI error mentioning the model ("not found", "not supported")
Google renames models often. In AI Studio, pick a model marked **free** (Flash or Flash-Lite), then add
`COPILOT_MODEL=that-model-name` to `.env` and restart.

### The answer ignores my memory files
Memory loads when a chat starts: **refresh the browser page** after editing. Only `.md` / `.txt` files in
`memory\private\` are read, up to ~40,000 characters in total.

## Advice looks wrong

- **Replay prices:** a saved workbook only has the end-of-game price list, so replay works from the prices you actually
  sold at. Live mode uses the real current price list.
- **"Costs from: config.yaml":** the feed has no supplier-price table; set `cost_per_box` for each product in `config.yaml`.
- **Wrong products treated as active:** set `plan:` and `mix:` for your products in `config.yaml`.
- **Thresholds feel too jumpy or too slow:** change them in `config.yaml` under `rules:` (e.g. `slow_share`,
  `raise_at`, `fg_high`, `undercut_gap`). Replay a past game to see the effect.
