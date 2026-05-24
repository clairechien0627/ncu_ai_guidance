from api.chat import _hydrate_assistant_meta, _hydrate_message_attachments
import json


class FakeTraceRow:
    def __init__(self, trace_id: str, metadata: dict):
        self.trace_id = trace_id
        self.metadata_json = json.dumps(metadata, ensure_ascii=False)


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
            return FakeQuery([
                FakeTraceRow("run-1", {"document_ids": [545, 96]}),
                FakeTraceRow("run-2", {"document_ids": [96]}),
            ])
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
    def __init__(self):
        self._calls = 0

    def query(self, *_args, **_kwargs):
        self._calls += 1
        if self._calls == 1:
            return FakeQuery([
                ("run-1", json.dumps({"agent_name": "research", "prompt_name": "research_writer", "prompt_version": "sha256:abc"})),
                ("run-2", json.dumps({"agent_name": "retrieval", "prompt_name": "chat", "prompt_version": "sha256:def"})),
            ])
        return FakeQuery([])


def test_hydrate_assistant_meta_uses_root_traces_by_assistant_turn():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "answer 2"},
    ]

    hydrated = _hydrate_assistant_meta(1, messages, FakeMetaSession())

    assert hydrated[1]["agent_name"] == "research"
    assert hydrated[1]["trace_id"] == "run-1"
    assert hydrated[3]["agent_name"] == "retrieval"
    assert "prompt_name" not in hydrated[3]
    assert "prompt_version" not in hydrated[3]
