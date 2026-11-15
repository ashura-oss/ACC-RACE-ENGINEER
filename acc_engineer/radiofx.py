"""Lightweight radio-style processing for synthesized race engineer speech."""

from __future__ import annotations

import io
import math
import wave

import numpy as np


_STRENGTHS = {
    "dry": (False, 0.0),
    "light": (True, 0.035),
    "gritty": (True, 0.075),
    "strong": (True, 0.12),
}


def _bandpass(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Apply a zero-phase, smoothly rolled-off 350–3500 Hz voice band."""
    frames = samples.shape[0]
    # Reflection padding avoids the hard wrap and ringing of a bare FFT filter.
    pad = min(max(256, sample_rate // 20), max(0, frames - 1))
    if pad:
        padded = np.pad(samples, ((pad, pad), (0, 0)), mode="reflect")
    else:
        padded = samples
    freqs = np.fft.rfftfreq(padded.shape[0], 1.0 / sample_rate)
    # Logistic edges provide gradual transitions without the metallic sound of
    # a brick-wall telephone filter.
    low = 1.0 / (1.0 + np.exp(np.clip((350.0 - freqs) / 65.0, -60, 60)))
    high = 1.0 / (1.0 + np.exp(np.clip((freqs - 3500.0) / 250.0, -60, 60)))
    filtered = np.fft.irfft(np.fft.rfft(padded, axis=0) * (low * high)[:, None], n=padded.shape[0], axis=0)
    if pad:
        filtered = filtered[pad:pad + frames]
    return filtered


def _compress(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Gentle RMS compressor with a 10 ms detector window."""
    window = max(1, round(0.010 * sample_rate))
    power = samples * samples
    sums = np.vstack((np.zeros((1, samples.shape[1])), np.cumsum(power, axis=0)))
    starts = np.maximum(0, np.arange(samples.shape[0]) - window + 1)
    counts = np.arange(samples.shape[0]) - starts + 1
    rms = np.sqrt(np.maximum(0.0, (sums[np.arange(1, samples.shape[0] + 1)] - sums[starts]) / counts[:, None]))
    threshold = 10.0 ** (-18.0 / 20.0)
    over = np.maximum(rms, threshold)
    target = np.where(rms > threshold, (threshold + (rms - threshold) / 2.6) / over, 1.0)
    # The trailing 10 ms RMS window already smooths syllable peaks without a
    # per-sample Python loop (important for short, real-time voice clips).
    return samples * target


def _atempo(samples: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
    """Use FFmpeg's pitch-preserving atempo filter through PyAV."""
    try:
        import av
    except ImportError as exc:  # pragma: no cover - exercised by dependency-missing installs
        raise RuntimeError("PyAV is required for pitch-preserving radio voice speed changes") from exc

    channels = samples.shape[1]
    target_frames = max(1, round(samples.shape[0] / speed))
    short = samples.shape[0] < sample_rate // 5
    if short:
        samples = np.pad(samples, ((0, sample_rate // 5 - samples.shape[0]), (0, 0)))
    layout = {1: "mono", 2: "stereo"}.get(channels)
    if layout is None:
        raise ValueError("Tempo processing currently supports mono or stereo WAV audio")

    packed = np.ascontiguousarray(np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).reshape(1, -1))
    frame = av.AudioFrame.from_ndarray(packed, format="s16", layout=layout)
    frame.sample_rate = sample_rate
    from fractions import Fraction
    frame.time_base = Fraction(1, sample_rate)
    frame.pts = 0

    graph = av.filter.Graph()
    source = graph.add_abuffer(sample_rate=sample_rate, format="s16", layout=layout,
                               time_base=frame.time_base)
    tempo = graph.add("atempo", args=f"tempo={speed:.8g}")
    sink = graph.add("abuffersink")
    source.link_to(tempo)
    tempo.link_to(sink)
    graph.configure()
    result = []
    graph.push(frame)
    graph.push(None)
    while True:
        try:
            output = graph.pull()
        except (av.error.BlockingIOError, av.error.EOFError):
            break
        raw = output.to_ndarray()
        result.append(raw.reshape(-1, channels).astype(np.float64) / 32768.0)
    if not result:
        raise RuntimeError("PyAV atempo produced no audio frames")
    output = np.concatenate(result, axis=0)
    return output[:target_frames] if short else output


def process_radio(data: bytes, speed: float = 1.18, strength: str = "gritty") -> bytes:
    """Process PCM16 WAV speech and return WAV with its rate/channels preserved.

    Tempo changes use FFmpeg's pitch-preserving ``atempo`` filter. The input
    sample rate and channel count are retained in the encoded result.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
        raise ValueError("Input must be non-empty PCM16 WAV bytes")
    try:
        with wave.open(io.BytesIO(bytes(data)), "rb") as src:
            if src.getcomptype() != "NONE" or src.getsampwidth() != 2:
                raise ValueError("Only uncompressed PCM16 WAV is supported")
            params = src.getparams()
            raw = src.readframes(src.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("Input must be a valid PCM16 WAV file") from exc
    if not raw or params.nframes <= 0:
        raise ValueError("WAV contains no audio frames")
    if params.nchannels not in (1, 2):
        raise ValueError("Only mono or stereo PCM16 WAV is supported")
    if params.framerate < 8000 or params.framerate > 192000:
        raise ValueError("WAV sample rate must be between 8 kHz and 192 kHz")
    if not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise ValueError("speed must be a finite number between 0.5 and 2.0")
    try:
        color, grit = _STRENGTHS[strength.lower()]
    except (AttributeError, KeyError) as exc:
        raise ValueError("strength must be one of: dry, light, gritty, strong") from exc

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float64).reshape(-1, params.nchannels) / 32768.0
    if color:
        samples = _bandpass(samples, params.framerate)
        samples = _compress(samples * 1.20, params.framerate)
        if grit:
            # Soft saturation creates harmonics without adding hiss or harsh clipping.
            samples = np.tanh(samples * (1.0 + grit * 2.0)) / np.tanh(1.0 + grit * 2.0)
            # A very small, deterministic quantization texture adds radio grit
            # without audible broadband noise or nondeterministic output.
            steps = 4096.0 / (1.0 + grit * 5.0)
            samples = np.round(samples * steps) / steps

    if speed != 1.0:
        samples = _atempo(samples, params.framerate, float(speed))

    if color and grit and samples.size:
        # A faint transmission bed and short squelch edges supply the radio
        # texture. Seeded noise keeps cached renders reproducible; it ends with
        # this clip, rather than leaving a continuous hiss in the headset.
        noise = np.random.default_rng(7).standard_normal(samples.shape)
        noise = _bandpass(noise, params.framerate)
        noise_peak = max(float(np.max(np.abs(noise))), 1e-9)
        envelope = np.full((samples.shape[0], 1), grit * .022)
        edge = min(samples.shape[0] // 2, max(1, int(params.framerate * .018)))
        if edge:
            envelope[:edge, 0] += np.linspace(grit * .12, 0, edge)
            envelope[-edge:, 0] += np.linspace(0, grit * .10, edge)
        samples += noise / noise_peak * envelope

    # Keep headroom below digital full scale and encode valid little-endian PCM16.
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0.98:
        samples *= 0.98 / peak
    pcm = np.rint(np.clip(samples, -1.0, 32767.0 / 32768.0) * 32768.0).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as dst:
        dst.setnchannels(params.nchannels)
        dst.setsampwidth(2)
        dst.setframerate(params.framerate)
        dst.writeframes(pcm)
    return output.getvalue()
