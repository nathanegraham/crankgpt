from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

try:
    import serial
except ImportError:  # pragma: no cover - optional until installed on the Jetson.
    serial = None

import requests
from flask import Flask, jsonify, render_template, request


@dataclass(frozen=True)
class LengthPreset:
    key: str
    label: str
    words: int
    max_words: int
    num_predict: int
    energy_required_mwh: float


@dataclass(frozen=True)
class RailSample:
    timestamp_s: float
    millivolts: int
    milliamps: int
    watts: float


@dataclass(frozen=True)
class EnergyMeasurement:
    source: str
    total_mwh: float
    idle_estimate_mwh: float
    net_mwh: float
    required_mwh: float
    idle_average_watts: float
    average_watts: float
    duration_s: float
    sample_count: int


@dataclass(frozen=True)
class UM34CReading:
    timestamp_s: float
    selected_group: int
    voltage_v: float
    current_a: float
    power_w: float
    accumulated_power_mwh: float


@dataclass
class UnlockSession:
    session_id: str
    prompt: str
    answer: str
    model: str
    preset: LengthPreset
    energy_required_mwh: float
    generation_seconds: float | None
    energy_source: str
    placeholder_mode: bool
    energy_measured_mwh: float | None
    idle_energy_estimate_mwh: float | None
    idle_average_watts: float | None
    answer_locked: bool = True
    crank_progress_percent: float = 0.0
    crank_generated_mwh: float = 0.0
    meter_connected: bool = False
    meter_error: str | None = None
    meter_accumulated_mwh: float | None = None
    meter_baseline_mwh: float | None = None
    created_at_s: float = 0.0
    first_crank_at_s: float | None = None
    unlocked_at_s: float | None = None
    unlock_mode: str | None = None
    generation_logged: bool = False
    completion_logged: bool = False


@dataclass(frozen=True)
class JetsonPowerRail:
    hwmon_path: Path
    channel: int
    label: str
    voltage_path: Path
    current_path: Path

    @classmethod
    def discover(cls, label: str = "VDD_IN") -> "JetsonPowerRail | None":
        hwmon_root = Path("/sys/class/hwmon")
        if not hwmon_root.exists():
            return None

        for hwmon_path in sorted(hwmon_root.glob("hwmon*")):
            device_name = _read_sysfs_text(hwmon_path / "name")
            if device_name != "ina3221":
                continue

            for label_path in sorted(hwmon_path.glob("in*_label")):
                channel = _channel_from_sensor_name(label_path.stem)
                if channel is None:
                    continue

                rail_label = _read_sysfs_text(label_path)
                if rail_label != label:
                    continue

                voltage_path = hwmon_path / f"in{channel}_input"
                current_path = hwmon_path / f"curr{channel}_input"
                if voltage_path.exists() and current_path.exists():
                    return cls(
                        hwmon_path=hwmon_path,
                        channel=channel,
                        label=rail_label,
                        voltage_path=voltage_path,
                        current_path=current_path,
                    )

        return None

    def read_sample(self) -> RailSample:
        millivolts = int(_read_sysfs_text(self.voltage_path))
        milliamps = int(_read_sysfs_text(self.current_path))
        watts = (millivolts * milliamps) / 1_000_000
        return RailSample(
            timestamp_s=time.monotonic(),
            millivolts=millivolts,
            milliamps=milliamps,
            watts=watts,
        )


