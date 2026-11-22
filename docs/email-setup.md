# Connect Jarvis to Gmail and Outlook

Email is currently on hold at your request. Jarvis says it does not have access
when asked. No account setup is needed now. The instructions below are only for
a future setup after you decide to enable email access.

The orb's Email accounts window shows what is actually connected. Jarvis cannot
read either mailbox until you sign in. Do not paste passwords or tokens in chat.

## Option 1: Use Outlook classic, which is installed on this PC

1. Right-click the Jarvis orb, choose Settings, then Email, then Open Outlook classic.
2. Add your Outlook/Microsoft account and your Gmail account in Outlook.
   Use File > Add Account if the first-run account window does not appear.
3. Complete the Google and Microsoft sign-in windows and let the inboxes sync.
4. In Jarvis Email accounts, click Refresh connections.
5. Say: Jarvis, read my latest emails. Or: Jarvis, search my Gmail for receipts.

This adapter uses classic Outlook's profile. The new Outlook app and browser
sign-ins do not expose that profile. Outlook classic may require an Office license.
If it shows a license or organization restriction, use a direct connection below.
Do not disable Outlook security prompts; allow the requested read if prompted.

## Option 2: Direct Gmail connection

1. In your Google Cloud project, enable the Gmail API. Configure an OAuth consent
   screen and, when the app is in testing, add your own Google account as a test user.
2. Create an OAuth client of type Desktop app and download its client JSON.
3. On the orb choose Email accounts > Connect Gmail and select that JSON.
4. Complete Google's sign-in and read-only consent in your browser.
5. Refresh connections and try reading your Gmail inbox.

Jarvis requests gmail.readonly, not send or modification access. Google testing
mode can expire refresh tokens after seven days; reconnect when needed.
https://developers.google.com/workspace/gmail/api/quickstart/python
https://developers.google.com/identity/protocols/oauth2/native-app

## Option 3: Direct Outlook connection

1. Register your own app in Microsoft Entra. For personal Outlook.com addresses,
   select an account type that includes personal Microsoft accounts.
2. Add a Mobile and desktop application redirect URI: http://localhost
3. Add delegated Mail.Read and User.Read permissions for Microsoft Graph.
4. On the orb choose Email accounts > Connect Outlook and enter its Application
   client ID. No client secret is needed for this desktop PKCE flow.
5. Complete Microsoft's sign-in and consent in your browser.

An organization may require its administrator to approve access. Jarvis does not
bypass those controls. Tokens are encrypted with this Windows user's DPAPI.
https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app
https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow

## Email behaviour

Jarvis only reads and searches. Reading does not mark mail as read or send replies.
OAuth searches use the provider's mailbox search. Outlook classic searches are
bounded to the latest 500 messages per folder, 50 folders, and 18 seconds; Jarvis
reports this scope. Gmail aliases under custom Workspace domains may appear as
Outlook accounts in the classic adapter; choose all mailboxes in that case.

Message bodies and subject lines are not written to Jarvis logs. They enter the
current conversation in RAM and are sent to Luna when you request mail help.
The existing Codex sign-in remains the model connection; it does not grant Gmail
or Outlook access. ChatGPT connector sign-ins also do not automatically authorize
this separate Windows desktop app.
