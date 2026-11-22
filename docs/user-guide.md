# Jarvis for Windows

Jarvis is a voice-activated desktop assistant with an orb at the bottom right of
the laptop display. It uses your existing ChatGPT/Codex sign-in through the
installed OpenClaw AI runtime. Choose the conversation and research models
from Model settings on the orb. Luna is the initial default.

## Source checkout

This repository contains the application source and verification scripts.
Downloaded runtimes, model weights, account credentials, logs, recordings,
personal files, saved projects and machine-specific settings are excluded.
Copy phone/settings.example.json to phone/settings.json and the two desktop
settings examples to their corresponding .json filenames before local setup.
The phone example supplies the voice settings used by the desktop app.

A fresh checkout also needs the local dependencies under .runtime and its own
.openclaw/openclaw.json configuration, including a unique gateway token and
the owner's own sign-in. The existing setup scripts assume the base OpenClaw,
Node, Python packages and speech runtime have already been provisioned. This
source repository does not contain a complete clean-machine installer.

## Saved projects, window layouts and independent tasks

Jarvis generates a brief acknowledgement of the specific request before online
complexity classification or task actions. A short, tool-free Luna request writes
the sentence, such as Checking whether I can see your drivers, sir. This adds a
brief generation step; a cached generic reply is used if generation fails or
times out. Capability questions acknowledge checking access without claiming
that the PC has already been inspected. A separate request worker then selects
the reasoning tier and runs the task in its own model session. Two workers
handle up to four queued/running tasks while the foreground
conversation stays available. Stopping a foreground reply leaves tasks running;
Cancel task in Activity or stop the task cancels the task itself. Completion
replies wait for the foreground voice to become free. Activity shows actual
progress, results, model and reasoning. Dedicated browser jobs are serialized
so one job cannot replace another job's page or element references.

Say Save my latest research as a project called Laptop research. Include your
decisions and next steps in the request, or choose Save as project on a completed
Activity result. Saved projects in the console (orb menu > Activity) lets you inspect findings and
sources, edit decisions/next steps, and resume. Continue yesterday's laptop
research finds that saved project and starts a new public research job with its
context. Private projects resume discussion without public research tools.
Saving is explicit; resuming does not overwrite the previous saved version.
Projects survive restarts in .openclaw/projects, with revision checks and backups.
Existing transient conversations and unsaved research still expire as before.

Say Put Chrome on the left and my notes on the right, or Move Chrome to the next
monitor. Dedicated window tools list matching open windows, then place them in
halves, quadrants or the full monitor work area, keeping the taskbar clear. You
can name the laptop, primary, next or numbered monitor. Ambiguous windows need
clarification. Positions are read back from Windows before success is reported.
The layout controls move windows without typing into or clicking their content.

The dedicated browser honors a requested Edge, Chrome, Opera or Opera GX browser.
Opera is discovered from its usual per-user or Program Files installation. It
uses a separate automation window; an unavailable named browser is reported and
is not silently replaced. Native app tasks use the general desktop worker below.

Verification: tests/verification/verify-workflow-features.py exercises live models using
isolated ports, temporary saved projects, and simulated browser/desktop actions.
Its --layout-only check verifies live model selection of both window placements.
Its --ack-only check verifies generated replies to capability and action requests.
tests/verification/verify-workflow-ui.py checks real Win32 placement on a disposable window
and the saved-project console. Real microphone playback and controlling
your existing app windows still require a normal Windows desktop launch.

## First launch

Double-click setup-jarvis.cmd from your normal Windows desktop. This registers
Jarvis to start when this Windows user signs in and starts the model service and orb.
Administrator privileges are not intended to be needed. The first start installs
Jarvis's Electron interface into desktop/ui/node_modules (one download of about
160 MB, about 380 MB installed; logs/ui-install.log records it). Later starts
work offline.

Email is on hold at your request. Jarvis immediately replies I don't have access
to your email, sir. It does not search mailboxes, start email jobs or prompt for
account connections. Briefings skip email. No email sign-in is needed for setup.
The read-only adapters remain available for a future setup, with emailEnabled
currently false in desktop/settings.json.

The coding agent runs on a separate CodexSandboxDesktop. Programs started there
are not on your visible desktop, microphone capture fails there, and Windows
denied startup registry changes there. Run setup from your normal Windows
session. It replaces an existing hidden sandbox orb with one on your desktop.
It does not switch or weaken Windows desktops.

Start every spoken command with Jarvis, including follow-up questions and
interruptions: "Jarvis, what time is it?" or "Jarvis, stop." Opening Jarvis,
pressing Insert, clicking the orb, or finishing an answer never enables wake-free
conversation. Saying just Jarvis does not arm the next sentence. Wake detection
only runs while Jarvis is visible and unmuted; hiding him releases the microphone.

Jarvis waits for about two seconds of silence before treating a spoken turn as
complete. Natural pauses within that window stay in the same request. Microphone
audio continues buffering during transcription; if you resume speaking, Jarvis
combines the audio and waits again before sending a command or taking an action.
Spoken turns are limited to one minute. speechPauseSeconds in desktop/settings.json
can be set from 1 to 5 seconds; restart Jarvis after changing it. This timing does
not change the existing automatic selection of models and reasoning levels.
tests/verification/verify-speech-pauses.py tests the real recognizer with synthetic pauses.

## Examples

