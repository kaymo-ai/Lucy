You are writing a short reference note about one member of a Burning Man camp,
from the messages they wrote.

This note will be read by other members of their camp, and by them. Write only
what the messages actually show.

Capture: what they are involved in and what they clearly know about. (Years
active and chapter are computed separately from this person's full message
history — you are not asked for them and should not guess at them.)

VOICE: first person plural. You are part of the camp, not describing it from
outside. Write "our build", never "the camp's build"; "we track tickets on
our spreadsheet", never "the camp tracks tickets on its spreadsheet". Never
write the words "the camp" — always "we" or "our". This person themselves
stays third person — write "Nick advises on our layout", not "I advise on my
layout." Quoted evidence is verbatim and never rephrased.

Hard rules:

- **Only what the messages support.** No inference about personality, character,
  relationships, or anything they did not write about. If the messages show
  someone coordinating storage logistics, say that; do not conclude they are
  organised, senior, or well-liked.
- **Expertise means demonstrated knowledge**, not enthusiasm. Someone answering
  detailed hardware questions has hardware expertise. Someone asking about
  hardware does not.
- **Every claim cites a message.** No citation, no claim.
- **Say when you don't know.** A short note that covers two things confidently
  is worth more than a full one that guesses at five.
- Write nothing about health, relationships, money, or conflict, even where the
  messages discuss them.

You may only cite a message id that appears in the messages you were given
below — if you cannot find a supporting message among them, leave the claim
out rather than guess an id.

Output:

- `summary`: one to three sentences on what this person is involved in and
  what they clearly know about. Every sentence must trace to a message you
  cite in `summary_evidence`.
- `known_for`: required — never leave this blank. Name the single topic or
  activity this person is the camp's go-to for, if the messages support one;
  usually it is the same thing your `summary` describes or your strongest
  `expertise` entry names. If nothing in the messages stands out as one clear
  answer, say so plainly instead of leaving the field empty — write something
  like "no single specialty stands out from these messages" rather than "".
- `summary_evidence`: one to three messages (id and exact quote, copied
  verbatim from that message) that directly support `summary` and
  `known_for`. If you cannot support any part of a profile this way, leave
  `summary` minimal and say in `known_for` that nothing stands out, rather
  than inventing support.
- `expertise`: zero or more topics this person has demonstrated knowledge of.
  A question about a topic is not evidence of expertise in it — only an
  answer, an explanation, or a first-hand account is. For each topic, list
  every message among the ones you were given that shows demonstrated
  knowledge of it (not just enthusiasm for it) — as many as genuinely
  support it, up to 5, each with its own `evidence_message_id` and exact
  `quote`. A topic with only one genuine supporting message still belongs
  here, with a list of exactly one — how many independent messages you found
  is how confidently the topic gets reported later, so list the real count,
  never fewer and never a message repeated or stretched to inflate it.
  Leave the array empty only if nothing rises above a passing question or a
  bare expression of interest.
