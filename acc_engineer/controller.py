"""Xbox/keyboard edge detection, explicit microphone toggles and local STT."""
from __future__ import annotations

import asyncio
import ctypes
import inspect
import io
import os
import time
import wave

XBOX_BUTTONS = {'A':0x1000,'B':0x2000,'X':0x4000,'Y':0x8000,'LB':0x100,'RB':0x200,'BACK':0x20,'START':0x10,'L3':0x40,'R3':0x80}


class ButtonEdge:
    """One event per press; repeated held packets never retrigger."""
    def __init__(self, debounce_s=0.18):
        self.debounce_s, self.pressed, self.last_press = debounce_s, False, -float('inf')

    def update(self, pressed, now=None):
        now = time.monotonic() if now is None else now
        rising = bool(pressed) and not self.pressed
        self.pressed = bool(pressed)
        if rising and now-self.last_press >= self.debounce_s:
            self.last_press = now
            return True
        return False


class _Gamepad(ctypes.Structure):
    _fields_ = [('buttons',ctypes.c_uint16),('left_trigger',ctypes.c_uint8),('right_trigger',ctypes.c_uint8),('left_x',ctypes.c_int16),('left_y',ctypes.c_int16),('right_x',ctypes.c_int16),('right_y',ctypes.c_int16)]


class _State(ctypes.Structure):
    _fields_ = [('packet',ctypes.c_uint32),('gamepad',_Gamepad)]


class ControllerService:
    """Poll XInput at 100Hz. on_toggle may be synchronous or awaitable.

    keyboard_vk is an optional Windows virtual-key code. No keys are captured
    or rebound inside ACC, and conflict inspection is advisory only.
    """
    def __init__(self, on_toggle, button='R3', keyboard_vk=None, on_cancel=None):
        if button not in XBOX_BUTTONS:
            raise ValueError('Unsupported Xbox button')
        if keyboard_vk is not None and not 1 <= keyboard_vk <= 254:
            raise ValueError('Keyboard virtual key must be 1..254')
        self.on_toggle, self.on_cancel = on_toggle, on_cancel
        self.button, self.keyboard_vk = button, keyboard_vk
        self.connected, self.error, self._task = False, None, None
        self._edge, self._keyboard_edge = ButtonEdge(), ButtonEdge()
        self._xinput, self._user32 = None, None

    async def _invoke(self, callback):
        if callback:
            value = callback()
            if inspect.isawaitable(value):
                await value

    async def _poll(self):
        if os.name != 'nt':
            self.error = 'Controller input requires Windows'
            return
        for dll in ('xinput1_4.dll','xinput1_3.dll','xinput9_1_0.dll'):
            try:
                self._xinput = ctypes.WinDLL(dll)
                break
            except OSError:
                pass
        if self._xinput is None:
            self.error = 'XInput unavailable'
            return
        self._xinput.XInputGetState.argtypes = [ctypes.c_uint32,ctypes.POINTER(_State)]
        self._xinput.XInputGetState.restype = ctypes.c_uint32
        self._user32 = ctypes.WinDLL('user32')
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_int16
        while True:
            try:
                state = _State()
                self.connected = False
                for index in range(4):
                    if self._xinput.XInputGetState(index,ctypes.byref(state)) == 0:
                        self.connected = True
                        break
                triggered = self._edge.update(self.connected and bool(state.gamepad.buttons & XBOX_BUTTONS[self.button]))
                if self.keyboard_vk:
                    triggered |= self._keyboard_edge.update(bool(self._user32.GetAsyncKeyState(self.keyboard_vk) & 0x8000))
                if triggered:
                    await self._invoke(self.on_toggle)
                self.error = None
            except Exception as exc:
                self.error = type(exc).__name__
            await asyncio.sleep(0.01)

    async def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.connected = False

    def snapshot(self):
        return {'connected':self.connected,'button':self.button,'keyboard_vk':self.keyboard_vk,'error':self.error,'conflict_check':'ACC control conflicts require manual verification'}


class MicrophoneRecorder:
    """Explicit recording only; bounded to 30 seconds, PCM16 mono WAV output.

    sounddevice is optional. No input devices are opened on construction.
    Caller must pause playback while recording to avoid speech feedback.
    """
    def __init__(self, device=None, sample_rate=16000, max_seconds=30):
        self.device, self.sample_rate, self.max_seconds = device, sample_rate, max_seconds
        self.recording, self._stream = False, None
        self._chunks, self._frames = [], 0
        self.truncated = False

    def start(self):
        if self.recording:
            return
        import sounddevice as sd
        self._chunks, self._frames, self.truncated = [], 0, False
        def receive(indata, frames, timing, status):
            remaining = max(0,int(self.sample_rate*self.max_seconds)-self._frames)
            count = min(frames,remaining)
            if count:
                self._chunks.append(bytes(indata)[:count*2])
                self._frames += count
            if count < frames:
                self.truncated = True
                raise sd.CallbackStop
        self._stream = sd.RawInputStream(samplerate=self.sample_rate,channels=1,dtype='int16',device=self.device,callback=receive)
        try:
            self._stream.start()
        except Exception:
            self._stream.close()
            self._stream = None
            raise
        self.recording = True

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.recording = False
        output = io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(b''.join(self._chunks))
        self._chunks, self._frames = [], 0
        return output.getvalue()

    def cancel(self):
        self.stop()


class LocalTranscriber:
    """Opt-in faster-whisper CPU recognizer; model download occurs on first use."""
    def __init__(self, model='base.en'):
        self.model_name, self._model = model, None

    def transcribe(self, wav_data):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name,device='cpu',compute_type='int8')
        segments, _ = self._model.transcribe(io.BytesIO(wav_data),beam_size=1,vad_filter=True)
        return ' '.join(part.text.strip() for part in segments).strip()