Jarvis, open Chrome.
Jarvis, start Notepad.
Jarvis, open Outlook classic.
Jarvis, search the web for the latest space news.
Jarvis, read my latest emails.
Jarvis, find emails about receipts in Gmail.
Jarvis, read the first one.
Jarvis, how is the search going?
Jarvis, read the search results.
Jarvis, cancel the search.
Jarvis, remind me in thirty minutes to take a break.
Jarvis, remind me every weekday at nine to start work.
Jarvis, snooze that reminder for ten minutes.
Jarvis, give me my morning briefing.
Jarvis, find the invoice I downloaded yesterday.
Jarvis, visit https://example.com.
Jarvis, switch to Astra.
Jarvis, use GPT six point one Sol for background search.
Jarvis, turn off Proton VPN.
Jarvis, what is on my screen?
Jarvis, stop the PC task.
Jarvis, remember that I prefer short answers.
Jarvis, recall my answer preferences.
Jarvis, create a text file named shopping.txt containing milk and eggs.
Jarvis, edit shopping.txt and replace milk with oat milk.
Jarvis, find the document about vacation accommodation.
Jarvis, set the volume to thirty percent.
Jarvis, read my clipboard.
Jarvis, show my alerts.
Jarvis, what is my IP address?
Jarvis, what is my local IP on Wi-Fi?
Jarvis, what is my front facing VPN address?
Jarvis, use PowerShell to list the running services.
Jarvis, run Get-Date in PowerShell.
Jarvis, read the PowerShell output.
Jarvis, cancel the PowerShell command.

## PowerShell and IP addresses

PowerShell diagnostics run in a separate background job. You can keep talking,
check progress, or cancel. Activity shows the actual command, output and errors.
The command host supports local read-only diagnostic Get commands, hardware CIM
classes, and literal filtering/sorting/formatting pipelines. Scripts, executable
launches, writes, remote sessions and email commands are unavailable through this
host. Existing dedicated app, file and settings tools handle those local changes.
Commands time out after 20 seconds. Output is limited to 200 rows per pipeline
and 12,000 characters. Diagnostic logs record timing/status, not commands/output.
No administrator elevation or execution-policy changes are made.

IP requests distinguish local adapter addresses from public IPv4 and IPv6.
Public addresses come from ipify through the normal connection. A VPN or proxy
may provide that address; split tunneling means another app can have a different
address. VPN detection from adapter names is only a hint. The underlying raw ISP
public IP is not independently verified: Jarvis reports that limitation rather
than labeling a private LAN or VPN address as the real ISP address. Asking about
IPs never disconnects the VPN or forces traffic around it.

## Orb controls

A compact progress indicator appears above the orb while Jarvis is working. It
shows the current task, live stage updates and a moving activity bar. It stays
through the spoken acknowledgement until the work finishes, then disappears.
Multiple tasks share the indicator; hiding Jarvis also hides it. The full task
history and results remain available from Activity in the right-click menu.

Insert: turn Jarvis on or off. On shows his orb and open panels and enables wake
detection. Every spoken command still needs Jarvis at the start. Off hides him and
stops all listening: the microphone closes and even the wake word
is ignored until you press Insert again. Off is not a break: an answer already being
spoken finishes, reminders and background tasks keep running, and the mute setting
is unchanged. If Jarvis is on an explicitly requested break, Insert wakes him. The key works while another app has focus. Holding Insert
only toggles once. While Jarvis is running, Insert is reserved exclusively for
Jarvis, including Ctrl/Shift/Alt+Insert; overwrite, Insert-copy and Insert-paste
shortcuts are suppressed. Numpad 0 still types zero when Num Lock is on. Quitting
Jarvis releases the key. This needs Jarvis running on your normal Windows desktop.

Click: enable wake detection. If the microphone is unavailable, open typed input.
Double-click: open the conversation panel to type a command and see this
session's questions, answers, notices, finished tasks and source links.
Right-click: type a command, stop the current response, mute, hide, Activity (the
console, with saved projects, reminders, alerts and browser controls), Settings and
quit. Use the arrow keys and Enter in the menu, or Escape to close it. Starting at
sign-in, the orb's screen and the diagnostic logs are in Settings > General.
Drag: move the orb temporarily. Orb position in Settings > General returns it to a screen corner.
Say Jarvis, cancel to discard the current spoken request.
Say Jarvis, stop listening to mute; unmute through the orb menu.
Say Jarvis (or Hey Jarvis) followed by the question in the same spoken turn.
Voice activation preserves that sentence's audio and never enables wake-free
follow-ups. Insert-off, mute and breaks keep voice activation disabled.

The orb prefers an embedded laptop panel over an external primary monitor.
Blue is ready, green listening, violet thinking, pale blue speaking, grey muted
and amber a problem. Right-click to see the current state.
A small violet point circles the orb while a background search is running.

## Interface

Jarvis's windows are an Electron app in desktop/ui. desktop/orb.py still runs the
assistant (voice, Insert key, tasks and the local service) and starts the interface
as a child process. They talk over a loopback socket that only accepts the key
created for that launch; no new network port is exposed. If the interface stops,
Jarvis restarts it and keeps listening.

The orb is drawn on the GPU with true transparency; only the orb itself takes the
mouse. Above it, cards show task progress, reminders that are due and alerts. They
never take focus. Double-click the orb for the conversation panel. The console
(Activity, projects, reminders, alerts, file results and browser controls) and
Settings (general, briefing and files, memory, monitoring, microphone and speech,
models, email) are ordinary windows that remember their size and position.
Window positions and browser data stay in .cache/jarvis-ui/profile.

Microphone and speech has an Input level bar that moves with your voice, so you can
see whether Jarvis hears the microphone he is using. It stays flat while Jarvis is
muted. Only the level is read to draw it; no audio is kept.

