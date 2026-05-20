from api.chat import _hydrate_assistant_meta, _hydrate_message_attachments


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self):
        self.calls = 0

    def query(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return FakeQuery([('[545, 96]',), ('[96]',)])
        return FakeQuery([
            (545, "navigation.pdf"),
            (96, "materials.pdf"),
        ])


def test_hydrate_message_attachments_uses_trace_document_ids_by_user_turn():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ]

    hydrated = _hydrate_message_attachments(1, messages, FakeSession())

    assert hydrated[0]["attached_docs"] == [
        {"id": 545, "name": "navigation.pdf"},
        {"id": 96, "name": "materials.pdf"},
    ]
    assert "attached_docs" not in hydrated[1]
    assert hydrated[2]["attached_docs"] == [{"id": 96, "name": "materials.pdf"}]


class FakeMetaSession:
    def query(self, *_args, **_kwargs):
        return FakeQuery([
            ("run-1", "document_extraction", None, "research_agent", "research_writer", "sha256:abc"),
            ("run-2", "retrieval_qa", "retrieval_agent", "retrieval_agent", "chat", "sha256:def"),
        ])


def test_hydrate_assistant_meta_uses_root_traces_by_assistant_turn():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "answer 2"},
    ]

    hydrated = _hydrate_assistant_meta(1, messages, FakeMetaSession())

    assert hydrated[1]["task_type"] == "document_extraction"
    assert hydrated[1]["trace_id"] == "run-1"
    assert hydrated[3]["agent_name"] == "retrieval_agent"
    assert hydrated[3]["prompt_version"] == "sha256:def"
