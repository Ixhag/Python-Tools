import base64
import io
import json
import math
import os
import re
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

# ---------------- QUICK SWITCHES ----------------
# Save a screenshot of the screen after every press (answers already filled
# in) into the "tempscreenshots" folder next to this script. More settings
# for this further down (search for SCREENSHOT_FOLDER).
SAVE_SCREENSHOTS = False

# Sounds: a chime when the answers are filled in, and a low "error" tone when
# the AI can't give a solution (no answer, no agreement, or an error).
# False = completely silent. Sound settings further down (search PING_).
PLAY_SOUNDS = True

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

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
# If both are True, Consult wins.
USE_GEMINI_FLASH_RACE = False
GEMINI_CONSULT = False

OPENROUTER_MODEL = "openrouter/free"

HOTKEY = "ctrl+alt+s"

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
REQUEST_TIMEOUT = 45

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

# Pause after finishing one radio/checkbox option before clicking the next
# one, so the page has time to process a blur/focus change. Only used when
# a single press selects more than one option; not applied to text boxes,
# since only ever one is filled per press (see ONE_TEXT_BOX_PER_PRESS).
TASK_GAP_SECONDS = 0.1

# A question with a radio/checkbox option AND several text blanks (e.g.
# "the solution is x=_, y=_, z=_") is filled in ACROSS MULTIPLE PRESSES:
# one press selects the option and fills the FIRST blank, then stops -
# press the hotkey again for each remaining blank. Each press re-screenshots
# from scratch anyway, so this sidesteps a real problem for free: filling in
# one blank can resize it and shift the others (a box widening to fit what
# was typed, a row reflowing once an option is picked), which made trying
# to fill several blanks in one pass unreliable. A question with only ONE
# control (a single text box, or a plain option with no blanks) is
# unaffected and still finishes in a single press. Set False to always
# attempt every blank in one press with their original positions.
ONE_TEXT_BOX_PER_PRESS = True

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

# type_text: click the field TWICE, as two separate clicks with a short
# pause between them (not one native double-click - see perform_task for
# why), before Ctrl+A selects everything and the answer is pasted in.
DOUBLE_CLICK_TEXTBOXES = True

# Optional: path to your OWN .wav file to play instead of the built-in chime
# (Windows plays .wav only), e.g. r"C:\Sounds\ping.wav". Leave "" for the chime.
PING_SOUND_FILE = ""

# Built-in chime: two soft rising notes (a gentle "messaging app" style ding).
PING_NOTES_HZ = (988, 1319)      # first note, second note
PING_NOTE_MS = (90, 220)         # how long each note rings
PING_VOLUME = 0.45               # 0.0 - 1.0 (both sounds)

# Error tone, played when the AI can't give a solution: two LOWER notes
# going DOWN, so it's easy to tell apart from the "done" chime by ear.
ERROR_NOTES_HZ = (440, 294)
ERROR_NOTE_MS = (150, 320)

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
# (Turn this on/off with SAVE_SCREENSHOTS at the top of the file.)
# Short wait before that final screenshot, so the page has finished showing
# the typed answers / selected options.
SCREENSHOT_DELAY_SECONDS = 0.3
SCREENSHOT_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tempscreenshots"
)

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

busy_lock = threading.Lock()

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


class SolverResponse(BaseModel):
    tasks: List[Task] = Field(default_factory=list)


# Computed once instead of on every request.
SOLVER_SCHEMA = SolverResponse.model_json_schema()

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
  itself (bbox = ONLY the small circle/checkbox graphic itself, tightly -
  see the bbox rule below for why).
- Emit ONE SEPARATE task with input_type "type_text" for EVERY individual
  blank inside that option - never merge two blanks into one task and
  never put two answers in one "answer" string (e.g. never "x=1, y=2").
  If the option reads "x = [ ], y = [ ], z = [ ]", that is 3 separate
  type_text tasks, each with its own tiny bbox around just that one blank
  and its own single value in "answer" (e.g. "5", not "x=5").
