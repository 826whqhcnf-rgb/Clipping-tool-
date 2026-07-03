const $ = (id) => document.getElementById(id);

let mode = "auto";
let source = "url";
let pollTimer = null;

// --- Source switching (URL / upload / server file) ---------------------------
document.querySelectorAll(".source").forEach((btn) => {
  btn.addEventListener("click", () => {
    source = btn.dataset.source;
    document.querySelectorAll(".source").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll("[data-srcpanel]").forEach((p) => {
      p.classList.toggle("hidden", p.dataset.srcpanel !== source);
    });
    $("submit-btn").textContent = source === "generate" ? "Generate Shorts" : "Make Shorts";
    if (source === "server") loadServerFiles();
  });
});

document.getElementById("refresh-files").addEventListener("click", loadServerFiles);

// Populate the Generate voice picker.
(async () => {
  try {
    const data = await safeJson(await fetch("/api/voices"));
    const sel = $("gen_voice");
    if (sel && data.voices) {
      sel.innerHTML = data.voices
        .map((v) => `<option value="${escapeAttr(v.id)}">🎙 ${escapeHtml(v.label)}</option>`)
        .join("");
    }
  } catch { /* leave empty; backend default voice is used */ }
})();

async function loadServerFiles() {
  try {
    const data = await safeJson(await fetch("/api/files"));
    const sel = $("server_file");
    const files = data.files || [];
    sel.innerHTML = files.length
      ? files.map((f) => `<option value="${escapeAttr(f.name)}">${escapeHtml(f.name)} (${f.size_mb} MB)</option>`).join("")
      : '<option value="">— no files found —</option>';
    $("server-hint").textContent = files.length
      ? "Pick a file, then Make Shorts."
      : `Drop videos into ${data.dir || "the input folder"} (VS Code Explorer → Upload…), then tap ↻.`;
  } catch {
    $("server-hint").textContent = "Could not list files.";
  }
}

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
    // Generate mode needs an AI key for the scripts — warn if missing.
    if (h.provider === "heuristic") {
      $("gen-hint").textContent =
        "⚠ Generate needs an AI key for scripts — add a free GEMINI_API_KEY (aistudio.google.com/apikey).";
      $("gen-hint").classList.add("warn");
    } else {
      $("gen-hint").textContent =
        `Writes a script (${h.provider}), voices it free, and adds captions — no source video needed.`;
    }
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
  if (source === "server" && !$("server_file").value) {
    return showError("Drop a video into the input folder, then pick it (tap ↻ to refresh).");
  }

  campaignTags = $("campaign_tags").value.trim();
  setBusy(true);
  resetStatus();
  renderedIds.clear();
  $("results").classList.add("hidden");
  $("clip-grid").innerHTML = "";

  try {
    const opts = {
      mode,
      reframe: $("reframe").value,
      caption_style: $("caption_style").value,
      captions: $("captions").checked,
      highlight: $("highlight").checked,
      watermark: $("watermark").value.trim(),
      language: $("language").value.trim() || null,
    };
    if (mode === "auto") {
      opts.num_clips = parseInt($("num_clips").value, 10) || 3;
    } else {
      opts.start = $("start").value.trim() || null;
      opts.end = $("end").value.trim() || null;
    }

    let jobId;
    if (source === "upload") {
      jobId = await uploadInChunks(file, opts);
    } else if (source === "generate") {
      const res = await fetch("/api/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: $("gen_topic").value.trim(),
          num_clips: parseInt($("num_clips").value, 10) || 3,
          caption_style: $("caption_style").value,
          voice: $("gen_voice").value || null,
          language: $("language").value.trim() || null,
        }),
      });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(detailMessage(data) || `Request failed (${res.status})`);
      jobId = data.id;
    } else {
      const payload = source === "server"
        ? { server_file: $("server_file").value, ...opts }
        : { url: $("url").value, ...opts };
      const res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(detailMessage(data) || `Request failed (${res.status})`);
      jobId = data.id;
    }
    poll(jobId);
  } catch (err) {
    showError(err.message);
    setBusy(false);
  }
});

async function safeJson(res) {
  try { return await res.json(); } catch { return {}; }
}

