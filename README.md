# Python Tools

A small collection of Python command-line tools.

- **Drip Writer**: types out text you paste into it, into any window, with randomized human-like pauses between keystrokes.
- **AI Screen Solver**: reads math and quiz questions off your screen with an AI model and fills in the answers for you.

Everything below is done in the command line.
- **Windows:** press the Start button, type `Terminal`, and open it.
- **Mac:** press `Cmd + Space`, type `Terminal`, and open it.

> **Heads up:** Windows and Mac use different commands for Python. Windows uses `py`. Mac uses `python3`. Follow the section for your system.

---

## 1. Install Python

Download and run the installer for your system from:

**[python.org/downloads](https://www.python.org/downloads/)**

The site detects Windows or Mac automatically. Run the installer with the default options.

When it's done, open a new Terminal and check that it worked:

**Windows:**
```
py --version
```
**Mac:**
```
python3 --version
```

---

## 2. Drip Writer

### Download it
Open `dripwriter.py` in this GitHub repo and click the **Download raw file** button (the download icon near the top right of the file). It saves to your **Downloads** folder.

### Go to your Downloads folder

**Windows:**
```
cd Downloads
```
**Mac:**
```
cd ~/Downloads
```

### Install and run

**Windows:**
```
py -m pip install keyboard
py dripwriter.py
```
**Mac:**
```
python3 -m pip install keyboard
python3 dripwriter.py
```

> **Mac note:** macOS blocks apps from reading keystrokes until you allow it. If pressing Backspace does nothing, go to **System Settings → Privacy & Security → Accessibility** and turn it on for **Terminal**, then restart Terminal.

---

## 3. AI Screen Solver

### Download it
1. On this repo's GitHub page, click the green **Code** button, then **Download ZIP**.
2. Unzip it and move the **`ai-solver`** folder into your **Downloads** folder.

### Go to the folder

**Windows:**
```
cd Downloads\ai-solver
```
**Mac:**
```
cd ~/Downloads/ai-solver
```

### Install and run

**Windows:**
```
py -m pip install keyboard pyautogui pyperclip python-dotenv google-genai openai pillow pydantic
py solver.py
```
**Mac:**
```
python3 -m pip install keyboard pyautogui pyperclip python-dotenv google-genai openai pillow pydantic
python3 solver.py
```

> **Mac note:** besides the Accessibility permission above, the solver needs to see your screen. Turn on **System Settings → Privacy & Security → Screen Recording** for **Terminal**, then restart Terminal.

The solver needs an API key to work. That setup (the `.env` file) is coming in a later update to this README.