- ALL tasks that belong to the same question (the click_option task and
  every type_text task) share the SAME question_id, and should carry a
  "part" naming which piece they are:
    click_option task: part = null
    each blank: part = the variable/label immediately before it if visible
      (e.g. "x", "y", "z", "r"), otherwise "1", "2", "3"... in the order
      the blanks appear (left-to-right, top-to-bottom).
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
- ONLY identify an actual answer field or actual answer option.
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
- answer: the single value for THIS control only (one option's text, or
  one blank's value). Never combine multiple blanks' values into one.
- input_type:
    "type_text" for a text/math answer box
    "click_option" for a multiple-choice/radio/checkbox option
- bbox: bounding box of the ACTUAL, SINGLE answer control, NOT the whole
  question and NOT a whole row containing several blanks.
  For click_option: bbox ONLY the small circle/checkbox graphic itself -
  TIGHT around just that icon, NOT the option's label text, and NOT the
  whole row. The circle is a real, always-clickable native control; the
  text label next to it is NOT guaranteed to be clickable on every site,
  so a click that lands on the letter or words instead of the circle can
  silently fail to select anything.
  For type_text: just the one input box itself.
  Coordinates MUST be normalized to 0..1000:
    x1 = left, y1 = top, x2 = right, y2 = bottom.
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

Before returning, verify:
A. Every task is visibly present.
B. The answer is mathematically correct.
C. Each bbox is on exactly ONE actual answer control, never a whole row
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
      "answer": "The solution is x = _, y = _, and z = _.",
      "input_type": "click_option",
      "bbox": {"x1": 30, "y1": 335, "x2": 44, "y2": 355},
      "option": "A. The solution is x = , y = , and z = .",
      "part": null
    },
    {
      "question_id": "1",
      "question": "Solve the system. Select the correct choice.",
      "answer": "4",
      "input_type": "type_text",
      "bbox": {"x1": 140, "y1": 335, "x2": 155, "y2": 350},
      "option": null,
      "part": "x"
    },
    {
      "question_id": "1",
      "question": "Solve the system. Select the correct choice.",
      "answer": "-3",
      "input_type": "type_text",
      "bbox": {"x1": 175, "y1": 335, "x2": 190, "y2": 350},
      "option": null,
      "part": "y"
    }
  ]
}

A simple question with exactly one control looks like this instead:
{
  "tasks": [
    {
      "question_id": "2",
      "question": "...",
      "answer": "...",
      "input_type": "type_text",
      "bbox": {"x1": 400, "y1": 500, "x2": 520, "y2": 550},
      "option": null,
      "part": null
    }
  ]
}
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

    return Task(
        question_id=clean_text(qid) or "unknown",
        question=question,
        answer=answer,
        input_type=input_type,
        bbox=bbox_obj,
        option=option,
        part=part,
    )


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

        seen.add(key)
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
    if len(enabled) > 1 and not GEMINI_CONSULT:
        print(f"[Gemini] WARNING: {len(enabled)} models set to True; using {enabled[0]}.")
    return enabled[0] if enabled else None


GEMINI_MODEL = pick_gemini_model()


def _is_rate_limit_error(e: Exception) -> bool:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    msg = str(e).lower()
    return code == 429 or any(
        m in msg for m in ("429", "resource_exhausted", "quota", "rate limit", "too_many_requests")
    )


def call_gemini_model(model: str, image_b64: str, mime: str):
    """One request to a SPECIFIC Gemini model. Returns a SolverResponse or
    None. Shared by the normal single-model path and the flash-race path."""
    if gemini_client is None:
        print("[Gemini] No API key configured.")
        return None

    start = time.perf_counter()

    try:
        print(f"[Gemini:{model}] Sending request...")

        interaction = gemini_client.interactions.create(
            model=model,
            input=[
                {"type": "text", "text": SOLVER_PROMPT},
                {"type": "image", "data": image_b64, "mime_type": mime},
            ],
            generation_config={"thinking_level": GEMINI_THINKING},
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": SOLVER_SCHEMA,
            },
        )

        result = parse_solver_text(interaction.output_text or "")

        print(
            f"[Gemini:{model}] Found {len(result.tasks)} question(s) "
            f"in {time.perf_counter() - start:.2f}s."
        )
        return result

    except Exception as e:
        print(f"[Gemini:{model}] ERROR: {e}")
        print(f"[Gemini:{model}] Failed after {time.perf_counter() - start:.2f}s.")
        if _is_rate_limit_error(e):
            print(f"  -> Rate limit / quota reached for {model}.")
        return None