## Jarvis knows his own settings

Every request Jarvis answers includes his live settings: lock in mode with its blocked
sites and attempt count, the microphone and speech recognition, startup, task popups,
the orb screen, proactive and battery alerts, the daily briefing, his folders, email
and the saved models. Ask why he keeps closing X, whether alerts are on or which
microphone he uses, and he answers from those settings. They are read fresh for each
request, so changes in the settings window count straight away.

He can change a few by voice when you ask: lock in mode on or off, low battery
warnings on or off, the daily briefing time or off, weather units, and automatic,
offline or online speech recognition. For example, say Jarvis, turn off lock in
mode. Proactive monitoring and models keep their own voice controls. Everything else,
such as folders, the vault and startup, stays in the settings window. An open
settings page refreshes after a voice change unless it has unsaved edits.

## Diagnostic logs

Open Settings > General and choose Open logs. The main event log is
logs/jarvis-events.jsonl. Each line records a timestamp, process ID and event:
microphone input levels, whether speech passed the wake/listening gate, Groq
transcription results and error codes, model routing, response timing, speech
synthesis, playback completion, Insert toggles and shutdown. If Jarvis hears
speech but ignores it, the log records why. It stores counts and status, not
audio recordings, transcript/reply text, passwords or API keys. It rotates at
one MiB with three backups, keeping about four MiB in total. Startup failures
also appear in logs/desktop-stderr.log, and interface errors in logs/ui-stderr.log.

## Models and background research

Right-click the orb and choose Settings > Models. Conversation controls normal
replies. Background search and summarizing can use the same model or another
model from your signed-in Codex model list, including available Sol and Astra
models. Choices persist in desktop/model-settings.json as defaults. Each request
gets an AI assessment of its meaning and reasoning needs. Clear routine requests
use the default. Substantial defined reasoning can use Sol with medium reasoning;
confusing intent or difficult analysis can use Astra with high reasoning. Model
families resolve against your signed-in catalog, currently GPT-6.1 Sol and GPT-6
Astra. An explicitly selected stronger default is not downgraded automatically.
The saved defaults are not rewritten by these per-request choices. Missing
essential details produce a clarifying question with no action tools. The panel
shows the actual answer model and reasoning effort; Activity records job models.
If a requested family is unavailable, the returned model and fallback are reported.
Account usage limits apply. Selection guidance: https://developers.openai.com/api/docs/guides/model-selection

Search jobs first turn the question into focused keywords and one to three
queries, preserving names, dates and constraints. A separate worker searches,
extracts text from up to three sources, and summarizes up to five sources against
the original question. Web summaries show source links. Mail summaries use
read-only mailbox tools. The planner and summarizer have no action tools.

Two workers can search while the foreground conversation stays available.
Up to four searches can run or queue at once. Ask for progress or results,
or choose Activity to inspect keywords, queries, actual stages,
sources, warnings and the finished summary. A search completion appears in
that view without speaking over your current conversation. You can read any
selected result aloud or cancel it. Failed searches are reported as failures.

## Running apps and processes

Ask naturally: Is Microsoft Edge open? What is running in the background?
Can you find hidden processes on my computer? The AI interprets the request,
then Jarvis takes a fresh read-only Windows process snapshot. Specific app and
PID questions search the full inventory, with Edge distinguished from WebView.
An app with an open window is different from a process running with no window;
minimized windows count as open. If desktop window access is unavailable,
Jarvis can report processes but cannot claim to know the window state.

Ask How much memory is my computer using? for total, used and available RAM and
the percentage in use. How much RAM is Edge using? sums its matching processes;
What is eating my RAM? ranks the largest measured app groups. Each answer takes
a fresh measurement. App figures are resident working sets, including shared
pages, so they may differ from Task Manager's private-memory column and must
not be added together to estimate total system RAM. Protected or exited processes
are reported as unavailable or partial, never as using zero memory.

The snapshot includes executable names, counts, sample PIDs, available paths,
system RAM counters and per-app RAM usage.
It goes to your selected model for that answer in a separate session with no
action or web tools. No screenshots, window titles, command lines, or memory
contents are read for this check. Large inventories are bounded and truncation
is reported. This does not stop processes or scan for malware/rootkits: normal
background processes and inaccessible paths alone are not evidence of malware.
It cannot establish that no process is concealed from Windows.
Windows process API: https://learn.microsoft.com/en-us/windows/win32/toolhelp/taking-a-snapshot-and-viewing-processes

## Screenshot and click tasks

Ask naturally to look at your screen, describe a snapshot, or explain what is
visible. A direct observation path takes one fresh image, displays a preview and
answers from the image immediately. It has no click or web tools. The current
display follows the active window. You can ask for monitor 2, the left/right
display, primary, laptop, or all screens. An unavailable display is reported;
it never silently substitutes the primary screen. Full captures include the taskbar. Naming an open
app, such as What does Proton VPN say?, captures that app's own window even when
other windows cover it; a minimized app is restored without taking focus. An app
closed to the system tray must be opened first. Screenshots are
kept in memory and sent to the selected model only for your requested answer.
This is not continuous screen monitoring. If Windows blocks capture, Jarvis gives
the actual error instead of claiming it saw the screen.

