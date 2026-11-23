from types import SimpleNamespace as NS
import unittest
from telethon import types
from telethon.tl.functions import messages
from topic_read_sync import safe_plan, reconcile


def incoming(ident, topic=7, *, outgoing=False, forum=True):
    class Message:
        id = ident
        out = outgoing
        reply_to = NS(reply_to_top_id=topic, reply_to_msg_id=5, forum_topic=forum)

        @property
        def message(self):
            raise AssertionError('Never read a real message body')
    return Message()


class PlanningTest(unittest.TestCase):
    def dialog(self, **changes):
        return NS(**dict(read_inbox_max_id=10, top_message=13, unread_count=3, unread_mark=False) | changes)

    def test_all_read_topics_clear_aggregate_without_inspecting_text(self):
        plan = safe_plan(self.dialog(), [incoming(i) for i in [13, 12, 11]], {7: NS(read_inbox_max_id=13)})
        self.assertTrue(plan['safe'])
        self.assertEqual(plan['read_through'], 13)

    def test_new_unread_message_stays_unread_while_old_read_prefix_syncs(self):
        plan = safe_plan(self.dialog(), [incoming(13, 8), incoming(12), incoming(11)],
                         {7: NS(read_inbox_max_id=12), 8: NS(read_inbox_max_id=10)})
        self.assertTrue(plan['safe'])
        self.assertEqual(plan['read_through'], 12)
        self.assertEqual([x['id'] for x in plan['messages']], [11, 12])

    def test_no_jump_over_unread_topic(self):
        plan = safe_plan(self.dialog(), [incoming(11, 8), incoming(12), incoming(13)],
                         {7: NS(read_inbox_max_id=13), 8: NS(read_inbox_max_id=10)})
        self.assertFalse(plan['safe'])
        self.assertEqual(plan['read_through'], 10)

    def test_unknown_topic_unthreaded_message_and_manual_unread_are_preserved(self):
        for row, topic_state in [(incoming(11), {}), (incoming(11, forum=False), {7: NS(read_inbox_max_id=11)})]:
            self.assertFalse(safe_plan(self.dialog(top_message=11, unread_count=1), [row], topic_state)['safe'])
        self.assertFalse(safe_plan(self.dialog(unread_mark=True), [incoming(i) for i in [11, 12, 13]],
                                   {7: NS(read_inbox_max_id=13)})['safe'])

    def test_incomplete_duplicate_out_of_range_or_large_history_never_writes(self):
        histories = [[incoming(11)], [incoming(11), incoming(11), incoming(13)],
                     [incoming(9), incoming(12), incoming(13)], [incoming(i) for i in range(11, 111)]]
        for history in histories:
            self.assertFalse(safe_plan(self.dialog(), history, {7: NS(read_inbox_max_id=200)})['safe'])

    def test_outgoing_messages_do_not_count_as_unread(self):
        plan = safe_plan(self.dialog(unread_count=2), [incoming(13), incoming(12, outgoing=True), incoming(11)],
                         {7: NS(read_inbox_max_id=13)})
        self.assertTrue(plan['safe'])
        self.assertEqual(plan['read_through'], 13)


class NativeRequestTest(unittest.IsolatedAsyncioTestCase):
    def client(self, *, changed=False):
        class Client:
            writes = []
            dialog_reads = 0

            async def __call__(self, request):
                if type(request) is messages.GetPeerDialogsRequest:
                    self.dialog_reads += 1
                    return NS(dialogs=[NS(read_inbox_max_id=10, top_message=13 if changed and self.dialog_reads > 1 else 12,
                                          unread_count=0 if self.writes else 2, unread_mark=False)])
                if type(request) is messages.GetHistoryRequest:
                    assert request.min_id == 10 and request.max_id == 13 and request.limit == 100
                    return NS(messages=[incoming(12), incoming(11)])
                if type(request) is messages.GetForumTopicsByIDRequest:
                    assert request.topics == [7]
                    return NS(topics=[NS(id=7, read_inbox_max_id=12)])
                if type(request) is messages.ReadHistoryRequest:
                    self.writes.append(request.max_id)
                    return NS()
                raise AssertionError('Unexpected Telegram request')
        return Client()

    async def test_dry_run_never_marks_read(self):
        client = self.client()
        result = await reconcile(client, types.InputPeerUser(123, 0))
        self.assertTrue(result['safe'])
        self.assertFalse(result['applied'])
        self.assertEqual(client.writes, [])

    async def test_exact_proven_boundary_and_server_readback(self):
        client = self.client()
        result = await reconcile(client, types.InputPeerUser(123, 0), apply=True)
        self.assertEqual(client.writes, [12])
        self.assertTrue(result['applied'])
        self.assertEqual(result['unread_after'], [0])

    async def test_new_arrival_between_plan_and_write_cancels_write(self):
        client = self.client(changed=True)
        result = await reconcile(client, types.InputPeerUser(123, 0), apply=True)
        self.assertFalse(result['applied'])
        self.assertEqual(client.writes, [])
