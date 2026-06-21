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

## Usage

Bind these to keyboard shortcuts, either in the GNOME settings, or via `gsettings set` (ask your AI agent to do it, this is much more complicated than it needs to be lol):

| Command                  | Action                                              |
| ------------------------ | --------------------------------------------------- |
| `whisper.py`             | Toggle: start recording, or stop + transcribe + paste |
| `whisper.py --yolo`      | Same, but press Enter after pasting                 |
| `whisper.py --cancel`    | Cancel an in-progress recording without transcribing |

## Notes

- Recordings cap out at 300 seconds.
- The clipboard is automatically restored after pasting in non-terminal windows.