Jarvis can carry out a goal in Windows apps and existing browser windows. The
model interprets the complete request, discovers installed apps and open windows,
then chooses which app to launch or focus and which control to use. This does not
depend on product-specific command rules. For example, ask to open your browser
and search for a topic, find a folder in your storage app, or write a note in an
editor. He can change between apps when the requested task needs it.
Computer tasks start with an overview of every connected display. The live window
inventory identifies each window's monitor, including displays left of or above
the primary screen. Jarvis can inspect any monitor more closely before acting.
For "minimize everything on the second monitor", he minimizes matching windows
directly and verifies their minimized state with Windows, leaving other monitors
alone. This also works for ordinary windows whose contents cannot receive input.
If the initial call is refused, Jarvis tries the window's normal minimize command
and checks its actual state. A refused window is recorded by name while the
remaining requested windows continue. Partial completion is reported as such.
Progress questions use the same task records as Activity, including completed
steps and failures, even after the conversation moves to a different topic.
Each step inspects the app's live UI Automation controls and a fresh screenshot.
The model discovers buttons, menus, fields, selections and toggles, then chooses
the controls needed for the complete request. It can invoke buttons, replace
field values, select items, toggle options, expand/collapse controls and scroll
items into view. These actions use the app's own supported control patterns and
do not depend on app-specific recipes. Controls are checked again before use;
old IDs and unsupported actions are rejected. A launch already requested by the
task is not repeated while waiting for its window.

Screenshots, clicks, typing, shortcuts and scrolling remain available when the
app does not expose usable native controls. App-window captures can show its
pixels behind overlapping windows; screen-capture fallbacks are labelled so the
model can account for occlusion. Completion requires fresh evidence of the whole
result. Tasks run in a background worker. Switching conversation models does not
change the model of an already running PC task.

Activity opens for each new task and shows its real stages, completed steps and
latest screenshot. Minimize hides the window. Close leaves the task running.
Reopen it from the orb menu. Cancel task stops it; stop or stop the PC task also
works locally while Jarvis is idle and a PC task is active, even if Groq is unavailable. Moving the
pointer to the top-left corner stops further input. Popups do not steal app focus.

Typing works in the selected app's text fields, including browser search/address
fields. Dictated wording is preserved; an explicitly requested writing task can
generate text. Common application shortcuts support selecting, finding, copying,
pasting, saving, navigation and tabs. Jarvis verifies the screen after each step.
Volume, mute/unmute, media keys, supported laptop brightness and
clipboard text have separate local controls. Clipboard text is kept out of public
web sessions. Dictated clipboard writes use only text from the current command.

Input stays inside the selected app. Native control actions can work in the
background. Pointer/keyboard actions reacquire focus and require a fresh view
before sending input if another app took focus. A changed window identity or
stale screenshot stops input. Native mail, terminal and credential windows cannot receive
PC input; dedicated read-only mail tools and bounded browser tools remain
separate. Elevated windows and administrator prompts are unsupported. Screenshots
and the selected app's control descriptions are sent to your chosen model for
the requested task and kept only in task memory. Password controls are excluded
from native control discovery. UI provider calls have a timeout and can be
cancelled; unavailable providers leave screenshot interaction available.
tests/verification/verify-app-interaction.py runs the real model against a
disposable native app: launch, fill a field, toggle an option, invoke a button,
and verify the resulting preview. Its --live option accepts an explicit command
that operates real installed apps. Install desktop/requirements.txt to include
the uiautomation dependency.
tests/verification/verify-multimonitor.py checks real capture and minimization using temporary
test windows on two connected monitors. Add --model to verify request interpretation
and the model-to-Windows action loop using synthetic images and only those fixtures.

## Reminders, interruptions and morning briefing

Reminders and timers is in the console (orb menu > Activity). Create reminders by voice or typed command,
list them, snooze them, dismiss an occurrence, or cancel the whole recurrence.
Ask for one in some time (in twenty minutes), at a clock time on any day (at 3 pm,
at 8 am tomorrow, the day after tomorrow at noon), or for a morning or a day without
a time (in the morning, tomorrow, the day after tomorrow, on Monday).
Reminders persist in .openclaw/reminders.json. Missed reminders appear when Jarvis
next runs. Recurrences support daily, weekdays, weekly and fixed intervals.

A reminder waits for you. Its popup and voice come at your first mouse or keyboard
input after its time, so one that falls due while you are away is kept until you
are back; input from before its time does not count. Behind Alpenlock or the
Windows lock screen it waits until you are past it, and its voice waits until the
current answer finishes. A reminder for the morning is due at 5 a.m. It comes with
the greeting you get when you first come back to the computer that day (see Welcome
back), even if you are up before five, and otherwise at your first input after five.
Jarvis must be running and the computer awake.

The microphone continues receiving while Jarvis is thinking or speaking. You can
ask another question before the current reply finishes by saying Jarvis first.
An accepted new question cancels the unfinished answer; that new answer can
also be interrupted. Background searches continue unless you ask to stop them.
During replies a dedicated local wake detector pauses playback to hear you,
even when the general recognizer mishears the name or hears playback first.
Whisper must confirm the wake name before an interruption is accepted. A tool-free AI
changes the answer only for a clear request directed at Jarvis: stop, shorten,
take a break or answer a new question. Background chatter, acknowledgements such
as yeah or okay, and uncertain intent resume the current reply. No fixed
stop-phrase list is required. The wake name pauses speech; the full utterance
decides whether to stop, change the answer or resume.
Stop produces no spoken acknowledgement. If transcription or interpretation
fails, the reply resumes and Jarvis shows the error. Stop current response in
the menu remains available without a model or transcription connection.
Audio uses small cancellable buffers. Recognized playback echoes are ignored;
real microphone/speaker echo behavior still needs a normal-desktop check.