def call_gemini_flash_race(image_b64: str, mime: str):
    """Send the SAME request to both Flash-Lite models at once, use
    whichever answers first with a usable result, and stop waiting for the
    other one. If the first to respond FAILS, falls back to whichever
    finishes next instead of giving up immediately."""
    models = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]

    with ThreadPoolExecutor(max_workers=len(models)) as executor:
        futures = {executor.submit(call_gemini_model, m, image_b64, mime): m for m in models}

        winning_result = None
        for future in as_completed(futures):
            model = futures[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"[Gemini:{model} FATAL] {e}")
                result = None

            if result:
                print(f"[Gemini Race] {model} won.")
                winning_result = result
                break

        # Don't block on the loser; it's fine if it finishes in the
        # background after we've already moved on.
        executor.shutdown(wait=False, cancel_futures=True)

        return winning_result


def call_gemini(image_b64: str, mime: str):
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
                    "image_url": {"url": f"data:{mime};base64,{image_b64}"},
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
    return _WS.sub(" ", task.answer.lower()).strip()


def question_text_key(task: Task) -> str:
    return _WS.sub(" ", task.question.lower()).strip()


def question_group_key(task: Task) -> str:
    # input_type + part keep a question's click_option task and its various
    # type_text blanks (x, y, z...) in separate consensus lanes, instead of
    # one crowding out another as "disagreement" or merging distinct blanks.
    part = (task.part or "").strip().lower()
    return f"{question_text_key(task)}|{task.input_type}|{part}"


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


def _group_by_question(results):
    groups = {}

    for source, result in results:
        seen = set()
        for task in result.tasks:
            qkey = question_group_key(task)
            if not question_text_key(task) or qkey in seen:
                continue
            seen.add(qkey)
            groups.setdefault(qkey, []).append((source, task))

    return groups


def _best_answer_group(entries):
    """Largest set of entries that agree on (answer, input_type)."""
    answer_groups = {}
    for source, task in entries:
        key = (answer_key(task), task.input_type)
        answer_groups.setdefault(key, []).append((source, task))
    return max(answer_groups.values(), key=len)


