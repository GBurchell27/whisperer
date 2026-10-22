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
- **Quickly toggle recording on and off** (under 0.5 seconds) before recording to keep the next transcript on the clipboard after pasting (normally your previous clipboard contents are restored).
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

## Configuration

Copy `whisperer.toml.example` to `whisperer.toml` in the directory you run from (the repo folder if you use `MyWhisper.cmd`), then edit and restart. Every setting is optional; missing keys keep the built-in defaults.

```toml
record_key = "<ctrl>+m"
translate_key = "shift_r"
email_key = "e"
sample_rate = 16000
min_duration_seconds = 1.0
tap_duration_seconds = 0.5
transcription_model = "gpt-4o-mini-transcribe"
translation_model = "gpt-5.6-luna"
rewrite_reasoning_effort = "low"
local_model_size = "small.en"
use_local_backend = false
transcription_prompt = "How are you doing today? I'm really looking forward to seeing you again!"
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