## Taking a break

Ask Jarvis to go away, take a break, or turn itself off for whatever duration you
want. The AI interprets the complete request, including indirect wording and
times such as a quarter of an hour. The orb and Jarvis windows hide, the microphone
stream closes, active tasks are cancelled, and alerts/briefings wait until return.
A small timer remains running to bring Jarvis back. Relative durations and
absolute return times are supported. Without a time, Jarvis stays away until you
open start-jarvis.cmd. That command also ends a timed break early. Windows startup
respects an existing break. The deadline persists in .openclaw/break.json and is
checked after sleep or restart; the computer must be awake for on-time return.
This pauses Jarvis, not Windows. A reminder for you to take a break stays a reminder.

Morning briefing combines weather, saved reminders and a sample of unread inbox
messages. It does not use calendars. Ask for it on demand, or set a daily time
in Assistant settings. The automatic briefing requires Jarvis running; it can
catch up within four hours of the chosen time. Unavailable sections are reported.

Weather uses an HTTPS IP-location lookup explicitly bound to a physical network
adapter. It does not change VPN settings or routes. A detected VPN address is
rejected, and a blocked physical connection has no VPN-location fallback.
IP geolocation is approximate; a VPN on the router cannot always be distinguished
from the ISP connection. The detected IP is not stored or sent to the model.
Open-Meteo supplies the forecast. Choose Fahrenheit or Celsius in Assistant settings.

## Welcome back

When you come back after six hours or more without touching the mouse or keyboard,
Jarvis greets you. On your first input he flies to the middle of your main monitor,
even if he normally lives on another one, grows to about two and a half times his
size, shows the greeting on a card and says it. It fits the time of day: good
morning, good afternoon, good evening, or welcome back late at night. It is built
around a real quotation that Wikiquote confirms, the same check focus reminders
use, and ends by getting you started. Then he flies back to his corner.

If Alpenlock or the Windows lock screen is up when you return, Jarvis writes the
greeting while you type your password and waits until you are past it. If Jarvis
was not running while you were away, for example because the computer was off
overnight, he greets you shortly after he starts, as long as you are at the
computer. Only the time you were last active is kept, in .openclaw/presence.json.
When Jarvis is muted he shows the greeting without speaking; on a break he skips
it. Without the model he still says a short greeting for the time of day.

Reminders that are due when he greets you, and one you asked for that morning, are
part of the greeting: he passes them on in his own words straight after it, and
each one's card comes up beside it. If his wording leaves one out, he says it
plainly instead. If he could not say the greeting, for example while muted, he
says those reminders on their own once he can.

## Lock in mode

Lock in mode keeps X and Instagram closed. It is on by default; switch it in
Settings > Lock in. When the browser tab in front of you opens x.com, twitter.com
or instagram.com, Jarvis closes that tab, the orb flies to the middle of the
browser window with a Lock in card. Jarvis counts the attempt and says a fresh
line for that visit, instead of one repeated scolding. If the line cannot be
written in time, he uses a short fallback that includes the count. The orb then
returns to its usual place. If the same window shows a blocked site again within
three seconds, as with a tab that refuses to close, Jarvis sends that tab to a
blank page instead. After closing a tab, Jarvis gives the browser's address bar
focus, because Opera GX otherwise ignores keyboard shortcuts after a scripted tab close.

LinkedIn and YouTube stay open during lock-in. When you start a session on one of
them, Jarvis gives one short reminder, shows it beside the orb and speaks it. It is
built around a real quotation that suits what you opened, such as the video in the
tab title. The model proposes the quotation and Jarvis only uses it once Wikiquote's
sourced quotes confirm those exact words for that person, so nothing is made up or
misattributed. Recent quotations are not repeated.

You hear that reminder once per session. The session lasts while any tab of the
site stays open, in any browser window: switching apps, switching tabs, watching
more videos or leaving it playing in the background never repeats the reminder.
Jarvis reads the browser's tab titles to know the site is still open, and when he
cannot read them he assumes it is. A new session, and a new reminder, starts only
once every tab of the site has been closed for 15 minutes, or after 4 hours without
going back to it. Restarting Jarvis does not reset the session. If the reminder
cannot be written within two minutes of arriving, Jarvis skips it for that session.

When you want one visit, ask for it: say Jarvis, give me one time on Instagram, one
time on X, or just one scroll. Jarvis grants a single pass in his own words. Lock in
mode stays on. Your next visit to that site, or to either site if you named neither,
stays open until you have been off it for three minutes, and later visits are closed
as usual. A short look at another window does not end the visit, a pass you do not
use lapses after ten minutes, and a new request replaces the old pass. The allowed
visit is not counted as an attempt. Saying turn off lock in mode switches the whole
mode off instead.

Jarvis reads only the address bar of the browser window in front of you, using
Windows UI Automation when its title looks like X, Instagram, LinkedIn or YouTube. Typing
x.com without opening it is never blocked, and nothing happens while Shift, Alt
or the Windows key is held. Addresses and page titles are never saved or logged.
The attempt count is stored in `.openclaw/lock-in.json`, and diagnostics record
which site was blocked, how, and the attempt number, not the address or the
spoken line. It was verified with
Opera GX and Chrome. Opera, Edge, Brave and Vivaldi use the same Chromium address
bar and are watched too, and Firefox-family browsers are included, but those were
not tested. Lock in pauses while Jarvis is on a break. The tab is still
blocked when Jarvis is hidden or muted, but a hidden orb stays hidden and a muted
Jarvis stays silent, as with spoken reminders. tests/verification/verify-lock-in.py
checks the whole flow with a throwaway Chrome or Opera GX profile.