def consensus_is_settled(results) -> bool:
    """
    True when at least one question exists AND every question seen so far
    already has CONSENSUS_MIN agreeing AIs. Used to stop waiting early.
    """
    groups = _group_by_question(results)
    if not groups:
        return False
    return all(
        len(_best_answer_group(entries)) >= CONSENSUS_MIN
        for entries in groups.values()
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

    for entries in _group_by_question(results).values():
        best = _best_answer_group(entries)
        chosen = best[0][1]

        print(f"\nQuestion {chosen.question_id}:")
        print(f"  Answer:    {chosen.answer}")
        print(f"  Type:      {chosen.input_type}")
        print(f"  Agreement: {len(best)}/{len(results)}")
        print("  Sources: " + ", ".join(source for source, _ in best))

        if len(best) >= CONSENSUS_MIN:
            chosen = chosen.model_copy(
                update={"bbox": merge_consensus_bbox([t for _, t in best])}
            )
            print("  -> ACCEPTED")
            final_tasks.append(chosen)
        else:
            print("  -> REJECTED (not enough agreement)")

    return sort_tasks(final_tasks)


# ============================================================
# UI ACTIONS
# ============================================================
def control_center(task: Task, width: int, height: int):
    x = (task.bbox.x1 + task.bbox.x2) / 2.0 / 1000.0 * width
    y = (task.bbox.y1 + task.bbox.y2) / 2.0 / 1000.0 * height
    return int(x), int(y)


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
        copy_to_clipboard(task.answer)
        time.sleep(CLIPBOARD_SETTLE_SECONDS)  # let the clipboard write commit
        pyautogui.hotkey("ctrl", "v")


def build_chime_wav(notes_hz=None, notes_ms=None) -> bytes:
    """Synthesize a soft chime (one bell-like tone per note) as an
    in-memory WAV - no sound files needed."""
    notes_hz = notes_hz or PING_NOTES_HZ
    notes_ms = notes_ms or PING_NOTE_MS
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

    peak = 32767 * max(0.0, min(1.0, PING_VOLUME))
    frames = b"".join(struct.pack("<h", int(v * peak)) for v in samples)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


_sound_cache = {}      # "done"/"error" -> WAV bytes
_sound_file_cache = {}  # "done"/"error" -> temp .wav path (Mac playback)


def _sound_wav(kind: str) -> bytes:
    if kind not in _sound_cache:
        if kind == "error":
            _sound_cache[kind] = build_chime_wav(ERROR_NOTES_HZ, ERROR_NOTE_MS)
        else:
            _sound_cache[kind] = build_chime_wav(PING_NOTES_HZ, PING_NOTE_MS)
    return _sound_cache[kind]


def _sound_path(kind: str) -> str:
    """Write the sound to a temp .wav once, for players that need a file."""
    if kind not in _sound_file_cache:
        fd, path = tempfile.mkstemp(prefix=f"solver_{kind}_", suffix=".wav")
        with os.fdopen(fd, "wb") as f:
            f.write(_sound_wav(kind))
        _sound_file_cache[kind] = path
    return _sound_file_cache[kind]


def play_sound(kind: str):
    """Play the "done" chime or the "error" tone in the background so it
    never delays anything. Silent when PLAY_SOUNDS is False."""
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
                    winsound.PlaySound(_sound_wav(kind), winsound.SND_MEMORY)
            elif sys.platform == "darwin":  # Mac: built-in afplay command
                path = PING_SOUND_FILE if (kind == "done" and PING_SOUND_FILE and os.path.isfile(PING_SOUND_FILE)) else _sound_path(kind)
                subprocess.run(["afplay", path], check=False)
            else:
                print("\a", end="", flush=True)  # terminal bell (Linux)
        except Exception:
            pass  # no sound device / bad file -> silently skip

    threading.Thread(target=_play, daemon=True).start()


def play_ping():
    """Answers are filled in."""
    play_sound("done")


def play_error_sound():
    """The AI couldn't give a solution."""
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
        models = enabled_gemini_models()
        if len(models) < 2:
            print(
                f"[Gemini Consult] Only {len(models)} model(s) enabled; "
                f"need at least 2 to consult."
            )
        executor = ThreadPoolExecutor(max_workers=max(1, len(models)))
        jobs = {executor.submit(call_gemini_model, m, image_b64, mime): m for m in models}
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
        print(
            f"BBOX:       {task.bbox.x1},{task.bbox.y1} -> "
            f"{task.bbox.x2},{task.bbox.y2}"
        )


def select_text_tasks_to_perform(text_tasks):
    """Which text boxes to fill THIS press. A blank that shares its
    question_id with other blanks (a multi-part answer like x=_, y=_, z=_)
    has real reflow risk - filling one can shift the others - so only the
    FIRST one for that question is included; the rest wait for the next
    press. A box that's the ONLY blank for its question is independent of
    every other box on the page, so it's always included alongside every
    other independent box - there's no reason to make those wait for each
    other one at a time."""
    if not ONE_TEXT_BOX_PER_PRESS:
        return list(text_tasks), []

    counts = {}
    for t in text_tasks:
        counts[t.question_id] = counts.get(t.question_id, 0) + 1

    selected, deferred, started_multi = [], [], set()
    for t in text_tasks:
        if counts[t.question_id] == 1:
            selected.append(t)  # standalone box: always safe to do now
        elif t.question_id not in started_multi:
            selected.append(t)  # first blank of a multi-part answer this press
            started_multi.add(t.question_id)
        else:
            deferred.append(t)  # a later blank of a multi-part answer: wait

    return selected, deferred


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
    Returns (final_tasks, results, width, height, timing_dict). Used both
    for the initial solve and, if needed, for a fresh re-solve after
    clicking a radio option (see run_solver: selecting an option can
    reflow the page, shifting where its text boxes actually are - so their
    positions from the FIRST screenshot can go stale)."""
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

    t0 = time.perf_counter()
    results = gather_results(image_b64, mime)
    t_ai = time.perf_counter() - t0

    print(f"\nReceived {len(results)} usable AI result(s).")

    if uses_single_result():
        final_tasks = sort_tasks(results[0][1].tasks) if results else []
    else:
        final_tasks = consensus_tasks(results) if len(results) >= CONSENSUS_MIN else []

    timing = {"capture": t_capture, "image": t_image, "ai": t_ai}
    return final_tasks, results, width, height, timing


def run_solver():
    if not busy_lock.acquire(blocking=False):
        print("\n[BUSY] A solve is already running.")
        return

    start_time = time.perf_counter()
    emergency_stop = False

    try:
        if GEMINI_ONLY and GEMINI_CONSULT:
            mode_label = "GEMINI CONSULT"
        elif GEMINI_ONLY and USE_GEMINI_FLASH_RACE:
            mode_label = "GEMINI FLASH RACE"
        elif GEMINI_ONLY:
            mode_label = "GEMINI ONLY"
        else:
            mode_label = "MULTI-AI CONSENSUS"

        print("\n" + "=" * 70)
        print(f"CAPTURING SCREEN  (mode: {mode_label})")
        print("=" * 70)

        final_tasks, results, width, height, timing = capture_and_solve()

        if uses_single_result():
            if not results:
                print("\nERROR: Gemini returned no usable result.")
                print("Nothing was clicked or typed.")
                play_error_sound()
                return
        else:
            if len(results) < CONSENSUS_MIN:
                print("\nERROR:")
                print("Not enough independent AI results for safe consensus.")
                print("Nothing was clicked or typed.")
                play_error_sound()
                return
            if not final_tasks:
                print("\nNo question reached AI agreement.")
                print("Nothing was clicked or typed.")
                play_error_sound()
                return

        print_final(final_tasks)

        t0 = time.perf_counter()

        option_tasks = [t for t in final_tasks if t.input_type == "click_option"]
        text_tasks = [t for t in final_tasks if t.input_type == "type_text"]

        # Radio/checkbox options are clicked FIRST - nothing has been typed
        # yet, so their positions are still accurate.
        if option_tasks:
            perform_tasks_with_gap(option_tasks, width, height)

        # Text boxes: independent single-blank questions are all done
        # together (filling one can't shift a completely different
        # question); a multi-blank question only gets its FIRST blank this
        # press (see select_text_tasks_to_perform for why). A single lone
        # text box still finishes in this same press either way.
        if text_tasks:
            selected, deferred = select_text_tasks_to_perform(text_tasks)
            if TEST_MODE:
                perform_tasks_with_gap(text_tasks, width, height)  # preview every position
            else:
                perform_tasks_with_gap(selected, width, height)
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
            f"AI {timing['ai']:.2f}s | actions {t_act * 1000:.0f}ms"
        )
        print("=" * 70)

    except pyautogui.FailSafeException:
        emergency_stop = True
        print("\n[EMERGENCY STOP] Mouse moved to the top-left corner.")
        print(f"Time before stop: {time.perf_counter() - start_time:.2f} seconds")

    except Exception as e:
        print(f"\n[FATAL ERROR] {type(e).__name__}: {e}")
        print(f"Time before error: {time.perf_counter() - start_time:.2f} seconds")
        play_error_sound()

    finally:
        try:
            # Taken AFTER the solve and the clicking/typing, so it shows the
            # result. Skipped on an emergency stop so nothing more happens.
            if SAVE_SCREENSHOTS and not emergency_stop:
                time.sleep(SCREENSHOT_DELAY_SECONDS)
                save_screenshot(pyautogui.screenshot())
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
        consult_models = enabled_gemini_models()
        print(f"Gemini:      CONSULT MODE - {' + '.join(consult_models) or 'NO MODELS ENABLED'} (thinking: {GEMINI_THINKING})")
    elif USE_GEMINI_FLASH_RACE:
        print(f"Gemini:      RACE MODE - gemini-3.1-flash-lite vs gemini-3.5-flash-lite (thinking: {GEMINI_THINKING})")
    else:
        print(f"Gemini:      {GEMINI_MODEL} (thinking: {GEMINI_THINKING})")
    if not GEMINI_ONLY:
        print(f"OpenRouter:  {OPENROUTER_MODEL} x{OPENROUTER_CALLS}")
    print()
    print(f"Press {HOTKEY.upper()} to capture and analyze the current screen.")
    print("Emergency stop: move the mouse to the TOP-LEFT corner.")
    print("=" * 70)

    if not GEMINI_API_KEY:
        print("WARNING: GEMINI_API_KEY is not set.")
    if GEMINI_CONSULT and len(enabled_gemini_models()) < 2:
        print("WARNING: GEMINI_CONSULT is on but fewer than 2 models are enabled - turn on at least 2.")
    elif not GEMINI_CONSULT and not USE_GEMINI_FLASH_RACE and GEMINI_MODEL is None:
        print("WARNING: no Gemini model enabled (all USE_GEMINI_... are False).")

    if PLAY_SOUNDS:
        _sound_wav("done")   # build both sounds now so the first one
        _sound_wav("error")  # plays instantly

    if WARM_UP_CONNECTIONS:
        warm_up()

    keyboard.add_hotkey(HOTKEY, run_solver)

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\nExiting.")