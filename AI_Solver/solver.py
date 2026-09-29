# ============================================================
# QUICK SWITCHES (the ones you flip most often)
# ============================================================
# Save a screenshot after every press (AFTER the answers are filled in) to
# the "tempscreenshots" folder next to this script. Never auto-deleted.
SAVE_SCREENSHOTS = False

# Rising chime when the answers are filled in; low falling tone when the AI
# gives no usable solution. (No sound on the emergency stop.)
PLAY_SOUNDS = True

import base64
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Literal, Optional

import keyboard
import pyautogui
import pyperclip
from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import BadRequestError, DefaultHttpxClient, OpenAI
from PIL import Image
from pydantic import BaseModel, Field

try:
    import httpx
except ImportError:  # httpx ships with both SDKs; this is just a safety net
    httpx = None

try:
    import winsound  # Windows only
except ImportError:
    winsound = None

# ============================================================
# CONFIG
# ============================================================
load_dotenv()

# Values that mean "no key yet" (the .env in the repo ships with a placeholder).
_PLACEHOLDER_KEYS = {"ENTER_KEY_HERE", "YOUR_KEY_HERE", "YOUR_API_KEY", "CHANGEME"}


def read_key(name: str) -> str:
    """Read an API key from the environment / .env. Returns "" when it is
    missing or still the placeholder, so the rest of the program treats it
    as "no key" instead of sending a fake key and getting auth errors."""
    value = os.getenv(name, "").strip().strip('"').strip("'").strip()
    if value.upper() in _PLACEHOLDER_KEYS:
        return ""
    return value


def key_is_placeholder(name: str) -> bool:
    raw = os.getenv(name, "").strip().strip('"').strip("'").strip()
    return raw.upper() in _PLACEHOLDER_KEYS


GEMINI_API_KEY = read_key("GEMINI_API_KEY")
OPENROUTER_API_KEY = read_key("OPENROUTER_API_KEY")

# ---------------- Gemini model switches ----------------
# Which model(s) are enabled. What this MEANS depends on the GEMINI MODE
# switches right below: in the default mode, turn ON exactly one (the rest
# OFF) - the first one enabled is used, nothing else. In Consult mode, turn
# ON every model you want consulting each other (two or more). Free daily
# limits differ per model and per project: check the real numbers in Google
# AI Studio (Rate limits page).
USE_GEMINI_3_1_FLASH_LITE = False   # gemini-3.1-flash-lite  (500/day free)
USE_GEMINI_3_5_FLASH_LITE = True    # gemini-3.5-flash-lite  (500/day free)
USE_GEMINI_3_5_FLASH = False        # gemini-3.5-flash       (20/day free)
USE_GEMINI_3_6_FLASH = False        # gemini-3.6-flash       (20/day free)
USE_GEMINI_3_7_FLASH = False        # gemini-3.7-flash       (20/day free)
USE_GEMINI_3_8_FLASH = False        # gemini-3.8-flash       (20/day free)

# Gemini reasoning effort. Lower = faster. Use "low" (fast) / "medium" / "high".
# NOTE: "minimal" is NOT supported by gemini-3.8-flash and returns an error.
GEMINI_THINKING = "medium"

# LOCAL MATHS (fast + exact): Gemini writes each numeric answer's
# calculation as a formula, e.g. exp(-3.42) rounded to 4 places, and YOUR PC
# works it out exactly (well under a millisecond) and types that result - so
# arithmetic/rounding slips by the AI can't get through. Only plain maths is
# allowed in the formula (numbers, + - * / ^, exp, ln, log, sqrt, trig...);
# anything else is ignored and the AI's own answer is used.
LOCAL_MATH = True

# Let Gemini write and RUN Python in Google's sandbox instead. Much SLOWER:
# every run is a round trip to Google (one e^-3.42 question took 38s with 7
# runs). LOCAL_MATH above gets the arithmetic right without that wait.
GEMINI_CODE_EXECUTION = False

# ---------------- GEMINI MODE (pick ONE of these True) ----------------
# GEMINI_ONLY: only Gemini is used at all - OpenRouter is skipped entirely.
# Turn this OFF to fall back to Gemini + OpenRouter multi-AI consensus
# instead (see OPENROUTER_MODEL / OPENROUTER_CALLS / CONSENSUS_MIN below).
# The three modes below only apply while this is True:
GEMINI_ONLY = True

#   - Plain (both False): the single model enabled above is used, alone.
#   - USE_GEMINI_FLASH_RACE: sends the SAME request to BOTH Flash-Lite
#     models (3.1 and 3.5) at once and uses whichever answers first,
#     ignoring the slower one - ignores the individual switches above.
#     Trades one extra concurrent API call (counts against BOTH models'
#     free-tier quotas) for lower, more consistent latency.
#   - GEMINI_CONSULT: every model switched ON above is queried in parallel
#     and their answers must AGREE (same consensus rule as multi-AI mode,
#     see CONSENSUS_MIN) before anything is clicked or typed. E.g. turn on
#     both Flash-Lite models with this True and they must consult and agree
#     on each answer. Needs at least 2 models enabled to do anything.
#   - USE_GEMINI_FLASH_CYCLE: uses the (non-Lite) Flash models in
#     FLASH_CYCLE_MODELS below, one at a time, at GEMINI_THINKING. It stays on
#     the first one until that model's free daily limit (20/day) runs out,
#     then moves to the next one automatically, in the SAME press - you don't
#     lose the press. When every Flash model is used up it falls back to
#     FLASH_CYCLE_FALLBACK. Ignores the individual switches above. Models come
#     back automatically when Google resets the daily limits (midnight
#     Pacific time), and used-up models are remembered across restarts.
# If more than one is True: Consult wins, then Cycle, then Race.
USE_GEMINI_FLASH_RACE = False
GEMINI_CONSULT = False
USE_GEMINI_FLASH_CYCLE = False

# Consult mode: how many times to ask EACH enabled model (all at the same
# time, so it barely adds waiting). E.g. both Flash-Lite models on and this
# at 2 = 4 answers per press; an answer is only filled in when the majority
# agree (at least CONSENSUS_MIN, and more votes than any other answer).
# Also works with just ONE model on (e.g. 3 = ask it 3 times and vote).
# Each ask counts against that model's daily limit.
GEMINI_CONSULT_REPEAT = 1

# Flash Cycle order: the first one is used until it runs out, then the next.
# Reorder or remove models freely.
FLASH_CYCLE_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]
# Used once ALL the Flash models above are used up for the day
# ("" = no fallback: presses just fail with the error tone until the reset).
FLASH_CYCLE_FALLBACK = "gemini-3.5-flash-lite"
# The cycle remembers which models are used up (until Google's daily reset)
# across restarts. Set True to forget all of that when the program starts -
# e.g. if you think a model was wrongly marked as used up.
RESET_FLASH_CYCLE_ON_START = False

OPENROUTER_MODEL = "openrouter/free"

HOTKEY = "ctrl+alt+s"

# SAFETY: controls the program will NEVER click, whatever the AI says. An
# option whose label matches one of these (upper/lower case ignored) is
# thrown out and reported as [BLOCKED]. Covers quiz-site navigation like a
# "Question list" sidebar (whose circles look just like radio buttons).
# Add your own as needed.
NEVER_CLICK_LABELS = [
    r"question\s*\d+",           # "Question 3" in a question list
    r"question list",
    r"(next|previous|prev|back|submit|save|close|menu)( question)?",
    r"check answer", r"clear all", r"help me solve this",
    r"view an example", r"textbook", r"similar question", r"try again",
]

# SAFETY: screen areas (in pixels: left, top, right, bottom) where nothing is
# ever clicked or typed. E.g. your quiz site's question list sidebar on a
# 2560x1440 screen: NEVER_CLICK_ZONES = [(0, 0, 505, 1440)]
# (Only use it if the sidebar is always open - if you collapse it, the
# question moves into that area and its boxes would be blocked too.)
NEVER_CLICK_ZONES = []

# Keep TRUE while testing coordinates.
# Set FALSE only after you verify the mouse reaches the correct control.
TEST_MODE = False

# Open network connections to the APIs in the background at startup so the
# first hotkey press doesn't pay for DNS + TLS handshakes.
WARM_UP_CONNECTIONS = True

# How many parallel OpenRouter calls to make (ignored if GEMINI_ONLY).
OPENROUTER_CALLS = 3

# OpenRouter: attempt 1 asks for JSON mode, attempt 2 drops response_format.
OPENROUTER_RETRIES = 2

# Never act on fewer than this many agreeing AI results (ignored if GEMINI_ONLY).
CONSENSUS_MIN = 2

# Stop waiting for slow AIs as soon as every question already has agreement.
EARLY_EXIT = True

# Network timeout per request, in seconds (prevents a hung request from
# blocking the whole run).
REQUEST_TIMEOUT = 90  # code execution + high thinking can take a while

# Screenshots wider than this are downscaled before upload. 0 = send at
# full native resolution (no resize) - PRECISION MATTERS MORE THAN SPEED
# HERE: small answer boxes need every pixel for the AI to locate them
# accurately, and native-res PNG costs only ~100ms more locally, which is
# nothing next to the multi-second AI call. Lower this only if uploads are
# genuinely too slow on your connection.
# Bboxes are normalized 0..1000 so clicks stay accurate regardless.
IMAGE_MAX_WIDTH = 0
IMAGE_FORMAT = "PNG"  # "PNG" (lossless - sharper box edges) or "JPEG" (smaller/faster)
JPEG_QUALITY = 85

# How much detail GEMINI actually looks at. This is the setting that really
# controls "image quality": however sharp the PNG is, Gemini shrinks every
# image down to a fixed token budget before reading it.
#   "ultra_high" = 2240 tokens (2x the default - Google recommends it for
#                  reading screens / computer use; a little slower)
#   "high"       = 1120 tokens (same as the default)
#   ""           = don't send the setting at all
# If a model rejects "ultra_high", the program notices once, prints a note,
# and falls back to the default for the rest of the run.
GEMINI_IMAGE_DETAIL = "ultra_high"

# Keep API connections open this long when idle. httpx's default is only 5s,
# which throws away the warm connection between hotkey presses and forces a
# fresh DNS + TLS handshake (~100-300ms) on almost every press.
KEEPALIVE_SECONDS = 300

# Brief pause after a click, before the next action, so the page's JS has
# time to register it. pyautogui.PAUSE=0 (below) fires actions back-to-back
# with no gap, which some JS-driven quiz sites can miss.
# The earlier "selects the whole page instead of the box" bug was fixed
# structurally (perform_task sends TWO SEPARATE click events instead of one
# native double-click - see DOUBLE_CLICK_TEXTBOXES below), not by this
# delay being large, so this can stay small without that bug coming back.
CLICK_SETTLE_SECONDS = 0.05

# pyautogui.click() teleports the cursor instantly by default with no real
# mouse-move event. Some web forms only register a click/focus after seeing
# the cursor actually arrive. This makes the FIRST click on a new control
# glide there over this many seconds instead of jumping.
MOVE_DURATION_SECONDS = 0.05

# Pause between one control and the next (option clicks and text boxes),
# so the page has time to process a blur/focus change. Not added after the
# last control.
TASK_GAP_SECONDS = 0.1

# Multi-blank questions:
#   - Several text boxes and NO radio/checkbox option to pick (e.g.
#     "x = [ ], y = [ ], z = [ ]" on its own): ALL boxes are filled in the
#     SAME press. No re-pressing needed.
#   - An option that has to be SELECTED and contains blanks (e.g.
#     "(o) The solution is x = [ ], y = [ ]"): with this True, one press
#     selects the option and fills the FIRST blank, then you press again for
#     the rest - selecting an option can reflow the page, so the positions of
#     its other blanks from the screenshot may be stale. On the next press
#     the option is already selected, so the remaining blanks are all filled
#     together. Set False to fill every blank right after the option click.
ONE_TEXT_BOX_PER_PRESS = True

# Fill controls from the BOTTOM of the page up (and right-to-left within a
# line). Typing into a box can widen it and push everything AFTER it right
# or down, but it never moves anything BEFORE it - so going last-to-first
# means every box is still exactly where the screenshot showed it when it is
# reached. Within one question the option is still clicked before its
# blanks. Costs nothing. Set False for plain top-to-bottom order.
FILL_BOTTOM_TO_TOP = True

# pyperclip.copy() followed immediately by Ctrl+V had ZERO gap before this -
# on Windows the clipboard can transiently fail to update (a clipboard
# manager, antivirus, etc. briefly holding it), silently pasting stale or
# empty content. This confirms the write actually landed before pasting.
CLIPBOARD_SETTLE_SECONDS = 0.05
CLIPBOARD_COPY_RETRIES = 3

# click_option (radio/checkbox): click it, wait, then click it again.
# Reinforces the selection if the first click didn't register. Safe for
# radio buttons (clicking twice keeps them selected either way). If any of
# your forms use CHECKBOXES rather than radios, turn this off - clicking a
# checkbox twice toggles it back OFF.
DOUBLE_CLICK_OPTIONS = True

# SNAP TO THE REAL CONTROL: the AI's box is an estimate that can be a few
# pixels off (or land on an option's label instead of its circle). Before
# clicking, the program looks at the SAME screenshot it sent to the AI, finds
# the actual text-box border / radio circle / checkbox near the AI's guess,
# and clicks its exact centre. Pure local pixel work - a few milliseconds, no
# extra screenshot, no retries. If it can't find a clear control nearby it
# simply uses the AI's position, exactly as before.
SNAP_TO_CONTROLS = True

# Two answers must never go into the same box (Ctrl+A would silently
# overwrite the first one). If two text answers would land in one box, the
# one the AI placed closest is typed and the other waits for the next press.
PREVENT_SAME_BOX = True

