import io
import sys
import threading
import types
import wave

import pytest

from acc_engineer.controller import LocalTranscriber, TranscriptionError


def wav_bytes(frames=1600, value=200):
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes((value.to_bytes(2, 'little', signed=True)) * frames)
    return out.getvalue()


def test_empty_and_silent_audio_skip_model(tmp_path):
    transcriber = LocalTranscriber(cache_dir=tmp_path)
    assert transcriber.transcribe(b'') == ''
    assert transcriber.transcribe(wav_bytes(value=0)) == ''
    assert transcriber.status()['ready'] is False


@pytest.mark.parametrize('audio', [b'not wav', b'RIFF', b'\x00' * 32])
def test_corrupt_audio_is_rejected(audio, tmp_path):
    with pytest.raises(TranscriptionError) as err:
        LocalTranscriber(cache_dir=tmp_path).transcribe(audio)
    assert err.value.category == 'invalid_audio'


def test_duration_over_limit_rejected(tmp_path):
    with pytest.raises(TranscriptionError) as err:
        LocalTranscriber(cache_dir=tmp_path).transcribe(wav_bytes(frames=16000 * 31))
    assert err.value.category == 'invalid_audio'


def test_model_setup_error_is_sanitized(tmp_path, monkeypatch):
    module = types.ModuleType('faster_whisper')
    def fail(*args, **kwargs):
        raise RuntimeError('token=secret conversation text')
    module.WhisperModel = fail
    monkeypatch.setitem(sys.modules, 'faster_whisper', module)
    with pytest.raises(TranscriptionError) as err:
        LocalTranscriber(cache_dir=tmp_path).transcribe(wav_bytes())
    assert err.value.category == 'model_setup'
    assert 'secret' not in str(err.value)


def test_model_loaded_once_and_cache_is_persistent(tmp_path, monkeypatch):
    calls = []
    class Model:
        def transcribe(self, audio, **kwargs):
            assert audio.dtype.name == 'float32'
            assert audio.ndim == 1
            return iter([types.SimpleNamespace(text=' recognized ')]), None
    def create(name, **kwargs):
        calls.append((name, kwargs))
        return Model()
    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=create))
    transcriber = LocalTranscriber(cache_dir=tmp_path)
    assert transcriber.transcribe(wav_bytes()) == 'recognized'
    assert transcriber.transcribe(wav_bytes()) == 'recognized'
    assert len(calls) == 1
    assert calls[0][1]['download_root'] == str(tmp_path)
    assert transcriber.status()['ready'] is True


def test_concurrent_first_use_initializes_once(tmp_path, monkeypatch):
    calls = []
    class Model:
        def transcribe(self, *args, **kwargs):
            return iter([]), None
    def create(*args, **kwargs):
        calls.append(1)
        return Model()
    monkeypatch.setitem(sys.modules, 'faster_whisper', types.SimpleNamespace(WhisperModel=create))
    transcriber = LocalTranscriber(cache_dir=tmp_path)
    threads = [threading.Thread(target=transcriber.transcribe, args=(wav_bytes(),)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
