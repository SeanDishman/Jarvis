function phaseOf(part) {
  if (part.phase === 'commentary' || part.phase === 'final_answer') return part.phase;
  try {
    const signature = JSON.parse(part.textSignature);
    if (signature?.v === 1 && ['commentary', 'final_answer'].includes(signature.phase)) return signature.phase;
  } catch {}
  return undefined;
}

export function finalResponseText(response) {
  const text = (response.content ?? []).filter(part => part.type === 'text' && typeof part.text === 'string');
  const final = text.filter(part => phaseOf(part) === 'final_answer');
  const selected = final.length ? final : text.filter(part => phaseOf(part) !== 'commentary');
  return selected.map(part => part.text).join('').trim();
}

export const desktopReplyFormat = {type:'json_schema',name:'desktop_reply',strict:true,
  schema:{type:'object',properties:{reply:{type:'string'},status:{type:'string',enum:['completed','started','failed','needs_clarification']}},
    required:['reply','status'],additionalProperties:false}};

export function desktopReply(response) {
  const result = JSON.parse(finalResponseText(response));
  if (typeof result.reply !== 'string' || !result.reply.trim() ||
      !desktopReplyFormat.schema.properties.status.enum.includes(result.status)) {
    throw new Error('The desktop model did not report a valid outcome');
  }
  return {reply:result.reply.trim(),status:result.status};
}
