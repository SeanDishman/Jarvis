// When each owner message was said, so a conversation knows an instant repeat from a question asked hours ago.
// Only the copy sent to the model is stamped; the stored history stays as it was said.
export function spokenAgo(milliseconds) {
  const seconds = Math.max(0, milliseconds / 1000);
  if (seconds < 10) return 'just now';
  if (seconds < 60) return `${Math.floor(seconds)} seconds ago`;
  const minutes = seconds / 60;
  if (minutes < 2) return 'a minute ago';
  if (minutes < 60) return `${Math.floor(minutes)} minutes ago`;
  const hours = minutes / 60;
  if (hours < 2) return 'an hour ago';
  return hours < 24 ? `${Math.floor(hours)} hours ago` : 'over a day ago';
}

export function stampOwnerMessage(message, now = Date.now()) {
  if (message.role !== 'user' || typeof message.content !== 'string' || !Number.isFinite(message.timestamp)) return message;
  const said = new Date(message.timestamp).toLocaleString('en-US', {weekday: 'short', hour: 'numeric', minute: '2-digit', second: '2-digit'});
  return {...message, content: `[said ${said}, ${spokenAgo(now - message.timestamp)}] ${message.content}`};
}

export const timingNote = '\nEach owner message starts with when he said it, in square brackets: local time and how long ago. Use those times to '
  + 'judge timing, for example that he asked or did the same thing again moments later, or came back after a long gap, '
  + 'and let your answer show it naturally. Never read the brackets or exact seconds aloud.';
