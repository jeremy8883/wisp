# wisp

Push-to-talk speech-to-text, specifically designed for GNOME Linux/Wayland. This project is a simple python script, which designed for you, yourself, to wire up to a shortcut keys.

Speech-to-text is poweref via the OpenAI Whisper API. BYO OpenAI API key.

And yes, the bulk of this script is generated using AI.

## Requirements

- Get a OpenAI API key at <https://platform.openai.com/api-keys>, then assign it to env var `OPENAI_API_KEY`.
- Download and install `ydotool` (`sudo dnf install -y ydotool`), and ensure `ydotoold` is running. This is so we can simulate Ctrl + V keystrokes for pasting the text content.
- Download [window-calls](https://github.com/ickyicky/window-calls) GNOME extension. This is used for automatically detecting the optimal pasting method for the currently open application. eg. Ctrl+V is used for regular GUI applications, but Shift+Insert is used for a terminal application.
- Also required are: `python3`, `pipewire` (so we can use `pw-record`), `libnotify` (for reporting errors), `libappindicator-gtk3` (for showing a notification icon during the record).

```
sudo dnf install python3-requests pipewire-utils ydotool wl-clipboard libnotify dbus-tools python3-gobject gtk3 libappindicator-gtk3
```

- For streaming mode, you also need the `websocket-client` Python package: `pip install websocket-client`.

## Usage

Bind these to keyboard shortcuts, either in the GNOME settings, or via `gsettings set` (ask your AI agent to do it, this is much more complicated than it needs to be lol):

| Command                  | Action                                              |
| ------------------------ | --------------------------------------------------- |
| `wisp.py`                | Toggle: start recording, or stop + transcribe + paste |
| `wisp.py --yolo`         | Same, but press Enter after pasting                 |
| `wisp.py --cancel`       | Cancel an in-progress recording without transcribing |
| `wisp.py --stream`       | Toggle live streaming: type as you speak, correcting in place. Still WIP, and doesn't work well at all |

## Streaming mode

`wisp.py --stream` is a toggle, just like the batch mode: press once to start, press again to stop.

**THIS IS STILL WIP, AND DOESN'T WORK WELL AT ALL**

Instead of recording to a file and pasting at the end, it opens a streaming connection to OpenAI's realtime transcription API (`gpt-4o-transcribe`) and **types words into the focused window as you speak**.

Because the model revises earlier words as it hears more of a sentence, wisp rewrites what it has already typed: it backspaces the part that changed and retypes the corrected tail.

Extra streaming flags:

| Flag                  | Effect                                                                 |
| --------------------- | --------------------------------------------------------------------- |
| `--safe`              | Only ever use single backspaces, never Ctrl+Backspace. Slowest but the most predictable across apps. |
| `--language en`       | Optional ISO language hint passed to the transcription model.          |
| `--silence-ms 1000`   | Silence (ms) before a sentence is finalized. Raise it if slow/deliberate speech gets split into separate sentences (default: 1000). |

Terminals are auto-detected (by window class) and use plain backspaces, since shells/TUIs don't treat Ctrl+Backspace as delete-word. `--safe` forces that everywhere.

If word-delete in a GUI app eats one character too many/few around spaces, try `--safe`, or flip `WORD_DELETE_CONSUMES_LEADING_SPACE` in `correction.py`.

## Unit tests

Run the tests with:

```
python3 -m unittest discover -s tests
```

## Notes

- Batch recordings cap out at 300 seconds.
- The clipboard is automatically restored after pasting in non-terminal windows (batch mode).
