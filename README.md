# Whisperer

Toggle dictation for your desktop. Press **Ctrl+M** to turn the microphone on, speak, then press **Ctrl+M** again to turn it off. The audio is then transcribed (OpenAI API or a local Whisper model) and pasted at your cursor, wherever it is.

## Requirements

- Python 3.11+
- An OpenAI API key (not needed if you only use the local backend without translation)

## Install

```
pip install .
```

To also enable the offline backend (faster-whisper):

```
pip install .[local]
```

## API key

Whisperer looks for the key in this order:

1. The `OPENAI_API_KEY` environment variable.
2. A `.env` file in the directory you run from, containing `OPENAI_API_KEY=sk-...`
3. A legacy `openai_api_key.txt` file containing just the key.

## Launch from Win+R (no admin required)

`MyWhisper.cmd` starts the app from the repo folder (so the `.env` is found). To make **Win+R → `MyWhisper`** work, register it once in the per-user App Paths registry key:

```
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\App Paths\MyWhisper.exe" /ve /t REG_SZ /d "<full path to MyWhisper.cmd>" /f
```

This writes only to HKEY_CURRENT_USER, so no admin rights are needed. Remove it with `reg delete` on the same key.

## Usage

```
whisperer            # transcribe via the OpenAI API
whisperer --local    # transcribe offline with faster-whisper
```

(Equivalently: `python -m whisperer`.)

- **Press Ctrl+M** to start recording; **press Ctrl+M again** to stop, transcribe, and paste at your cursor. Either Ctrl key works; releasing the keys leaves recording on.
- **Tap right Shift while recording** to translate the transcript (Quebec French by default).
- **Tap E while recording** to rewrite the transcript as a succinct email.
- Transcripts stay on the clipboard after pasting, so **Ctrl+V** can recover a failed paste. Set `restore_clipboard = true` to restore your previous clipboard instead. With restoration enabled, **quickly toggle recording on and off** (under 0.5 seconds) to keep the next transcript.
- Say **"New paragraph."** to insert a blank line.
- Recordings shorter than 1 second are discarded.
- **Ctrl+C** in the console quits.

A small always-on-top bubble appears above the taskbar at the bottom right when
Whisperer starts, including when launched using Win+R → `MyWhisper`. Drag it to
move it anywhere on your desktop. It stays out of the way of your typing focus.

- **Mic off:** a quiet, crossed-out microphone; no audio is being captured.
- **Listening:** a pink pulsing microphone while recording with Ctrl+M.
- **Processing:** an amber spinning ring while transcribing, rewriting, and pasting;
  the microphone is off unless you start another recording.
- **Try again:** processing failed; see the console for details.

The bubble's **×** button quits Whisperer and stops the microphone. Ctrl+M still
toggles recording; holding Control alone does not record. To use the console
without a bubble, run `whisperer --no-overlay`. The bubble uses Python's built-in
Tkinter; if Tkinter or a desktop display is unavailable, console dictation still works.

On Windows, Ctrl+M is consumed by Whisperer so it doesn't trigger a second
shortcut in the app you're dictating into. Simulated input, including Whisperer's
own Ctrl+V, cannot trigger recording. Microphone operations and logging run on a
separate action thread so a slow audio device or console cannot freeze global
keyboard input. Actions run in order; quitting removes the keyboard hook before
waiting for microphone work to finish. A failed action stops the hook and reports
the original error. Ctrl remains available for other shortcuts even if you press
M before Ctrl while toggling recording.

Automatic paste waits for you to release the recording shortcut, V, and modifier
keys. If a key stays held for more
than three seconds, the transcript stays on the clipboard for a manual Ctrl+V.
The error names the keys Windows still reports as held. If you've already
released them, tap and release those keys again to clear a stuck state.
Key releases follow the same suppression decision as their initial press, so
holding right Shift across a recording toggle cannot swallow only its release.
Pastes from overlapping recordings are serialized. Windows paste key events are
sent as one checked batch; rejected input is logged as an error with the transcript
still on the clipboard. The log says `shortcut sent` because the app cannot verify
that another application actually inserted the text. Optional clipboard restoration
leaves anything you copied during the paste alone.

For slow recordings, look for the console's `timing` line after processing:
`total=3.3s  transcription=3.2s  paste=0.1s`. Email/translation also shows a
`rewrite` time. These times measure the wait after recording stops, excluding
time spent speaking. Windows console QuickEdit is disabled while Whisperer runs
to prevent accidental text selection from pausing output before the paste.

For longer recordings, hosted transcription looks for quiet pauses near each
30-second mark and sends up to two chunks at a time, joining the complete result
in speech order. Chunk timing lines show the request time for each part. Continuous
speech is kept intact if no suitable pause exists; short recordings use one request.
Set `transcription_chunk_seconds = 0` to disable splitting. The transcription prompt
defaults to empty, and digital silence is skipped to avoid inventing text from an
example greeting. You can supply a prompt containing names or vocabulary you use.

## Configuration

Copy `whisperer.toml.example` to `whisperer.toml` in the directory you run from (the repo folder if you use `MyWhisper.cmd`), then edit and restart. Every setting is optional; missing keys keep the built-in defaults.

```toml
record_key = "<ctrl>+m"
translate_key = "shift_r"
email_key = "e"
sample_rate = 16000
min_duration_seconds = 1.0
tap_duration_seconds = 0.5
restore_clipboard = false
transcription_model = "gpt-4o-mini-transcribe"
translation_model = "gpt-5.6-luna"
rewrite_reasoning_effort = "low"
local_model_size = "small.en"
use_local_backend = false
transcription_prompt = ""
transcription_chunk_seconds = 30.0
translation_system_prompt = "You translate the input text to Quebec French using 'vous'. You only output the text and nothing else."
email_system_prompt = "Turn the spoken transcript into a succinct email. Output only a one-line subject prefixed with 'Subject: ', a blank line, and a short body of at most a few sentences. Do not add a greeting, sign-off, or commentary unless the speaker included one. Preserve the speaker's intent, names, facts, and requests. Be concise."
```

The recording shortcut uses pynput hotkey syntax (`<ctrl>+m`, `<alt>+r`, …), a single pynput key name (`ctrl_r`, `f13`, …), or a single character. Recording always toggles on each press. The translation key is a pynput key name or a single character.

After changing the code or settings, close any running Whisperer console and launch `MyWhisper` again from Win+R.

Rewrite uses OpenAI [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) (`gpt-5.6-luna`) by default — the cost-optimized GPT-5.6 tier. `rewrite_reasoning_effort` can be `none`, `low`, `medium`, `high`, `xhigh`, or `max`. Use `low` for fast email/translate; set it to `""` if you switch `translation_model` back to a non-reasoning model such as `gpt-4o-mini`.

## Notes

- Audio is captured at 16 kHz mono and sent to the API as an in-memory FLAC — nothing is written to disk.
- Transcription runs on a background thread, so the hotkeys stay responsive.
- On Linux, `pyperclip` needs `xclip`: `sudo apt-get install xclip`.
