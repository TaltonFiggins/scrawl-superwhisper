#!/usr/bin/env python3
"""Superwhisper words for Scrawl captures.

Scrawl (https://scrawl.kitlangton.com) transcribes each capture with Apple Speech. This add-on
watches Scrawl's captures folder and, for every new capture, has Superwhisper transcribe the same
audio, then puts Superwhisper's words into the capture so that pasting it (Option-Shift-V) or
reading `summary.md` gives Superwhisper's text. Scrawl itself is not modified.

How the words are placed: Superwhisper's own word times drift seconds late over a recording with
pauses, so they are not used. Instead Superwhisper's words are lined up with Apple's (longest
common subsequence of normalized words); each matched word takes Apple's times, and the rest are
spread between their neighbours. Scrawl hands each keyframe one unbroken run of Apple's words, so
every Superwhisper word goes to the keyframe that owns the Apple word it lines up with. When too
few words line up, or anything looks off, the capture is left exactly as Scrawl wrote it.

Commands:
  scrawl_superwhisper.py watch              process new captures (run by the LaunchAgent)
  scrawl_superwhisper.py process <bundle>   process one capture ("latest" for the newest)
  scrawl_superwhisper.py undo <bundle>      put Apple's transcript back
  scrawl_superwhisper.py status             show what's installed and recent results

Python 3.9+ standard library only. Unofficial; not affiliated with Scrawl or Superwhisper.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import wave

VERSION = "0.1.0"
HOME = os.path.expanduser("~")
CAPTURES = os.path.join(HOME, "Library/Application Support/scrawl/captures")
STATE = os.path.join(HOME, "Library/Application Support/scrawl-superwhisper")
LOG = os.path.join(HOME, "Library/Logs/scrawl-superwhisper.log")
SETTINGS = os.path.join(STATE, "settings.json")

MANIFEST = "scrawl.json"
APPLE_BACKUP = "scrawl.apple.json"  # Scrawl's own manifest, kept for `undo`
DONE = ".superwhisper.json"  # what was written, so a later rewrite by Scrawl can be repaired

MIN_MATCHED = 0.6  # below this share of words lined up, leave the capture alone
UNMATCHED_WORD_MS = 250
RECENT_S = 15 * 60  # `watch` looks at captures this new
SETTLE_S = 1.5  # scrawl.json unchanged this long means Scrawl finished writing it
READY_WAIT_S = 90
GUARD_S = 25  # after writing, keep reapplying if Scrawl rewrites scrawl.json
SW_RETRY_S = 8
SW_BASE_WAIT_S = 30
SW_MAX_WAIT_S = 600


def log(message):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + message
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------------------------
# Superwhisper

def superwhisper_app():
    for path in ["/Applications/superwhisper.app", os.path.join(HOME, "Applications/superwhisper.app")]:
        if os.path.isdir(path):
            return path
    return None


def superwhisper_recordings():
    """`<appFolderDirectory>/superwhisper/recordings`, else ~/Documents/superwhisper or ~/superwhisper."""
    candidates = []
    try:
        out = subprocess.run(["defaults", "read", "com.superduper.superwhisper", "appFolderDirectory"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            candidates.append(os.path.join(out.stdout.strip(), "superwhisper/recordings"))
    except (OSError, subprocess.TimeoutExpired):
        pass
    candidates += [os.path.join(HOME, "Documents/superwhisper/recordings"),
                   os.path.join(HOME, "superwhisper/recordings")]
    return next((c for c in candidates if os.path.isdir(c)), None)


def wav_duration_ms(path):
    with wave.open(path) as w:
        return w.getnframes() * 1000 // w.getframerate()


def find_result(folder, before, duration_ms):
    """The newest finished Superwhisper result in `folder`, not in `before`, of this length."""
    for name in sorted(set(os.listdir(folder)) - before, reverse=True):
        try:
            with open(os.path.join(folder, name, "meta.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue  # still transcribing, or half written
        if abs(int(meta.get("duration") or 0) - duration_ms) <= max(1000, duration_ms // 20):
            return meta
    return None


def superwhisper_transcribe(wav):
    """Superwhisper's words for `wav` as [{"text"}], plus its model key and name."""
    app = superwhisper_app()
    if not app:
        raise RuntimeError("Superwhisper isn't installed")
    folder = superwhisper_recordings()
    if not folder:
        raise RuntimeError("Superwhisper's recordings folder wasn't found")
    duration_ms = wav_duration_ms(wav)
    before = set(os.listdir(folder))

    def send():
        if subprocess.run(["open", "-g", "-a", app, wav]).returncode != 0:
            raise RuntimeError("Superwhisper wouldn't open the recording")
        return time.monotonic()

    sent, retried = send(), False
    deadline = time.monotonic() + min(SW_BASE_WAIT_S + duration_ms / 1000, SW_MAX_WAIT_S)
    while True:
        meta = find_result(folder, before, duration_ms)
        if meta is not None:
            words = [{"text": s["text"].strip()}
                     for s in sorted(meta.get("segments") or [], key=lambda s: s.get("start", 0))
                     if str(s.get("text", "")).strip()]
            # Phrase-level segments: split into words (their order is all that's used).
            words = [{"text": t} for w in words for t in w["text"].split()]
            if not words:
                raise RuntimeError("Superwhisper returned no words")
            return words, meta.get("modelKey") or "unknown", meta.get("modelName") or ""
        if time.monotonic() >= deadline:
            raise RuntimeError("Superwhisper gave no result in time")
        if time.monotonic() - sent >= SW_RETRY_S and not (set(os.listdir(folder)) - before):
            if retried:
                raise RuntimeError("Superwhisper ignored the recording twice")
            log("Superwhisper didn't take the recording; sending it again")
            sent, retried = send(), True
        time.sleep(0.25)


