import io
import wave

import numpy as np
import pytest

from acc_engineer.radiofx import process_radio


def make_wav(samples: np.ndarray, sample_rate: int = 24000) -> bytes:
    samples = np.asarray(samples, dtype="<i2")
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    raw = samples.reshape(-1).tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(raw)
    return output.getvalue()


def read_wav(data: bytes):
    with wave.open(io.BytesIO(data), "rb") as wav:
        meta = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes())
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    return meta, samples


def tone(frequency=440, seconds=1.0, sample_rate=24000, amplitude=9000):
    t = np.arange(round(seconds * sample_rate)) / sample_rate
    return np.rint(amplitude * np.sin(2 * np.pi * frequency * t)).astype("<i2")


def test_speed_preserves_pitch_and_changes_duration():
    source = make_wav(tone(seconds=2.0))
    output = process_radio(source, speed=1.18, strength="dry")
    (channels, width, rate, frames), samples = read_wav(output)
    assert (channels, width, rate) == (1, 2, 24000)
    assert frames / rate == pytest.approx(2.0 / 1.18, abs=0.025)
    peak_hz = np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(np.abs(np.fft.rfft(samples)))]
    assert peak_hz == pytest.approx(440, abs=2.0)


def test_tempo_keeps_stereo_channels():
    mono = tone(seconds=0.6)
    source = make_wav(np.column_stack((mono, np.roll(mono, 21))))
    output = process_radio(source, speed=1.12, strength="dry")
    (channels, width, rate, frames), samples = read_wav(output)
    assert (channels, width, rate) == (2, 2, 24000)
    assert frames / rate == pytest.approx(0.6 / 1.12, abs=0.025)
    assert samples.size == frames * channels


def test_gritty_output_preserves_stereo_metadata_and_limits_peak():
    mono = tone(seconds=0.3, amplitude=24000)
    stereo = np.column_stack((mono, np.roll(mono, 31)))
    output = process_radio(make_wav(stereo), speed=1.0, strength="gritty")
    (channels, width, rate, frames), samples = read_wav(output)
    assert (channels, width, rate, frames) == (2, 2, 24000, len(mono))
    assert np.max(np.abs(samples.astype(np.int32))) <= 32113
    assert not np.array_equal(samples, stereo.reshape(-1))


def test_strong_style_is_available_and_more_colored():
    source = make_wav(tone(seconds=0.3, amplitude=22000))
    gritty = read_wav(process_radio(source, speed=1.0, strength="gritty"))[1]
    strong = read_wav(process_radio(source, speed=1.0, strength="strong"))[1]
    assert not np.array_equal(gritty, strong)
    assert np.max(np.abs(strong.astype(np.int32))) <= 32113


def test_dry_at_unity_is_sample_preserving():
    source_samples = tone(seconds=0.1, amplitude=4000)
    output = process_radio(make_wav(source_samples), speed=1.0, strength="dry")
    _, actual = read_wav(output)
    np.testing.assert_array_equal(actual, source_samples)


@pytest.mark.parametrize("data", [b"", b"not wav", make_wav(np.array([], dtype="<i2"))])
def test_rejects_invalid_or_empty_wav(data):
    with pytest.raises(ValueError):
        process_radio(data)


@pytest.mark.parametrize("speed", [0.0, 2.5, float("nan")])
def test_rejects_invalid_speed(speed):
    with pytest.raises(ValueError, match="speed"):
        process_radio(make_wav(tone(seconds=0.1)), speed=speed)


def test_rejects_unknown_strength():
    with pytest.raises(ValueError, match="strength"):
        process_radio(make_wav(tone(seconds=0.1)), strength="stadium")


def test_very_short_audio_survives_tempo_filter():
    (channels, width, rate, frames), _ = read_wav(process_radio(make_wav(tone(seconds=.001))))
    assert (channels, width, rate) == (1, 2, 24000)
    assert frames > 0


def test_stereo_tempo_preserves_distinct_channel_pitches():
    source = make_wav(np.column_stack((tone(440, seconds=.6), tone(880, seconds=.6))))
    (_, _, rate, _), samples = read_wav(process_radio(source, speed=1.18, strength="dry"))
    channels = samples.reshape(-1, 2)
    peaks = [np.fft.rfftfreq(len(channels), 1 / rate)[np.argmax(np.abs(np.fft.rfft(channels[:, i])))]
             for i in range(2)]
    assert peaks == pytest.approx([440, 880], abs=3)