class UM34CSerialReader:
    def __init__(
        self,
        port: str,
        baudrate: int,
        data_group: int,
        timeout_s: float,
        command_settle_s: float,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.data_group = data_group
        self.timeout_s = timeout_s
        self.command_settle_s = command_settle_s

    def read(self) -> UM34CReading:
        if serial is None:
            raise RuntimeError("pyserial is not installed.")

        packet = self._exchange()
        if len(packet) != 130:
            raise RuntimeError(f"Incomplete UM34C packet: expected 130 bytes, got {len(packet)}.")

        model_id = int.from_bytes(packet[0:2], "big")
        if model_id != 0x0D4C:
            raise RuntimeError(f"Unexpected UM34C model id: 0x{model_id:04x}")

        selected_group = int.from_bytes(packet[14:16], "big")
        group_index = self.data_group if 0 <= self.data_group <= 9 else selected_group
        group_offset = 16 + (group_index * 8)
        accumulated_power_mwh = float(int.from_bytes(packet[group_offset + 4 : group_offset + 8], "big"))

        return UM34CReading(
            timestamp_s=time.monotonic(),
            selected_group=group_index,
            voltage_v=int.from_bytes(packet[2:4], "big") / 100.0,
            current_a=int.from_bytes(packet[4:6], "big") / 1000.0,
            power_w=int.from_bytes(packet[6:10], "big") / 1000.0,
            accumulated_power_mwh=accumulated_power_mwh,
        )

    def _exchange(self) -> bytes:
        with serial.Serial(
            self.port,
            baudrate=self.baudrate,
            timeout=self.timeout_s,
            write_timeout=self.timeout_s,
        ) as connection:
            connection.reset_input_buffer()
            connection.write(bytes([0xA0 + self.data_group]))
            connection.flush()
            time.sleep(self.command_settle_s)

            connection.reset_input_buffer()
            connection.write(b"\xF0")
            connection.flush()
            packet = connection.read(130)

        return packet


LENGTH_PRESETS = {
    preset.key: preset
    for preset in (
        LengthPreset(
            key="small",
            label="Small",
            words=25,
            max_words=35,
            num_predict=64,
            energy_required_mwh=20.2,
        ),
        LengthPreset(
            key="medium",
            label="Medium",
            words=50,
            max_words=65,
            num_predict=112,
            energy_required_mwh=31.4,
        ),
        LengthPreset(
            key="large",
            label="Large",
            words=100,
            max_words=120,
            num_predict=192,
            energy_required_mwh=53.8,
        ),
    )
}

DEFAULT_PRESET_KEY = "small"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
LLAMA_CPP_BASE_URL = os.getenv("LLAMA_CPP_BASE_URL", "http://127.0.0.1:11439").rstrip("/")
MODEL_BACKEND = os.getenv("MODEL_BACKEND", "ollama").strip().lower()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
LLAMA_CPP_MODEL = os.getenv("LLAMA_CPP_MODEL", "Qwen3.5-2B.q4_k_m.gguf")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))
JETSON_POWER_SENSOR_LABEL = os.getenv("JETSON_POWER_SENSOR_LABEL", "VDD_IN")
JETSON_POWER_SAMPLE_INTERVAL_SECONDS = float(
    os.getenv("JETSON_POWER_SAMPLE_INTERVAL_SECONDS", "0.1")
)
JETSON_IDLE_SAMPLE_SECONDS = float(os.getenv("JETSON_IDLE_SAMPLE_SECONDS", "1.5"))
JETSON_IDLE_CACHE_SECONDS = float(os.getenv("JETSON_IDLE_CACHE_SECONDS", "300"))
JETSON_IDLE_WATTS_OVERRIDE = os.getenv("JETSON_IDLE_WATTS")
JETSON_ENERGY_DEMO_MULTIPLIER = float(os.getenv("JETSON_ENERGY_DEMO_MULTIPLIER", "1.0"))
UM34C_SERIAL_PORT = os.getenv("UM34C_SERIAL_PORT", "/dev/rfcomm0")
UM34C_BAUDRATE = int(os.getenv("UM34C_BAUDRATE", "9600"))
UM34C_DATA_GROUP = int(os.getenv("UM34C_DATA_GROUP", "0"))
UM34C_READ_TIMEOUT_SECONDS = float(os.getenv("UM34C_READ_TIMEOUT_SECONDS", "2.0"))
UM34C_COMMAND_SETTLE_SECONDS = float(os.getenv("UM34C_COMMAND_SETTLE_SECONDS", "0.2"))
UNLOCK_SESSION_TTL_SECONDS = float(os.getenv("UNLOCK_SESSION_TTL_SECONDS", "7200"))
SESSION_LOG_PATH = Path(
    os.getenv(
        "SESSION_LOG_PATH",
        str(Path(__file__).resolve().parent / "logs" / "session-events.jsonl"),
    )
).expanduser()
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

app = Flask(__name__)

_POWER_SENSOR_LOCK = threading.Lock()
_POWER_SENSOR_READY = False
_POWER_SENSOR: JetsonPowerRail | None = None
_IDLE_POWER_LOCK = threading.Lock()
_IDLE_POWER_CACHE_WATTS: float | None = None
_IDLE_POWER_CACHE_AT: float = 0.0
_UM34C_READ_LOCK = threading.Lock()
_SESSION_LOCK = threading.Lock()
_UNLOCK_SESSIONS: dict[str, UnlockSession] = {}
_SESSION_LOG_LOCK = threading.Lock()