# ---------------------------------------------------------------------------------------------
# Lining up words

def token(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def align(ours, theirs):
    """For each of `ours` (word texts), the index into `theirs` it lines up with, or None."""
    a, b = [token(t) for t in ours], [token(t) for t in theirs]
    n, m = len(a), len(b)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            lcs[i][j] = lcs[i + 1][j + 1] + 1 if a[i] and a[i] == b[j] else max(lcs[i + 1][j], lcs[i][j + 1])
    out, i, j = [None] * n, 0, 0
    while i < n and j < m:
        if a[i] and a[i] == b[j]:
            out[i] = j
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def retime(words, apple_words, matches):
    """Times for `words`: Apple's for matched words, spread between neighbours for the rest."""
    placed = [(apple_words[k]["t0"], apple_words[k]["t1"]) if k is not None else None for k in matches]
    i, n = 0, len(placed)
    while i < n:
        if placed[i] is not None:
            i += 1
            continue
        end = next((k for k in range(i, n) if placed[k] is not None), n)
        count = end - i
        before = placed[i - 1] if i > 0 else None
        after = placed[end] if end < n else None
        if before and after:
            start, stop = before[1], max(after[0], before[1])
        elif before:
            start, stop = before[1], before[1] + count * UNMATCHED_WORD_MS
        else:
            start, stop = max(0, after[0] - count * UNMATCHED_WORD_MS), after[0]
        for k in range(count):
            placed[i + k] = (start + (stop - start) * k // count, start + (stop - start) * (k + 1) // count)
        i = end
    return [{"t0": t0, "t1": t1, "text": w["text"]} for (t0, t1), w in zip(placed, words)]


def owners_of_apple_words(keyframes, apple_words):
    """Which keyframe (list position) owns each Apple word, from the keyframes' `said` text.

    Scrawl gives every word to one keyframe, in time order, so the keyframes' texts joined are
    exactly Apple's words. Returns None when they aren't (an unexpected Scrawl version)."""
    apple_tokens = [t for t in (token(w["text"]) for w in apple_words)]
    owners, position = [None] * len(apple_words), 0
    for index, frame in enumerate(keyframes):
        for said_token in [token(t) for t in (frame.get("said") or "").split()]:
            if not said_token:
                continue
            while position < len(apple_tokens) and not apple_tokens[position]:
                position += 1
            if position >= len(apple_tokens) or apple_tokens[position] != said_token:
                return None
            owners[position] = index
            position += 1
    if any(t for t, owner in zip(apple_tokens, owners) if owner is None):
        return None
    # Punctuation-only Apple words go with the word before (or after) them.
    for k in range(len(owners)):
        if owners[k] is None:
            owners[k] = next((owners[j] for j in range(k - 1, -1, -1) if owners[j] is not None),
                             next((o for o in owners[k:] if o is not None), 0))
    return owners


def join_words(words):
    text = ""
    for word in words:
        word = word.strip()
        attached = word[:1] in ",.!?;:’')]”"
        if text and not attached and not text.endswith(("(", "[", "“")):
            text += " "
        text += word
    return text


def sentences(words):
    segments, start = [], 0
    for i, word in enumerate(words):
        last = i + 1 == len(words)
        ends = re.search(r"[.!?;:][\"'”’)\]]*$", word["text"]) is not None
        pause = not last and words[i + 1]["t0"] - word["t1"] >= 1500
        if last or ends or pause:
            run = words[start:i + 1]
            segments.append({"t0": run[0]["t0"], "t1": run[-1]["t1"], "text": join_words(w["text"] for w in run)})
            start = i + 1
    return segments


def rewrite(manifest, sw_words, model_key, model_name):
    """The manifest with Superwhisper's words, or (None, reason) when it should be left alone."""
    transcript = manifest.get("transcript") or {}
    apple_words = transcript.get("words") or []
    keyframes = manifest.get("keyframes") or []
    if not apple_words:
        return None, "Apple Speech had no timed words to place Superwhisper's on"
    owners = owners_of_apple_words(keyframes, apple_words)
    if owners is None:
        return None, "the keyframes' text doesn't match the transcript (unexpected Scrawl format)"
    matches = align([w["text"] for w in sw_words], [w["text"] for w in apple_words])
    matched = sum(1 for m in matches if m is not None)
    if matched < MIN_MATCHED * len(sw_words):
        return None, f"only {matched} of {len(sw_words)} words lined up with Apple's"
    words = retime(sw_words, apple_words, matches)
    # Each Superwhisper word goes to the keyframe owning its Apple word; unmatched ones follow
    # the previous matched word (or the next, at the start).
    word_owner, last = [], None
    first = next(owners[m] for m in matches if m is not None)
    for m in matches:
        if m is not None:
            last = owners[m]
        word_owner.append(last if last is not None else first)
    out = json.loads(json.dumps(manifest))
    for index, frame in enumerate(out.get("keyframes") or []):
        mine = [w["text"] for w, o in zip(words, word_owner) if o == index]
        if mine:
            frame["said"] = join_words(mine)
        else:
            frame.pop("said", None)
    out["transcript"] = {
        "source": f"superwhisper:{model_key}+{transcript.get('source') or 'apple'}",
        "model": model_name or transcript.get("model"),
        "text": join_words(w["text"] for w in words),
        "segments": sentences(words),
        "words": words,
    }
    if transcript.get("estimated"):
        out["transcript"]["estimated"] = True
    return out, f"{matched} of {len(sw_words)} words lined up"


# ---------------------------------------------------------------------------------------------
# Captures

def scrawl_binary():
    for app in [os.path.join(HOME, "Applications/Scrawl.app"), "/Applications/Scrawl.app"]:
        path = os.path.join(app, "Contents/MacOS/scrawl")
        if os.path.exists(path):
            return path
    return None


def read_manifest(bundle):
    with open(os.path.join(bundle, MANIFEST)) as f:
        return json.load(f)


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def write_summary(bundle):
    binary = scrawl_binary()
    if not binary:
        return
    out = subprocess.run([binary, "inspect", bundle], capture_output=True, text=True, timeout=30)
    if out.returncode == 0 and out.stdout.strip():
        with open(os.path.join(bundle, "summary.md.tmp"), "w") as f:
            f.write(out.stdout)
        os.replace(os.path.join(bundle, "summary.md.tmp"), os.path.join(bundle, "summary.md"))


def ours(manifest):
    return str((manifest.get("transcript") or {}).get("source", "")).startswith("superwhisper:")


def apply(bundle, done):
    manifest = read_manifest(bundle)
    if ours(manifest):
        return False
    for frame, said in zip(manifest.get("keyframes") or [], done["said"]):
        if said is None:
            frame.pop("said", None)
        else:
            frame["said"] = said
    manifest["transcript"] = done["transcript"]
    write_json(os.path.join(bundle, MANIFEST), manifest)
    write_summary(bundle)
    return True


def wait_until_ready(bundle):
    path = os.path.join(bundle, MANIFEST)
    deadline = time.monotonic() + READY_WAIT_S
    while time.monotonic() < deadline:
        try:
            age = time.time() - os.path.getmtime(path)
            if age >= SETTLE_S:
                return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def process(bundle, guard=True):
    name = os.path.basename(bundle.rstrip("/"))
    if os.path.exists(os.path.join(bundle, DONE)):
        return
    if not wait_until_ready(bundle):
        log(f"{name}: never finished saving; skipped")
        return
    manifest = read_manifest(bundle)
    audio = manifest.get("audio") and os.path.join(bundle, manifest["audio"])
    if ours(manifest):
        print(f"{name} already has Superwhisper's words (`undo` puts Apple's back).")
        return
    if not audio or not os.path.exists(audio):
        log(f"{name}: no audio; left as is")
        write_json(os.path.join(bundle, DONE), {"skipped": "no audio"})
        return
    began = time.monotonic()
    try:
        sw_words, model_key, model_name = superwhisper_transcribe(audio)
    except Exception as error:  # Superwhisper trouble leaves Apple's words in place
        log(f"{name}: Superwhisper failed ({error}); kept Apple's words")
        write_json(os.path.join(bundle, DONE), {"skipped": str(error)})
        return
    manifest = read_manifest(bundle)  # Scrawl may have rewritten it meanwhile
    out, reason = rewrite(manifest, sw_words, model_key, model_name)
    if out is None:
        log(f"{name}: {reason}; kept Apple's words")
        write_json(os.path.join(bundle, DONE), {"skipped": reason})
        return
    if not os.path.exists(os.path.join(bundle, APPLE_BACKUP)):
        write_json(os.path.join(bundle, APPLE_BACKUP), manifest)
    done = {"version": VERSION, "transcript": out["transcript"],
            "said": [f.get("said") for f in out.get("keyframes") or []]}
    write_json(os.path.join(bundle, DONE), done)
    apply(bundle, done)
    log(f"{name}: Superwhisper words added ({reason}, {time.monotonic() - began:.1f} s)")
    if settings().get("notify", True):
        subprocess.run(["osascript", "-e", 'display notification "Superwhisper words added — '
                        'ready to paste" with title "Scrawl + Superwhisper"'], capture_output=True)
    if guard:
        # Scrawl records where a capture was sent by rewriting scrawl.json a few seconds later.
        until = time.monotonic() + GUARD_S
        while time.monotonic() < until:
            time.sleep(1)
            try:
                if apply(bundle, done):
                    log(f"{name}: Scrawl rewrote the capture; Superwhisper words put back")
            except (OSError, ValueError):
                pass


def undo(bundle):
    backup = os.path.join(bundle, APPLE_BACKUP)
    if not os.path.exists(backup):
        sys.exit("No Apple transcript saved for this capture.")
    os.replace(backup, os.path.join(bundle, MANIFEST))
    write_summary(bundle)
    with open(os.path.join(bundle, DONE), "w") as f:
        json.dump({"skipped": "undone"}, f)
    print("Apple's transcript restored.")


def resolve(arg):
    if arg == "latest":
        bundles = sorted((os.path.join(CAPTURES, d) for d in os.listdir(CAPTURES) if d.endswith(".scrawl")),
                         key=os.path.getmtime)
        if not bundles:
            sys.exit("No captures yet.")
        return bundles[-1]
    return os.path.abspath(os.path.expanduser(arg))


def watch():
    os.makedirs(STATE, exist_ok=True)
    lock = open(os.path.join(STATE, "watch.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another run is already working through the folder
    while True:
        now = time.time()
        pending = [os.path.join(CAPTURES, d) for d in sorted(os.listdir(CAPTURES))
                   if d.endswith(".scrawl") and now - os.path.getmtime(os.path.join(CAPTURES, d)) < RECENT_S
                   and not os.path.exists(os.path.join(CAPTURES, d, DONE))] if os.path.isdir(CAPTURES) else []
        if not pending:
            return
        for bundle in pending:
            try:
                process(bundle)
            except Exception as error:
                log(f"{os.path.basename(bundle)}: {error!r}")
                try:
                    write_json(os.path.join(bundle, DONE), {"skipped": repr(error)})
                except OSError:
                    pass


def status():
    print(f"scrawl-superwhisper {VERSION}")
    print(f"Scrawl:        {scrawl_binary() or 'not found'}")
    print(f"Superwhisper:  {superwhisper_app() or 'not found'}")
    print(f"  recordings:  {superwhisper_recordings() or 'not found'}")
    print(f"Captures:      {CAPTURES}")
    agent = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.scrawl-superwhisper.watch"],
                           capture_output=True, text=True)
    print(f"Watcher:       {'installed' if agent.returncode == 0 else 'not installed'}")
    try:
        with open(LOG) as f:
            lines = f.readlines()[-5:]
        print("Recent:\n  " + "  ".join(lines).rstrip())
    except OSError:
        pass


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    command = argv[1]
    if command == "watch":
        watch()
    elif command == "process" and len(argv) == 3:
        bundle = resolve(argv[2])
        if os.path.exists(os.path.join(bundle, DONE)):
            os.remove(os.path.join(bundle, DONE))
        process(bundle, guard=False)
    elif command == "undo" and len(argv) == 3:
        undo(resolve(argv[2]))
    elif command == "status":
        status()
    elif command == "--version":
        print(VERSION)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
