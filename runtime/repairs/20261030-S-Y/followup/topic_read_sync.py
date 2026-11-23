"""Reconcile S's aggregate read pointer only after proving each incoming message was read."""
from telethon import types
from telethon.tl.functions import messages


def safe_plan(dialog, history, topics):
    before = getattr(dialog, 'read_inbox_max_id', None)
    top = getattr(dialog, 'top_message', None)
    count = getattr(dialog, 'unread_count', None)
    result = {'safe': False, 'read_before': before, 'snapshot_top': top, 'read_through': before,
              'unread_count': count}
    if not all(type(value) is int for value in [before, top, count]):
        return dict(result, reason='invalid counters')
    if count == 0:
        return dict(result, reason='already read')
    if getattr(dialog, 'unread_mark', False):
        return dict(result, reason='preserve manually marked unread')
    if count < 0 or top <= before or len(history) >= 100:
        return dict(result, reason='range not safely bounded')
    seen = set()
    incoming = 0
    for message in history:
        ident, outgoing = getattr(message, 'id', None), getattr(message, 'out', None)
        if type(ident) is not int or not before < ident <= top or ident in seen:
            return dict(result, reason='invalid message range')
        seen.add(ident)
        if type(outgoing) is not bool:
            return dict(result, reason='unknown message direction')
        incoming += not outgoing
    if incoming != count:
        return dict(result, reason='unread count does not match bounded incoming range')
    rows, through = [], before
    stop = 'all bounded incoming messages already read in topics'
    for message in sorted(history, key=lambda item: item.id):
        if message.out:
            through = message.id
            continue
        header = getattr(message, 'reply_to', None)
        topic = getattr(header, 'reply_to_top_id', None) or getattr(header, 'reply_to_msg_id', None)
        if not getattr(header, 'forum_topic', False) or type(topic) is not int:
            stop = 'preserve incoming message without proven topic'
            break
        read_max = getattr(topics.get(topic), 'read_inbox_max_id', None)
        if type(read_max) is not int or read_max < message.id:
            stop = 'preserve topic message not yet read'
            break
        through = message.id
        rows.append({'id': message.id, 'topic': topic, 'topic_read_through': read_max})
    return dict(result, safe=bool(rows), read_through=through if rows else before,
                reason=stop, messages=rows)


async def inspect(client, peer):
    response = await client(messages.GetPeerDialogsRequest([types.InputDialogPeer(peer)]))
    if len(response.dialogs) != 1:
        return {'safe': False, 'reason': 'expected one exact S dialog'}
    dialog = response.dialogs[0]
    if getattr(dialog, 'unread_count', None) == 0:
        return safe_plan(dialog, [], {})
    before, top = getattr(dialog, 'read_inbox_max_id', None), getattr(dialog, 'top_message', None)
    if type(before) is not int or type(top) is not int or top <= before:
        return {'safe': False, 'reason': 'invalid dialog range'}
    response = await client(messages.GetHistoryRequest(
        peer=peer, offset_id=0, offset_date=None, add_offset=0, limit=100,
        max_id=top + 1, min_id=before, hash=0))
    # Access routing metadata only. Never inspect text, media, entities or forwarded content.
    history = response.messages
    ids = set()
    for item in history:
        header = getattr(item, 'reply_to', None)
        topic = getattr(header, 'reply_to_top_id', None) or getattr(header, 'reply_to_msg_id', None)
        if getattr(item, 'out', None) is False and getattr(header, 'forum_topic', False) and type(topic) is int:
            ids.add(topic)
    topics = {}
    if ids:
        response = await client(messages.GetForumTopicsByIDRequest(peer=peer, topics=sorted(ids)))
        topics = {getattr(topic, 'id', None): topic for topic in response.topics}
    return safe_plan(dialog, history, topics)


async def reconcile(client, peer, *, apply=False):
    plan = await inspect(client, peer)
    if not plan.get('safe') or not apply:
        return dict(plan, applied=False)
    latest = await client(messages.GetPeerDialogsRequest([types.InputDialogPeer(peer)]))
    if len(latest.dialogs) != 1:
        return dict(plan, safe=False, applied=False, reason='dialog changed before write')
    current = latest.dialogs[0]
    if getattr(current, 'unread_mark', False):
        return dict(plan, safe=False, applied=False, reason='preserve newly marked unread')
    if any(getattr(current, key, None) != plan[value] for key, value in
           [('read_inbox_max_id', 'read_before'), ('top_message', 'snapshot_top'), ('unread_count', 'unread_count')]):
        return dict(plan, safe=False, applied=False, reason='dialog changed before write')
    await client(messages.ReadHistoryRequest(peer=peer, max_id=plan['read_through']))
    after = await client(messages.GetPeerDialogsRequest([types.InputDialogPeer(peer)]))
    return dict(plan, applied=True, unread_after=[getattr(row, 'unread_count', None) for row in after.dialogs])