def _read_sysfs_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").replace("\x00", "").strip()


def isoformat_local_timestamp(epoch_s: float | None = None) -> str:
    dt = datetime.fromtimestamp(epoch_s if epoch_s is not None else time.time()).astimezone()
    return dt.isoformat(timespec="seconds")


def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text.strip()))


def _channel_from_sensor_name(sensor_name: str) -> int | None:
    match = re.search(r"(\d+)", sensor_name)
    return int(match.group(1)) if match else None


def get_jetson_power_sensor() -> JetsonPowerRail | None:
    global _POWER_SENSOR_READY, _POWER_SENSOR

    if _POWER_SENSOR_READY:
        return _POWER_SENSOR

    with _POWER_SENSOR_LOCK:
        if not _POWER_SENSOR_READY:
            _POWER_SENSOR = JetsonPowerRail.discover(JETSON_POWER_SENSOR_LABEL)
            _POWER_SENSOR_READY = True
        return _POWER_SENSOR


def _average_power_watts(sensor: JetsonPowerRail, duration_s: float, interval_s: float) -> float:
    duration_s = max(duration_s, interval_s)
    start = sensor.read_sample()
    previous = start
    energy_ws = 0.0
    deadline = start.timestamp_s + duration_s

    while previous.timestamp_s < deadline:
        sleep_s = min(interval_s, max(0.0, deadline - time.monotonic()))
        if sleep_s > 0:
            time.sleep(sleep_s)
        current = sensor.read_sample()
        dt = max(0.0, current.timestamp_s - previous.timestamp_s)
        energy_ws += ((previous.watts + current.watts) / 2) * dt
        previous = current

    elapsed_s = max(previous.timestamp_s - start.timestamp_s, interval_s)
    return energy_ws / elapsed_s


def get_idle_power_watts(sensor: JetsonPowerRail) -> float:
    if JETSON_IDLE_WATTS_OVERRIDE:
        return float(JETSON_IDLE_WATTS_OVERRIDE)

    global _IDLE_POWER_CACHE_WATTS, _IDLE_POWER_CACHE_AT
    now = time.monotonic()

    with _IDLE_POWER_LOCK:
        if (
            _IDLE_POWER_CACHE_WATTS is not None
            and now - _IDLE_POWER_CACHE_AT < JETSON_IDLE_CACHE_SECONDS
        ):
            return _IDLE_POWER_CACHE_WATTS

    idle_watts = _average_power_watts(
        sensor,
        duration_s=JETSON_IDLE_SAMPLE_SECONDS,
        interval_s=JETSON_POWER_SAMPLE_INTERVAL_SECONDS,
    )

    with _IDLE_POWER_LOCK:
        _IDLE_POWER_CACHE_WATTS = idle_watts
        _IDLE_POWER_CACHE_AT = time.monotonic()

    return idle_watts


class PowerIntegrator:
    def __init__(self, sensor: JetsonPowerRail, interval_s: float) -> None:
        self.sensor = sensor
        self.interval_s = interval_s
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._error: Exception | None = None
        self._start_sample: RailSample | None = None
        self._end_sample: RailSample | None = None
        self._energy_ws = 0.0
        self._sample_count = 0

    def start(self) -> None:
        self._thread.start()
        self._ready_event.wait(timeout=1.0)
        if self._error is not None:
            raise self._error

    def stop(self) -> tuple[float, float, int]:
        self._stop_event.set()
        self._thread.join(timeout=max(self.interval_s * 5, 1.0))

        if self._thread.is_alive():
            raise RuntimeError("Timed out while stopping the power integrator.")

        if self._error is not None:
            raise self._error

        if self._start_sample is None or self._end_sample is None:
            raise RuntimeError("Power integrator did not capture any samples.")

        duration_s = max(self._end_sample.timestamp_s - self._start_sample.timestamp_s, 0.0)
        total_mwh = self._energy_ws / 3.6
        return total_mwh, duration_s, self._sample_count

    def _run(self) -> None:
        try:
            previous = self.sensor.read_sample()
            self._start_sample = previous
            self._sample_count = 1
            self._ready_event.set()

            while not self._stop_event.wait(self.interval_s):
                current = self.sensor.read_sample()
                self._integrate(previous, current)
                previous = current
                self._sample_count += 1

            current = self.sensor.read_sample()
            self._integrate(previous, current)
            self._sample_count += 1
            self._end_sample = current
        except Exception as exc:
            self._error = exc
            self._ready_event.set()

    def _integrate(self, previous: RailSample, current: RailSample) -> None:
        dt = max(0.0, current.timestamp_s - previous.timestamp_s)
        self._energy_ws += ((previous.watts + current.watts) / 2) * dt