# type_text: click the field TWICE, as two separate clicks with a short
# pause between them (not one native double-click - see perform_task for
# why), before Ctrl+A selects everything and the answer is pasted in.
DOUBLE_CLICK_TEXTBOXES = True

# ---- Sounds (master switch PLAY_SOUNDS is at the very top) ----
# Optional: path to your OWN .wav file to play instead of the built-in chime
# (Windows plays .wav only), e.g. r"C:\Sounds\ping.wav". Leave "" for the chime.
PING_SOUND_FILE = ""

# Built-in "done" chime: two soft rising notes.
PING_NOTES_HZ = (988, 1319)      # first note, second note
PING_NOTE_MS = (90, 220)         # how long each note rings
PING_VOLUME = 0.45               # 0.0 - 1.0

# Built-in "no usable answer" tone: two low falling notes.
ERROR_NOTES_HZ = (392, 262)
ERROR_NOTE_MS = (160, 320)
ERROR_VOLUME = 0.45

# After each press FINISHES (the AI has answered and the answers have been
# clicked/typed in), a NEW screenshot is taken and saved as a PNG in this
# folder, so you can see exactly what got filled in. One file per press -
# if the AI fails or nothing is filled in, the screen is still saved as it
# was at the end, so every press leaves a screenshot. (The screenshot sent to
# the AI is a separate, earlier one and is not saved.)
# The folder sits next to this script and is created automatically, even if
# you delete it. Nothing is ever deleted automatically - clear it out by hand
# whenever you like. Files are named by date and time, e.g.
# screenshot_2026-09-24_14-05-33-123.png, so they sort in the order taken.
# (The on/off switch SAVE_SCREENSHOTS is at the very top of the file.)

# Draw a small red ring on the saved screenshot at every spot that was
# clicked, so you can tell a WRONG ANSWER apart from a WRONG POSITION when
# something is missed.
MARK_CLICKS_ON_SCREENSHOTS = True

# Short wait before that final screenshot, so the page has finished showing
# the typed answers / selected options.
SCREENSHOT_DELAY_SECONDS = 0.3
SCREENSHOT_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tempscreenshots"
)

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

busy_lock = threading.Lock()
_clicked_points = []  # screen spots clicked this press (for marked screenshots)

# ============================================================
# DATA MODELS
# ============================================================
class BoundingBox(BaseModel):
    # Coordinates normalized to 0..1000.
    x1: int = Field(ge=0, le=1000)
    y1: int = Field(ge=0, le=1000)
    x2: int = Field(ge=0, le=1000)
    y2: int = Field(ge=0, le=1000)


class Task(BaseModel):
    question_id: str
    question: str
    answer: str
    input_type: Literal["type_text", "click_option"]
    bbox: BoundingBox
    option: Optional[str] = None
    # Distinguishes multiple blanks within the SAME question (e.g. a chosen
    # option reading "x = __, y = __, z = __" needs 3 type_text tasks, each
    # with its own part: "x", "y", "z"). None for a single-control question.
    part: Optional[str] = None
    # Exact screen pixel to click, set when the control was snapped to on the
    # screenshot (see SNAP_TO_CONTROLS). None = use the centre of bbox.
    click: Optional[List[int]] = None
    # LOCAL_MATH: the formula for this answer (e.g. "exp(-3.42)") and how
    # many decimal places to round it to (None = exact).
    calc: Optional[str] = None
    round_to: Optional[int] = None


class SolverResponse(BaseModel):
    tasks: List[Task] = Field(default_factory=list)


# What the AI is asked to return. Boxes use Gemini's native detection format
# "box_2d": [ymin, xmin, ymax, xmax] normalised 0-1000 (the format Google's
# own object-detection examples use), and are converted to BoundingBox when
# parsed. Property order matters: the model writes the question and answer
# BEFORE it locates the control.
_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
SOLVER_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "question": {"type": "string"},
                    "calc": _NULLABLE_STR,
                    "round_to": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "answer": {"type": "string"},
                    "input_type": {"type": "string", "enum": ["type_text", "click_option"]},
                    "option": _NULLABLE_STR,
                    "part": _NULLABLE_STR,
                    "box_2d": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "[ymin, xmin, ymax, xmax] normalized to 0-1000",
                    },
                },
                "required": [
                    "question_id", "question", "calc", "round_to", "answer",
                    "input_type", "option", "part", "box_2d",
                ],
            },
        }
    },
    "required": ["tasks"],
}

# ============================================================
# SHARED API CLIENTS (created once -> connection reuse, no repeated
# TLS handshakes / client construction inside each request)
# ============================================================
def _limits():
    if httpx is None:
        return None
    return httpx.Limits(max_keepalive_connections=10, keepalive_expiry=KEEPALIVE_SECONDS)


def make_gemini_client():
    if not GEMINI_API_KEY:
        return None

    base = {"api_version": "v1", "timeout": int(REQUEST_TIMEOUT * 1000)}  # ms

    extras = {}
    limits = _limits()
    if limits is not None:
        extras["client_args"] = {"limits": limits}

    # One attempt only: a 429 should fail immediately instead of the SDK
    # sleeping for the server's "retry in 29s" hint and trying again
    # (that is what made a rate-limit failure take ~90s).
    retry_cls = getattr(types, "HttpRetryOptions", None)
    if retry_cls is not None:
        try:
            extras["retry_options"] = retry_cls(attempts=1)
        except Exception:
            pass

    # Use as many optional settings as this SDK version accepts.
    attempts = [extras]
    attempts += [{k: v} for k, v in extras.items()] if len(extras) > 1 else []
    attempts.append({})

    for opts in attempts:
        try:
            return genai.Client(
                api_key=GEMINI_API_KEY,
                http_options=types.HttpOptions(**base, **opts),
            )
        except Exception:
            continue

    return genai.Client(api_key=GEMINI_API_KEY)


def make_openrouter_client():
    if not OPENROUTER_API_KEY or GEMINI_ONLY:
        return None

    kwargs = dict(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        timeout=REQUEST_TIMEOUT,
        max_retries=0,  # we handle retries ourselves; SDK default is 2 hidden retries
        default_headers={
            "HTTP-Referer": "http://localhost",
            "X-Title": "Screen Math Solver",
        },
    )

    limits = _limits()
    if limits is not None:
        try:
            return OpenAI(**kwargs, http_client=DefaultHttpxClient(limits=limits))
        except Exception:
            pass

    return OpenAI(**kwargs)


gemini_client = make_gemini_client()
openrouter_client = make_openrouter_client()

# Flips to False if OpenRouter rejects response_format, so later runs
# don't waste a request re-discovering that.
_openrouter_json_mode = True

# ============================================================
# AI PROMPT
# ============================================================
SOLVER_PROMPT = r"""
You are a computer-vision math homework UI solver.

Analyze ONLY the supplied screenshot.

Your job:
1. Find EVERY visible, answerable math or multiple-choice question.
2. Solve each question correctly.
3. Identify EVERY UI control that needs to be clicked or filled in to
   completely answer it - a single question can need SEVERAL controls.
4. Return JSON only.

ONE QUESTION CAN NEED MULTIPLE TASKS - THIS IS COMMON, NOT AN EXCEPTION:
Many questions are a multiple-choice list where ONE option is itself a
sentence with one or more blanks to fill in, e.g.:
  "The solution is x = [ ], y = [ ], and z = [ ]."
  "There are infinitely many solutions of the form x = [ ], y = r, z = s."
To answer a question like this you must BOTH select the correct radio
button/option AND fill in every blank that belongs to THAT option. Blanks
belonging to the OTHER, unselected options must be left alone entirely -
do not emit tasks for them.
Rules for these questions:
- Emit ONE task with input_type "click_option" for the radio button/option
  itself (box_2d = ONLY the small circle/checkbox graphic itself, tightly -
  see the box_2d rule below for why).
- Emit ONE SEPARATE task with input_type "type_text" for EVERY individual
  blank inside that option - never merge two blanks into one task and
  never put two answers in one "answer" string (e.g. never "x=1, y=2").
  If the option reads "x = [ ], y = [ ], z = [ ]", that is 3 separate
  type_text tasks, each with its own tight box_2d around just that one blank
  and its own single value in "answer" (e.g. "5", not "x=5").
- ALL tasks that belong to the same question (the click_option task and
  every type_text task) share the SAME question_id, and should carry a
  "part" naming which piece they are:
    click_option task: part = null
    each blank: part = the variable/label immediately before it if visible
      (e.g. "x", "y", "z", "r"), otherwise "1", "2", "3"... in the order
      the blanks appear (left-to-right, top-to-bottom).
- The same applies to a question with SEVERAL text boxes and NO option to
  select (e.g. "x = [ ], y = [ ], z = [ ]" on its own): one type_text task
  per box, all with the same question_id and each with its own part.
- For every multi-box question, first COUNT the empty boxes that belong to
  it, then return exactly that many type_text tasks - no box skipped, no
  two tasks on the same box. Neighbouring boxes are separate controls even
  when they sit very close together on one line.
- A question with only ONE control (a single text box, or a plain radio
  list with no embedded blanks) still gets exactly ONE task, part = null.

CRITICAL RULES:
- The screenshot is the source of truth.
- Never invent a question that is not clearly visible.
- Never solve a question that is off-screen.
- Do not duplicate the same control twice.
- Do not mistake ordinary page text, instructions, examples,
  advertisements, browser controls, or navigation for questions.
- NEVER identify or click Next, Previous, Submit, Save, Close,
  Menu, browser controls, tabs, scrollbars, headers, footers,
  or other navigation controls.
- IGNORE SIDE PANELS AND QUESTION LISTS COMPLETELY. A "Question list" /
  question menu (entries like "Question 1", "Question 2", "Question 3" with
  circles, check marks or ticks beside them) is NAVIGATION: those circles
  are progress indicators showing which questions are done, NOT answer
  options, even though they look like radio buttons. Clicking one leaves
  the current question and loses the work. Never return a task for them.
- Never click page buttons such as Check answer, Clear all, Help me solve
  this, View an example, Textbook, Similar question, Try again.
- ONLY answer the question shown in the main work area, and ONLY identify
  an actual answer field or actual answer option inside it.
- Questions with parts (a), (b), (c)... often reveal the next part's box
  only after the previous part is answered. Answer EVERY part that
  currently shows an EMPTY box; parts whose boxes are already filled in
  are done.
- SKIP ANY CONTROL THAT IS ALREADY ANSWERED - this is critical, since the
  screenshot may be mid-way through a worksheet someone is filling in:
    * A text box that ALREADY contains a typed value (not empty, not
      placeholder/greyed-out example text) is DONE. Do not return a task
      for it, even to "confirm" or re-enter the same value.
    * An option (radio/checkbox) that is ALREADY selected/checked is DONE.
      Do not return a task for it.
  Only return tasks for controls that are still EMPTY or unselected.

For each task return:
- question_id: visible question number if available, e.g. "1". The SAME
  value for every task belonging to the same question.
- question: concise transcription of the visible question.
- calc: for a type_text answer that is a NUMBER worked out from numbers
  (arithmetic, powers, exponentials, logarithms, roots, trig, a solved
  equation's value, ...), ONE formula that computes it from the numbers in
  the question - the program calculates it exactly and types that result.
  Allowed: numbers, + - * / ^ ( ), pi, e, and exp(x), ln(x), log(x) (= ln),
  log(x, base), log10(x), log2(x), sqrt(x), cbrt(x), root(x, n), abs(x),
  factorial(n), comb(n, k), perm(n, k), floor(x), ceil(x), sin/cos/tan and
  asin/acos/atan (radians), sind/cosd/tand and asind/acosd/atand (degrees).
  Always write * for multiplication ("2*pi", never "2pi").
  Examples: e^(-3.42) -> "exp(-3.42)"; 3^x = 20 -> "ln(20)/ln(3)";
  1000(1 + 0.05/12)^(12*3) -> "1000*(1+0.05/12)^(12*3)".
  Use null when the answer is not a single number (an expression with a
  variable, an interval, a list, text) or for click_option tasks.
- round_to: the number of decimal places the question asks the answer to
  be rounded to (nearest ten-thousandth = 4, nearest hundredth = 2, nearest
  whole number = 0). null if the question wants an exact answer.
- answer: the single value for THIS control only (one option's text, or
  one blank's value). Never combine multiple blanks' values into one.
- input_type:
    "type_text" for a text/math answer box
    "click_option" for a multiple-choice/radio/checkbox option
- box_2d: bounding box of the ACTUAL, SINGLE answer control, NOT the whole
  question and NOT a whole row containing several blanks.
  For click_option: box_2d ONLY the small circle/checkbox graphic itself -
  TIGHT around just that icon, NOT the option's label text, and NOT the
  whole row. The circle is a real, always-clickable native control; the
  text label next to it is NOT guaranteed to be clickable on every site,
  so a click that lands on the letter or words instead of the circle can
  silently fail to select anything.
  For type_text: just the one input box itself, edge to edge.
  Format: [ymin, xmin, ymax, xmax], normalized to 0-1000 (0,0 = top-left
  of the screenshot, 1000,1000 = bottom-right). Note the order: y first.
- option:
    for click_option, transcribe visible option text when possible.
    for type_text, use null.
- part: see above. null for a click_option task or a question with only
  one control; otherwise the blank's variable/label or position index.

Math rules:
- Follow the visible instructions exactly.
- Use exact form when requested.
- Use radicals rather than decimals when requested.
- For multiple answers, preserve the requested order/separator.
- For multiple choice, answer must be the option text.
- Fractions: fully simplified, e.g. "3/4" not "6/8". Follow any rounding
  instruction exactly (e.g. "round to two decimal places").
- CHECK every answer before returning it: substitute it back into the
  original equation(s) / re-do the calculation a second way. If the check
  fails, solve again.

Before returning, verify:
A. Every task is visibly present.
B. The answer is mathematically correct.
C. Each box_2d is on exactly ONE actual answer control, never a whole row
   of multiple blanks.
D. No navigation control is included.
E. If the chosen option contains multiple blanks, there is one
   click_option task PLUS one type_text task per blank - not one merged
   task, and not tasks for blanks in the options you did NOT choose.
F. None of the returned tasks point at a control that is already
   filled in or already selected.

Return exactly one JSON object. Example for a question needing BOTH an
option click and two separate blanks inside that option:
{
  "tasks": [
    {
      "question_id": "1",
      "question": "Solve the system. Select the correct choice.",
      "calc": null,
      "round_to": null,
      "answer": "The solution is x = _, y = _, and z = _.",
      "input_type": "click_option",
      "option": "A. The solution is x = , y = , and z = .",
      "part": null,
      "box_2d": [337, 30, 353, 44]
    },
    {
      "question_id": "1",
      "question": "Solve the system. Select the correct choice.",
      "calc": "4",
      "round_to": null,
      "answer": "4",
      "input_type": "type_text",
      "option": null,
      "part": "x",
      "box_2d": [335, 140, 350, 155]
    },
    {
      "question_id": "1",
      "question": "Solve the system. Select the correct choice.",
      "calc": "-3",
      "round_to": null,
      "answer": "-3",
      "input_type": "type_text",
      "option": null,
      "part": "y",
      "box_2d": [335, 175, 350, 190]
    }
  ]
}

A simple question with exactly one control looks like this instead:
{
  "tasks": [
    {
      "question_id": "2",
      "question": "Use a calculator to find the value to the nearest ten-thousandth. e^(-3.42)",
      "calc": "exp(-3.42)",
      "round_to": 4,
      "answer": "0.0327",
      "input_type": "type_text",
      "option": null,
      "part": null,
      "box_2d": [500, 400, 550, 520]
    }
  ]
}
"""