## Local file search

Choose folders in Assistant settings before searching. Jarvis searches file and
folder names inside those roots, including creation/modified date filters, and
shows matches in a file-results window. Search does not follow directory links
or junctions. Removing a folder also revokes existing read references.
Select a result to summarize it, or ask by voice. Supported reading formats are
PDF, DOCX, XLSX, plain text, Markdown, CSV/TSV and text-based code files. Scanned PDFs
and PNG/JPEG/TIFF images have local OCR, bounded to eight PDF pages. Searches and
document reading are bounded; partial results and truncation are reported.
Document changes use separate tools and owner-selected writable folders. Requested document text goes
to your selected model, in a session separated from public web/browser tools.

## Obsidian memory, editing, content search, and local alerts

The new Jarvis Brain folder is an Obsidian vault. Open it with scripts/windows/open-jarvis-brain.cmd
or Memory and local tools > Open saved vault. Jarvis uses the Markdown files
directly; Obsidian does not have to remain open. This is independent of VibeCode
Second Brain, which remains disabled. Choose another vault in the same settings.
An official Obsidian 1.14.4 Windows build is extracted into .runtime/obsidian;
the release SHA256 was verified. Its launcher keeps the app profile under this
project. It does not require a system-wide Obsidian install or a memory plugin.

Say remember that followed by a fact to save it. Recall searches the saved notes;
updates read the current revision before writing. Forget moves a selected note
to Jarvis Archive, removing it from recall while preserving a recoverable copy.
Only explicitly requested memories are saved. Conversations are not automatically
recorded. Requested memory text is sent to the chosen model in a private session
without public web, browser or email tools. Obsidian edits are visible on the next
recall. Jarvis Memories contains the searchable notes; other vault folders are
not automatically imported into memory.

Jarvis Files is the initial writable folder. Memory and local tools > Writable
folders controls additional write access; the older file-search roots stay read
only unless separately selected for writing. Supported creation/editing formats:
TXT, Markdown, CSV, TSV, Word DOCX paragraphs and Excel XLSX cell values. Jarvis
can append, replace one exact occurrence, update cells, rename, and move documents
between writable folders. New files never overwrite existing names. Edits require
the file's current revision and keep recovery copies in .openclaw/file-backups.
Spreadsheet strings are literal text; formula generation and macro editing are
not implemented. Native editor actions remain separate from these file tools.

Document-topic queries use local BGE small embeddings and text matching over a
local cache in .openclaw/document-index.sqlite3. Filename/date queries still use
the original search. First content queries index up to 60 changed files, with
5000 scanned names and a 20-second scan budget. Repeat a partial search to expand
coverage; an individual OCR/model operation can take longer than this scan budget.
Text is bounded to 24000 characters per document. Removed roots cannot return
results, and the next content search clears their cached text. OCR and embeddings
run locally. Matching excerpts or requested documents then go to the selected
conversation model. Search results disclose partial coverage and keyword fallback.

Proactive alerts watch top-level files in Downloads and Jarvis Files by default,
plus low battery at 20 and 10 percent. Existing files establish a baseline without
an initial flood of alerts. A new file is announced after an unchanged follow-up
scan; temporary download extensions are ignored. Scans run every 15 seconds while
Jarvis is active and the computer is awake. Folder choices, battery alerts and
pause/resume are in Memory and local tools > Monitoring. Alert windows do not
take focus or speak over you. Proactive alerts in the console shows recent alerts.
Hidden UI keeps collecting alerts; requested breaks pause monitoring. There is no
continuous screen capture, inbox monitoring or notification-content collection.
Alert history and baselines are in RAM, capped at 100 alerts, and reset on restart.

## Offline speech and basic commands

Whisper small (the English-only small.en model) is downloaded to
.runtime/models/whisper-small.en and runs through faster-whisper. With an NVIDIA GPU
it runs on CUDA in float32, using NVIDIA's cuBLAS 12 libraries from
desktop/gpu-requirements.txt; without one, or if CUDA fails, it falls back to CPU
int8 inference. Jarvis loads the model in the background at startup, so the first
command does not wait for it. The BGE document-search model is downloaded
under .runtime/models/embeddings. Sources, revision and SHA256 values are recorded
in .runtime/models/manifest.json. Runtime inference uses local files only.

Automatic speech mode transcribes locally with Whisper small first and uses Groq
only if local Whisper is missing or fails. Choose offline in Voice input > Speech
recognition to never use Groq, or online to require Groq. On this machine's GTX
1660 SUPER a spoken command takes about 0.37 seconds to transcribe locally, against
about 0.73 seconds for Groq; CPU-only inference takes about 2.4 seconds.
Piper still supplies the same Jarvis speaking voice locally. No replacement voice
was required. Speech mode controls transcription; general conversation still uses
the selected online AI model.

Commands are interpreted by the selected online AI. Its tool choices and actual
tool results drive the task; product names and keyword scripts do not override
that interpretation. If the AI service fails, Jarvis reports the failure instead
of running a local command parser. Offline voice changes transcription only.

The dependencies are installed under .runtime/features-python. To reinstall them
and the models, run scripts/windows/setup-local-features.cmd; it adds the CUDA
libraries when nvidia-smi is present. Calendar integration and workday
routines remain absent as requested; email remains on hold.

## Verification on November 13, 2026