def call_model_with_energy(prompt: str, preset: LengthPreset) -> tuple[dict, EnergyMeasurement | None]:
    sensor = get_jetson_power_sensor()
    if sensor is None:
        return call_model(prompt, preset), None

    try:
        idle_watts = get_idle_power_watts(sensor)
        integrator = PowerIntegrator(sensor, JETSON_POWER_SAMPLE_INTERVAL_SECONDS)
        integrator.start()
    except (OSError, RuntimeError, ValueError):
        return call_model(prompt, preset), None

    try:
        model_payload = call_model(prompt, preset)
    except Exception:
        try:
            integrator.stop()
        except Exception:
            pass
        raise

    try:
        total_mwh, duration_s, sample_count = integrator.stop()
    except (OSError, RuntimeError, ValueError):
        return model_payload, None

    idle_estimate_mwh = (idle_watts * duration_s) / 3.6
    net_mwh = max(0.0, total_mwh - idle_estimate_mwh)
    required_mwh = net_mwh * JETSON_ENERGY_DEMO_MULTIPLIER
    average_watts = (total_mwh * 3.6 / duration_s) if duration_s > 0 else 0.0

    return model_payload, EnergyMeasurement(
        source=f"jetson:{sensor.label.lower()}",
        total_mwh=total_mwh,
        idle_estimate_mwh=idle_estimate_mwh,
        net_mwh=net_mwh,
        required_mwh=required_mwh,
        idle_average_watts=idle_watts,
        average_watts=average_watts,
        duration_s=duration_s,
        sample_count=sample_count,
    )


def get_um34c_reader() -> UM34CSerialReader | None:
    if serial is None:
        return None

    return UM34CSerialReader(
        port=UM34C_SERIAL_PORT,
        baudrate=UM34C_BAUDRATE,
        data_group=UM34C_DATA_GROUP,
        timeout_s=UM34C_READ_TIMEOUT_SECONDS,
        command_settle_s=UM34C_COMMAND_SETTLE_SECONDS,
    )


def read_um34c() -> UM34CReading:
    reader = get_um34c_reader()
    if reader is None:
        raise RuntimeError("pyserial is not installed.")

    if not Path(UM34C_SERIAL_PORT).exists():
        raise RuntimeError(f"UM34C serial device is not available at {UM34C_SERIAL_PORT}.")

    with _UM34C_READ_LOCK:
        return reader.read()


def crank_progress_percent(generated_mwh: float, required_mwh: float) -> float:
    if required_mwh <= 0:
        return 100.0
    return min(100.0, max(0.0, (generated_mwh / required_mwh) * 100.0))


