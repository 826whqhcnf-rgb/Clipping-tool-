const $ = (id) => document.getElementById(id);

let mode = "auto";
let source = "url";
let pollTimer = null;

// --- Source switching (URL vs file upload) -----------------------------------
document.querySelectorAll(".source").forEach((btn) => {
  btn.addEventListener("click", () => {
    source = btn.dataset.source;
    document.querySelectorAll(".source").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll("[data-srcpanel]").forEach((p) => {
      p.classList.toggle("hidden", p.dataset.srcpanel !== source);
    });
  });
});

// --- Mode switching ----------------------------------------------------------
document.querySelectorAll(".mode").forEach((btn) => {
  btn.addEventListener("click", () => {
    mode = btn.dataset.mode;
    document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".mode-panel").forEach((p) => {
      p.classList.toggle("hidden", p.dataset.panel !== mode);
    });
    $("submit-btn").textContent = mode === "auto" ? "Make Shorts" : "Make Short";
  });
});

// --- Environment check (ffmpeg + Claude) -------------------------------------
fetch("/api/health")
  .then((r) => r.json())
  .then((h) => {
    if (!h.ffmpeg || !h.ffprobe) {
      $("ffmpeg-warn").textContent =
        "⚠ ffmpeg/ffprobe not found on the server — install them or clips will fail to render.";
    }
    const hints = {
      gemini: "Google Gemini will analyze the transcript to pick the most viral moments.",
      claude: "Claude will analyze the transcript to pick the most viral moments.",
      heuristic: "No AI key set — using a speech-density heuristic. Add a GEMINI_API_KEY for smarter clip picks.",
    };
    $("ai-hint").textContent = hints[h.provider] || hints.heuristic;
  })
  .catch(() => {});

// --- Submit ------------------------------------------------------------------
$("clip-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (pollTimer) clearInterval(pollTimer);

  const file = $("file").files[0];
  if (source === "url" && !$("url").value.trim()) {
    return showError("Please paste a video URL.");
  }
  if (source === "upload" && !file) {
    return showError("Please choose a video file to upload.");
  }

  setBusy(true);
  resetStatus();
  renderedIds.clear();
  $("results").classList.add("hidden");
  $("clip-grid").innerHTML = "";

  try {
    let res;
    if (source === "upload") {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("mode", mode);
      fd.append("reframe", $("reframe").value);
      fd.append("captions", $("captions").checked);
      fd.append("highlight", $("highlight").checked);
      fd.append("language", $("language").value.trim());
      if (mode === "auto") {
        fd.append("num_clips", parseInt($("num_clips").value, 10) || 3);
      } else {
        fd.append("start", $("start").value.trim());
        fd.append("end", $("end").value.trim());
      }
      $("status-message").textContent = "Uploading file…";
      res = await fetch("/api/uploads", { method: "POST", body: fd });
    } else {
      const body = {
        url: $("url").value,
        mode,
        reframe: $("reframe").value,
        captions: $("captions").checked,
        highlight: $("highlight").checked,
        language: $("language").value.trim() || null,
      };
      if (mode === "auto") {
        body.num_clips = parseInt($("num_clips").value, 10) || 3;
      } else {
        body.start = $("start").value.trim() || null;
        body.end = $("end").value.trim() || null;
      }
      res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Request failed");
    poll(data.id);
  } catch (err) {
    showError(err.message);
    setBusy(false);
  }
});

// --- Polling -----------------------------------------------------------------
function poll(jobId) {
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();
      if (!res.ok) throw new Error(job.detail || "Lost the job");

      updateStatus(job);
      renderClips(job.clips || []);

      if (job.status === "done") {
        clearInterval(pollTimer);
        setBusy(false);
        $("results-title").textContent =
          (job.clips || []).length > 1 ? `Your ${job.clips.length} Shorts` : "Your Short";
      } else if (job.status === "error") {
        clearInterval(pollTimer);
        setBusy(false);
        showError(job.error || "Something went wrong.");
      }
    } catch (err) {
      clearInterval(pollTimer);
      setBusy(false);
      showError(err.message);
    }
  }, 1500);
}

// --- UI helpers --------------------------------------------------------------
function setBusy(busy) {
  $("submit-btn").disabled = busy;
}

function resetStatus() {
  $("status-card").classList.remove("hidden");
  $("status-error").classList.add("hidden");
  $("bar-fill").style.width = "0%";
  $("status-message").textContent = "Starting…";
  $("status-pct").textContent = "0%";
}

function updateStatus(job) {
  $("status-message").textContent = job.message || job.stage || "Working…";
  $("status-pct").textContent = `${job.progress || 0}%`;
  $("bar-fill").style.width = `${job.progress || 0}%`;
}

function showError(msg) {
  $("status-error").textContent = `❌ ${msg}`;
  $("status-error").classList.remove("hidden");
}

const renderedIds = new Set();

function renderClips(clips) {
  if (clips.length && $("results").classList.contains("hidden")) {
    $("results").classList.remove("hidden");
  }
  const grid = $("clip-grid");
  for (const clip of clips) {
    if (renderedIds.has(clip.id)) continue;
    renderedIds.add(clip.id);

    const src = `/api/clips/${clip.id}`;
    const scoreClass = clip.score == null ? "" : clip.score >= 70 ? "" : clip.score >= 40 ? "mid" : "low";
    const scoreHtml = clip.score == null ? "" : `<span class="score ${scoreClass}">🔥 ${clip.score}</span>`;
    const tc = `${fmt(clip.start)}–${fmt(clip.end)}`;

    const card = document.createElement("div");
    card.className = "clip";
    card.innerHTML = `
      <video src="${src}" controls playsinline preload="metadata"></video>
      <div class="clip-body">
        <div class="clip-title">${escapeHtml(clip.title || "Clip " + clip.index)}</div>
        <div class="clip-reason">${escapeHtml(clip.reason || "")}</div>
        <div class="clip-meta">
          ${scoreHtml}
          <span class="timecode">${tc}</span>
        </div>
        <a class="download" href="${src}" download>⬇ Download</a>
      </div>`;
    grid.appendChild(card);
  }
}

function fmt(s) {
  s = Math.round(s || 0);
  const m = Math.floor(s / 60);
  const sec = String(s % 60).padStart(2, "0");
  return `${m}:${sec}`;
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}
