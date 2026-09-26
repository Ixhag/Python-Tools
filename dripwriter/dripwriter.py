import os
import random
import subprocess
import sys
from time import monotonic, sleep

import keyboard

# ============================================================
# SETTINGS
# ============================================================

# Play a short beep when the text has finished typing.
PLAY_SOUND = True

# Typing speed, in words per minute. Each run picks a random speed in this
# range, so no two runs type at exactly the same pace.
MIN_WPM = 40
MAX_WPM = 70

# Chance (0.0 - 1.0) that any single letter is mistyped. The typo is
# usually noticed after 0-2 more letters, deleted with Backspace, and
# retyped correctly. Set to 0 to never make typos.
TYPO_CHANCE = 0.05

# Pauses, in seconds (a random value between the two numbers is used).
PAUSE_AFTER_SENTENCE = (1.0, 2.5)   # after . ! ?
PAUSE_AFTER_COMMA = (0.3, 1.0)      # after , ; :
PAUSE_AFTER_NEWLINE = (1.5, 5.0)    # after pressing Enter
PAUSE_NOTICE_TYPO = (0.2, 1.0)      # "wait, that's wrong" before Backspace

# Now and then the writer stops to "think" before a word.
THINK_CHANCE = 0.05                 # chance per word
THINK_PAUSE = (3.0, 6.0)

# Key to hold to stop typing early.
STOP_KEY = "escape"

# ============================================================

# Neighbouring keys on a QWERTY keyboard, used to make realistic typos.
NEIGHBOURS = {
    "q": "wa", "w": "qes", "e": "wrd", "r": "etf", "t": "ryg", "y": "tuh",
    "u": "yij", "i": "uok", "o": "ipl", "p": "ol", "a": "qsz", "s": "awdx",
    "d": "sefc", "f": "drgv", "g": "fthb", "h": "gyjn", "j": "hukm",
    "k": "jil", "l": "kop", "z": "asx", "x": "zsdc", "c": "xdfv",
    "v": "cfgb", "b": "vghn", "n": "bhjm", "m": "njk",
}


class Stopped(Exception):
    """Raised when the user holds the stop key."""


def pause(seconds):
    """Sleep, but keep checking the stop key so it responds instantly even
    during a long pause."""
    end = monotonic() + seconds
    while True:
        if keyboard.is_pressed(STOP_KEY):
            raise Stopped
        left = end - monotonic()
        if left <= 0:
            return
        sleep(min(left, 0.02))


def type_char(char):
    if keyboard.is_pressed(STOP_KEY):
        raise Stopped
    keyboard.write(char)


def backspace():
    if keyboard.is_pressed(STOP_KEY):
        raise Stopped
    keyboard.send("backspace")


def typo_for(char):
    """A plausible wrong key for this letter (a neighbouring key), keeping
    its upper/lower case. None if the character shouldn't get a typo."""
    near = NEIGHBOURS.get(char.lower())
    if not near:
        return None
    wrong = random.choice(near)
    return wrong.upper() if char.isupper() else wrong


def beep():
    """Short beep when typing finishes. Never raises."""
    if not PLAY_SOUND:
        return
    try:
        if sys.platform == "win32":
            import winsound
            winsound.Beep(880, 200)
        elif sys.platform == "darwin":
            sound = "/System/Library/Sounds/Glass.aiff"
            if os.path.exists(sound):
                subprocess.run(["afplay", sound], check=False)
            else:
                print("\a", end="", flush=True)
        else:
            print("\a", end="", flush=True)
    except Exception:
        pass


def type_like_a_human(text):
    """Type `text` with human-like rhythm: a random base speed, faster
    bursts inside words, pauses between words and sentences, occasional
    thinking pauses, and occasional typos that get corrected."""
    wpm = random.uniform(MIN_WPM, MAX_WPM)
    base = 60.0 / (wpm * 5)  # seconds per character (a "word" = 5 characters)
    print(f"(Typing at about {wpm:.0f} words per minute. Hold {STOP_KEY.upper()} to stop.)")

    word_speed = 1.0
    i = 0
    while i < len(text):
        char = text[i]
        at_word_start = i == 0 or text[i - 1] in " \n\t"

        # Each word gets its own speed: some come out in a quick burst,
        # others more slowly. Sometimes the writer stops to think first.
        if at_word_start and not char.isspace():
            word_speed = random.choice((0.6, 0.8, 1.0, 1.0, 1.3))
            if random.random() < THINK_CHANCE:
                pause(random.uniform(*THINK_PAUSE))

        # Occasionally hit a neighbouring key instead, keep going for a
        # letter or two before noticing, then Backspace and fix it.
        wrong = typo_for(char) if random.random() < TYPO_CHANCE else None
        if wrong:
            type_char(wrong)
            pause(base * random.uniform(0.5, 1.2))

            # Typed past the mistake before noticing it, within the same word.
            extra = 0
            for _ in range(random.randint(0, 2)):
                nxt = i + 1 + extra
                if nxt >= len(text) or not text[nxt].isalpha():
                    break
                type_char(text[nxt])
                pause(base * random.uniform(0.5, 1.2))
                extra += 1

            pause(random.uniform(*PAUSE_NOTICE_TYPO))
            for _ in range(extra + 1):
                backspace()
                pause(base * random.uniform(0.3, 0.6))
            # Fall through and type the correct character now.

        type_char(char)

        # How long to wait before the next character.
        if char in ".!?":
            delay = random.uniform(*PAUSE_AFTER_SENTENCE)
        elif char in ",;:":
            delay = random.uniform(*PAUSE_AFTER_COMMA)
        elif char == "\n":
            delay = random.uniform(*PAUSE_AFTER_NEWLINE)
        elif char == " ":
            delay = base * random.uniform(1.0, 2.5)
        else:
            delay = base * word_speed * random.uniform(0.5, 1.5)
        pause(delay)

        i += 1


def read_text():
    """Read pasted text until Enter is pressed twice. Returns None to exit."""
    print("Paste your text below, then press Enter twice when done:\n")
    lines = []
    while True:
        line = input()
        if line.strip().lower() == "exit" and not lines:
            return None
        if line == "" and lines and lines[-1] == "":
            break
        lines.append(line)
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def dripwriter():
    print("======== Drip Writer ========")
    print("Type 'exit' at any time to quit.")
    print(f"Hold '{STOP_KEY}' to stop a typing sequence.\n")

    while True:
        text = read_text()
        if text is None:
            print("Goodbye!")
            return
        if not text.strip():
            print("No text entered. Try again.\n")
            continue

        print("\nReady! Click into your target window, then press BACKSPACE to start typing...")
        keyboard.wait("backspace")
        sleep(0.5)

        try:
            type_like_a_human(text)
            print("\n[Done]")
            beep()
        except Stopped:
            print("\n[Stopped by user]")

        print()


if __name__ == "__main__":
    dripwriter()
