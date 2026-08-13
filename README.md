# Whisperer

Push-to-talk dictation for your desktop. Hold **right Ctrl**, speak, release — the audio is transcribed (OpenAI API or a local Whisper model) and pasted at your cursor, wherever it is.

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

- **Hold right Ctrl** to record; **release** to transcribe and paste at your cursor.
- **Tap right Shift while recording** to translate the transcript (Quebec French by default).
- **Quick-tap right Ctrl** before recording to keep the next transcript on the clipboard after pasting (normally your previous clipboard contents are restored).
- Say **"New paragraph."** to insert a blank line.
- Recordings shorter than 1 second are discarded.
- **Ctrl+C** in the console quits.

## Configuration

All settings can be overridden by creating a `whisperer.toml` in the directory you run from. Defaults:

```toml
record_key = "ctrl_r"
translate_key = "shift_r"
sample_rate = 16000
min_duration_seconds = 1.0
tap_duration_seconds = 0.5
transcription_model = "gpt-4o-mini-transcribe"
translation_model = "gpt-4o-mini"
local_model_size = "small.en"
use_local_backend = false
transcription_prompt = "How are you doing today? I'm really looking forward to seeing you again!"
translation_system_prompt = "You translate the input text to Quebec French using 'vous'. You only output the text and nothing else."
```

Key names are pynput names (`ctrl_r`, `alt_r`, `f13`, …) or a single character.

## Notes

- Audio is captured at 16 kHz mono and sent to the API as an in-memory FLAC — nothing is written to disk.
- Transcription runs on a background thread, so the hotkeys stay responsive.
- On Linux, `pyperclip` needs `xclip`: `sudo apt-get install xclip`.