# Added to the prompt when GEMINI_CODE_EXECUTION is on.
CODE_EXECUTION_PROMPT = r"""

YOU CAN RUN PYTHON CODE. Use it for ALL the maths:
- First read every question and its numbers carefully from the screenshot.
- Then write and run Python for every calculation. Use sympy for exact
  algebra (sympy.Rational for fractions, sympy.linsolve / sympy.solve for
  equations and systems, sympy.simplify / sympy.nsimplify for final forms).
- Check each result in code by substituting it back into the original
  equation(s).
- Base every answer on the code's output, never on mental arithmetic.
- Finally, return ONLY the JSON object described above.
"""

# ============================================================
# RESPONSE PARSING
# ============================================================
_WS = re.compile(r"\s+")
_FENCE_START = re.compile(r"^\s*```(?:json)?\s*", re.I)
_FENCE_END = re.compile(r"\s*```\s*$")
_DIGITS = re.compile(r"\d+")

_TEXT_TYPES = {"text", "text_input", "input"}
_OPTION_TYPES = {"option", "multiple_choice", "radio", "checkbox"}


def clean_text(value) -> str:
    return "" if value is None else str(value).strip()


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:
        return False


def normalize_json_text(text: str) -> str:
    if not text:
        return ""

    text = _FENCE_END.sub("", _FENCE_START.sub("", text.strip())).strip()

    if _is_json(text):
        return text

    # Try whichever bracket type appears FIRST, so a bare "[{...}, {...}]"
    # array isn't mistaken for its first inner object.
    spans = []
    for open_c, close_c in (("{", "}"), ("[", "]")):
        first, last = text.find(open_c), text.rfind(close_c)
        if first >= 0 and last > first:
            spans.append((first, last))

    for first, last in sorted(spans):
        candidate = text[first:last + 1]
        if _is_json(candidate):
            return candidate

    return ""


# ============================================================
# LOCAL MATHS (see LOCAL_MATH)
# ============================================================
# A tiny calculator for the formulas the AI writes in "calc". It does NOT
# run the formula as Python: it reads the formula's structure and only
# understands numbers, + - * / ** ( ), pi, e and the functions listed below.
# Anything else (names, attributes, strings, imports...) makes it give up,
# and the AI's own answer is used instead.
import ast
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction

_CALC_MAX_CHARS = 300
_CALC_MAX_NODES = 200
_CALC_MAX_INT_EXP = 1000


class CalcError(Exception):
    pass


def _f(v):
    return float(v)


def _real_root(x, n):
    x, n = float(x), float(n)
    if n == 0:
        raise CalcError("0th root")
    if x < 0:
        if n != int(n) or int(n) % 2 == 0:
            raise CalcError("even root of a negative number")
        return -((-x) ** (1.0 / n))
    return x ** (1.0 / n)


def _log(x, base=None):
    if base is None:
        return math.log(_f(x))
    return math.log(_f(x)) / math.log(_f(base))


def _int_arg(v, limit):
    if isinstance(v, float):
        if v != int(v):
            raise CalcError("needs a whole number")
        v = Fraction(int(v))
    if v.denominator != 1 or abs(v.numerator) > limit:
        raise CalcError("needs a small whole number")
    return int(v.numerator)


def _sqrt(x):
    if isinstance(x, Fraction) and x >= 0:
        n, d = math.isqrt(x.numerator), math.isqrt(x.denominator)
        if n * n == x.numerator and d * d == x.denominator:
            return Fraction(n, d)  # exact: sqrt(9/4) = 3/2
    return math.sqrt(_f(x))


_CALC_FUNCS = {
    "exp": lambda x: math.exp(_f(x)),
    "ln": lambda x: math.log(_f(x)),
    "log": _log,
    "log10": lambda x: math.log10(_f(x)),
    "log2": lambda x: math.log2(_f(x)),
    "sqrt": _sqrt,
    "cbrt": lambda x: _real_root(x, 3),
    "root": _real_root,
    "abs": abs,
    "floor": lambda x: Fraction(math.floor(x)),
    "ceil": lambda x: Fraction(math.ceil(x)),
    "factorial": lambda n: Fraction(math.factorial(_int_arg(n, 170))),
    "comb": lambda n, k: Fraction(math.comb(_int_arg(n, 1000), _int_arg(k, 1000))),
    "perm": lambda n, k: Fraction(math.perm(_int_arg(n, 1000), _int_arg(k, 1000))),
    "sin": lambda x: math.sin(_f(x)),
    "cos": lambda x: math.cos(_f(x)),
    "tan": lambda x: math.tan(_f(x)),
    "asin": lambda x: math.asin(_f(x)),
    "acos": lambda x: math.acos(_f(x)),
    "atan": lambda x: math.atan(_f(x)),
    "sind": lambda x: math.sin(math.radians(_f(x))),
    "cosd": lambda x: math.cos(math.radians(_f(x))),
    "tand": lambda x: math.tan(math.radians(_f(x))),
    "asind": lambda x: math.degrees(math.asin(_f(x))),
    "acosd": lambda x: math.degrees(math.acos(_f(x))),
    "atand": lambda x: math.degrees(math.atan(_f(x))),
}
_CALC_CONSTS = {"pi": math.pi, "e": math.e}


