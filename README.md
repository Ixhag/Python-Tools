# Python Tools

A small collection of Python command-line tools.
- **Drip Writer**: types out text you paste into it, into any window, with randomized pauses between keystrokes.
- **AI Solver**: reads questions off your screen with an AI model and fills in the answers for you.

All commands below are typed into the command line:
- **Windows:** press the Start button, type `Terminal`, and open it.
- **Mac:** press `Cmd + Space`, type `Terminal`, and open it.

Copy and paste each command **one at a time**, pressing Enter after each one.

> Windows and Mac use different commands. Only follow the steps for your system.

---

## Step 1: Install Python

Download and run the installer from **[python.org/downloads](https://www.python.org/downloads/)** (it works for both Windows and Mac). Use the default options.

Then open a **new** Terminal and check that it worked.

**Windows:**
```
py --version
```

**Mac:**
```
python3 --version
```

If it shows a version number, you're ready.

---

## Drip Writer

### 2.1 Download it
Open `dripwriter.py` in this GitHub repo and click the **Download raw file** button (the download icon near the top right of the file). It saves to your **Downloads** folder.

### 2.2 Install its dependencies (one time only)

**Windows:**
```
py -m pip install keyboard
```

**Mac:**
```
python3 -m pip install keyboard
```

### 2.3 Go to your Downloads folder

**Windows:**
```
cd Downloads
```

**Mac:**
```
cd ~/Downloads
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

> **Mac note:** if pressing Backspace does nothing, go to **System Settings → Privacy & Security → Accessibility**, turn it on for **Terminal**, then restart Terminal.

---

## AI Solver

### 2.1 Download it
1. On this repo's GitHub page, click the green **Code** button, then **Download ZIP**.
2. Unzip it and move the **`AI_Solver`** folder into your **Downloads** folder.

### 2.2 Install its dependencies (one time only)

**Windows:**
```
py -m pip install keyboard pyautogui pyperclip python-dotenv google-genai openai pillow pydantic
```

**Mac:**
```
python3 -m pip install keyboard pyautogui pyperclip python-dotenv google-genai openai pillow pydantic
```

### 2.3 Go to the AI_Solver folder

**Windows:**
```
cd Downloads\AI_Solver
```

**Mac:**
```
cd ~/Downloads/AI_Solver
```

### 2.4 Run it

**Windows:**
```
py solver.py
```

**Mac:**
```
python3 solver.py
```

### From now on
You only need steps **2.3** and **2.4** each time you want to use it: go to the folder, then run it.

> **Mac note:** the solver needs the same **Accessibility** permission as Drip Writer, plus **System Settings → Privacy & Security → Screen Recording** turned on for **Terminal** so it can see your screen. Restart Terminal after changing either.

The solver needs an API key to work. That setup (the `.env` file) is coming in a later update to this README.