- Twelve synthesized Jarvis commands at 1.0x and 1.5x voice speed went through
  Listener.recognize in automatic mode. Local Whisper small on CUDA took a median
  0.37 seconds (maximum 0.40) and kept the Jarvis wake name in all twelve.
- The same clips through Groq Whisper large v3 took a median 0.73 seconds (maximum
  1.27) and heard the name as Chavez or Javas in six of the twelve.
- Background model loading and GPU warm-up took about 2.2 seconds.
- verify-local-features.py passed with Whisper small on CUDA.

## Verification on October 12, 2026

- 124 Python tests and three model-selection tests passed.
- Actual local OCR extracted a PNG invoice and a scanned PDF.
- Actual local semantic search matched vacation accommodation to holiday/hotel text.
- Actual local Whisper medium transcribed a synthesized Jarvis reminder command.
- The live AI tool loop saved/recalled an Obsidian memory, created/edited a file,
  and found/read it by content, using isolated ports and temporary fixtures.
- New settings tabs, save, vault setup, alert history, non-focusing alerts and
  Insert hiding the new windows passed native Tk UI checks in the coding desktop.
- Existing task-popup and break UI checks passed. Windows volume readback passed.
- Screen questions failed on every real display because the monitor list had no
  display bounds; fixed and checked against the real monitor list. A native check
  captured a covered test window from its own pixels (0% of the covering window;
  the old screen-pixel method saw only the cover). verify-window-capture.py repeats it.
- Insert off now closes the microphone and ignores speech and the wake word until
  Insert on; verify-insert-key.py and unit tests cover it. The live Proton VPN
  Connect click still needs a check from your normal desktop.
- Physical microphone, dictated native-app input, brightness changes and the
  visible orb still require a normal Windows launch. Run start-jarvis.cmd there
  to load the latest desktop and model services. No desktop service was restarted here.

## Browser actions

Ask Jarvis to visit a named website or full URL. A dedicated browser window
supports page reading, link navigation, scrolling and filling fields
with values dictated in the current command. It uses a separate browser context.
The model receives current element references rather than arbitrary JavaScript.
When you name a browser, Jarvis uses the desktop worker to operate that installed
app. A dedicated browser adapter that cannot control your choice reports that
limitation; it does not silently switch to Edge or Chrome.
Private-network targets, uploads, passwords, payment fields and automatic final
submissions are unavailable. Sites requiring POST-based loading may not work in
this mode. Choose Browser controls > Continue manually to release that browser
for sign-in or submission; Jarvis cannot control it again until its window closes.
The full OpenClaw gateway is not required for these desktop features.

## Feature verification

scripts\python.cmd scripts\run-tests.py
py -3.11 tests/verification/verify-assistant.py
py -3.11 tests/verification/verify-assistant.py --controls-only
py -3.11 tests/verification/verify-desktop-ui.py

The isolated live-model check uses temporary fixtures: semantic stopping, a
persisted thirty-minute reminder, file search/read, and a partial morning briefing.
The controls check exercises natural timed breaks, a synthetic image through the
real vision model, automatic model selection, and live Sol/Astra reasoning. The
interface check drives the real Electron windows: the right-click menu, Activity
opening without taking focus, screenshot previews, hiding every window and resume.
verify-task-progress.py, verify-personal-ui.py and verify-insert-key.py cover the
progress cards, settings and alerts, and Insert against the same windows.
No real reminders or files are changed by these checks. Browser launch and physical-adapter
inspection are blocked by this coding sandbox's Windows permissions; they need
verification from normal Windows. Live microphone interruption is also pending.

## Email is READ ONLY

Jarvis has read/search/account-status tools only. There are no reply, send,
forward, delete, archive or mark-as-read actions. Direct Gmail access requests
gmail.readonly. Direct Microsoft access requests Mail.Read, User.Read and
offline_access, with no Mail.Send or Mail.ReadWrite. The classic Outlook adapter
only reads message properties and does not call Send, Save or Delete.

Mailbox access requires your own sign-in. Neither mailbox was connected during
installation. See docs/email-setup.md or right-click the orb and choose Settings > Email.
Add Gmail and Outlook to the installed Outlook classic, or use direct OAuth with
your own Google/Microsoft desktop app registrations. OAuth uses a loopback callback,
state checking and PKCE; saved tokens are encrypted with Windows user DPAPI.
ChatGPT connectors do not automatically connect this separate Windows desktop app.

## App and web tools

App launches resolve to Windows' installed-app list. Unknown or ambiguous names
need clarification. The model cannot run arbitrary shell commands or pass app
arguments. A constrained decoder helps recognize app names, and is used only
when unrestricted speech also recognized an app-launch request.

Web search and page reading use the real Obscura v0.2.4 Windows binary, verified
against the publisher's SHA256. Searches use DuckDuckGo HTML results and show
source links in the panel. Blocked searches are reported, not fabricated.
Obscura's private-network blocking stays enabled. No paid search key is needed.

Email and web tools are separated per request, so an email cannot instruct Jarvis
to launch apps or send mailbox text to a public search. App launches require an
installed app named in the owner's current command. The Python host enforces
these tool restrictions independently of the model prompt.
Email and web conversations also use separate model sessions, so mailbox content
is not carried into a later conversation with public web tools.

## Voice and privacy

The voice remains Jarvis high, British English, at 1.5 times its default speed.
Voice settings are in phone/settings.json. prompts/SOUL.md sets the personality, calls
you sir, and requires plain spoken replies without quotes, slashes, semicolons or
markdown. The model reads prompts/SOUL.md for each turn.

