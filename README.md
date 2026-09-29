# Python Tools

A small collection of Python command-line tools.
- **Drip Writer**: types out text you paste into it, into any window, with randomized pauses between keystrokes.
- **AI Solver**: reads questions off your screen with an AI model and fills in the answers for you.

All commands below are typed into the command line:
- **Windows:** press the Start button, type `Terminal`, and open it. (On older Windows, open `PowerShell` instead.)
- **Mac:** press `Cmd + Space`, type `Terminal`, and open it.

Copy and paste each command **one at a time**, pressing Enter after each one.

> Windows and Mac use different commands. Only follow the steps for your system.

---

## Step 1: Install Python

Download and run the installer from **[python.org/downloads](https://www.python.org/downloads/)** (it works for both Windows and Mac). Use the default options.

**Mac only:** when the installer finishes, a Finder window opens. Double-click **`Install Certificates.command`** in it. (You can also find it later in **Applications → Python 3.x**.) This prevents connection errors later.

Then **close Terminal and open a new one**, and check that it worked.

**Windows:**
```
py --version
```

**Mac:**
```
python3 --version
```

If it shows **Python 3.10 or newer**, you're ready. Open the tool you want below and continue with Step 2.

> **Mac:** if it shows an older version like `Python 3.9.6`, that's the old Python built into macOS. Quit Terminal completely (`Cmd + Q`), open it again, and check again.

---

<details>
<summary><strong>Step 2: Drip Writer</strong> (click to open)</summary>

### 2.1 Download it
1. On this repo's GitHub page, click the **Code** button, then **Download ZIP**, or use https://u2l.ai/6ztWrn.
2. Unzip it. Inside the unzipped folder, find the **`dripwriter`** folder and move it into your **Downloads** folder.

### 2.2 Install its dependencies (one time only)

**Windows:**
```
py -m pip install keyboard
```

**Mac:**
```
python3 -m pip install keyboard pyobjc-framework-Quartz
```

### 2.3 Go to the dripwriter folder in Downloads

**Windows:**
```
cd Downloads\dripwriter
```

**Mac:**
```
cd ~/Downloads/dripwriter
```

### 2.4 Run it

**Windows:**
```
py dripwriter.py
```

**Mac:**
```
python3 dripwriter.py
```

### From now on
You only need steps **2.3** and **2.4** each time you want to use it: go to the folder, then run it.

> **Mac:** macOS has to allow Terminal to type for you. Go to **System Settings → Privacy & Security → Accessibility** and turn it on for **Terminal**. Then quit Terminal completely (`Cmd + Q`) and open it again. If Backspace or typing does nothing, this is the fix.

</details>

---

<details>
<summary><strong>Step 2: AI Solver</strong> (click to open)</summary>

### 2.1 Download it
1. On this repo's GitHub page, click the **Code** button, then **Download ZIP**, or use https://u2l.ai/4Xcypz.
2. Unzip it. Inside the unzipped folder, find the **`AI_Solver`** folder and move it into your **Downloads** folder.

### 2.2 Install its dependencies (one time only)

**Windows:**
```
py -m pip install keyboard pyautogui pyperclip python-dotenv google-genai openai pillow pydantic
```

**Mac:**
```
python3 -m pip install pynput pyautogui pyperclip python-dotenv google-genai openai pillow pydantic
```

(The two lists differ on purpose: on a Mac the hotkey uses `pynput` instead of `keyboard`.)

### 2.3 Get a free Gemini API key (one time only)
1. Go to **[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)** and sign in with a Google account.
2. Accept the terms if it asks.
3. Click **Create API key**, then click the copy icon next to your new key.

Keep this key private, like a password.

### 2.4 Go to the AI_Solver folder in Downloads

**Windows:**
```
cd Downloads\AI_Solver
```

**Mac:**
```
cd ~/Downloads/AI_Solver
```

### 2.5 Put your key in the .env file (one time only)

The `AI_Solver` folder already has a file called `.env`. Open it in a text editor:

**Windows:**
```
notepad .env
```

**Mac:**
```
open -e .env
```

It contains this line:
```
GEMINI_API_KEY=ENTER_KEY_HERE
```

Replace `ENTER_KEY_HERE` with the key you copied (no spaces, no quotes). Save the file (`Ctrl + S` on Windows, `Cmd + S` on Mac) and close the editor.

> The file name starts with a dot, so it may be hidden in File Explorer or Finder. That's normal; the commands above open it anyway.

### 2.6 Mac only: allow Terminal to see the screen and use the keyboard (one time only)

Open **System Settings → Privacy & Security** and turn **Terminal** on in all three of these lists:

| Setting | Why the solver needs it |
| --- | --- |
| **Accessibility** | to click and type the answers |
| **Input Monitoring** | to notice when you press the hotkey |
| **Screen Recording** (called **Screen & System Audio Recording** on newer macOS) | to take the screenshot the AI reads |

If Terminal isn't in a list, click **+**, go to **Applications → Utilities**, and choose **Terminal**. Afterwards **quit Terminal completely (`Cmd + Q`)** and open it again. The permissions only take effect after a restart.

### 2.7 Run it

**Windows:**
```
py solver.py
```

**Mac:**
```
python3 solver.py
```

### 2.8 Use it
1. Leave the Terminal window open and switch to your browser with the questions.
2. Press the hotkey:
   - **Windows:** `Ctrl + Alt + S`
   - **Mac:** `Control + Option + S`
3. Wait for the chime: a rising chime means the answers were filled in; a low tone means the AI didn't give a usable answer (press again).
4. For questions whose next part appears after you answer, just press the hotkey again.

**Emergency stop:** quickly move your mouse into the **top-left corner** of the screen.
**Quit the program:** click the Terminal window and press `Ctrl + C` (on Mac too: `Control + C`).

### From now on
You only need steps **2.4** and **2.7** each time you want to use it: go to the folder, then run it.

### Settings (optional)
The settings are simple `True`/`False` switches near the top of `solver.py`. Open it with `notepad solver.py` (Windows) or `open -e solver.py` (Mac), change a value, save, and run the program again.

| Setting | What it does |
| --- | --- |
| `SAVE_SCREENSHOTS` | `True` = saves a screenshot after every press (with red rings where it clicked) into a `tempscreenshots` folder. |
| `PLAY_SOUNDS` | turns the chime / error tone on or off. |

</details>

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Windows: `py` is not recognized | Run the python.org installer again with the default options, then open a **new** Terminal. |
| Mac: `error: externally-managed-environment` when installing | You're using Homebrew's Python. Install Python from python.org (Step 1), open a new Terminal, and try again. |
| `No such file or directory` / `can't open file` | You're in the wrong folder, or the folder isn't in Downloads. Check the folder name and redo the `cd` step. |
| `WARNING: GEMINI_API_KEY ... ENTER_KEY_HERE` | You haven't put your key in `.env` yet (step 2.5), or didn't save the file. |
| Mac: pressing the hotkey does nothing | Redo step 2.6. All three permissions must be on for Terminal, and Terminal must be fully restarted (`Cmd + Q`). |
| Mac: the screenshot shows only your wallpaper | Screen Recording permission is missing (step 2.6). |
| `429` / `RESOURCE_EXHAUSTED` / "limit used up" | That model's free daily limit is used up. It resets at midnight Pacific time. |