def append_session_event(session: UnlockSession, event_type: str) -> None:
    SESSION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    created_at_iso = isoformat_local_timestamp(session.created_at_s)
    unlocked_at_iso = isoformat_local_timestamp(session.unlocked_at_s) if session.unlocked_at_s else None
    first_crank_at_iso = (
        isoformat_local_timestamp(session.first_crank_at_s) if session.first_crank_at_s else None
    )
    unlock_elapsed_s = (
        round(session.unlocked_at_s - session.created_at_s, 2)
        if session.unlocked_at_s is not None
        else None
    )

    event = {
        "event_type": event_type,
        "event_logged_at": isoformat_local_timestamp(),
        "session_id": session.session_id,
        "created_at": created_at_iso,
        "completed_at": unlocked_at_iso,
        "first_crank_at": first_crank_at_iso,
        "unlock_elapsed_seconds": unlock_elapsed_s,
        "unlock_mode": session.unlock_mode,
        "model": session.model,
        "prompt": session.prompt,
        "answer": session.answer,
        "answer_word_count": count_words(session.answer),
        "answer_length_key": session.preset.key,
        "answer_length_label": session.preset.label,
        "answer_length_target_words": session.preset.words,
        "generation_seconds": session.generation_seconds,
        "energy_source": session.energy_source,
        "energy_required_mwh": round(session.energy_required_mwh, 4),
        "energy_measured_mwh": session.energy_measured_mwh,
        "idle_energy_estimate_mwh": session.idle_energy_estimate_mwh,
        "idle_average_watts": session.idle_average_watts,
        "crank_generated_mwh": round(session.crank_generated_mwh, 4),
        "crank_progress_percent": round(session.crank_progress_percent, 1),
        "meter_connected": session.meter_connected,
        "meter_error": session.meter_error,
        "meter_baseline_mwh": session.meter_baseline_mwh,
        "meter_accumulated_mwh": session.meter_accumulated_mwh,
        "answer_locked": session.answer_locked,
        "placeholder_mode": session.placeholder_mode,
    }

    with _SESSION_LOG_LOCK:
        with SESSION_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(event, ensure_ascii=True) + "\n")


def maybe_log_session_generated(session: UnlockSession) -> None:
    if session.generation_logged:
        return
    append_session_event(session, "generated")
    session.generation_logged = True


def maybe_log_session_completed(session: UnlockSession) -> None:
    if session.answer_locked or session.completion_logged:
        return
    append_session_event(session, "completed")
    session.completion_logged = True


def cleanup_unlock_sessions(now_s: float | None = None) -> None:
    cutoff = (now_s or time.time()) - UNLOCK_SESSION_TTL_SECONDS
    with _SESSION_LOCK:
        stale_session_ids = [
            session_id
            for session_id, session in _UNLOCK_SESSIONS.items()
            if session.created_at_s < cutoff
        ]
        for session_id in stale_session_ids:
            _UNLOCK_SESSIONS.pop(session_id, None)


def save_unlock_session(session: UnlockSession) -> None:
    cleanup_unlock_sessions(session.created_at_s)
    with _SESSION_LOCK:
        _UNLOCK_SESSIONS[session.session_id] = session


def get_unlock_session(session_id: str) -> UnlockSession | None:
    cleanup_unlock_sessions()
    with _SESSION_LOCK:
        return _UNLOCK_SESSIONS.get(session_id)


def refresh_unlock_session(session: UnlockSession) -> UnlockSession:
    try:
        reading = read_um34c()
    except RuntimeError as exc:
        session.meter_connected = False
        session.meter_error = str(exc)
        return session
    except OSError as exc:
        session.meter_connected = False
        session.meter_error = f"UM34C read failed: {exc}"
        return session
    except Exception as exc:
        session.meter_connected = False
        session.meter_error = f"UM34C read failed: {exc}"
        return session

    session.meter_connected = True
    session.meter_error = None
    session.meter_accumulated_mwh = reading.accumulated_power_mwh

    if session.meter_baseline_mwh is None:
        session.meter_baseline_mwh = reading.accumulated_power_mwh
        session.crank_generated_mwh = 0.0
        session.crank_progress_percent = 0.0
        return session

    delta_mwh = reading.accumulated_power_mwh - session.meter_baseline_mwh
    if delta_mwh < -0.1:
        # The meter likely power-cycled and restarted its accumulated counter.
        # Re-baseline the session instead of hard-failing the unlock flow.
        session.meter_baseline_mwh = reading.accumulated_power_mwh
        session.crank_generated_mwh = 0.0
        session.crank_progress_percent = 0.0
        session.meter_error = None
        session.meter_connected = True
        return session

    session.crank_generated_mwh = max(0.0, delta_mwh)
    if session.crank_generated_mwh > 0 and session.first_crank_at_s is None:
        session.first_crank_at_s = time.time()
    session.crank_progress_percent = crank_progress_percent(
        session.crank_generated_mwh,
        session.energy_required_mwh,
    )

    if session.crank_progress_percent >= 100.0:
        session.answer_locked = False
        if session.unlocked_at_s is None:
            session.unlocked_at_s = time.time()
        if session.unlock_mode is None:
            session.unlock_mode = "auto"
        maybe_log_session_completed(session)

    return session