def _calc_node(node):
    if isinstance(node, ast.Expression):
        return _calc_node(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        # Exact: 3.42 is kept as 342/100, not the float 3.4199999...
        return Fraction(repr(node.value)) if isinstance(node.value, float) else Fraction(node.value)
    if isinstance(node, ast.Name) and node.id.lower() in _CALC_CONSTS:
        return _CALC_CONSTS[node.id.lower()]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _calc_node(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
        a, b = _calc_node(node.left), _calc_node(node.right)
        if isinstance(node.op, ast.Pow):
            return _calc_pow(a, b)
        exact = isinstance(a, Fraction) and isinstance(b, Fraction)
        if not exact:
            a, b = _f(a), _f(b)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if b == 0:
            raise CalcError("division by zero")
        return a / b
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.lower() in _CALC_FUNCS
        and not node.keywords
        and 1 <= len(node.args) <= 2
    ):
        args = [_calc_node(a) for a in node.args]
        return _CALC_FUNCS[node.func.id.lower()](*args)
    raise CalcError(f"not allowed: {type(node).__name__}")


def _calc_pow(a, b):
    if isinstance(b, Fraction) and b.denominator == 1 and isinstance(a, Fraction):
        if abs(b.numerator) > _CALC_MAX_INT_EXP:
            raise CalcError("exponent too large")
        if a == 0 and b < 0:
            raise CalcError("division by zero")
        return a ** b.numerator  # exact
    fa, fb = _f(a), _f(b)
    if fa < 0:
        # Real odd roots of negatives, e.g. (-8)^(1/3) = -2.
        if isinstance(b, Fraction) and b.denominator % 2 == 1:
            return -((-fa) ** fb) if b.numerator % 2 else (-fa) ** fb
        if fb == int(fb):
            return math.pow(fa, fb)
        raise CalcError("negative number to a fractional power")
    try:
        return math.pow(fa, fb)
    except OverflowError:
        raise CalcError("too large")


def evaluate_calc(expr: str):
    """Value of a maths formula as an exact Fraction or a float.
    Raises CalcError (or ValueError etc.) if it isn't plain, valid maths."""
    if not isinstance(expr, str) or not expr.strip() or len(expr) > _CALC_MAX_CHARS:
        raise CalcError("empty or too long")
    text = expr.strip().replace("^", "**").replace("\u2212", "-").replace("\u00d7", "*").replace("\u00f7", "/")
    tree = ast.parse(text, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > _CALC_MAX_NODES:
        raise CalcError("too long")
    value = _calc_node(tree)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise CalcError("not a finite number")
    return value


def _to_decimal(value) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        if isinstance(value, Fraction):
            return Decimal(value.numerator) / Decimal(value.denominator)
        return Decimal(repr(float(value)))


_PLAIN_NUMBER = re.compile(r"^-?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?$")
_PLAIN_FRACTION = re.compile(r"^-?\d+/\d+$")


def format_calc(value, round_to, ai_answer: str):
    """The locally computed value written the way it should be typed, or
    None if it can't be written safely in a form the question wants."""
    ai = (ai_answer or "").strip().replace(" ", "").replace("\u2212", "-")
    commas = "," in ai

    def fixed(places):
        with localcontext() as ctx:
            ctx.prec = 60
            q = _to_decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        if q == 0:
            q = abs(q)  # no "-0.0000"
        return format(q, ",f" if commas else "f")

    if round_to is not None:
        if not isinstance(round_to, int) or not 0 <= round_to <= 12:
            return None
        return fixed(round_to)

    # No rounding asked for: exact answers only.
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return format(value.numerator, "," if commas else "d")
        if _PLAIN_FRACTION.match(ai):
            return f"{value.numerator}/{value.denominator}"
        d = value.denominator
        while d % 2 == 0:
            d //= 2
        while d % 5 == 0:
            d //= 5
        if d == 1:  # ends: 3/8 = 0.375
            return format(_to_decimal(value).normalize(), ",f" if commas else "f")
    # A non-terminating value with no rounding instruction: use as many
    # decimal places as the AI itself wrote.
    if _PLAIN_NUMBER.match(ai) and "." in ai:
        return fixed(len(ai.split(".", 1)[1]))
    return None


def apply_local_math(task):
    """If the task carries a formula, work it out here and use that as the
    answer. Returns (task, note) - note is None when nothing changed."""
    if not LOCAL_MATH or task.input_type != "type_text" or not task.calc:
        return task, None
    ai = task.answer.strip().replace(" ", "").replace("\u2212", "-")
    if not (_PLAIN_NUMBER.match(ai) or _PLAIN_FRACTION.match(ai)) or ai in ("", "-", "."):
        return task, None  # the answer isn't a plain number (an expression, text...)
    try:
        value = evaluate_calc(task.calc)
        local = format_calc(value, task.round_to, task.answer)
    except Exception as e:
        return task, f"could not compute {task.calc!r} ({e}) - keeping the AI's {task.answer!r}"
    if local is None:
        return task, None
    if local == task.answer.strip():
        return task, f"{task.calc} = {local} (AI agrees)"
    return task.model_copy(update={"answer": local}), (
        f"{task.calc} = {local} - AI had {task.answer!r}, using {local!r}"
    )


def task_from_dict(raw) -> Optional[Task]:
    if not isinstance(raw, dict):
        return None

    qid = raw.get("question_id", raw.get("id", raw.get("number", "")))
    question = raw.get("question", raw.get("question_text", ""))
    answer = raw.get("answer", raw.get("final_answer", raw.get("value", "")))
    input_type = raw.get("input_type", raw.get("type", ""))

    if input_type in _TEXT_TYPES:
        input_type = "type_text"
    elif input_type in _OPTION_TYPES:
        input_type = "click_option"

    if input_type not in ("type_text", "click_option"):
        return None

    bbox = raw.get("bbox", raw.get("bounding_box", raw.get("box")))

    # Gemini's native format: box_2d = [ymin, xmin, ymax, xmax].
    box_2d = raw.get("box_2d")
    if bbox is None and isinstance(box_2d, (list, tuple)) and len(box_2d) == 4:
        try:
            ymin, xmin, ymax, xmax = (max(0, min(1000, int(float(v)))) for v in box_2d)
        except (TypeError, ValueError):
            return None
        ymin, ymax = sorted((ymin, ymax))
        xmin, xmax = sorted((xmin, xmax))
        # A box squashed to a line/point (common for tiny controls) is
        # widened by 1 unit so its centre is still usable.
        if xmax == xmin:
            xmax = min(1000, xmin + 1)
            xmin = xmax - 1
        if ymax == ymin:
            ymax = min(1000, ymin + 1)
            ymin = ymax - 1
        bbox = {"x1": xmin, "y1": ymin, "x2": xmax, "y2": ymax}

    if bbox is None and all(k in raw for k in ("x", "y", "width", "height")):
        bbox = {
            "x1": raw["x"],
            "y1": raw["y"],
            "x2": raw["x"] + raw["width"],
            "y2": raw["y"] + raw["height"],
        }

    if not isinstance(bbox, dict):
        return None

    def num(*keys):
        for key in keys:
            if key in bbox:
                return int(float(bbox[key]))
        raise KeyError(keys)

    try:
        bbox_obj = BoundingBox(
            x1=num("x1", "left"),
            y1=num("y1", "top"),
            x2=num("x2", "right"),
            y2=num("y2", "bottom"),
        )
    except Exception:
        return None

    if bbox_obj.x2 <= bbox_obj.x1 or bbox_obj.y2 <= bbox_obj.y1:
        return None

    question = clean_text(question)
    answer = clean_text(answer)
    if not question or not answer:
        return None

    option = clean_text(raw.get("option", raw.get("option_text", ""))) or None
    if input_type == "click_option" and not option:
        option = answer

    part = clean_text(raw.get("part", raw.get("field", raw.get("label", "")))) or None

    calc = clean_text(raw.get("calc", "")) or None
    round_to = raw.get("round_to")
    try:
        round_to = int(round_to) if round_to is not None and str(round_to).strip() != "" else None
    except (TypeError, ValueError):
        round_to = None

    return Task(
        question_id=clean_text(qid) or "unknown",
        question=question,
        answer=answer,
        input_type=input_type,
        bbox=bbox_obj,
        option=option,
        part=part,
        calc=calc if input_type == "type_text" else None,
        round_to=round_to if input_type == "type_text" else None,
    )


def _label_text(text) -> str:
    return _WS.sub(" ", (text or "").strip().lower()).strip(" .:!?")


def blocked_label(task) -> Optional[str]:
    """The label of a click_option task that matches NEVER_CLICK_LABELS,
    else None. Whole-label matches only, so a real option that merely
    mentions a word (e.g. "Check the answer is positive") isn't blocked."""
    if task.input_type != "click_option":
        return None
    for raw in (task.option, task.answer):
        label = _label_text(raw)
        if not label:
            continue
        for pattern in NEVER_CLICK_LABELS:
            try:
                if re.fullmatch(pattern, label, re.I):
                    return (raw or "").strip()
            except re.error:
                continue
    return None


def in_never_click_zone(x, y) -> bool:
    for zone in NEVER_CLICK_ZONES:
        try:
            l, t, r, b = zone
            if l <= x <= r and t <= y <= b:
                return True
        except (TypeError, ValueError):
            continue
    return False


def normalize_solver_json(data) -> SolverResponse:
    if isinstance(data, dict):
        if isinstance(data.get("tasks"), list):
            raw_tasks = data["tasks"]
        elif "question" in data and any(
            k in data for k in ("answer", "final_answer", "value")
        ):
            raw_tasks = [data]
        else:
            raw_tasks = []
            for value in data.values():
                if isinstance(value, list):
                    raw_tasks.extend(value)
    elif isinstance(data, list):
        raw_tasks = data
    else:
        raw_tasks = []

    tasks, seen = [], set()

    for raw in raw_tasks:
        task = task_from_dict(raw)
        if task is None:
            continue

        # input_type is part of the key: a question can legitimately need
        # BOTH a click_option task and one or more type_text tasks (a radio
        # + textbox(es)). "part" further distinguishes multiple separate
        # blanks within the same question/type (e.g. x, y, z boxes) so they
        # don't collapse into a single "duplicate" and get dropped. When the
        # model didn't supply "part", fall back to the bbox so genuinely
        # different controls still aren't merged.
        base = (
            task.question_id.lower(),
            _WS.sub(" ", task.question.lower()),
            task.input_type,
        )
        if task.part:
            key = base + (task.part.strip().lower(),)
        else:
            key = base + ("", (task.bbox.x1, task.bbox.y1, task.bbox.x2, task.bbox.y2))

        if key in seen:
            continue

        blocked = blocked_label(task)
        if blocked:
            print(
                f"[BLOCKED] Ignoring the AI's click on {blocked!r} - it's navigation, "
                f"not an answer (NEVER_CLICK_LABELS)."
            )
            continue

        seen.add(key)
        task, note = apply_local_math(task)
        if note:
            print(f"[MATH] Q{task.question_id}{' ' + task.part if task.part else ''}: {note}")
        tasks.append(task)

    return SolverResponse(tasks=tasks)


def parse_solver_text(text: str) -> SolverResponse:
    clean = normalize_json_text(text)

    if not clean:
        raise ValueError("Could not find valid JSON in AI response.")

    result = normalize_solver_json(json.loads(clean))

    if not result.tasks:
        raise ValueError("Valid JSON contained no usable tasks.")

    return result


def _text_of(item) -> Optional[str]:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("text", "content"):
            if isinstance(item.get(key), str):
                return item[key]
    return None


def extract_openrouter_content(response) -> str:
    try:
        content = response.choices[0].message.content
    except Exception:
        return ""

    if isinstance(content, list):
        return "\n".join(t for t in map(_text_of, content) if t)

    return _text_of(content) or ""


# ============================================================
# IMAGE PREP (done ONCE, in memory, shared by every AI call)
# ============================================================
def prepare_image(screenshot: Image.Image):
    """Return (base64_string, mime_type). Downscaled + compressed in RAM."""
    # Only convert when needed (convert() copies the whole frame even if RGB).
    img = screenshot if screenshot.mode == "RGB" else screenshot.convert("RGB")

    if IMAGE_MAX_WIDTH and img.width > IMAGE_MAX_WIDTH:
        new_h = round(img.height * IMAGE_MAX_WIDTH / img.width)
        # BOX (area-average) is ~3x faster than LANCZOS and stays sharp
        # enough for text when only shrinking; reducing_gap speeds it up more.
        img = img.resize(
            (IMAGE_MAX_WIDTH, new_h), Image.Resampling.BOX, reducing_gap=2.0
        )

    buf = io.BytesIO()

    if IMAGE_FORMAT.upper() == "PNG":
        img.save(buf, "PNG")
        mime = "image/png"
    else:
        img.save(buf, "JPEG", quality=JPEG_QUALITY)
        mime = "image/jpeg"

    return base64.b64encode(buf.getvalue()).decode("ascii"), mime


# ============================================================
# GEMINI
# ============================================================
def enabled_gemini_models():
    """Every Gemini model whose switch is True, in a fixed order."""
    return [
        name
        for name, on in (
            ("gemini-3.1-flash-lite", USE_GEMINI_3_1_FLASH_LITE),
            ("gemini-3.5-flash-lite", USE_GEMINI_3_5_FLASH_LITE),
            ("gemini-3.5-flash", USE_GEMINI_3_5_FLASH),
            ("gemini-3.6-flash", USE_GEMINI_3_6_FLASH),
            ("gemini-3.7-flash", USE_GEMINI_3_7_FLASH),
            ("gemini-3.8-flash", USE_GEMINI_3_8_FLASH),
        )
        if on
    ]


def pick_gemini_model():
    """Return the single enabled Gemini model (first enabled wins), or
    None. Used by plain and race mode. In Consult mode, multiple switches
    being True is normal and expected - use enabled_gemini_models() there
    instead, and this function stays quiet about it."""
    enabled = enabled_gemini_models()
    if len(enabled) > 1 and not GEMINI_CONSULT and not USE_GEMINI_FLASH_CYCLE:
        print(f"[Gemini] WARNING: {len(enabled)} models set to True; using {enabled[0]}.")
    return enabled[0] if enabled else None


GEMINI_MODEL = pick_gemini_model()


def _is_rate_limit_error(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    msg = str(e).lower()
    return code == 429 or any(
        m in msg for m in ("429", "resource_exhausted", "quota", "rate limit", "too_many_requests")
    )


# Models that rejected GEMINI_IMAGE_DETAIL this run (asked once, then skipped).
_no_image_detail = set()


def _looks_like_detail_rejection(e: Exception) -> bool:
    msg = str(e).lower()
    return "resolution" in msg and any(
        m in msg for m in ("400", "invalid", "unsupported", "not supported")
    )


# Models that rejected code execution this run (asked once, then skipped).
_no_code_execution = set()


def _looks_like_tool_rejection(e: Exception) -> bool:
    msg = str(e).lower()
    return any(m in msg for m in ("code_execution", "code execution", "tool")) and any(
        m in msg for m in ("400", "invalid", "unsupported", "not supported", "not enabled")
    )


def _create_interaction(model: str, image_b64: str, mime: str):
    image_part = {"type": "image", "data": image_b64, "mime_type": mime}
    use_detail = bool(GEMINI_IMAGE_DETAIL) and model not in _no_image_detail
    use_code = GEMINI_CODE_EXECUTION and model not in _no_code_execution

    def send():
        part = dict(image_part)
        if use_detail:
            part["resolution"] = GEMINI_IMAGE_DETAIL
        prompt = SOLVER_PROMPT + (CODE_EXECUTION_PROMPT if use_code else "")
        kwargs = dict(
            model=model,
            input=[{"type": "text", "text": prompt}, part],
            generation_config={"thinking_level": GEMINI_THINKING},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": SOLVER_SCHEMA,
            },
        )
        if use_code:
            kwargs["tools"] = [{"type": "code_execution"}]
        return gemini_client.interactions.create(**kwargs)

    # At most one retry per optional feature the model turns out not to
    # support; any other error is raised as normal.
    for _ in range(3):
        try:
            return send()
        except Exception as e:
            if use_detail and _looks_like_detail_rejection(e):
                use_detail = False
                _no_image_detail.add(model)
                print(
                    f"[Gemini:{model}] Doesn't accept image detail "
                    f"'{GEMINI_IMAGE_DETAIL}' - using the default detail from now on."
                )
            elif use_code and _looks_like_tool_rejection(e):
                use_code = False
                _no_code_execution.add(model)
                print(
                    f"[Gemini:{model}] Doesn't support code execution - "
                    f"answering without it from now on."
                )
            else:
                raise
            count_request(model)
    return send()


def _final_json_text(interaction) -> str:
    """With code execution on, a reply is a series of steps (the model's
    notes, its code, the code's output, ...) and only the LAST text is the
    JSON answer. Try the text blocks from last to first, then the whole
    output_text as before."""
    texts = []
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for block in getattr(step, "content", None) or []:
            if getattr(block, "type", None) == "text" and getattr(block, "text", None):
                texts.append(block.text)
    for text in reversed(texts):
        if normalize_json_text(text):
            return text
    return getattr(interaction, "output_text", None) or ""


def _code_runs(interaction) -> int:
    return sum(
        1 for step in (getattr(interaction, "steps", None) or [])
        if getattr(step, "type", None) == "code_execution_call"
    )


def request_gemini(model: str, image_b64: str, mime: str):
    """One request to a SPECIFIC Gemini model. Returns a SolverResponse and
    RAISES on any failure (network, quota, unusable answer)."""
    start = time.perf_counter()
    print(f"[Gemini:{model}] Sending request...")
    count_request(model)
    interaction = _create_interaction(model, image_b64, mime)
    result = parse_solver_text(_final_json_text(interaction))
    runs = _code_runs(interaction)
    ran = f", ran Python {runs}x" if runs else ""
    print(
        f"[Gemini:{model}] Found {len(result.tasks)} question(s) "
        f"in {time.perf_counter() - start:.2f}s{ran}."
    )
    return result


def call_gemini_model(model: str, image_b64: str, mime: str):
    """Like request_gemini, but prints the error and returns None instead of
    raising. Used by plain, race and consult mode."""
    if gemini_client is None:
        print("[Gemini] No API key - put your real key in .env as GEMINI_API_KEY=...")
        return None

    start = time.perf_counter()
    try:
        return request_gemini(model, image_b64, mime)
    except Exception as e:
        print(f"[Gemini:{model}] ERROR: {e}")
        print(f"[Gemini:{model}] Failed after {time.perf_counter() - start:.2f}s.")
        if _is_rate_limit_error(e):
            print(f"  -> Rate limit / quota reached for {model}.")
        return None


# ============================================================
# FLASH CYCLE (see USE_GEMINI_FLASH_CYCLE)
# ============================================================
# model -> epoch time until which it is skipped. Saved (with how many
# requests this program sent each model today) to a small file in the temp
# folder, so a restart doesn't re-ask models that really are used up.
_CYCLE_STATE_FILE = os.path.join(tempfile.gettempdir(), "ai_solver_flash_cycle.json")
_cycle_blocked = {}
_cycle_sent = {}       # model -> requests this program sent it since the last daily reset
_cycle_sent_reset = 0.0  # when _cycle_sent was last cleared (epoch of the reset it counts toward)
_cycle_strikes = {}    # model -> (unclear quota errors in a row, time of the last one)
_cycle_current = None  # last model that answered (for the "switched" message)
_STRIKE_PAUSES = (60, 120, 300, 900)  # growing pause for UNCLEAR quota errors
_STRIKE_FORGET_SECONDS = 1800         # a streak older than this starts over


def _next_daily_reset() -> float:
    """Epoch time of the next midnight Pacific time (when Google resets the
    free daily limits). Uses the real Pacific time zone when Python has it;
    otherwise assumes UTC-8, which at worst brings models back an hour late
    in summer."""
    now = time.time()
    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo("America/Los_Angeles")
        local = datetime.fromtimestamp(now, pacific)
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp()
    except Exception:
        day = 86400
        shifted = now - 8 * 3600
        return (shifted // day + 1) * day + 8 * 3600


def _roll_sent_counts():
    """Start today's request counts from zero after each daily reset."""
    global _cycle_sent_reset
    reset = _next_daily_reset()
    if _cycle_sent_reset != reset:
        _cycle_sent.clear()
        _cycle_sent_reset = reset


def count_request(model: str):
    """Called once per request actually sent to Gemini."""
    _roll_sent_counts()
    _cycle_sent[model] = _cycle_sent.get(model, 0) + 1
    if USE_GEMINI_FLASH_CYCLE:
        _save_cycle_state()


def _load_cycle_state():
    global _cycle_sent_reset
    try:
        with open(_CYCLE_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return  # no file yet / unreadable -> start fresh
    if not isinstance(data, dict):
        return
    blocked = data.get("blocked", {}) if "blocked" in data else data  # older format
    now = time.time()
    for model, until in blocked.items():
        if isinstance(until, (int, float)) and until > now:
            _cycle_blocked[model] = float(until)
    if data.get("sent_reset") == _next_daily_reset() and isinstance(data.get("sent"), dict):
        _cycle_sent.update({m: int(n) for m, n in data["sent"].items()})
        _cycle_sent_reset = data["sent_reset"]


def _save_cycle_state():
    try:
        now = time.time()
        data = {
            "blocked": {m: u for m, u in _cycle_blocked.items() if u > now and u != float("inf")},
            "sent": _cycle_sent,
            "sent_reset": _cycle_sent_reset,
        }
        with open(_CYCLE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def forget_cycle_state():
    """Clear every remembered 'used up' model (RESET_FLASH_CYCLE_ON_START)."""
    _cycle_blocked.clear()
    _cycle_strikes.clear()
    try:
        os.remove(_CYCLE_STATE_FILE)
    except OSError:
        pass


def _reset_time_text(until: float) -> str:
    return time.strftime("%H:%M", time.localtime(until))


def _retry_delay_seconds(msg: str) -> float:
    m = re.search(r"retry in ([0-9.]+)\s*s", msg, re.I) or re.search(
        r"retryDelay['\"]?\s*[:=]\s*['\"]?([0-9.]+)s", msg
    )
    try:
        return min(300.0, max(5.0, float(m.group(1)))) if m else 60.0
    except ValueError:
        return 60.0


_QUOTA_ID = re.compile(r"quotaId['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9_\-]+)")
_QUOTA_LIMIT = re.compile(r"limit:\s*(\d+)")


def describe_quota_error(msg: str):
    """What Google's 429 actually says: (quota_ids, limits, per_day, per_minute)."""
    ids = _QUOTA_ID.findall(msg)
    limits = [int(n) for n in _QUOTA_LIMIT.findall(msg)]
    if ids:
        per_day = any("perday" in q.lower() for q in ids)
        per_minute = any("perminute" in q.lower() for q in ids)
    else:
        low = msg.lower()
        per_day = "perday" in low or "per day" in low
        per_minute = "perminute" in low or "per minute" in low
    return ids, limits, per_day, per_minute


def _cycle_handle_quota(model: str, msg: str) -> str:
    """Decide what a quota (429) error means for this model and return a
    description for the log. A model is only written off until the daily
    reset when GOOGLE SAYS its daily limit is used up. Per-minute and unclear
    errors only pause it for a while."""
    ids, limits, per_day, per_minute = describe_quota_error(msg)
    _roll_sent_counts()
    sent = _cycle_sent.get(model, 0)
    google_said = f"Google: {', '.join(ids)}" if ids else "Google gave no quota name"
    if limits:
        google_said += f", limit {'/'.join(str(n) for n in limits)}"

    if 0 in limits and not per_minute:
        _cycle_blocked[model] = _next_daily_reset()
        _save_cycle_state()
        return (
            f"not available on your free tier ({google_said}). "
            f"Skipped until {_reset_time_text(_cycle_blocked[model])}"
        )

    if per_day and not per_minute:
        _cycle_blocked[model] = _next_daily_reset()
        _cycle_strikes.pop(model, None)
        _save_cycle_state()
        text = (
            f"DAILY limit used up ({google_said}). This program sent it "
            f"{sent} request(s) since the last reset. Back at "
            f"{_reset_time_text(_cycle_blocked[model])}"
        )
        day_limit = max(limits) if limits else 0
        if day_limit and sent < day_limit:
            text += (
                f". NOTE: that's fewer than the {day_limit}/day limit - the rest "
                f"came from something else on the same Google project, or it's "
                f"a problem on Google's side"
            )
        return text

    if per_minute:
        wait = _retry_delay_seconds(msg)
        _cycle_blocked[model] = time.time() + wait
        return f"per-minute limit ({google_said}) - skipped for {wait:.0f}s, NOT used up"

    # Unclear: pause, growing each time it happens again soon after.
    count, last = _cycle_strikes.get(model, (0, 0.0))
    if time.time() - last > _STRIKE_FORGET_SECONDS:
        count = 0
    count += 1
    _cycle_strikes[model] = (count, time.time())
    wait = max(_retry_delay_seconds(msg), _STRIKE_PAUSES[min(count, len(_STRIKE_PAUSES)) - 1])
    _cycle_blocked[model] = time.time() + wait
    return f"quota error Google didn't explain ({google_said}) - skipped for {wait / 60:.0f} min, NOT written off for the day"


def _is_unavailable_error(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    msg = str(e).lower()
    return code in (500, 502, 503, 504) or any(
        m in msg for m in ("503", "unavailable", "overloaded", "internal error")
    )


def _is_unsupported_setting_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "thinking" in msg and any(m in msg for m in ("400", "invalid", "not supported", "unsupported"))


def cycle_order():
    order = list(FLASH_CYCLE_MODELS)
    if FLASH_CYCLE_FALLBACK and FLASH_CYCLE_FALLBACK not in order:
        order.append(FLASH_CYCLE_FALLBACK)
    return order


def cycle_next_model():
    """The model the next press will use (None = everything used up)."""
    now = time.time()
    for model in cycle_order():
        if _cycle_blocked.get(model, 0) <= now:
            return model
    return None


def call_gemini_flash_cycle(image_b64: str, mime: str):
    """Use the first Flash model that isn't used up. If it turns out to be
    used up (or overloaded) the SAME press moves straight on to the next."""
    global _cycle_current

    if gemini_client is None:
        print("[Gemini] No API key - put your real key in .env as GEMINI_API_KEY=...")
        return None

    for model in cycle_order():
        if _cycle_blocked.get(model, 0) > time.time():
            continue
        _cycle_blocked.pop(model, None)

        if _cycle_current and model != _cycle_current:
            print(f"[Flash Cycle] Switched to {model}.")

        start = time.perf_counter()
        try:
            result = request_gemini(model, image_b64, mime)
            _cycle_strikes.pop(model, None)
            _cycle_current = model
            return result

        except Exception as e:
            took = time.perf_counter() - start
            msg = str(e)

            if _is_rate_limit_error(e):
                why = _cycle_handle_quota(model, msg)
                print(f"[Flash Cycle] {model}: {why} ({took:.2f}s). Trying the next model...")
                continue
            if _is_unsupported_setting_error(e):
                _cycle_blocked[model] = float("inf")  # this run only, not saved
                print(
                    f"[Flash Cycle] {model} rejected thinking level "
                    f"'{GEMINI_THINKING}' - skipping it. Trying the next model..."
                )
                continue
            if _is_unavailable_error(e):
                print(f"[Flash Cycle] {model} is overloaded/unavailable right now. Trying the next model...")
                continue

            # Anything else (unusable answer, timeout, bad key): the press
            # fails as usual; the same model is tried again next press.
            print(f"[Gemini:{model}] ERROR: {e}")
            print(f"[Gemini:{model}] Failed after {took:.2f}s.")
            return None

    upcoming = [u for u in _cycle_blocked.values() if u != float("inf")]
    when = f" The first one comes back at {_reset_time_text(min(upcoming))}." if upcoming else ""
    print(f"[Flash Cycle] Every model in the cycle is used up or unavailable.{when}")
    return None


def call_gemini_flash_race(image_b64: str, mime: str):
    """Send the SAME request to both Flash-Lite models at once, use
    whichever answers first with a usable result, and stop waiting for the
    other one. If the first to respond FAILS, falls back to whichever
    finishes next instead of giving up immediately."""
    models = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]

    # NOT a "with" block: leaving a "with ThreadPoolExecutor" always waits
    # for EVERY thread to finish, so the race used to be as slow as the
    # SLOWER model. shutdown(wait=False) lets the loser finish in the
    # background while we move on immediately.
    executor = ThreadPoolExecutor(max_workers=len(models))
    try:
        futures = {executor.submit(call_gemini_model, m, image_b64, mime): m for m in models}

        for future in as_completed(futures):
            model = futures[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"[Gemini:{model} FATAL] {e}")
                result = None

            if result:
                print(f"[Gemini Race] {model} won.")
                return result

        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def call_gemini(image_b64: str, mime: str):
    if USE_GEMINI_FLASH_CYCLE:
        return call_gemini_flash_cycle(image_b64, mime)

    if USE_GEMINI_FLASH_RACE:
        return call_gemini_flash_race(image_b64, mime)

    if GEMINI_MODEL is None:
        print("[Gemini] No model enabled. Set one USE_GEMINI_... option to True.")
        return None

    return call_gemini_model(GEMINI_MODEL, image_b64, mime)


# ============================================================
# OPENROUTER
# ============================================================
def call_openrouter(image_b64: str, mime: str, index: int):
    global _openrouter_json_mode

    tag = f"[OpenRouter #{index}]"

    if openrouter_client is None:
        print(f"{tag} No API key configured.")
        return None

    start = time.perf_counter()

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": SOLVER_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{image_b64}",
                        "detail": "high",  # full-detail vision where supported
                    },
                },
            ],
        }
    ]

    for attempt in range(1, OPENROUTER_RETRIES + 1):
        use_json = _openrouter_json_mode and attempt == 1

        print(f"{tag} Attempt {attempt}/{OPENROUTER_RETRIES}...")

        request = {
            "model": OPENROUTER_MODEL,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 4096,
        }
        if use_json:
            request["response_format"] = {"type": "json_object"}

        try:
            response = openrouter_client.chat.completions.create(**request)
            result = parse_solver_text(extract_openrouter_content(response))

            print(
                f"{tag} Found {len(result.tasks)} question(s) "
                f"in {time.perf_counter() - start:.2f}s "
                f"(model used: {getattr(response, 'model', 'unknown')})."
            )
            return result

        except BadRequestError as e:
            print(f"[OpenRouter #{index} ERROR] {e}")
            if use_json:
                _openrouter_json_mode = False  # remember for future runs

        except Exception as e:
            print(f"[OpenRouter #{index} ERROR] {e}")

    print(f"{tag} Failed after {time.perf_counter() - start:.2f}s.")
    return None