// Upload a file in small chunks (init -> chunk* -> complete). Returns the job id.
// Chunking keeps each request small enough to pass proxy/body-size limits.
async function uploadInChunks(file, opts) {
  const CHUNK = 2 * 1024 * 1024; // 2 MB per request — well under proxy body limits

  const initRes = await fetch("/api/uploads/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, ...opts }),
  });
  const init = await safeJson(initRes);
  if (!initRes.ok) throw new Error(detailMessage(init) || `Upload init failed (${initRes.status})`);
  const id = init.id;

  for (let pos = 0; pos < file.size; pos += CHUNK) {
    const blob = file.slice(pos, pos + CHUNK);
    const res = await fetch(`/api/uploads/${id}/chunk`, { method: "POST", body: blob });
    if (!res.ok) {
      const data = await safeJson(res);
      throw new Error(detailMessage(data) || `Upload failed (${res.status})`);
    }
    const done = Math.min(100, Math.round(((pos + CHUNK) / file.size) * 100));
    $("status-message").textContent = "Uploading file…";
    $("status-pct").textContent = `${done}%`;
    $("bar-fill").style.width = `${done}%`;
  }

  const compRes = await fetch(`/api/uploads/${id}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name }),
  });
  const comp = await safeJson(compRes);
  if (!compRes.ok) throw new Error(detailMessage(comp) || `Upload finalize failed (${compRes.status})`);
  return comp.id;
}

// Turn a FastAPI error body into a readable string (detail can be a validation array).
function detailMessage(data) {
  const d = data && data.detail;
  if (!d) return "";
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return JSON.stringify(d);
}

// --- Polling -----------------------------------------------------------------
function poll(jobId) {
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();
      if (!res.ok) throw new Error(job.detail || "Lost the job");

      updateStatus(job);
      renderClips(job.clips || []);

      if (job.warning) {
        $("status-note").textContent = `⚠ ${job.warning}`;
        $("status-note").classList.remove("hidden");
      }
      if (job.status === "done") {
        clearInterval(pollTimer);
        setBusy(false);
        const n = (job.clips || []).length;
        $("results-title").textContent = n > 1 ? `Your ${n} Shorts` : "Your Short";
        const all = $("download-all");
        if (n > 1) {
          all.href = `/api/jobs/${jobId}/zip`;
          all.classList.remove("hidden");
        } else {
          all.classList.add("hidden");
        }
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
  $("status-note").classList.add("hidden");
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

    // Merge the clip's own hashtags with any campaign-required tags/mention.
    const hashtags = mergeHashtags(clip.hashtags || []);
    const tags = hashtags.map((t) => "#" + t);
    const tagsHtml = tags.length
      ? `<div class="tags">${tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("")}</div>`
      : "";
    const mentions = campaignMentions();
    const caption = [clip.title || "", clip.reason || "", tags.join(" "), mentions]
      .filter(Boolean).join("\n\n");

    const card = document.createElement("div");
    card.className = "clip";
    card.innerHTML = `
      <video src="${src}" controls playsinline preload="metadata"></video>
      <div class="clip-body">
        <div class="clip-title">${escapeHtml(clip.title || "Clip " + clip.index)}</div>
        <div class="clip-reason">${escapeHtml(clip.reason || "")}</div>
        ${tagsHtml}
        <div class="clip-meta">
          ${scoreHtml}
          <span class="timecode">${tc}</span>
        </div>
        <div class="clip-actions">
          <a class="download" href="${src}" download>⬇ Download</a>
          <button type="button" class="copy" data-caption="${escapeAttr(caption)}">📋 Caption</button>
        </div>
        <button type="button" class="post yt-only hidden" data-id="${clip.id}"
          data-title="${escapeAttr(clip.title || "")}"
          data-desc="${escapeAttr(caption)}"
          data-tags="${escapeAttr(hashtags.join(","))}">▶️ Post to YouTube</button>
        <span class="post-result"></span>
      </div>`;
    grid.appendChild(card);
  }
  applyYouTubeVisibility();
}

// Clip actions: copy caption, or post to YouTube.
$("clip-grid").addEventListener("click", async (e) => {
  const copyBtn = e.target.closest(".copy");
  if (copyBtn) {
    try {
      await navigator.clipboard.writeText(copyBtn.dataset.caption);
      const old = copyBtn.textContent;
      copyBtn.textContent = "✓ Copied";
      setTimeout(() => (copyBtn.textContent = old), 1500);
    } catch {
      copyBtn.textContent = "Copy failed";
    }
    return;
  }

  const postBtn = e.target.closest(".post");
  if (postBtn) {
    if (!ytConnected) return showError("Connect a YouTube account first (button above the clips).");
    await uploadClip(postBtn);
  }
});

// Upload one clip to YouTube. Returns true on success. Skips already-posted clips.
async function uploadClip(postBtn) {
  if (postBtn.dataset.posted === "1") return true;
  const result = postBtn.parentElement.querySelector(".post-result");
  postBtn.disabled = true;
  postBtn.textContent = "Uploading to YouTube…";
  result.textContent = "";
  try {
    const res = await fetch("/api/youtube/upload", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clip_id: postBtn.dataset.id,
        title: postBtn.dataset.title,
        description: postBtn.dataset.desc,
        tags: (postBtn.dataset.tags || "").split(",").filter(Boolean),
        privacy: $("yt-privacy").value,
      }),
    });
    const data = await safeJson(res);
    if (!res.ok) throw new Error(detailMessage(data) || `Upload failed (${res.status})`);
    postBtn.dataset.posted = "1";
    postBtn.textContent = "✓ Posted";
    result.innerHTML = ` <a href="${data.url}" target="_blank" rel="noopener">${data.url}</a>`;
    return true;
  } catch (err) {
    postBtn.disabled = false;
    postBtn.textContent = "▶️ Post to YouTube";
    result.textContent = " " + err.message;
    return false;
  }
}

// Post every clip to YouTube, one after another.
$("yt-post-all").addEventListener("click", async () => {
  if (!ytConnected) return showError("Connect a YouTube account first.");
  const btn = $("yt-post-all");
  const buttons = [...document.querySelectorAll(".post")];
  btn.disabled = true;
  let done = 0;
  for (const b of buttons) {
    const ok = await uploadClip(b);
    if (ok) done += 1;
    btn.textContent = `Posting… ${done}/${buttons.length}`;
  }
  btn.textContent = `Posted ${done}/${buttons.length}`;
  btn.disabled = false;
});

// --- Campaign tags (applied to every clip) -----------------------------------
let campaignTags = "";

function campaignTokens() {
  return campaignTags.split(/\s+/).map((t) => t.trim()).filter(Boolean);
}

function mergeHashtags(clipTags) {
  const extra = campaignTokens()
    .filter((t) => !t.startsWith("@") && !t.startsWith("http"))
    .map((t) => t.replace(/^#/, "").replace(/[^0-9a-zA-Z_]/g, "").toLowerCase())
    .filter(Boolean);
  const out = [];
  for (const t of [...clipTags, ...extra]) if (t && !out.includes(t)) out.push(t);
  return out.slice(0, 15);
}

// Any @mentions or links the campaign requires, kept verbatim for the description.
function campaignMentions() {
  return campaignTokens().filter((t) => t.startsWith("@") || t.startsWith("http")).join(" ");
}

// --- YouTube posting ---------------------------------------------------------
let ytConfigured = false;
let ytConnected = false;
let ytPollTimer = null;

function applyYouTubeVisibility() {
  document.querySelectorAll(".yt-only").forEach((el) => el.classList.toggle("hidden", !ytConnected));
}

async function refreshYouTube() {
  const s = await safeJson(await fetch("/api/youtube/status"));
  ytConfigured = !!s.configured;
  ytConnected = !!s.connected;
  $("yt-bar").classList.toggle("hidden", !ytConfigured);
  $("yt-connect").classList.toggle("hidden", ytConnected);
  $("yt-status").textContent = ytConnected ? "▶️ YouTube connected" : "▶️ Post to YouTube";
  applyYouTubeVisibility();
}

$("yt-connect").addEventListener("click", async () => {
  const btn = $("yt-connect");
  btn.disabled = true;
  try {
    const data = await safeJson(await fetch("/api/youtube/connect", { method: "POST" }));
    if (!data.user_code) throw new Error(detailMessage(data) || "Couldn't start authorization.");
    $("yt-status").innerHTML =
      `Open <a href="${data.verification_url}" target="_blank" rel="noopener">${data.verification_url}</a> and enter code <b>${data.user_code}</b>`;
    const interval = (data.interval || 5) * 1000;
    clearInterval(ytPollTimer);
    ytPollTimer = setInterval(async () => {
      const p = await safeJson(await fetch("/api/youtube/poll", { method: "POST" }));
      if (p.status === "connected") {
        clearInterval(ytPollTimer);
        await refreshYouTube();
      } else if (p.status === "expired" || (p.status && !["pending", "no_flow"].includes(p.status))) {
        clearInterval(ytPollTimer);
        btn.disabled = false;
        $("yt-status").textContent = `▶️ Authorization ${p.status}. Try again.`;
      }
    }, interval);
  } catch (err) {
    btn.disabled = false;
    $("yt-status").textContent = "▶️ " + err.message;
  }
});

refreshYouTube().catch(() => {});

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

function escapeAttr(str) {
  return String(str).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/\n/g, "&#10;");
}