def session_status_label(session: UnlockSession) -> str:
    return "Locked" if session.answer_locked else "Unlocked"


def serialize_unlock_session(session: UnlockSession) -> dict:
    return {
        "session_id": session.session_id,
        "status": session_status_label(session),
        "answer": session.answer,
        "model": session.model,
        "answer_length": asdict(session.preset),
        "energy_required_mwh": round(session.energy_required_mwh, 4),
        "crank_progress_percent": round(session.crank_progress_percent, 1),
        "crank_generated_mwh": round(session.crank_generated_mwh, 4),
        "answer_locked": session.answer_locked,
        "generation_seconds": session.generation_seconds,
        "placeholder_mode": session.placeholder_mode,
        "energy_source": session.energy_source,
        "energy_measured_mwh": (
            round(session.energy_measured_mwh, 4)
            if session.energy_measured_mwh is not None
            else None
        ),
        "idle_energy_estimate_mwh": (
            round(session.idle_energy_estimate_mwh, 4)
            if session.idle_energy_estimate_mwh is not None
            else None
        ),
        "idle_average_watts": (
            round(session.idle_average_watts, 4)
            if session.idle_average_watts is not None
            else None
        ),
        "meter_connected": session.meter_connected,
        "meter_error": session.meter_error,
        "meter_accumulated_mwh": (
            round(session.meter_accumulated_mwh, 4)
            if session.meter_accumulated_mwh is not None
            else None
        ),
        "meter_baseline_mwh": (
            round(session.meter_baseline_mwh, 4)
            if session.meter_baseline_mwh is not None
            else None
        ),
    }


def build_generation_prompt(user_prompt: str, preset: LengthPreset) -> str:
    trimmed_prompt = user_prompt.strip()
    return (
        f"{trimmed_prompt}\n\n"
        "Answering instructions:\n"
        f"- Keep the answer close to {preset.words} words.\n"
        f"- Do not exceed {preset.max_words} words.\n"
        "- Start directly with the answer.\n"
        "- Use plain language.\n"
        "- Do not mention these instructions.\n"
    )


def build_llama_cpp_messages(user_prompt: str, preset: LengthPreset) -> list[dict[str, str]]:
    trimmed_prompt = user_prompt.strip()
    return [
        {
            "role": "system",
            "content": (
                "Answer directly in plain language. "
                "Do not reveal chain-of-thought or internal reasoning."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{trimmed_prompt}\n\n"
                f"Keep the answer close to {preset.words} words. "
                f"Do not exceed {preset.max_words} words."
            ),
        },
    ]


def strip_reasoning_tags(answer: str) -> str:
    stripped = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL | re.IGNORECASE)
    return stripped.strip()


def call_ollama(prompt: str, preset: LengthPreset) -> dict:
    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": preset.num_predict,
            },
        },
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()

    answer = payload.get("response", "").strip()
    if not answer:
        raise ValueError("Ollama returned an empty response.")

    return payload