# ============================================================
# CONSENSUS HELPERS
# ============================================================
def answer_key(task: Task) -> str:
    """An answer reduced to its VALUE for voting: case, spaces, a leading
    "x =" and the kind of minus sign don't matter, and plain numbers are
    compared as numbers ("0.5" == "1/2" == ".50")."""
    text = _WS.sub("", (task.answer or "").lower())
    text = text.replace("\u2212", "-").replace("\u2013", "-").rstrip(".")
    text = re.sub(r"^[a-z][a-z0-9_]*=", "", text)
    if re.fullmatch(r"-?(\d+\.?\d*|\.\d+)(/-?\d+)?", text):
        try:
            from fractions import Fraction
            return str(Fraction(text))
        except (ValueError, ZeroDivisionError):
            pass
    return text


def _median(values):
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return round((values[mid - 1] + values[mid]) / 2)


def merge_consensus_bbox(tasks: List[Task]) -> BoundingBox:
    """Median bounding box from the agreeing AIs."""

    def clamp(v):
        return max(0, min(1000, v))

    return BoundingBox(
        x1=clamp(_median(t.bbox.x1 for t in tasks)),
        y1=clamp(_median(t.bbox.y1 for t in tasks)),
        x2=clamp(_median(t.bbox.x2 for t in tasks)),
        y2=clamp(_median(t.bbox.y2 for t in tasks)),
    )


# The screenshot of the current press, so answers from different AIs can be
# matched by WHERE their control is (set in capture_and_solve).
_press_gray = None
_press_size = (1000, 1000)
_point_cache = {}


def _task_point(task: Task):
    """Screen point of a task's control: the snapped centre of the real
    control when one is found, else the centre of the AI's box."""
    key = id(task)
    if key in _point_cache:
        return _point_cache[key]
    w, h = _press_size
    x1, y1, x2, y2 = _task_pixels(task, w, h)
    point = ((x1 + x2) / 2, (y1 + y2) / 2)
    if _press_gray is not None and SNAP_TO_CONTROLS:
        cands = _candidates(_press_gray, task)
        if cands:
            l, t, r, b = cands[0][1]
            point = ((l + r) / 2, (t + b) / 2)
    _point_cache[key] = point
    return point


def _cluster_tasks(results):
    """Group every AI's tasks by the control they point at. Returns a list
    of clusters: {"type": input_type, "entries": [(source, task), ...]}.
    Each AI contributes at most one task per control."""
    w, h = _press_size
    clusters = []
    for source, result in results:
        for task in result.tasks:
            pt = _task_point(task)
            x1, y1, x2, y2 = _task_pixels(task, w, h)
            tol = max(8.0, min(40.0, 0.5 * min(x2 - x1, y2 - y1)))
            best = None
            for c in clusters:
                if c["type"] != task.input_type:
                    continue
                if any(src == source for src, _ in c["entries"]):
                    continue
                d = math.hypot(pt[0] - c["point"][0], pt[1] - c["point"][1])
                if d <= max(tol, c["tol"]) and (best is None or d < best[0]):
                    best = (d, c)
            if best:
                best[1]["entries"].append((source, task))
            else:
                clusters.append({
                    "type": task.input_type, "point": pt, "tol": tol,
                    "entries": [(source, task)],
                })
    return clusters


