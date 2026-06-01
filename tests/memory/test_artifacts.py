"""Conversation-scoped artifact memory (ADR-0021): registry add/query and the
refcounted cascade prune (single reaped, shared survives, pinned survives)."""

import uuid

import pytest

from jvagent.memory.conversation import Conversation


def _sid():
    return f"art-{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_add_and_query_artifacts(test_db):
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    try:
        i1 = await conv.add_interaction(utterance="hi")
        await conv.add_artifact(
            i1,
            name="vis1",
            data="a red car",
            summary="red car",
            source="vision",
            tags=["image"],
        )
        got = await conv.get_artifacts()
        assert [a.name for a in got] == ["vis1"]
        assert (await conv.get_artifacts(source="vision"))[0].data == "a red car"
        assert await conv.get_artifacts(source="file") == []
        assert (await conv.get_artifacts(tags=["image"]))[0].name == "vis1"
        assert (await conv.get_artifacts(name="vis1"))[0].summary == "red car"
        # index row carries no payload
        row = got[0].index_row()
        assert row["name"] == "vis1" and "data" not in row
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_refcounted_artifact_pruning(test_db):
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    conv.interaction_limit = 2  # keep the last 2 interactions
    try:
        i1 = await conv.add_interaction(utterance="m1")
        # solo: produced only by i1 -> reaped when i1 is pruned
        await conv.add_artifact(i1, name="solo", source="vision", data="d")
        # shared: produced by i1 AND i2 -> survives i1 pruning (refcount)
        shared = await conv.add_artifact(i1, name="shared", source="file", data="s")
        # pinned: produced only by i1 but pinned -> survives
        await conv.add_artifact(i1, name="pin", source="x", data="p", pinned=True)

        i2 = await conv.add_interaction(utterance="m2")
        await i2.connect(shared, direction="out")  # second producer

        # adding i3 pushes count to 3 > limit 2 -> prune oldest (i1) + reap
        await conv.add_interaction(utterance="m3")

        names = {a.name for a in await conv.get_artifacts()}
        assert "solo" not in names  # refcount 0 -> deleted
        assert "shared" in names  # i2 still produces it
        assert "pin" in names  # pinned -> exempt
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_file_backed_artifact_indexrow_and_reap_deletes_file(
    test_db, monkeypatch
):
    deleted = []

    class _App:
        async def delete_file(self, path):
            deleted.append(path)
            return True

        async def now(self):
            import datetime as _dt

            return _dt.datetime.now(_dt.timezone.utc)

    async def _get():
        return _App()

    monkeypatch.setattr("jvagent.core.app.App.get", staticmethod(_get))

    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    conv.interaction_limit = 1
    try:
        i1 = await conv.add_interaction(utterance="m1")
        art = await conv.add_artifact(
            i1,
            name="r.pdf",
            data="Uploaded file: r.pdf",
            source="upload",
            kind="file",
            filename="r.pdf",
            mime="application/pdf",
            size=2048,
            path="ag/us/uploads/int1/0_r.pdf",
        )
        row = art.index_row()
        assert row["filename"] == "r.pdf" and row["kind"] == "file"
        assert row["mime"] == "application/pdf" and "data" not in row

        await conv.add_interaction(utterance="m2")  # prunes i1
        names = {a.name for a in await conv.get_artifacts()}
        assert "r.pdf" not in names
        assert deleted == ["ag/us/uploads/int1/0_r.pdf"]
    finally:
        await conv.delete(cascade=True)


@pytest.mark.asyncio
async def test_prune_flag_off_keeps_artifacts(test_db):
    conv = await Conversation.create(session_id=_sid(), user_id="u", channel="default")
    conv.interaction_limit = 1
    conv.prune_artifacts_with_interaction = False
    try:
        i1 = await conv.add_interaction(utterance="m1")
        await conv.add_artifact(i1, name="keep", source="vision", data="d")
        await conv.add_interaction(utterance="m2")  # prunes i1, but flag is off
        names = {a.name for a in await conv.get_artifacts()}
        assert "keep" in names
    finally:
        await conv.delete(cascade=True)
