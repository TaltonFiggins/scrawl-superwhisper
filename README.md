# Scrawl + Superwhisper

An unofficial add-on that puts [Superwhisper](https://superwhisper.com)'s transcription into
[Scrawl](https://scrawl.kitlangton.com) captures. Scrawl itself is untouched: you keep the
official app and its updates.

Scrawl records your screen and voice and pairs each keyframe with what you said. It transcribes
with Apple Speech. This add-on runs in the background, has Superwhisper transcribe the same audio
after each capture, and swaps in Superwhisper's words, which are usually more accurate with names
and developer jargon (Tailwind, Supabase, z-index, …). A notification tells you when it's done,
about two seconds after the capture finishes; then paste with **Option-Shift-V** as usual.

## Install

You need macOS, [Scrawl](https://scrawl.kitlangton.com), [Superwhisper](https://superwhisper.com)
and Apple's Command Line Tools (for Python; the installer tells you if they're missing).

```bash
curl -fsSL https://raw.githubusercontent.com/TaltonFiggins/scrawl-superwhisper/main/install.sh | bash
```

Uninstall:

```bash
curl -fsSL https://raw.githubusercontent.com/TaltonFiggins/scrawl-superwhisper/main/uninstall.sh | bash
```

## How it works

1. Scrawl saves a capture to `~/Library/Application Support/scrawl/captures/`.
2. A LaunchAgent notices the new capture and opens its `audio.wav` in Superwhisper (the same as
   dropping a file on it), then reads the result from Superwhisper's history.
3. Superwhisper's words are lined up with Apple's. Each word takes Apple's timing (Superwhisper's
   own word times drift late through a recording with pauses) and goes to the keyframe that
   Scrawl gave the matching Apple word. Words only Superwhisper heard go with their neighbours.
4. The capture's `scrawl.json` and `summary.md` are updated. The original is kept as
   `scrawl.apple.json`.

If Superwhisper fails or ignores the file, or fewer than 60% of the words line up, the capture is
left exactly as Scrawl wrote it. Superwhisper's words never change which keyframes Scrawl chose.

## Good to know

- Every capture also appears in Superwhisper's history.
- Superwhisper's cloud models (such as S1-Voice) upload the audio. Pick a local model in
  Superwhisper to keep it on your Mac.
- If you paste within the first couple of seconds, you get Apple's words; wait for the
  notification.
- If Superwhisper keeps its files in `~/Documents`, macOS may ask once whether Python can
  access Documents.
- Add project names to Superwhisper's vocabulary: they are the words it misses most.

## Commands

```bash
python3 ~/Library/Application\ Support/scrawl-superwhisper/scrawl_superwhisper.py status
python3 ~/Library/Application\ Support/scrawl-superwhisper/scrawl_superwhisper.py process latest
python3 ~/Library/Application\ Support/scrawl-superwhisper/scrawl_superwhisper.py undo latest
```

`status` shows what's installed and recent results (log: `~/Library/Logs/scrawl-superwhisper.log`).
`process` runs one capture by hand; `undo` restores Apple's transcript. To turn off the
notification, put `{"notify": false}` in
`~/Library/Application Support/scrawl-superwhisper/settings.json`.

## Status

A personal tool, shared as is. It depends on Scrawl's capture format and Superwhisper's history
folder, neither of which is a published interface, so an update to either can stop it working.
When that happens it leaves captures alone rather than breaking them. Not affiliated with Scrawl
or Superwhisper.
