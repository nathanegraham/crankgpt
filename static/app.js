const config = window.CRANKINATOR_CONFIG;

const state = {
  status: "Idle",
  answer: "",
  answerLocked: true,
  crankProgressPercent: 0,
  energyRequiredMwh: null,
  sessionId: null,
  meterConnected: false,
  meterError: null,
};

let sessionPollTimer = null;

const promptForm = document.getElementById("promptForm");
const promptInput = document.getElementById("promptInput");
const lengthInput = document.getElementById("lengthInput");
const lengthButtons = Array.from(document.querySelectorAll(".length-button"));
const submitButton = document.getElementById("submitButton");
const unlockButton = document.getElementById("unlockButton");
const resetButton = document.getElementById("resetButton");
const statusChip = document.getElementById("statusChip");
const messageBanner = document.getElementById("messageBanner");
const answerCard = document.getElementById("answerCard");
const answerText = document.getElementById("answerText");
const answerMask = document.getElementById("answerMask");
const energyRequiredValue = document.getElementById("energyRequiredValue");
const crankProgressValue = document.getElementById("crankProgressValue");
const answerLockedValue = document.getElementById("answerLockedValue");
const progressFill = document.getElementById("progressFill");
const progressHeadline = document.getElementById("progressHeadline");
const generationTiming = document.getElementById("generationTiming");
const sessionValue = document.getElementById("sessionId");
const presetEnergy = document.getElementById("presetEnergy");
const presetWords = document.getElementById("presetWords");

function getPreset(key) {
  return config.presets.find((preset) => preset.key === key) || config.presets[0];
}

function selectedPreset() {
  return getPreset(lengthInput.value);
}

function setStatus(nextStatus) {
  state.status = nextStatus;
  statusChip.textContent = nextStatus;
  statusChip.className = `status-chip status-${nextStatus.toLowerCase()}`;
}

function setMessage(message, isError = false) {
  messageBanner.textContent = message;
  messageBanner.classList.toggle("is-error", isError);
}