def _answer_votes(entries):
    """[(votes, [(source, task), ...]), ...] most votes first."""
    groups = {}
    for source, task in entries:
        groups.setdefault(answer_key(task), []).append((source, task))
    return sorted(((len(g), g) for g in groups.values()), key=lambda v: -v[0])


def _majority_qid(entries):
    ids = [t.question_id for _, t in entries]
    return max(set(ids), key=ids.count)


def _decide(results):
    """Voting. Returns a list of (cluster, winning_entries or None, reason)."""
    clusters = _cluster_tasks(results)
    decisions = []

    # Text boxes: most common answer VALUE wins - if it has at least
    # CONSENSUS_MIN votes AND more than any other answer (a tie = no fill).
    for c in clusters:
        if c["type"] != "type_text":
            continue
        votes = _answer_votes(c["entries"])
        top, winners = votes[0]
        runner_up = votes[1][0] if len(votes) > 1 else 0
        if top >= CONSENSUS_MIN and top > runner_up:
            decisions.append((c, winners, "ACCEPTED"))
        elif top > runner_up:
            decisions.append((c, None, "REJECTED (not enough agreement)"))
        else:
            decisions.append((c, None, "REJECTED (tie - the AIs disagree)"))

    # Options: each vote is "click THIS one". For a radio question (every AI
    # picked at most one option) only the clear winner is clicked; for a
    # "select all that apply" question each option is judged on its own.
    by_q = {}
    for c in clusters:
        if c["type"] == "click_option":
            by_q.setdefault(_majority_qid(c["entries"]), []).append(c)
    for qid, opts in by_q.items():
        picks = {}
        for c in opts:
            for src, _ in c["entries"]:
                picks[src] = picks.get(src, 0) + 1
        multi_select = any(n > 1 for n in picks.values())
        opts.sort(key=lambda c: -len(c["entries"]))
        for i, c in enumerate(opts):
            n = len(c["entries"])
            if n < CONSENSUS_MIN:
                decisions.append((c, None, "REJECTED (not enough agreement)"))
            elif multi_select:
                decisions.append((c, c["entries"], "ACCEPTED"))
            elif i == 0 and (len(opts) == 1 or n > len(opts[1]["entries"])):
                decisions.append((c, c["entries"], "ACCEPTED"))
            elif i == 0:
                decisions.append((c, None, "REJECTED (tie - the AIs picked different options)"))
            else:
                decisions.append((c, None, "REJECTED (another option got more votes)"))
    return decisions


def consensus_is_settled(results) -> bool:
    """True when at least one control exists and EVERY control seen so far
    already has an accepted answer - used to stop waiting for slower AIs.
    (Losing options of a radio question don't count against it.)"""
    decisions = _decide(results)
    if not decisions:
        return False
    return all(
        winners is not None or reason.startswith("REJECTED (another option")
        for _, winners, reason in decisions
    )


def sort_tasks(tasks: List[Task]) -> List[Task]:
    # Within the same question, click_option runs before type_text: some
    # forms only enable/reset their text boxes after an option is picked.
    type_order = {"click_option": 0, "type_text": 1}

    def sort_key(task):
        match = _DIGITS.search(task.question_id)
        qpart = (0, int(match.group()), "") if match else (1, 0, task.question_id.lower())
        return qpart + (type_order.get(task.input_type, 2), (task.part or "").lower())

    return sorted(tasks, key=sort_key)


def consensus_tasks(results):
    print("\n" + "=" * 70)
    print("CONSENSUS")
    print("=" * 70)

    final_tasks = []
    for cluster, winners, reason in _decide(results):
        entries = cluster["entries"]
        shown = winners or _answer_votes(entries)[0][1]
        first = shown[0][1]
        label = f"Question {first.question_id}" + (f" ({first.part})" if first.part else "")
        if cluster["type"] == "click_option":
            print(f"\n{label}: option '{first.option or first.answer}'")
            print(f"  Votes:  {len(entries)}/{len(results)} ({', '.join(s for s, _ in entries)})")
        else:
            print(f"\n{label}: box")
            for n, group in _answer_votes(entries):
                forms = [t.answer for _, t in group]
                print(f"  {max(set(forms), key=forms.count)!r}: {n} vote(s) ({', '.join(s for s, _ in group)})")
        print(f"  -> {reason}")

        if winners:
            forms = [t.answer for _, t in winners]
            chosen = winners[0][1].model_copy(update={
                "answer": max(set(forms), key=forms.count),  # most common way of writing it
                "bbox": merge_consensus_bbox([t for _, t in winners]),
            })
            final_tasks.append(chosen)

    return sort_tasks(final_tasks)


# ============================================================
# UI ACTIONS
# ============================================================
def control_center(task: Task, width: int, height: int):
    if task.click:
        return int(task.click[0]), int(task.click[1])
    x = (task.bbox.x1 + task.bbox.x2) / 2.0 / 1000.0 * width
    y = (task.bbox.y1 + task.bbox.y2) / 2.0 / 1000.0 * height
    return int(x), int(y)


# ============================================================
# SNAP TO THE REAL CONTROL (see SNAP_TO_CONTROLS)
# ============================================================
# Works on a grayscale copy of the screenshot that was sent to the AI.
# A control is found by starting from a "seed" pixel and walking outwards
# while the colour stays the same (the empty inside of a box or circle); the
# walk stops at the control's border. The result is only accepted if it
# really looks like a box (4 straight, continuous borders) or a small
# round/square option, so plain page background or text never qualifies.
_SNAP_TOLERANCE = 24      # grey-level difference that counts as an edge
_RECT_BORDER_MIN = 0.9    # share of each border side that must be an edge
_OPTION_MIN_RADIUS = 4    # px - smaller "holes" are letters like o, e, a
_OPTION_MAX_RADIUS = 30   # px


class _Gray:
    def __init__(self, image: Image.Image):
        g = image if image.mode == "L" else image.convert("L")
        self.w, self.h = g.size
        self.buf = g.tobytes()

    def px(self, x, y):
        return self.buf[y * self.w + x]


def _walk(g: _Gray, x, y, dx, dy, base, limit):
    """Steps from (x, y) in direction (dx, dy) that stay the same colour as
    base, or None if no edge is found within `limit` pixels / the screen."""
    buf, w, h, tol = g.buf, g.w, g.h, _SNAP_TOLERANCE
    for n in range(limit):
        nx, ny = x + dx * (n + 1), y + dy * (n + 1)
        if nx < 0 or ny < 0 or nx >= w or ny >= h:
            return None
        if abs(buf[ny * w + nx] - base) > tol:
            return n
    return None


def _share_different(g: _Gray, points, base):
    points = list(points)
    if not points:
        return 0.0
    tol = _SNAP_TOLERANCE
    return sum(1 for x, y in points if abs(g.px(x, y) - base) > tol) / len(points)


def _share_same(g: _Gray, points, base):
    points = list(points)
    if not points:
        return 0.0
    tol = _SNAP_TOLERANCE
    return sum(1 for x, y in points if abs(g.px(x, y) - base) <= tol) / len(points)


