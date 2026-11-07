# Whisperer

Toggle dictation for your desktop. Press **Ctrl+M** to turn the microphone on, speak, then press **Ctrl+M** again to turn it off. The audio is then transcribed (OpenAI API or a local Whisper model) and pasted at your cursor, wherever it is.

Press **Ctrl+P** for a quick screen recording with your microphone, a circular
webcam bubble, and visible click highlights. Recordings save locally as MP4 files.

## Requirements

- Python 3.11+
- An OpenAI API key (not needed if you only use the local backend)

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
- Dictation keeps the spoken language. Right Shift works normally while recording.
- Transcripts stay on the clipboard after pasting, so **Ctrl+V** can recover a failed paste. Set `restore_clipboard = true` to restore your previous clipboard instead. With restoration enabled, **quickly toggle recording on and off** (under 0.5 seconds) to keep the next transcript.
- Say **"New paragraph."** to insert a blank line.
- Recordings shorter than 1 second are discarded.
- **Ctrl+C** in the console quits.

A small always-on-top bubble appears above the taskbar at the bottom right when
Whisperer starts, including when launched using Win+R → `MyWhisper`. Drag it to
move it anywhere on your desktop. It stays out of the way of your typing focus.
It always shows **Ctrl+M · Start/Finish dictation** and **Ctrl+P · Open screen
recorder**. You can also click the screen recorder row. During capture, that row
changes to **Ctrl+P · Stop & save video**, even if you're dictating at the same time.

- **Mic off:** a quiet, crossed-out microphone; no audio is being captured.
- **Listening:** a pink pulsing microphone while recording with Ctrl+M.
- **Recording screen:** a pink pulsing stop icon while screen capture is active.
- **Starting screen recording / Saving video:** the recorder is preparing or finishing the file.
- **Processing:** an amber spinning ring while transcribing and pasting;
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

Whisperer checks for missed modifier releases against Windows' current key state
and clears stale shortcut suppression before forwarding ordinary typing. Shortcut
state and suppression are handled in the same event order, so a delayed callback
cannot leave M intercepted while the microphone is off. Stopped recordings release
their captured audio buffers immediately; microphone failures still close the
stream and clear the recording indicator.

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
`total=3.3s  transcription=3.2s  paste=0.1s`. These times measure the wait after
recording stops, excluding time spent speaking. Windows console QuickEdit is disabled while Whisperer runs
to prevent accidental text selection from pausing output before the paste.

For longer recordings, hosted transcription looks for quiet pauses near each
30-second mark and sends up to two chunks at a time, joining the complete result
in speech order. Chunk timing lines show the request time for each part. Continuous
speech is kept intact if no suitable pause exists; short recordings use one request.
Set `transcription_chunk_seconds = 0` to disable splitting. The transcription prompt
defaults to empty, and digital silence is skipped to avoid inventing text from an
example greeting. You can supply a prompt containing names or vocabulary you use.

## Quick screen recordings

On **Windows 10 version 2004 or newer / Windows 11**, with the normal overlay enabled:

1. Press **Ctrl+P** or click the floating **Open screen recorder** control. A blue
   border marks the selected screen; recording hasn't started yet. If you have
   multiple monitors, choose one from the screen menu.
2. Leave **Webcam bubble**, **Microphone**, and **Highlight clicks** on, or turn
   off anything you don't need. Drag the webcam bubble to move it.
3. Click **Record**. The border turns **red while capture is active**. Click
   **Stop recording**, click **Stop & save video** in the floating controls, or
   press **Ctrl+P** to finish. The border disappears as the MP4 is saved.
4. The panel shows the saved file and size. **Play video** opens it in your video
   player; **Show in folder** selects it in Explorer. Use **New recording** to
   prepare another clip. Closing the panel while recording stops and saves it.

Files go to **`~/Videos/Whisperer`** by default. They are ordinary, shareable MP4s
with the webcam, cursor, and click ripples baked in. No upload or account is
involved in screen recording. Audio uses the default microphone; webcam uses
camera 0. System/desktop audio is not recorded.

Videos use H.264, up to a 1280-pixel longest edge at 20 fps, and AAC microphone
audio. Compression happens while recording, so there is no huge uncompressed
video or slow second video compression pass. The video bitrate is capped around
1.8 Mbps, plus 64 kbps audio: busy recordings can approach **14 MB per minute**;
quiet screen recordings are usually smaller. Long clips can still exceed email
attachment limits. No fixed email-size guarantee is imposed.

The recorder controls and red border are excluded from the video on supported
Windows versions; the webcam preview is composited separately. On other systems,
screen capture is best effort and controls may be visible. If a camera or microphone
fails, the panel reports the error; turn that option off to record the screen alone.
Capture and encoding run off the hotkey/UI threads, and audio is streamed to disk.
If finishing fails, the panel links to a recovery folder containing the captured
files. Quitting Whisperer finishes an active recording before exiting.

After updating, install the added capture/encoder dependencies once from the repo:

```powershell
python -m pip install -e .
```

Restart `MyWhisper` after updating. Ctrl+P overrides the usual Print shortcut while
Whisperer is running; change `video_key` if you prefer another shortcut. The
screen recorder is unavailable with `--no-overlay` or without Tkinter, and Ctrl+P
then retains its normal behavior. Ctrl+M continues to control dictation.

## Configuration

Copy `whisperer.toml.example` to `whisperer.toml` in the directory you run from (the repo folder if you use `MyWhisper.cmd`), then edit and restart. Every setting is optional; missing keys keep the built-in defaults.

```toml
record_key = "<ctrl>+m"
video_key = "<ctrl>+p"
video_output_dir = "~/Videos/Whisperer"
sample_rate = 16000
min_duration_seconds = 1.0
tap_duration_seconds = 0.5
restore_clipboard = false
transcription_model = "gpt-4o-mini-transcribe"
local_model_size = "small.en"
use_local_backend = false
transcription_prompt = ""
transcription_chunk_seconds = 30.0
```

The recording shortcut uses pynput hotkey syntax (`<ctrl>+m`, `<alt>+r`, …), a single pynput key name (`ctrl_r`, `f13`, …), or a single character. Recording always toggles on each press.

Translation has been removed. Older translation settings in `whisperer.toml` are ignored.

After changing the code or settings, close any running Whisperer console and launch `MyWhisper` again from Win+R.

## Notes

- Audio is captured at 16 kHz mono and sent to the API as an in-memory FLAC — nothing is written to disk.
- Transcription runs on a background thread, so the hotkeys stay responsive.
- On Linux, `pyperclip` needs `xclip`: `sudo apt-get install xclip`.
