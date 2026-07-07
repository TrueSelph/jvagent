"""Tests for Meta audio transcoding helper."""

from unittest.mock import MagicMock

from jvagent.action.whatsapp.utils.meta_audio import transcode_mp3_to_ogg_opus


class TestMetaAudioTranscode:
    def test_empty_input_returns_none(self):
        assert transcode_mp3_to_ogg_opus(b"") is None

    def test_no_ffmpeg_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "jvagent.action.whatsapp.utils.meta_audio.shutil.which",
            lambda _: None,
        )
        assert transcode_mp3_to_ogg_opus(b"mp3") is None

    def test_ffmpeg_success_returns_stdout(self, monkeypatch):
        monkeypatch.setattr(
            "jvagent.action.whatsapp.utils.meta_audio.shutil.which",
            lambda _: "/usr/bin/ffmpeg",
        )

        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = b"OggS\xff"
        proc.stderr = b""

        monkeypatch.setattr(
            "jvagent.action.whatsapp.utils.meta_audio.subprocess.run",
            lambda *args, **kwargs: proc,
        )

        assert transcode_mp3_to_ogg_opus(b"fake-mp3") == b"OggS\xff"