function formatEnergyMwh(value) {
  if (value === null || value === undefined) {
    return "--";
  }
  if (value >= 10) {
    return `${value.toFixed(1)} mWh`;
  }
  if (value >= 1) {
    return `${value.toFixed(2)} mWh`;
  }
  return `${value.toFixed(3)} mWh`;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function renderPresetSummary() {
  const preset = selectedPreset();
  presetWords.textContent = `${preset.label} · ${preset.words} words`;
  presetEnergy.textContent = "Measured live";

  lengthButtons.forEach((button) => {
    const isActive = button.dataset.presetKey === preset.key;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
}

function renderMetrics() {
  energyRequiredValue.textContent = formatEnergyMwh(state.energyRequiredMwh);
  crankProgressValue.textContent = `${Math.round(state.crankProgressPercent)}%`;
  answerLockedValue.textContent = state.answerLocked ? "true" : "false";
  progressFill.style.width = `${state.crankProgressPercent}%`;

  if (state.status === "Generating") {
    progressHeadline.textContent = "Generating answer";
  } else if (state.status === "Locked" && state.meterConnected) {
    progressHeadline.textContent = state.crankProgressPercent > 0
      ? "Keep cranking to unlock"
      : "Crank to unlock the answer";
  } else if (state.status === "Locked") {
    progressHeadline.textContent = "Waiting for UM34C meter";
  } else if (state.status === "Unlocked") {
    progressHeadline.textContent = "Answer revealed";
  } else {
    progressHeadline.textContent = "Waiting for a generated answer";
  }
}

function renderAnswer() {
  answerText.textContent = state.answer || "Your answer will appear here.";
  answerCard.dataset.locked = String(state.answerLocked);
  answerMask.querySelector(".mask-kicker").textContent = state.answerLocked ? "Locked" : "Unlocked";
  answerMask.querySelector("h3").textContent = state.answerLocked ? "Answer hidden" : "Answer revealed";
  if (state.answerLocked && state.meterConnected) {
    answerMask.querySelector("p").textContent = "Keep cranking until progress reaches 100%.";
  } else if (state.answerLocked && state.meterError) {
    answerMask.querySelector("p").textContent = "UM34C is unavailable. Reconnect it or use manual unlock.";
  } else if (state.answerLocked) {
    answerMask.querySelector("p").textContent = "Waiting for the UM34C reading to start the unlock session.";
  } else {
    answerMask.querySelector("p").textContent = "Energy target reached. The answer is now visible.";
  }
}

function setBusy(isBusy) {
  submitButton.disabled = isBusy;
  unlockButton.disabled = isBusy || !state.answer || !state.answerLocked || !state.sessionId;
  resetButton.disabled = isBusy;
  promptInput.disabled = isBusy;
  lengthButtons.forEach((button) => {
    button.disabled = isBusy;
  });
}

function stopPolling() {
  if (sessionPollTimer !== null) {
    window.clearTimeout(sessionPollTimer);
    sessionPollTimer = null;
  }
}

function schedulePoll(delayMs = 1000) {
  stopPolling();
  if (!state.sessionId || !state.answerLocked) {
    return;
  }

  sessionPollTimer = window.setTimeout(fetchSessionStatus, delayMs);
}

function applySessionPayload(payload, { manual = false, polled = false } = {}) {
  state.sessionId = payload.session_id || state.sessionId;
  state.answer = payload.answer ?? state.answer;
  state.answerLocked = Boolean(payload.answer_locked);
  state.crankProgressPercent = Number(payload.crank_progress_percent || 0);
  state.energyRequiredMwh = payload.energy_required_mwh ?? state.energyRequiredMwh;
  state.meterConnected = Boolean(payload.meter_connected);
  state.meterError = payload.meter_error || null;

  setStatus(state.answerLocked ? "Locked" : "Unlocked");
  renderAnswer();
  renderMetrics();

  if (!polled && payload.generation_seconds) {
    generationTiming.textContent = `Generated in ${payload.generation_seconds.toFixed(2)}s`;
  }
  sessionValue.textContent = state.sessionId || "Waiting";

  if (!state.answerLocked) {
    setMessage(
      manual ? "Manual unlock complete. The answer is now visible." : "Crank target reached. Answer unlocked."
    );
    stopPolling();
    return;
  }

  if (state.meterConnected) {
    if (state.crankProgressPercent > 0) {
      const remainingPercent = Math.max(0, 100 - Math.round(state.crankProgressPercent));
      setMessage(`Keep cranking. ${remainingPercent}% remaining.`);
    } else {
      setMessage("Answer generated. Start cranking to unlock it.");
    }
    return;
  }

  if (state.meterError) {
    setMessage(`Answer generated, but the UM34C is unavailable: ${state.meterError}`);
    return;
  }

  if (!polled && payload.placeholder_mode) {
    setMessage("Answer generated. Jetson energy measurement fell back to the preset estimate.");
    return;
  }

  setMessage("Answer generated. Waiting for the UM34C session to start.");
}

async function fetchSessionStatus() {
  if (!state.sessionId) {
    return;
  }

  try {
    const response = await fetch(`/api/session/${encodeURIComponent(state.sessionId)}`);
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.error || "Could not refresh the unlock session.");
    }

    applySessionPayload(payload, { polled: true });

    if (state.answerLocked) {
      schedulePoll(state.meterConnected ? 800 : 1500);
    }
  } catch (error) {
    setMessage(error.message, true);
    schedulePoll(1500);
  }
}

function resetState() {
  stopPolling();
  state.status = "Idle";
  state.answer = "";
  state.answerLocked = true;
  state.crankProgressPercent = 0;
  state.energyRequiredMwh = null;
  state.sessionId = null;
  state.meterConnected = false;
  state.meterError = null;

  lengthInput.value = config.defaultPresetKey;

  setStatus("Idle");
  setMessage("Prompt is ready. Generate a locked answer.");
  renderPresetSummary();
  renderAnswer();
  renderMetrics();

  generationTiming.textContent = "No generation yet";
  sessionValue.textContent = "Waiting";
  promptInput.disabled = false;
  submitButton.disabled = false;
  unlockButton.disabled = true;
  resetButton.disabled = false;
  lengthButtons.forEach((button) => {
    button.disabled = false;
  });
}

async function generateAnswer(event) {
  event.preventDefault();

  const prompt = promptInput.value.trim();
  if (!prompt) {
    setMessage("Enter a prompt before generating an answer.", true);
    promptInput.focus();
    return;
  }

  setStatus("Generating");
  setMessage("Sending the prompt to the local model service.");
  setBusy(true);
  stopPolling();

  state.answer = "";
  state.answerLocked = true;
  state.crankProgressPercent = 0;
  state.energyRequiredMwh = null;
  state.sessionId = null;
  state.meterConnected = false;
  state.meterError = null;
  renderAnswer();
  renderMetrics();

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        prompt,
        answer_length: lengthInput.value,
      }),
    });

    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.error || "Generation failed.");
    }

    applySessionPayload(payload);
    generationTiming.textContent = payload.generation_seconds
      ? `Generated in ${payload.generation_seconds.toFixed(2)}s`
      : "Generation complete";
    unlockButton.disabled = false;

    if (state.answerLocked) {
      schedulePoll(payload.meter_connected ? 800 : 1500);
    }
  } catch (error) {
    state.answer = "";
    state.answerLocked = true;
    state.crankProgressPercent = 0;
    state.energyRequiredMwh = null;
    state.sessionId = null;
    state.meterConnected = false;
    state.meterError = null;
    setStatus("Idle");
    setMessage(error.message, true);
    renderAnswer();
    renderMetrics();
    generationTiming.textContent = "Generation failed";
    sessionValue.textContent = "Waiting";
  } finally {
    setBusy(false);
  }
}

async function unlockAnswer() {
  if (!state.answer || !state.answerLocked || !state.sessionId) {
    return;
  }

  setBusy(true);

  try {
    const response = await fetch(`/api/session/${encodeURIComponent(state.sessionId)}/unlock`, {
      method: "POST",
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(payload.error || "Manual unlock failed.");
    }

    applySessionPayload(payload, { manual: true });
    unlockButton.disabled = true;
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    setBusy(false);
  }
}

promptForm.addEventListener("submit", generateAnswer);
unlockButton.addEventListener("click", unlockAnswer);
resetButton.addEventListener("click", () => {
  promptInput.value = "";
  resetState();
});

lengthButtons.forEach((button) => {
  button.addEventListener("click", () => {
    lengthInput.value = button.dataset.presetKey;
    renderPresetSummary();
  });
});

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    generateAnswer(event);
  }
});

resetState();