def call_llama_cpp(user_prompt: str, preset: LengthPreset) -> dict:
    response = requests.post(
        f"{LLAMA_CPP_BASE_URL}/v1/chat/completions",
        json={
            "model": LLAMA_CPP_MODEL,
            "messages": build_llama_cpp_messages(user_prompt, preset),
            "max_tokens": preset.num_predict,
            "temperature": 0.3,
            "stream": False,
            "reasoning": "off",
        },
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()

    answer = strip_reasoning_tags(
        (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    )
    if not answer:
        raise ValueError("llama.cpp returned an empty response.")

    prompt_ms = ((payload.get("timings") or {}).get("prompt_ms")) or 0
    predicted_ms = ((payload.get("timings") or {}).get("predicted_ms")) or 0

    return {
        "response": answer,
        "total_duration": int((prompt_ms + predicted_ms) * 1_000_000),
        "raw_payload": payload,
    }


def call_model(prompt: str, preset: LengthPreset) -> dict:
    if MODEL_BACKEND == "llama_cpp":
        return call_llama_cpp(prompt, preset)
    return call_ollama(prompt, preset)


def current_model_name() -> str:
    if MODEL_BACKEND == "llama_cpp":
        return LLAMA_CPP_MODEL
    return OLLAMA_MODEL


def current_model_display_name() -> str:
    if MODEL_BACKEND == "llama_cpp":
        display_name = LLAMA_CPP_MODEL.removesuffix(".gguf")
        for suffix in (".q4_k_m", ".q5_k_m", ".q6_k", ".q8_0"):
            if display_name.endswith(suffix):
                display_name = display_name[: -len(suffix)]
                break
        return display_name
    return OLLAMA_MODEL


def current_model_base_url() -> str:
    if MODEL_BACKEND == "llama_cpp":
        return LLAMA_CPP_BASE_URL
    return OLLAMA_BASE_URL


def current_runtime_label() -> str:
    if MODEL_BACKEND == "llama_cpp":
        return "Local llama.cpp"
    return "Local Ollama"


def serialize_presets() -> list[dict]:
    return [asdict(preset) for preset in LENGTH_PRESETS.values()]


@app.get("/")
def index() -> str:
    return render_template(
        "index.html",
        ui_config={
            "model": current_model_name(),
            "modelDisplay": current_model_display_name(),
            "runtimeLabel": current_runtime_label(),
            "defaultPresetKey": DEFAULT_PRESET_KEY,
            "presets": serialize_presets(),
        },
    )


@app.post("/api/generate")
def generate() -> tuple:
    payload = request.get_json(silent=True) or {}
    prompt = (payload.get("prompt") or "").strip()
    preset_key = payload.get("answer_length") or DEFAULT_PRESET_KEY
    preset = LENGTH_PRESETS.get(preset_key)

    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400

    if preset is None:
        return jsonify({"error": "Unknown answer length."}), 400

    final_prompt = build_generation_prompt(prompt, preset)

    try:
        model_payload, energy_measurement = call_model_with_energy(final_prompt, preset)
    except requests.RequestException as exc:
        return (
            jsonify(
                {
                    "error": (
                        "Could not reach the local model service. "
                        f"Expected it at {current_model_base_url()}."
                    ),
                    "details": str(exc),
                }
            ),
            502,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 502

    session_id = f"SES-{uuid.uuid4().hex[:8].upper()}"
    total_duration_ns = model_payload.get("total_duration") or 0
    if energy_measurement is not None:
        generation_seconds = round(energy_measurement.duration_s, 2)
        energy_required_mwh = energy_measurement.required_mwh
        placeholder_mode = False
    else:
        generation_seconds = round(total_duration_ns / 1_000_000_000, 2) if total_duration_ns else None
        energy_required_mwh = preset.energy_required_mwh
        placeholder_mode = True

    session = UnlockSession(
        session_id=session_id,
        prompt=prompt,
        answer=model_payload["response"].strip(),
        model=current_model_name(),
        preset=preset,
        energy_required_mwh=energy_required_mwh,
        generation_seconds=generation_seconds,
        energy_source=energy_measurement.source if energy_measurement is not None else "preset-fallback",
        placeholder_mode=placeholder_mode,
        energy_measured_mwh=(
            round(energy_measurement.total_mwh, 4) if energy_measurement is not None else None
        ),
        idle_energy_estimate_mwh=(
            round(energy_measurement.idle_estimate_mwh, 4)
            if energy_measurement is not None
            else None
        ),
        idle_average_watts=(
            round(energy_measurement.idle_average_watts, 4)
            if energy_measurement is not None
            else None
        ),
        created_at_s=time.time(),
    )
    refresh_unlock_session(session)
    maybe_log_session_generated(session)
    save_unlock_session(session)

    return jsonify(serialize_unlock_session(session))


@app.get("/api/session/<session_id>")
def session_status(session_id: str) -> tuple:
    session = get_unlock_session(session_id)
    if session is None:
        return jsonify({"error": "Unknown session."}), 404

    refresh_unlock_session(session)
    return jsonify(serialize_unlock_session(session))


@app.post("/api/session/<session_id>/unlock")
def manual_unlock(session_id: str) -> tuple:
    session = get_unlock_session(session_id)
    if session is None:
        return jsonify({"error": "Unknown session."}), 404

    session.answer_locked = False
    session.crank_progress_percent = 100.0
    session.crank_generated_mwh = max(session.crank_generated_mwh, session.energy_required_mwh)
    if session.unlocked_at_s is None:
        session.unlocked_at_s = time.time()
    session.unlock_mode = "manual"
    maybe_log_session_completed(session)

    return jsonify(serialize_unlock_session(session))


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