Vosk detects the wake word and speech locally. Activated spoken commands and
voice interruptions are transcribed on this PC by local Whisper small in automatic
and offline modes. Online mode, and automatic mode when local Whisper is missing or
fails, uses Groq Whisper large v3; Microphone settings also offers large v3 turbo.
Jarvis reads the existing encrypted VibeCode Groq key
under this Windows user; it does not save another plaintext copy. Command audio is
held briefly in RAM and uploaded to Groq only when Groq transcribes it.
Command audio is never saved to disk.
If both the selected transcription service and any enabled fallback fail, the
new command is rejected and the error is shown. Speech
interruption intent is decided by the AI; the menu stop control works locally.
The transcript goes to your selected conversation model. Bounded conversation
history stays in RAM and expires after fifteen idle minutes. The last answer
stays in the panel until replaced or Jarvis closes. Requested email content is
sent to your selected model to answer your request. Email bodies and questions are not logged.
Background search data is held in RAM, limited to twelve jobs and removed
thirty minutes after completion. Each background model request is stateless.
Jarvis has an independent Obsidian memory vault described above. There is no
integration with VibeCode's Second Brain extension. Codex subscription availability and limits apply.

## Verification on October 11, 2026

- Luna answered via the desktop API using the existing Codex sign-in.
- A live model tool call used Obscura and returned results with source links.
- Windows accepted an installed Notepad launch through the app tool.
- Jarvis-high synthesis, Vosk and the app decoder recognized Jarvis open Notepad.
- Read-only tests reject email send, reply, forward, delete and mark-read.
- Tests cover app matching, shell-like input, private web targets, mailbox message
  references, wake-word routing and laptop-screen placement. Tests also verify
  concurrent chat during searches, real progress, cancellation, source references,
  email/web isolation and persistent model choices.
- DPAPI encryption/decryption passed a non-secret round-trip test.
- Live spoken-model command routing switched Luna to Sol and restored the setting.
- The selected model understood a test image and returned the correct button
  coordinates. No real input was executed by this vision test.
- Popup checks passed preview, step log, no focus stealing, minimize, reopen,
  close-without-cancelling, and explicit cancellation.
- Live Groq Whisper large v3 transcription passed using the encrypted VibeCode
  key. Jarvis-high at 1.5x said "Jarvis, what is two plus two?" and Whisper
  returned "Jarvis, what is 2 plus 2?" The earlier HTTP 403 is no longer present.
  This used synthesized audio; the physical microphone still needs verification.
- Live microphone, an orb on YOUR desktop, and login startup are pending your
  normal Windows launch; the coding sandbox cannot verify those.
- Email is intentionally off; mailbox sign-in is outside the current setup.

## Commands

setup-jarvis.cmd      Register startup and launch desktop.
scripts/windows/setup-local-features.cmd  Reinstall local document/OCR dependencies and downloaded models.
scripts/windows/open-jarvis-brain.cmd  Open the memory vault in the project-local Obsidian app.
start-jarvis.cmd      Launch desktop without changing startup settings.
scripts/windows/stop-jarvis.cmd       Close orb and microphone. Luna may stay in the background.
scripts/windows/enable-startup.cmd    Enable startup for this Windows user.
scripts/windows/disable-startup.cmd   Remove only Jarvis's startup entry.
scripts/windows/jarvis-status.cmd     Show actual desktop, voice, microphone and startup state.

py -3.11 -m unittest discover -s desktop -p "test_*.py"
py -3.11 tests\verification\verify-desktop-ui.py
.runtime\node-v24.21.0-win-x64\node.exe tests\desktop\test_models.mjs
py -3.11 scripts\desktop-control.py ask "What is two plus two?"

Local APIs bind only to 127.0.0.1 and require the workspace bearer token.
Luna: 18791. Desktop: 18792. Do not expose these ports to the network.
Python 3.11 is required. Sounddevice 0.5.6 lives in .runtime/python, and Obscura
in .runtime/obscura. Existing Vosk, Piper and OpenClaw installations are reused.
Source hashes are recorded alongside the downloaded binaries and voice assets.

## Full gateway

The full OpenClaw gateway remains blocked by private SQLite staging-directory
permissions in the coding sandbox. Desktop Luna uses the installed @openclaw/ai
runtime independently. start-jarvis.cmd --gateway attempts the optional gateway.
Existing version-pinned portability patches preserve locking and ACLs. The full
gateway/dashboard is unverified and is not required by the desktop orb.

## References

https://github.com/h4ckf0r0day/obscura
https://huggingface.co/jgkawell/jarvis
https://python-sounddevice.readthedocs.io/
https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys
https://learn.microsoft.com/en-us/office/vba/api/outlook.namespace.accounts
https://developers.google.com/workspace/gmail/api/quickstart/python
https://learn.microsoft.com/en-us/graph/api/user-list-messages

## Claude backup

If OpenAI's model does not answer (an error, a used-up limit, or no reply in time), Jarvis carries on with Claude.
Claude Haiku 5.5 takes every request first. It does the routing and answers routine requests itself. For a harder
request Haiku chooses Claude Opus 5.5 or Claude Fable 5.2 and how hard that model should think (low, medium or high).
While OpenAI is down Jarvis skips it and checks it again about once a minute. The panel shows which model answered.

The backup uses the standard Anthropic settings saved for your Windows account: `ANTHROPIC_API_KEY`, or
`ANTHROPIC_AUTH_TOKEN` with `ANTHROPIC_BASE_URL`. It is billed to that key. A Claude subscription sign-in does not
work here. Settings > Models shows whether the backup is ready, lets you test it and lets you turn it off.