def _find_box(g: _Gray, sx, sy, max_w, max_h):
    """An empty text box around seed (sx, sy): (left, top, right, bottom)
    of its inside, or None."""
    base = g.px(sx, sy)
    l = _walk(g, sx, sy, -1, 0, base, max_w)
    r = _walk(g, sx, sy, 1, 0, base, max_w)
    u = _walk(g, sx, sy, 0, -1, base, max_h)
    d = _walk(g, sx, sy, 0, 1, base, max_h)
    if None in (l, r, u, d):
        return None
    left, right, top, bottom = sx - l, sx + r, sy - u, sy + d
    w, h = right - left + 1, bottom - top + 1
    if w < 8 or h < 8 or w > max_w or h > max_h:
        return None
    if left < 1 or top < 1 or right >= g.w - 1 or bottom >= g.h - 1:
        return None

    # Check the middle 80% of each side (skips rounded corners).
    xs = range(left + w // 10, right - w // 10 + 1)
    ys = range(top + h // 10, bottom - h // 10 + 1)
    borders = (
        [(x, top - 1) for x in xs],
        [(x, bottom + 1) for x in xs],
        [(left - 1, y) for y in ys],
        [(right + 1, y) for y in ys],
    )
    if any(_share_different(g, side, base) < _RECT_BORDER_MIN for side in borders):
        return None
    # ...and that the inside edges are clean (not text that happened to
    # stop the walk).
    insides = (
        [(x, top) for x in xs],
        [(x, bottom) for x in xs],
        [(left, y) for y in ys],
        [(right, y) for y in ys],
    )
    if any(_share_same(g, side, base) < _RECT_BORDER_MIN for side in insides):
        return None
    # ...and that the box is EMPTY all the way through. A box that already
    # has an answer in it must never be snapped to: Ctrl+A would replace
    # that answer. The only mark allowed inside is a text caret: one thin,
    # tall vertical line.
    marks = [
        (x, y)
        for y in range(top + 1, bottom, 2)
        for x in range(left + 1, right)
        if abs(g.px(x, y) - base) > _SNAP_TOLERANCE
    ]
    if marks:
        mxs = [x for x, _ in marks]
        mys = [y for _, y in marks]
        is_caret = max(mxs) - min(mxs) <= 2 and max(mys) - min(mys) >= 0.5 * h
        if not is_caret:
            return None
    return (left, top, right, bottom)


def _find_option(g: _Gray, sx, sy):
    """An empty radio circle / checkbox around seed (sx, sy): its inside as
    (left, top, right, bottom), or None."""
    base = g.px(sx, sy)
    lim = _OPTION_MAX_RADIUS
    axis = [_walk(g, sx, sy, dx, dy, base, lim) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
    diag = [_walk(g, sx, sy, dx, dy, base, lim) for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1))]
    if None in axis or None in diag:
        return None
    l, r, u, d = axis
    rx, ry = (l + r + 1) / 2, (u + d + 1) / 2          # radii through the centre
    if min(rx, ry) < _OPTION_MIN_RADIUS or max(rx, ry) > _OPTION_MAX_RADIUS:
        return None
    if max(rx, ry) / min(rx, ry) > 1.35:               # must be round / square
        return None
    # Diagonals: circle ~ same as the radius, square ~ 1.41x. Anything far
    # outside that (a gap in the border, a letter) is rejected.
    radius = (rx + ry) / 2
    for dist in diag:
        if dist is None or not (0.45 * radius <= (dist + 1) * 1.414 <= 1.9 * radius):
            return None
    return (sx - l, sy - u, sx + r, sy + d)


def _task_pixels(task: Task, width: int, height: int):
    x1 = task.bbox.x1 / 1000 * width
    x2 = task.bbox.x2 / 1000 * width
    y1 = task.bbox.y1 / 1000 * height
    y2 = task.bbox.y2 / 1000 * height
    return x1, y1, x2, y2


def _candidates(g: _Gray, task: Task):
    """Every real control near the AI's box, nearest first:
    list of (distance_px, (left, top, right, bottom))."""
    x1, y1, x2, y2 = _task_pixels(task, g.w, g.h)
    pw, ph = max(1.0, x2 - x1), max(1.0, y2 - y1)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    is_option = task.input_type == "click_option"

    if is_option:
        margin = max(16.0, min(60.0, 1.0 * max(pw, ph)))
        find = lambda sx, sy: _find_option(g, sx, sy)
    else:
        margin = max(8.0, min(60.0, 0.5 * max(pw, ph)))
        max_w = int(max(3 * pw, pw + 80))
        max_h = int(max(3 * ph, ph + 50))
        min_w, min_h = max(8.0, 0.3 * pw), max(8.0, 0.3 * ph)

        def find(sx, sy):
            rect = _find_box(g, sx, sy, max_w, max_h)
            if rect is None:
                return None
            w, h = rect[2] - rect[0] + 1, rect[3] - rect[1] + 1
            return rect if (w >= min_w and h >= min_h) else None

    rx1, ry1 = max(0, int(x1 - margin)), max(0, int(y1 - margin))
    rx2, ry2 = min(g.w - 1, int(x2 + margin)), min(g.h - 1, int(y2 + margin))

    # Seeds: the AI's centre first, then a grid over the search area.
    seeds = [(min(g.w - 1, max(0, int(cx))), min(g.h - 1, max(0, int(cy))))]
    step = max(3, int(min(pw, ph, 3 * margin) / 3))
    seeds += [(x, y) for y in range(ry1, ry2 + 1, step) for x in range(rx1, rx2 + 1, step)]

    found = {}
    for sx, sy in seeds:
        if any(l <= sx <= r and t <= sy <= b for l, t, r, b in found):
            continue  # already inside a control we found
        rect = find(sx, sy)
        if rect is not None and rect not in found:
            mx, my = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            found[rect] = math.hypot(mx - cx, my - cy)
        if found and (sx, sy) == seeds[0]:
            break  # the AI's own centre is inside a real control: done

    return sorted((dist, rect) for rect, dist in found.items())


def snap_tasks(tasks: List[Task], screenshot: Image.Image):
    """Returns (tasks, deferred). Tasks come back with `click` set to the
    exact centre of the real control when one was found. With
    PREVENT_SAME_BOX, a text answer that would share a box with another is
    moved to `deferred` (done on the next press) instead of overwriting it."""
    if not tasks:
        return tasks, []

    g = _Gray(screenshot) if SNAP_TO_CONTROLS else None
    width, height = screenshot.size

    cands = {i: (_candidates(g, t) if g is not None else []) for i, t in enumerate(tasks)}

    # Hand out controls nearest-first; a text box goes to one answer only.
    assigned, taken = {}, set()
    pairs = sorted(
        (dist, i, rect) for i, lst in cands.items() for dist, rect in lst
    )
    for dist, i, rect in pairs:
        if i in assigned:
            continue
        is_text = tasks[i].input_type == "type_text"
        if is_text and PREVENT_SAME_BOX and rect in taken:
            continue
        assigned[i] = rect
        if is_text:
            taken.add(rect)

    out, deferred, used_points = [], [], []
    for i, task in enumerate(tasks):
        rect = assigned.get(i)
        if rect is not None:
            click = [round((rect[0] + rect[2]) / 2), round((rect[1] + rect[3]) / 2)]
        else:
            click = list(control_center(task, width, height))

        if task.input_type == "type_text" and PREVENT_SAME_BOX:
            inside_taken = rect is None and any(
                l <= click[0] <= r and t <= click[1] <= b for l, t, r, b in taken
            )
            too_close = any(math.hypot(click[0] - px, click[1] - py) < 6 for px, py in used_points)
            if inside_taken or too_close:
                print(
                    f"[SNAP] Q{task.question_id} part {task.part}: would land in a box "
                    f"another answer is using - leaving it for the next press."
                )
                deferred.append(task)
                continue
            used_points.append(tuple(click))

        if SNAP_TO_CONTROLS:
            ax, ay = control_center(task, width, height)
            if rect is not None:
                print(
                    f"[SNAP] Q{task.question_id} {task.part or task.input_type}: "
                    f"found the control, moved {click[0] - ax:+d},{click[1] - ay:+d} px"
                )
            else:
                print(
                    f"[SNAP] Q{task.question_id} {task.part or task.input_type}: "
                    f"no clear control found - using the AI's position"
                )
        out.append(task.model_copy(update={"click": click}))

    return out, deferred


def copy_to_clipboard(text: str) -> bool:
    """Copy to the OS clipboard and CONFIRM it actually landed before
    returning, retrying if not. A transient clipboard failure (common on
    Windows with a clipboard manager, antivirus, etc. briefly holding it)
    otherwise pastes stale or empty content with no visible error."""
    for attempt in range(1, CLIPBOARD_COPY_RETRIES + 1):
        try:
            pyperclip.copy(text)
            time.sleep(CLIPBOARD_SETTLE_SECONDS)
            if pyperclip.paste() == text:
                return True
        except Exception as e:
            print(f"[CLIPBOARD] copy attempt {attempt} failed: {e}")
        if attempt < CLIPBOARD_COPY_RETRIES:
            time.sleep(CLIPBOARD_SETTLE_SECONDS)

    print(
        f"[CLIPBOARD] Could not confirm the clipboard after "
        f"{CLIPBOARD_COPY_RETRIES} attempts - pasting anyway."
    )
    return False


def perform_task(task: Task, width: int, height: int):
    x, y = control_center(task, width, height)
    _clicked_points.append((x, y))

    print("\n" + "-" * 60)
    print(f"Question: {task.question}")
    print(f"Answer:   {task.answer}")
    print(f"Type:     {task.input_type}")
    print(f"Part:     {task.part}")
    print(f"Control:  ({x}, {y})")
    print(f"Option:   {task.option}")

    if TEST_MODE:
        print("TEST_MODE=True -> moving cursor only. No click/type.")
        pyautogui.moveTo(x, y, duration=0)
        return

    if task.input_type == "click_option":
        pyautogui.moveTo(x, y, duration=MOVE_DURATION_SECONDS)  # real glide, not a teleport
        pyautogui.click(x, y)

        if DOUBLE_CLICK_OPTIONS:
            time.sleep(CLICK_SETTLE_SECONDS)
            pyautogui.click(x, y)  # reinforcement, not a native double-click

    else:  # type_text
        # Clipboard first: copy_to_clipboard reads it back to confirm the
        # text really landed, so it's ready before we even reach the box
        # (Ctrl+A / clicks don't touch the clipboard).
        copy_to_clipboard(task.answer)
        pyautogui.moveTo(x, y, duration=MOVE_DURATION_SECONDS)  # real glide, not a teleport
        pyautogui.click(x, y)

        if DOUBLE_CLICK_TEXTBOXES:
            time.sleep(CLICK_SETTLE_SECONDS)
            pyautogui.click(x, y)  # 2 separate clicks, not a native double-click:
            # some web front-ends don't focus the field on a synthetic
            # doubleClick() event, which is what causes Ctrl+A to select the
            # WHOLE PAGE instead of just the box's contents.

        time.sleep(CLICK_SETTLE_SECONDS)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(CLIPBOARD_SETTLE_SECONDS)  # let select-all register first
        pyautogui.hotkey("ctrl", "v")


def build_tone_wav(notes_hz, notes_ms, volume) -> bytes:
    """Synthesize a short run of soft bell-like notes as an in-memory WAV."""
    rate = 44100
    samples = []

    for hz, ms in zip(notes_hz, notes_ms):
        n = int(rate * ms / 1000)
        attack = max(1, int(rate * 0.004))  # 4 ms fade-in: no click
        for i in range(n):
            t = i / rate
            decay = math.exp(-t * 14.0)      # bell-like fade-out
            fade_in = min(1.0, i / attack)
            wave_val = math.sin(2 * math.pi * hz * t) + 0.25 * math.sin(4 * math.pi * hz * t)
            samples.append(wave_val / 1.25 * decay * fade_in)

    samples.extend([0.0] * int(rate * 0.03))  # tiny tail of silence

    peak = 32767 * max(0.0, min(1.0, volume))
    frames = b"".join(struct.pack("<h", int(v * peak)) for v in samples)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def build_chime_wav() -> bytes:
    return build_tone_wav(PING_NOTES_HZ, PING_NOTE_MS, PING_VOLUME)


def build_error_wav() -> bytes:
    return build_tone_wav(ERROR_NOTES_HZ, ERROR_NOTE_MS, ERROR_VOLUME)


_SOUND_BUILDERS = {"done": build_chime_wav, "error": build_error_wav}
_sound_wavs = {}    # kind -> WAV bytes (built once)
_sound_files = {}   # kind -> temp .wav path (Mac/Linux players need a file)


def _sound_bytes(kind: str) -> bytes:
    if kind not in _sound_wavs:
        _sound_wavs[kind] = _SOUND_BUILDERS[kind]()
    return _sound_wavs[kind]


def _sound_file(kind: str) -> str:
    """The sound as a .wav file on disk, written once per run."""
    if kind == "done" and PING_SOUND_FILE and os.path.isfile(PING_SOUND_FILE):
        return PING_SOUND_FILE
    path = _sound_files.get(kind)
    if path is None or not os.path.isfile(path):
        path = os.path.join(tempfile.gettempdir(), f"ai_solver_{kind}.wav")
        with open(path, "wb") as f:
            f.write(_sound_bytes(kind))
        _sound_files[kind] = path
    return path


def prepare_sounds():
    """Build the sounds at startup so the first press doesn't pay for it."""
    if not PLAY_SOUNDS:
        return
    try:
        for kind in _SOUND_BUILDERS:
            if winsound is not None:
                _sound_bytes(kind)
            else:
                _sound_file(kind)
    except Exception:
        pass


def play_sound(kind: str):
    """kind = "done" (rising chime) or "error" (low falling tone).
    Runs in the background so it never delays anything."""
    if not PLAY_SOUNDS:
        return

    def _play():
        try:
            if winsound is not None:  # Windows
                if kind == "done" and PING_SOUND_FILE and os.path.isfile(PING_SOUND_FILE):
                    winsound.PlaySound(PING_SOUND_FILE, winsound.SND_FILENAME)
                else:
                    # SND_MEMORY can't be combined with SND_ASYNC, so this
                    # blocks - fine, we're already in a background thread.
                    winsound.PlaySound(_sound_bytes(kind), winsound.SND_MEMORY)
                return

            player = None
            if sys.platform == "darwin" and shutil.which("afplay"):
                player = ["afplay"]
            elif shutil.which("paplay"):
                player = ["paplay"]
            elif shutil.which("aplay"):
                player = ["aplay", "-q"]

            if player:
                subprocess.Popen(
                    player + [_sound_file(kind)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                print("\a", end="", flush=True)  # terminal bell as a last resort
        except Exception:
            pass  # no sound device / bad file -> silently skip

    threading.Thread(target=_play, daemon=True).start()


def play_ping():
    play_sound("done")


def play_error():
    play_sound("error")


# ============================================================
# AI DISPATCH
# ============================================================
def gather_parallel(executor, jobs):
    """Common as_completed + early-exit + non-blocking-shutdown collection,
    shared by multi-AI (Gemini+OpenRouter) mode and Gemini Consult mode.
    `jobs` maps {future: source_name}."""
    results = []

    try:
        for future in as_completed(jobs):
            source = jobs[future]

            try:
                result = future.result()
            except Exception as e:
                print(f"[{source} FATAL] {e}")
                result = None

            if not result:
                continue

            results.append((source, result))

            if (
                EARLY_EXIT
                and len(results) >= CONSENSUS_MIN
                and len(results) < len(jobs)
                and consensus_is_settled(results)
            ):
                print(
                    f"\n[EARLY EXIT] All questions already agreed by "
                    f"{len(results)} source(s); not waiting for the rest."
                )
                break
    finally:
        # Don't block on stragglers; drop anything not started yet.
        executor.shutdown(wait=False, cancel_futures=True)

    return results


def consult_asks():
    """[(label, model), ...] - every enabled model, GEMINI_CONSULT_REPEAT times."""
    repeat = max(1, int(GEMINI_CONSULT_REPEAT))
    asks = []
    for m in enabled_gemini_models():
        for i in range(1, repeat + 1):
            asks.append((f"{m} #{i}" if repeat > 1 else m, m))
    return asks


def gather_results(image_b64: str, mime: str):
    """
    Launch AI calls in parallel and collect usable results.
    - GEMINI_ONLY + GEMINI_CONSULT: every ENABLED Gemini model is queried
      and must reach the same agreement multi-AI mode requires - no
      OpenRouter involved.
    - GEMINI_ONLY (plain or race): a single Gemini request.
    - Otherwise: Gemini + OpenRouter in parallel, and (if EARLY_EXIT)
      stop waiting once every question already has agreement.
    """
    if GEMINI_ONLY and GEMINI_CONSULT:
        asks = consult_asks()
        if len(asks) < 2:
            print(
                f"[Gemini Consult] Only {len(asks)} answer(s) per press; need at "
                f"least 2 - enable another model or raise GEMINI_CONSULT_REPEAT."
            )
        executor = ThreadPoolExecutor(max_workers=max(1, len(asks)))
        jobs = {executor.submit(call_gemini_model, m, image_b64, mime): label for label, m in asks}
        return gather_parallel(executor, jobs)

    if GEMINI_ONLY:
        # One request: run it right here, no thread pool overhead.
        try:
            result = call_gemini(image_b64, mime)
        except Exception as e:
            print(f"[Gemini FATAL] {e}")
            result = None
        return [("Gemini", result)] if result else []

    executor = ThreadPoolExecutor(max_workers=1 + OPENROUTER_CALLS)

    jobs = {executor.submit(call_gemini, image_b64, mime): "Gemini"}

    for i in range(1, OPENROUTER_CALLS + 1):
        jobs[executor.submit(call_openrouter, image_b64, mime, i)] = (
            f"OpenRouter #{i}"
        )

    return gather_parallel(executor, jobs)


# ============================================================
# MAIN RUN
# ============================================================
def print_final(final_tasks):
    print("\n" + "=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)

    for i, task in enumerate(final_tasks, 1):
        print(f"\nQUESTION {i}")
        print("-" * 50)
        print(f"Question:   {task.question}")
        print(f"ANSWER:     {task.answer}")
        print(f"INPUT TYPE: {task.input_type}")
        print(f"PART:       {task.part}")
        print(f"OPTION:     {task.option}")
        if task.calc:
            rounding = "exact" if task.round_to is None else f"{task.round_to} decimal places"
            print(f"CALC:       {task.calc}  ({rounding})")
        print(
            f"BBOX:       {task.bbox.x1},{task.bbox.y1} -> "
            f"{task.bbox.x2},{task.bbox.y2}"
        )


def reading_order(tasks: List[Task]) -> List[Task]:
    """Sort controls the way a person reads the page: top-to-bottom by line,
    left-to-right within a line. Two controls are on the same line when one's
    vertical centre falls inside the other's box (so boxes of slightly
    different heights on one line still group together)."""
    by_y = sorted(tasks, key=lambda t: ((t.bbox.y1 + t.bbox.y2) / 2, t.bbox.x1))
    rows = []
    for task in by_y:
        yc = (task.bbox.y1 + task.bbox.y2) / 2
        if rows and rows[-1][0].bbox.y1 <= yc <= rows[-1][0].bbox.y2:
            rows[-1].append(task)
        else:
            rows.append([task])
    return [t for row in rows for t in sorted(row, key=lambda t: t.bbox.x1)]


def plan_actions(final_tasks: List[Task]):
    """Decide WHAT to do this press and in WHICH order.
    Returns (actions, deferred):
      - A question with several text boxes and no option to select: every
        box is filled now.
      - A question whose option is being selected THIS press and that has
        several blanks: the option plus its FIRST blank now, the other
        blanks deferred to the next press (if ONE_TEXT_BOX_PER_PRESS).
      - Within a question, option clicks always come before its blanks.
      - With FILL_BOTTOM_TO_TOP, questions are done bottom-up and blanks
        right-to-left, so a box that grows as it's typed into can't shift a
        box that hasn't been filled yet."""
    page_pos = {id(t): i for i, t in enumerate(reading_order(final_tasks))}

    groups = {}
    for task in final_tasks:
        groups.setdefault(task.question_id, []).append(task)

    planned, deferred = [], []
    for qtasks in groups.values():
        options = reading_order([t for t in qtasks if t.input_type == "click_option"])
        texts = reading_order([t for t in qtasks if t.input_type == "type_text"])

        if ONE_TEXT_BOX_PER_PRESS and options and len(texts) > 1:
            deferred.extend(texts[1:])
            texts = texts[:1]

        if FILL_BOTTOM_TO_TOP:
            texts = texts[::-1]

        top = min(page_pos[id(t)] for t in qtasks)
        planned.append((top, options + texts))

    planned.sort(key=lambda item: item[0], reverse=FILL_BOTTOM_TO_TOP)
    actions = [t for _, group in planned for t in group]
    return actions, deferred


def perform_tasks_with_gap(tasks, width: int, height: int):
    """Act on each task, pausing TASK_GAP_SECONDS between controls (not
    after the last one) so the page has time to settle before the next
    click - skipped in TEST_MODE."""
    for i, task in enumerate(tasks):
        perform_task(task, width, height)
        if not TEST_MODE and i < len(tasks) - 1:
            time.sleep(TASK_GAP_SECONDS)


def uses_single_result():
    """True when a solve just takes the one result directly (plain Gemini
    or race mode); False whenever multiple sources need to agree first
    (Consult mode, or Gemini+OpenRouter multi-AI mode)."""
    return GEMINI_ONLY and not GEMINI_CONSULT


def mark_clicks(shot: Image.Image, points):
    """Draw a small red ring at every spot clicked this press."""
    try:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(shot)
        for x, y in points:
            draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline=(230, 0, 0), width=2)
    except Exception as e:
        print(f"[SCREENSHOT] Could not mark clicks: {e}")


def save_screenshot(screenshot: Image.Image):
    """Save this exact screenshot to SCREENSHOT_FOLDER under a unique,
    timestamped name. Never raises: a failed save is reported but never
    stops the solve itself."""
    try:
        os.makedirs(SCREENSHOT_FOLDER, exist_ok=True)  # recreate if deleted
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        millis = int((time.time() % 1) * 1000)
        base = f"screenshot_{stamp}-{millis:03d}"
        path = os.path.join(SCREENSHOT_FOLDER, base + ".png")
        n = 1
        while os.path.exists(path):  # two presses in the same millisecond
            path = os.path.join(SCREENSHOT_FOLDER, f"{base}_{n}.png")
            n += 1
        screenshot.save(path, "PNG", compress_level=1)  # fast, still lossless
        print(f"Saved screenshot: {path}")
        return path
    except Exception as e:
        print(f"[SCREENSHOT] Could not save screenshot: {e}")
        return None


def capture_and_solve():
    """Screenshot -> AI -> final task list for THIS moment of the page.
    Returns (final_tasks, results, width, height, timing_dict, screenshot).
    Called once
    per hotkey press - every press starts from a fresh screenshot."""
    t0 = time.perf_counter()
    screenshot = pyautogui.screenshot()
    width, height = screenshot.size
    t_capture = time.perf_counter() - t0

    t0 = time.perf_counter()
    image_b64, mime = prepare_image(screenshot)
    t_image = time.perf_counter() - t0

    print(
        f"Screen resolution: {width} x {height} | "
        f"upload size: {len(image_b64) / 1024:.0f} KB ({mime})"
    )

    global _press_gray, _press_size
    _point_cache.clear()
    _press_size = (width, height)
    _press_gray = _Gray(screenshot) if (SNAP_TO_CONTROLS and not uses_single_result()) else None

    t0 = time.perf_counter()
    results = gather_results(image_b64, mime)
    t_ai = time.perf_counter() - t0

    print(f"\nReceived {len(results)} usable AI result(s).")

    if uses_single_result():
        final_tasks = sort_tasks(results[0][1].tasks) if results else []
    else:
        final_tasks = consensus_tasks(results) if len(results) >= CONSENSUS_MIN else []

    timing = {"capture": t_capture, "image": t_image, "ai": t_ai}
    return final_tasks, results, width, height, timing, screenshot


def run_solver():
    if not busy_lock.acquire(blocking=False):
        print("\n[BUSY] A solve is already running.")
        return

    start_time = time.perf_counter()
    emergency_stop = False

    try:
        if GEMINI_ONLY and GEMINI_CONSULT:
            mode_label = "GEMINI CONSULT"
        elif GEMINI_ONLY and USE_GEMINI_FLASH_CYCLE:
            mode_label = f"GEMINI FLASH CYCLE - {cycle_next_model() or 'all used up'}"
        elif GEMINI_ONLY and USE_GEMINI_FLASH_RACE:
            mode_label = "GEMINI FLASH RACE"
        elif GEMINI_ONLY:
            mode_label = "GEMINI ONLY"
        else:
            mode_label = "MULTI-AI CONSENSUS"

        print("\n" + "=" * 70)
        print(f"CAPTURING SCREEN  (mode: {mode_label})")
        print("=" * 70)

        _clicked_points.clear()
        final_tasks, results, width, height, timing, screenshot = capture_and_solve()

        if uses_single_result():
            if not results:
                print("\nERROR: Gemini returned no usable result.")
                print("Nothing was clicked or typed.")
                play_error()
                return
        else:
            if len(results) < CONSENSUS_MIN:
                print("\nERROR:")
                print("Not enough independent AI results for safe consensus.")
                print("Nothing was clicked or typed.")
                play_error()
                return
            if not final_tasks:
                print("\nNo question reached AI agreement.")
                print("Nothing was clicked or typed.")
                play_error()
                return

        print_final(final_tasks)

        t0 = time.perf_counter()
        final_tasks, same_box = snap_tasks(final_tasks, screenshot)
        t_snap = time.perf_counter() - t0

        if NEVER_CLICK_ZONES:
            kept = []
            for t in final_tasks:
                x, y = control_center(t, width, height)
                if in_never_click_zone(x, y):
                    print(
                        f"[BLOCKED] Q{t.question_id} {t.part or t.input_type} at ({x}, {y}) "
                        f"is inside NEVER_CLICK_ZONES - not clicked."
                    )
                else:
                    kept.append(t)
            final_tasks = kept

        t0 = time.perf_counter()

        if TEST_MODE:
            # Preview EVERY position, including blanks that a real run would
            # leave for the next press.
            actions, deferred = plan_actions(final_tasks)
            actions = actions + deferred
            deferred = []
        else:
            actions, deferred = plan_actions(final_tasks)
        deferred = deferred + same_box

        if not actions and not deferred:
            print("\nNothing left to click or type (everything was blocked or skipped).")
            play_error()
            return

        perform_tasks_with_gap(actions, width, height)

        if deferred:
            labels = ", ".join(t.part or t.question_id for t in deferred)
            print(
                f"\n[NEXT] {len(deferred)} more box(es) remaining ({labels}). "
                f"Press {HOTKEY.upper()} again to continue."
            )

        t_act = time.perf_counter() - t0

        play_ping()  # answers are filled in

        elapsed = time.perf_counter() - start_time

        print("\n" + "=" * 70)
        print("DONE")
        print("=" * 70)
        print(f"Questions accepted:  {len(final_tasks)}")
        print(f"AI results received: {len(results)}")
        print(f"Total time:          {elapsed:.2f} seconds")
        print(
            f"  capture {timing['capture'] * 1000:.0f}ms | image prep {timing['image'] * 1000:.0f}ms | "
            f"AI {timing['ai']:.2f}s | snap {t_snap * 1000:.0f}ms | actions {t_act * 1000:.0f}ms"
        )
        print("=" * 70)

    except pyautogui.FailSafeException:
        emergency_stop = True
        print("\n[EMERGENCY STOP] Mouse moved to the top-left corner.")
        print(f"Time before stop: {time.perf_counter() - start_time:.2f} seconds")

    except Exception as e:
        print(f"\n[FATAL ERROR] {type(e).__name__}: {e}")
        print(f"Time before error: {time.perf_counter() - start_time:.2f} seconds")
        play_error()

    finally:
        try:
            # Taken AFTER the solve and the clicking/typing, so it shows the
            # result. Skipped on an emergency stop so nothing more happens.
            if SAVE_SCREENSHOTS and not emergency_stop:
                time.sleep(SCREENSHOT_DELAY_SECONDS)
                shot = pyautogui.screenshot()
                if MARK_CLICKS_ON_SCREENSHOTS:
                    mark_clicks(shot, _clicked_points)
                save_screenshot(shot)
        except Exception as e:
            print(f"[SCREENSHOT] Could not take the final screenshot: {e}")
        finally:
            busy_lock.release()


# ============================================================
# CONNECTION WARM-UP
# ============================================================
def warm_up():
    """Best-effort: open pooled connections in the background. Errors ignored."""

    def _gemini(model):
        try:
            gemini_client.models.get(model=model)
        except Exception:
            pass

    def _openrouter():
        try:
            openrouter_client.models.list()
        except Exception:
            pass

    if gemini_client is not None:
        if GEMINI_CONSULT:
            warm_models = enabled_gemini_models()
        elif USE_GEMINI_FLASH_CYCLE:
            warm_models = [cycle_next_model()]
        elif USE_GEMINI_FLASH_RACE:
            warm_models = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
        else:
            warm_models = [GEMINI_MODEL]
        for model in warm_models:
            if model:
                threading.Thread(target=_gemini, args=(model,), daemon=True).start()
    if openrouter_client is not None:
        threading.Thread(target=_openrouter, daemon=True).start()


# ============================================================
# START
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("SCREEN MATH SOLVER")
    print("=" * 70)
    print(f"Hotkey:      {HOTKEY}")
    print(f"Test mode:   {TEST_MODE}")
    print(f"Gemini only: {GEMINI_ONLY}")
    if GEMINI_CONSULT:
        asks = consult_asks()
        print(
            f"Gemini:      CONSULT MODE - {' + '.join(l for l, _ in asks) or 'NO MODELS ENABLED'} "
            f"(thinking: {GEMINI_THINKING}; majority of {len(asks)}, at least {CONSENSUS_MIN} must agree)"
        )
    elif USE_GEMINI_FLASH_CYCLE:
        if RESET_FLASH_CYCLE_ON_START:
            forget_cycle_state()
            print("Gemini:      Flash Cycle memory cleared (RESET_FLASH_CYCLE_ON_START).")
        _load_cycle_state()
        print(f"Gemini:      FLASH CYCLE - {' -> '.join(cycle_order())} (thinking: {GEMINI_THINKING})")
        now = time.time()
        for m in cycle_order():
            sent = _cycle_sent.get(m, 0)
            if _cycle_blocked.get(m, 0) > now:
                print(
                    f"             {m}: skipped until {_reset_time_text(_cycle_blocked[m])} "
                    f"({sent} request(s) sent today)"
                )
            elif sent:
                print(f"             {m}: {sent} request(s) sent today")
        print(f"             starting with: {cycle_next_model() or 'NONE - all used up for today'}")
    elif USE_GEMINI_FLASH_RACE:
        print(f"Gemini:      RACE MODE - gemini-3.1-flash-lite vs gemini-3.5-flash-lite (thinking: {GEMINI_THINKING})")
    else:
        print(f"Gemini:      {GEMINI_MODEL} (thinking: {GEMINI_THINKING})")
    maths = []
    if LOCAL_MATH:
        maths.append("worked out on this PC (LOCAL_MATH)")
    if GEMINI_CODE_EXECUTION:
        maths.append("Python in Google's sandbox (slow)")
    print(f"Maths:       {' + '.join(maths) or 'AI only'}")
    if not GEMINI_ONLY:
        print(f"OpenRouter:  {OPENROUTER_MODEL} x{OPENROUTER_CALLS}")
    print()
    print(f"Press {HOTKEY.upper()} to capture and analyze the current screen.")
    print("Emergency stop: move the mouse to the TOP-LEFT corner.")
    print("=" * 70)

    if key_is_placeholder("GEMINI_API_KEY"):
        print("WARNING: GEMINI_API_KEY in .env is still the placeholder ENTER_KEY_HERE.")
        print("         Replace it with your real key (Google AI Studio -> Get API key).")
    elif not GEMINI_API_KEY:
        print("WARNING: GEMINI_API_KEY is not set. Add GEMINI_API_KEY=your_key to .env")
        print("         next to this script.")
    if not GEMINI_ONLY and not OPENROUTER_API_KEY:
        if key_is_placeholder("OPENROUTER_API_KEY"):
            print("WARNING: OPENROUTER_API_KEY in .env is still a placeholder.")
        else:
            print("WARNING: GEMINI_ONLY is off but OPENROUTER_API_KEY is not set.")
    if GEMINI_CONSULT and len(consult_asks()) < 2:
        print("WARNING: GEMINI_CONSULT needs at least 2 answers per press - turn on another")
        print("         model or set GEMINI_CONSULT_REPEAT to 2 or more.")
    elif USE_GEMINI_FLASH_CYCLE and not cycle_order():
        print("WARNING: USE_GEMINI_FLASH_CYCLE is on but FLASH_CYCLE_MODELS is empty.")
    elif (not GEMINI_CONSULT and not USE_GEMINI_FLASH_CYCLE
          and not USE_GEMINI_FLASH_RACE and GEMINI_MODEL is None):
        print("WARNING: no Gemini model enabled (all USE_GEMINI_... are False).")

    prepare_sounds()  # built before the first press

    if WARM_UP_CONNECTIONS:
        warm_up()

    # Each press runs in its own thread, so the keyboard hook is never
    # blocked while the AI is thinking, and a second press during a solve
    # is refused with [BUSY] instead of queueing up a second full solve.
    keyboard.add_hotkey(
        HOTKEY, lambda: threading.Thread(target=run_solver, daemon=True).start()
    )

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\nExiting.")
